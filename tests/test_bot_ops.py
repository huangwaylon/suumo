"""Alerts (who hears about what), the per-person store, state persistence and channel upkeep. No Discord."""
import asyncio
import json
import shutil
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from suumo.bot import alerts
from suumo.bot import text as T
from suumo.bot.app import SuumoBot
from suumo.bot.store import VERSION, DiscordStateBackend, Store
from suumo.catalog import Catalog, Query

FIXTURE = Path(__file__).parent / "fixtures" / "data"
TODAY = date(2026, 10, 6)


@pytest.fixture
def data(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "data")
    return tmp_path / "data"


def records(data, type_="used_condo"):
    return [json.loads(line) for line in (data / "tokyo" / type_ / "13219.jsonl").read_text().splitlines()]


def event(kind, r, **payload):
    return {"kind": kind, "type": r["type"], "id": r["id"], "pref": "tokyo", "area_code": r["area_code"],
            "payload": {"area": r["area"], "price": r.get("price"), "path": r["url"][len("https://suumo.jp"):],
                        **payload}}


def add_run(data, run_id, events):
    (data / "events" / f"{run_id}.json").write_text(json.dumps(events, ensure_ascii=False))


def snap_of(data):
    c = Catalog(data, lambda: TODAY)
    c.refresh()
    return c.snap


def heading(q):
    return f"S:{q.types}"


# ---------- which events alert whom ----------

def test_saved_search_gets_matching_new_relisted_and_drops(data):
    condos, houses = records(data), records(data, "used_house")
    snap = snap_of(data)
    store = Store()
    u = store.user(1)
    store.save_search(u, Query(types=("used_condo",)), "2026-10-06", "20261004T040000")
    evs = [event("new", condos[2]), event("new", houses[0]), event("relisted", condos[3]),
           event("price_changed", condos[5], old_price=condos[5]["price"] + 1_000_000),
           event("price_changed", condos[6], old_price=condos[6]["price"] - 1_000_000),   # went up
           event("removed", condos[8]),
           event("new", condos[4])]                                                      # same property as 2
    sections = alerts.collect(u, snap, [("r", evs)], heading, "FAV")
    assert [(h, [(e["kind"], e["id"]) for e in es]) for h, es in sections] == [
        ("S:('used_condo',)", [("new", condos[2]["id"]), ("relisted", condos[3]["id"]),
                               ("price_changed", condos[5]["id"])])]


def test_favorites_get_any_change_unless_turned_off(data):
    condos = records(data)
    snap = snap_of(data)
    u = Store().user(1)
    key = f"used_condo:{condos[0]['id']}"
    u.favorites[key] = {"price": condos[0]["price"], "added": "2026-10-01"}
    evs = [event("removed", condos[0]), event("price_changed", condos[0], old_price=1), event("new", condos[1])]
    [(h, es)] = alerts.collect(u, snap, [("r", evs)], heading, "FAV")
    assert h == "FAV" and [e["kind"] for e in es] == ["price_changed", "removed"]
    u.notify_favorites = False
    assert alerts.collect(u, snap, [("r", evs)], heading, "FAV") == []


def test_unfetched_page_doesnt_hide_a_new_listing(data):
    condos = records(data)
    target = next(r for r in condos if not r.get("features"))
    snap = snap_of(data)
    item = snap.by_key[f"used_condo:{target['id']}"]
    item.has_detail = False   # its page comes tomorrow
    u = Store().user(1)
    Store().save_search(u, Query(features=("ペット相談",)), "d", "r0")
    assert alerts.collect(u, snap, [("r", [event("new", target)])], heading, "FAV")


def test_same_property_by_two_agents_alerts_once(data):
    condos = records(data)
    a = next(r for r in condos if r.get("dup_key"))
    b = {**a, "id": "99999999"}
    with open(data / "tokyo" / "used_condo" / "13219.jsonl", "a") as f:
        f.write(json.dumps(b, ensure_ascii=False) + "\n")
    snap = snap_of(data)
    u = Store().user(1)
    Store().save_search(u, Query(types=("used_condo",)), "d", "r0")
    [(_, es)] = alerts.collect(u, snap, [("r", [event("new", a), event("new", b)])], heading, "FAV")
    assert len(es) == 1


def test_alert_message_fits_and_summarizes():
    e = {"kind": "new", "type": "used_condo", "payload": {"area": "狛江市", "town": "x" * 120, "price": 1}}
    text = T.alert_message([("💾 条件", [e] * 40)], mention=5)
    assert len(text) <= 2000 and text.startswith("<@5>") and "ほか" in text


# ---------- store ----------

def test_store_round_trip_and_version_guard():
    s = Store()
    u = s.user(1, "Alice")
    s.save_search(u, Query(types=("land",), price_max=30_000_000), "2026-10-06", "r1")
    s.toggle_favorite(u, "land:1", 100, "2026-10-06", "r1")
    back = Store.from_json(json.loads(json.dumps(s.to_json())))
    assert back.to_json() == s.to_json()
    assert back.users["1"].searches[0].query == Query(types=("land",), price_max=30_000_000)
    with pytest.raises(SystemExit):
        Store.from_json({"version": VERSION + 1})


def test_favorite_toggle_and_purged_favorites(data):
    s = Store()
    u = s.user(1)
    assert s.toggle_favorite(u, "used_condo:1", 5, "d", "r") is True
    assert s.toggle_favorite(u, "used_condo:1", 5, "d", "r") is False
    s.toggle_favorite(u, "used_condo:404", 5, "d", "r")
    snap = snap_of(data)
    s.drop_missing_favorites(snap, date(2026, 10, 6))      # one reload can catch the export mid-way: kept
    assert u.favorites["used_condo:404"]["missing"] == "2026-10-06"
    s.drop_missing_favorites(snap, date(2026, 10, 12))
    assert "used_condo:404" in u.favorites
    s.drop_missing_favorites(snap, date(2026, 10, 13))     # gone for a week: purged from data/
    assert u.favorites == {}


def test_favorite_that_reappears_is_no_longer_missing(data):
    s = Store()
    u = s.user(1)
    key = f"used_condo:{records(data)[0]['id']}"
    u.favorites[key] = {"price": 1, "added": "d", "missing": "2026-10-01"}
    s.drop_missing_favorites(snap_of(data), date(2026, 10, 20))
    assert u.favorites[key] == {"price": 1, "added": "d"}


def test_alerts_restart_from_now_after_being_off():
    s = Store()
    u = s.user(1)
    s.save_search(u, Query(types=("land",)), "d", "r1")
    s.delete_search(u, u.searches[0].id)
    s.save_search(u, Query(types=("used_condo",)), "d", "r9")   # weeks later: no backlog of r2..r9
    assert u.last_run == "r9"
    s.save_search(u, Query(types=("land",)), "d", "r10")        # already on: progress kept
    assert u.last_run == "r9"
    u2 = s.user(2)
    s.toggle_favorite(u2, "land:1", 1, "d", "r3")
    s.set_notify_favorites(u2, False, "r3")
    s.set_notify_favorites(u2, True, "r7")
    assert u2.last_run == "r7"


# ---------- the bot process (Discord mocked) ----------

class AlertBot:
    send_alerts = SuumoBot.send_alerts

    def __init__(self, data):
        self.data_dir = data
        self.catalog = Catalog(data, lambda: TODAY)
        self.catalog.refresh()
        self.store = Store()
        self.persist = AsyncMock()
        self.delivered = []
        self.fail = False
        self._retry = {}

    async def deliver(self, uid, u, sections):
        if self.fail:
            return False
        self.delivered.append((uid, sections))
        return True


async def test_alerts_advance_per_person_and_retry_on_failure(data):
    bot = AlertBot(data)
    condos = records(data)
    u = bot.store.user(1)
    bot.store.save_search(u, Query(types=("used_condo",)), "d", "20261004T040000")
    await bot.send_alerts()
    assert bot.delivered == []                        # nothing newer than when the search was saved
    add_run(data, "20261005T040000", [event("new", condos[3])])
    bot.catalog.refresh()
    bot.fail = True
    await bot.send_alerts()
    assert u.last_run == "20261004T040000"            # not delivered: retried later
    bot.fail = False
    await bot.send_alerts()
    assert bot.delivered == []                        # backing off
    bot._retry.clear()                                # time passes
    await bot.send_alerts()
    assert [uid for uid, _ in bot.delivered] == ["1"] and u.last_run == "20261005T040000"
    bot.persist.assert_awaited()
    await bot.send_alerts()
    assert len(bot.delivered) == 1                    # never twice


async def test_first_alert_check_starts_from_now(data):
    bot = AlertBot(data)
    u = bot.store.user(1)
    u.favorites["used_condo:1"] = {"price": 1, "added": "d"}   # added by an older version without last_run
    await bot.send_alerts()
    assert u.last_run == "20261004T040000" and bot.delivered == []


async def test_dm_refused_falls_back_to_a_channel_mention(data):
    bot = MagicMock()
    bot.store = Store()
    u = bot.store.user(5)
    user = MagicMock()
    user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    bot.get_user.return_value = user
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.channel = AsyncMock(return_value=channel)
    assert await SuumoBot.deliver(bot, "5", u, [("h", [])])
    assert u.dm_failed and channel.send.await_args.args[0].startswith("<@5>")


async def test_persist_coalesces_and_retries(tmp_path):
    bot = MagicMock()
    bot.store = Store()
    bot.store.user(1)
    bot._save_lock = asyncio.Lock()
    bot.backend.save = AsyncMock(side_effect=[RuntimeError("discord down"), None])
    await SuumoBot.persist(bot)
    assert bot.store.dirty                            # failed: still dirty, saved on the next change
    await SuumoBot.persist(bot)
    assert not bot.store.dirty and bot.backend.save.await_count == 2


# ---------- state message ----------

def message(author, *, attachment=None, menu=False):
    m = MagicMock()
    m.author.id = author
    m.attachments = []
    if attachment is not None:
        a = MagicMock()
        a.filename = "state.json"
        a.read = AsyncMock(return_value=json.dumps(attachment).encode())
        m.attachments = [a]
    button = MagicMock()
    button.custom_id = "suumo:search" if menu else None
    row = MagicMock()
    row.children = [button]
    m.components = [row] if menu else []
    m.delete = AsyncMock()
    return m


class History:
    def __init__(self, messages):
        self.messages = messages

    def __call__(self, limit):
        async def gen():
            for m in self.messages[:limit]:
                yield m
        return gen()


async def test_state_loads_from_channel_then_backup(tmp_path):
    client = MagicMock()
    client.user.id = 9
    channel = MagicMock()
    channel.history = History([message(1, attachment={"x": 1}), message(9, attachment={"version": 1})])
    client.get_channel.return_value = channel
    backend = DiscordStateBackend(client, 2, tmp_path / "backup.json")
    assert await backend.load() == {"version": 1}            # only our own message counts
    channel.history = History([])
    assert await backend.load() is None
    channel.send = AsyncMock(return_value=MagicMock())
    await backend.save({"version": 1, "users": {}}, "summary")
    assert json.loads((tmp_path / "backup.json").read_text())["version"] == 1
    assert await backend.load() == {"version": 1, "users": {}}


async def test_tidy_channel_keeps_one_menu_at_the_bottom():
    bot = MagicMock()
    bot.user.id = 9
    bot.is_menu = lambda m: SuumoBot.is_menu(bot, m)
    old_menu, feed = message(9, menu=True), message(9)
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.history = History([feed, old_menu])                # newest first: a feed post after the menu
    bot.channel = AsyncMock(return_value=channel)
    await SuumoBot.tidy_channel(bot)
    old_menu.delete.assert_awaited()
    feed.delete.assert_not_awaited()
    assert channel.send.await_args.kwargs["silent"] is True
    newest_menu = message(9, menu=True)
    channel.history = History([newest_menu, feed])
    channel.send.reset_mock()
    await SuumoBot.tidy_channel(bot)
    channel.send.assert_not_awaited()
    newest_menu.delete.assert_not_awaited()
