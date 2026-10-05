"""The bot's screens, driven with fake interactions over real listings (tests/fixtures/data). No Discord."""
import json
import shutil
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from suumo.bot import app as app_mod
from suumo.bot import text as T
from suumo.bot.app import SuumoBot
from suumo.bot.store import MAX_SEARCHES, Store
from suumo.bot.ui import PAGE_SIZE, MainMenu, Session
from suumo.catalog import Catalog, Query

FIXTURE = Path(__file__).parent / "fixtures" / "data"
TODAY = date(2026, 10, 6)
CHANNEL = 111


class FakeBot:
    """Real catalog, store and session bookkeeping; Discord calls mocked."""
    register = SuumoBot.register
    unregister = SuumoBot.unregister
    ready_catalog = SuumoBot.ready_catalog
    on_interaction = SuumoBot.on_interaction

    def __init__(self, data):
        self.catalog = Catalog(data, lambda: TODAY)
        self.catalog.refresh()
        self.store = Store()
        self.sessions = {}
        self.channel_id = CHANNEL
        self.loaded = True
        self.after_change = AsyncMock()


@pytest.fixture
def data(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture
def bot(data):
    return FakeBot(data)


def make_interaction(uid=42, name="Alice"):
    i = MagicMock()
    i.user.id, i.user.display_name = uid, name
    i.channel_id = CHANNEL
    i.response.send_message = AsyncMock()
    i.response.edit_message = AsyncMock()
    i.response.is_done = MagicMock(return_value=False)
    i.data = {}
    return i


@pytest.fixture
def interaction():
    return make_interaction()


def session_of(bot):
    return list(bot.sessions.values())[-1]


def item(view, name):
    return next(c for c in view.children if c.custom_id.endswith(f":{name}"))


async def tap(view, name, interaction):
    await item(view, name).callback(interaction)
    return last(interaction)


async def pick(view, name, interaction, *values):
    """Choose options as Discord would: only values the dropdown offers."""
    offered = options(view, name)
    assert all(v in offered for v in values), (values, offered)
    interaction.data = {"values": list(values)}
    await item(view, name).callback(interaction)
    return last(interaction)


def last(interaction):
    """(content, embeds) of the latest redraw."""
    name, args, kwargs = [c for c in interaction.response.mock_calls if c[0] in ("send_message", "edit_message")][-1]
    content = kwargs.get("content", args[0] if args else None)
    return content, kwargs.get("embeds", [])


def options(view, name):
    return [o.value for o in item(view, name).options]


def assert_discord_limits(view, content, embeds):
    assert content is None or len(content) <= 2000
    assert len(embeds) <= 10 and sum(len(e) for e in embeds) <= 6000
    rows = {}
    for c in view.children:
        rows.setdefault(c.row, []).append(c)
        assert len(c.custom_id) <= 100
        if isinstance(c, discord.ui.Select):
            assert 1 <= len(c.options) <= 25
            assert all(len(o.label) <= 100 and len(o.description or "") <= 100 for o in c.options)
            assert len({o.value for o in c.options}) == len(c.options)
        else:
            assert len(c.label) <= 80
    assert len(rows) <= 5
    for row in rows.values():
        assert len(row) == 1 if isinstance(row[0], discord.ui.Select) else len(row) <= 5
    for e in embeds:
        assert len(e.title or "") <= 256 and len(e.fields) <= 25
        assert all(len(f.value) <= 1024 and len(f.name) <= 256 for f in e.fields)


# ---------- menu ----------

async def test_menu_is_persistent_and_channel_only(bot, interaction):
    menu = MainMenu(bot)
    assert menu.is_persistent()
    labels = ["物件をさがす", "新着", "値下げ", "お気に入り", "保存した条件", "使い方"]
    assert [c.label for c in menu.children] == labels
    assert await menu.interaction_check(interaction)
    interaction.channel_id = 999
    assert not await menu.interaction_check(interaction)


async def test_help(bot, interaction):
    await MainMenu(bot).help.callback(interaction)
    assert interaction.response.send_message.await_args.args[0] == T.HELP


async def test_waits_for_the_catalog(bot, interaction):
    bot.loaded = False
    await Session.open(bot, interaction, "panel")
    assert interaction.response.send_message.await_args.args[0] == T.LOADING
    assert not bot.sessions


# ---------- search panel ----------

async def test_panel_opens_privately_with_live_count(bot, interaction):
    await Session.open(bot, interaction, "panel")
    call = interaction.response.send_message.await_args
    assert call.kwargs["ephemeral"] is True
    s = session_of(bot)
    total = bot.catalog.snap.count(Query())
    assert f"該当 {total:,}件" in call.args[0]
    assert item(s, "show").label == f"{total:,}件を見る"
    assert_discord_limits(s, call.args[0], [])


async def test_choosing_conditions_updates_the_count(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "types", interaction, "used_condo")
    await pick(s, "price_max", interaction, "60000000")
    content, _ = await pick(s, "rooms", interaction, "3")
    q = Query(types=("used_condo",), price_max=60_000_000, rooms=(3,))
    assert s.q == q
    n = bot.catalog.snap.count(q)
    assert f"該当 {n:,}件" in content and "中古マンション" in content and "〜6,000万円" in content
    # the chosen values are shown as defaults in the dropdowns
    assert [o.value for o in item(s, "price_max").options if o.default] == ["60000000"]


async def test_land_only_hides_building_conditions(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "types", interaction, "land")
    assert not any(c.custom_id.endswith(":rooms") for c in s.children)
    await tap(s, "size", interaction)
    names = {c.custom_id.split(":")[-1] for c in s.children}
    assert "land_min" in names and "size_min" not in names and "age" not in names


async def test_zero_results_names_the_condition_to_loosen(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "price_max", interaction, "20000000")
    content, _ = await pick(s, "rooms", interaction, "4")
    assert bot.catalog.snap.count(s.q) == 0
    assert "「予算の上限」の条件を外すと 10件 あります" in content
    assert item(s, "show").disabled


async def test_reset(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "types", interaction, "land")
    await tap(s, "reset", interaction)
    assert s.q == Query()
    assert item(s, "reset").disabled


async def test_sub_screens_respect_discord_limits(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    for screen in ("stations", "size", "extra"):
        content, embeds = await tap(s, screen, interaction)
        assert_discord_limits(s, content, embeds)
        await tap(s, "back", interaction)
    assert s.screen == "panel"


async def test_station_screen_line_then_station_then_walk(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "stations", interaction)
    assert "小田急線" in options(s, "line")
    await pick(s, "line", interaction, "小田急線")
    assert "狛江" in options(s, "station")
    await pick(s, "station", interaction, "狛江")
    content, _ = await pick(s, "walk", interaction, "10")
    assert s.q.stations == ("狛江",) and s.q.walk_max == 10
    assert "狛江駅" in content and "徒歩10分以内" in content
    await tap(s, "back", interaction)
    assert item(s, "stations").label.startswith("駅: 狛江")


async def test_age_choice_includes_quake_standard(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "size", interaction)
    await pick(s, "age", interaction, "quake")
    assert s.q.post_1981 and s.q.age_max is None
    await pick(s, "age", interaction, "20")
    assert s.q.age_max == 20 and not s.q.post_1981


async def test_extra_toggles(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "extra", interaction)
    await tap(s, "freehold_only", interaction)
    assert s.q.freehold_only and item(s, "freehold_only").label.startswith("✅")
    await pick(s, "features", interaction, options(s, "features")[0])
    assert len(s.q.features) == 1
    await tap(s, "clear_extra", interaction)
    assert not s.q.freehold_only and not s.q.features


async def test_area_screen_merges_choices_across_dropdowns(data):
    # a second prefecture's worth of areas: more than one dropdown
    extra = {f"13{n:03d}": f"市{n}" for n in range(201, 231)}
    recs = [json.loads(line) for line in (data / "tokyo" / "used_condo" / "13219.jsonl").read_text().splitlines()]
    more = [{**recs[0], "id": str(9_000_000 + n), "area_code": code, "dup_key": None}
            for n, code in enumerate(extra)]
    with open(data / "tokyo" / "used_condo" / "13219.jsonl", "a") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in more))
    (data / "tokyo" / "areas.json").write_text(json.dumps({"13219": "狛江市", **extra}, ensure_ascii=False))
    bot = FakeBot(data)
    interaction = make_interaction()
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    content, embeds = await tap(s, "areas", interaction)
    assert_discord_limits(s, content, embeds)
    selects = [c for c in s.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 2 and all(len(x.options) <= 25 for x in selects)   # 31 市部 split in halves
    first, second = selects[0].options[0].value, selects[1].options[0].value
    await pick(s, "area0", interaction, first)
    await pick(s, "area1", interaction, second)
    assert set(s.q.areas) == {first, second}
    await pick(s, "area0", interaction)                       # cleared the first dropdown only
    assert s.q.areas == (second,)


# ---------- results and detail ----------

async def test_results_pages_and_detail(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    content, embeds = await tap(s, "show", interaction)
    total = bot.catalog.snap.count(Query())
    assert f"{total:,}件" in content and len(embeds) == PAGE_SIZE
    assert all(e.thumbnail.url for e in embeds)
    assert_discord_limits(s, content, embeds)
    await tap(s, "next", interaction)
    assert s.page == 1
    content, embeds = await pick(s, "detail", interaction, str(PAGE_SIZE + 1))
    assert s.screen == "detail" and len(embeds) == 1 and "w=600&h=450" in embeds[0].image.url
    assert_discord_limits(s, content, embeds)
    await tap(s, "next_item", interaction)
    assert s.pos == PAGE_SIZE + 2
    await tap(s, "to_list", interaction)
    assert s.screen == "results" and s.page == 1


async def test_every_listing_detail_fits(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "show", interaction)
    await pick(s, "detail", interaction, "0")
    for _ in range(len(s.hits) - 1):
        content, embeds = await tap(s, "next_item", interaction)
        assert_discord_limits(s, content, embeds)


async def test_sort_changes_order(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "show", interaction)
    await pick(s, "sort", interaction, "price_asc")
    prices = [h.item.price_lo for h in s.hits if h.item.price_lo is not None]
    assert prices == sorted(prices)


async def test_results_remember_last_conditions(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "types", interaction, "used_house")
    await tap(s, "show", interaction)
    assert bot.store.user(42).last_query == Query(types=("used_house",))
    assert bot.store.dirty
    await Session.open(bot, interaction, "panel")
    assert session_of(bot).q == Query(types=("used_house",))


async def test_new_and_drops_shortcuts(bot, interaction):
    await Session.open(bot, interaction, "new")
    s = session_of(bot)
    assert s.screen == "results" and s.q.new_only and len(s.hits) == 1
    assert "🆕" in last(interaction)[0]
    await Session.open(bot, interaction, "drops")
    s = session_of(bot)
    assert len(s.hits) == 1 and "⬇️" in last(interaction)[1][0].description
    # narrowing from a shortcut keeps its condition and doesn't overwrite the saved last search
    await tap(s, "to_panel", interaction)
    assert s.screen == "panel" and s.q.drops_only
    assert bot.store.user(42).last_query == Query()


# ---------- favorites ----------

async def test_star_and_favorites_list(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "show", interaction)
    await pick(s, "detail", interaction, "0")
    key = s.hits[0].key
    content, _ = await tap(s, "star", interaction)
    assert T.FAV_ADDED in content and key in bot.store.user(42).favorites
    assert item(s, "star").label == "お気に入りから外す"
    bot.after_change.assert_awaited()
    await Session.open(bot, interaction, "favorites")
    f = session_of(bot)
    assert [h.key for h in f.hits] == [key]
    assert "⭐ お気に入り 1件" in last(interaction)[0]
    await pick(f, "detail", interaction, "0")
    await tap(f, "star", interaction)
    assert key not in bot.store.user(42).favorites


async def test_empty_favorites(bot, interaction):
    await Session.open(bot, interaction, "favorites")
    assert last(interaction)[0] == T.FAV_EMPTY


async def test_favorite_price_change_is_shown(bot, interaction):
    u = bot.store.user(42)
    it = bot.catalog.snap.items[0]
    u.favorites[it.key] = {"price": it.price_lo + 1_000_000, "added": "2026-10-01"}
    await Session.open(bot, interaction, "favorites")
    assert "追加時から⬇️100万円" in last(interaction)[1][0].description


# ---------- saved searches ----------

async def test_save_search_rules(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    content, _ = await tap(s, "save", interaction)
    assert T.SAVE_EMPTY in content
    await pick(s, "types", interaction, "used_condo")
    content, _ = await tap(s, "save", interaction)
    assert T.SAVED_OK in content
    u = bot.store.user(42)
    assert len(u.searches) == 1 and u.last_run == "20261004T040000"   # alerts start from the newest run
    content, _ = await tap(s, "save", interaction)
    assert T.SAVE_DUP in content
    for price in T.PRICE_MAX[: MAX_SEARCHES]:
        await pick(s, "price_max", interaction, str(price * 1_000_000))
        content, _ = await tap(s, "save", interaction)
    assert T.SAVE_FULL.format(n=MAX_SEARCHES) in content and len(u.searches) == MAX_SEARCHES


async def test_saved_screen_run_and_delete(bot, interaction):
    u = bot.store.user(42)
    bot.store.save_search(u, Query(types=("land",)), "2026-10-06", "x")
    await Session.open(bot, interaction, "saved")
    s = session_of(bot)
    assert item(s, "run_saved").disabled
    sid = u.searches[0].id
    await pick(s, "saved", interaction, sid)
    content, _ = await tap(s, "run_saved", interaction)
    assert s.screen == "results" and s.q == Query(types=("land",))
    await Session.open(bot, interaction, "saved")
    s = session_of(bot)
    await pick(s, "saved", interaction, sid)
    content, _ = await tap(s, "delete_saved", interaction)
    assert T.DELETED in content and not u.searches


async def test_saved_search_results_lead_back_to_saved_list(bot, interaction):
    u = bot.store.user(42)
    bot.store.save_search(u, Query(types=("land",)), "2026-10-06", "x")
    await Session.open(bot, interaction, "saved")
    s = session_of(bot)
    await pick(s, "saved", interaction, u.searches[0].id)
    await tap(s, "run_saved", interaction)
    assert u.last_query == Query()                         # a saved search isn't "your last conditions"
    await tap(s, "to_saved", interaction)
    assert s.screen == "saved"
    await tap(s, "new_search", interaction)
    assert s.screen == "panel" and s.q == Query()


async def test_panel_edits_are_remembered_without_viewing(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "types", interaction, "land")
    await Session.open(bot, interaction, "panel")
    assert session_of(bot).q == Query(types=("land",))
    s = session_of(bot)
    await tap(s, "reset", interaction)
    await Session.open(bot, interaction, "panel")
    assert session_of(bot).q == Query()


async def test_long_lines_page_their_stations(data):
    recs = [json.loads(line) for line in (data / "tokyo" / "used_condo" / "13219.jsonl").read_text().splitlines()]
    more = [{**recs[0], "id": str(8_000_000 + n), "dup_key": None,
             "stations": [{"line": "小田急線", "name": f"駅{n:02d}", "walk": 5}]} for n in range(30)]
    with open(data / "tokyo" / "used_condo" / "13219.jsonl", "a") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in more))
    bot = FakeBot(data)
    interaction = make_interaction()
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await tap(s, "stations", interaction)
    await pick(s, "line", interaction, "小田急線")
    first = set(options(s, "station"))
    content, embeds = await tap(s, "station_next", interaction)
    assert_discord_limits(s, content, embeds)
    second = set(options(s, "station"))
    assert len(first) == 25 and second and not first & second
    await pick(s, "station", interaction, sorted(second)[0])
    assert s.q.stations == (sorted(second)[0],)


async def test_favorite_notifications_toggle(bot, interaction):
    await Session.open(bot, interaction, "saved")
    s = session_of(bot)
    await tap(s, "fav_notify", interaction)
    assert bot.store.user(42).notify_favorites is False
    assert "オフ" in item(s, "fav_notify").label


# ---------- sessions ----------

async def test_old_sessions_are_forgotten(bot):
    for _ in range(5):
        await Session.open(bot, make_interaction(), "panel")
    await Session.open(bot, make_interaction(uid=7), "panel")
    assert [s.uid for s in bot.sessions.values()] == [42, 42, 42, 7]


async def test_tap_on_forgotten_session_says_so(bot, monkeypatch):
    i = make_interaction()
    i.type = discord.InteractionType.component
    i.data = {"custom_id": "s:deadbeef:show"}
    await bot.on_interaction(i)
    assert i.response.edit_message.await_args.kwargs == {"content": T.EXPIRED, "embeds": [], "view": None}
    # a live session is left to its own view...
    monkeypatch.setattr(app_mod, "TAP_GRACE", 0)
    await Session.open(bot, make_interaction(), "panel")
    live = make_interaction()
    live.type = discord.InteractionType.component
    live.data = {"custom_id": session_of(bot).cid("show")}
    live.response.is_done = MagicMock(return_value=True)
    live.response.defer = AsyncMock()
    await bot.on_interaction(live)
    live.response.edit_message.assert_not_awaited()
    live.response.defer.assert_not_awaited()
    # ...unless the view dropped it (a second tap during a redraw): acknowledged quietly
    live.response.is_done = MagicMock(return_value=False)
    await bot.on_interaction(live)
    live.response.defer.assert_awaited()


async def test_errors_in_a_change_show_a_short_message(bot, interaction):
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)

    def boom():
        raise RuntimeError("x")

    s.add_button("boom", "boom", None, boom, 4)
    content, _ = await tap(s, "boom", interaction)
    assert T.ERROR in content


async def test_listings_that_look_alike_get_distinct_choices(data):
    """Regression: the 詳しく見る values were the descriptions, so two similar units broke the screen (HTTP 400)."""
    recs = [json.loads(line) for line in (data / "tokyo" / "used_condo" / "13219.jsonl").read_text().splitlines()]
    twin = {**recs[0], "id": "7777777", "dup_key": None}
    with open(data / "tokyo" / "used_condo" / "13219.jsonl", "a") as f:
        f.write(json.dumps(twin, ensure_ascii=False) + "\n")
    bot = FakeBot(data)
    interaction = make_interaction()
    await Session.open(bot, interaction, "panel")
    s = session_of(bot)
    await pick(s, "types", interaction, "used_condo")
    content, embeds = await tap(s, "show", interaction)
    for page in range(len(s.hits) // PAGE_SIZE + 1):
        assert_discord_limits(s, content, embeds)
        if page < len(s.hits) // PAGE_SIZE:
            content, embeds = await tap(s, "next", interaction)
