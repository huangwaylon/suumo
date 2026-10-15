"""Lifecycle rules, offline: reconcile() is driven with synthetic list records, no network."""
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from suumo import pipeline
from suumo.archive import Archive
from suumo.db import DB
from suumo.export import export
from suumo.pipeline import Pipeline
from suumo.scope import Target
from tests.helpers import FIXTURES, QUIET

JST = ZoneInfo("Asia/Tokyo")
AREA = {"code": "13219", "name": "狛江市", "slug": "sc_komae", "expected": 0}
T0 = datetime(2026, 10, 5, 3, 0, tzinfo=JST)


def rec(i, price=50_000_000, **kw):
    return {"id": str(i), "path": f"/ms/chuko/tokyo/sc_komae/nc_{i}/", "name": f"物件{i}", "price": price,
            "layout": "2LDK", "floor_m2": 55.0, **kw}


@pytest.fixture
def env(tmp_path):
    db = DB(tmp_path / "state.db")
    archive = Archive(tmp_path / "archive")
    targets = [Target("tokyo", "used_condo", frozenset({"13219"}))]
    db.upsert_area("tokyo", "used_condo", AREA)

    def crawl(day, recs, complete=True, hits=None, saved=(), hours=0):
        now = T0 + timedelta(days=day, hours=hours)
        p = Pipeline(db, archive, None, targets, now, now.strftime("%Y%m%dT%H%M%S"), log=QUIET, saved=saved)
        db.x("INSERT OR IGNORE INTO runs (run_id, started) VALUES (?, ?)", p.run_id, p.ts)
        out = p.reconcile("tokyo", "used_condo", AREA, len(recs) if hits is None else hits,
                          {r["id"]: r for r in recs}, complete)
        db.commit()
        return p, out

    return db, archive, targets, crawl, tmp_path


def events(db, kind=None):
    rows = db.x("SELECT kind, id FROM events ORDER BY seq").fetchall()
    return [(r["kind"], r["id"]) for r in rows if kind in (None, r["kind"])]


def status(db, lid):
    row = db.listing("used_condo", str(lid))
    return row["status"] if row else "deleted"


def test_first_complete_crawl_is_a_silent_baseline(env):
    db, _, _, crawl, _ = env
    _, out = crawl(0, [rec(1), rec(2)])
    assert out["baseline"] and events(db) == []
    q = db.x("SELECT id, priority, reason FROM queue ORDER BY id").fetchall()
    assert [(r["id"], r["priority"], r["reason"]) for r in q] == [("1", 0, "backfill"), ("2", 0, "backfill")]


def test_incomplete_first_crawl_does_not_baseline(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)], complete=False, hits=2)
    crawl(1, [rec(1), rec(2)])          # still the baseline: listing 2 is not "new"
    assert events(db) == []
    crawl(2, [rec(1), rec(2), rec(3)])
    assert events(db) == [("new", "3")]


def test_new_listing_after_baseline_is_an_event_and_jumps_the_queue(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    crawl(1, [rec(1), rec(2)])
    assert events(db) == [("new", "2")]
    top = db.x("SELECT id, reason FROM queue ORDER BY priority DESC, enqueued_at LIMIT 1").fetchone()
    assert (top["id"], top["reason"]) == ("2", "new")


def test_deleted_after_two_complete_misses(env):
    db, archive, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    archive.save_detail("tokyo", "used_condo", "2", "<html></html>")
    crawl(1, [rec(1)])
    assert status(db, 2) == "active" and db.listing("used_condo", "2")["missed"] == 1
    crawl(2, [rec(1)])
    assert status(db, 2) == "deleted" and events(db) == [("removed", "2")]
    assert not archive.detail_path("tokyo", "used_condo", "2").exists()


def test_a_second_crawl_on_the_same_crawl_day_counts_no_second_miss(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)])                          # 03:00: still the previous crawl day (it starts at 04:00)
    _, out = crawl(1, [rec(1)], hours=0.5)      # 03:30 the same night: a restarted crawl
    assert status(db, 2) == "active" and db.listing("used_condo", "2")["missed"] == 1 and out["missing"] == 0
    crawl(1, [rec(1)], hours=1.5)               # 04:30: the next crawl day
    assert status(db, 2) == "deleted"


def test_an_incomplete_crawl_does_not_use_up_the_crawl_day(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)], complete=False, hits=2)
    crawl(1, [rec(1)], hours=0.5)               # complete, same day: counts
    crawl(1, [rec(1)], hours=0.7)               # again: doesn't
    assert db.listing("used_condo", "2")["missed"] == 1
    crawl(2, [rec(1)])
    assert status(db, 2) == "deleted"


def test_a_suspect_crawl_uses_up_the_crawl_day(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(i) for i in range(1, 28)])
    crawl(1, [rec(i) for i in range(1, 19)])                # suspect
    crawl(1, [rec(i) for i in range(1, 19)], hours=0.5)     # the same day, no longer suspect: still not counted
    assert db.listing("used_condo", "27")["missed"] == 0
    crawl(2, [rec(i) for i in range(1, 19)])
    crawl(3, [rec(i) for i in range(1, 19)])
    assert status(db, 27) == "deleted"


def test_migrate_adds_columns_to_an_old_database(tmp_path):
    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("CREATE TABLE areas (pref TEXT NOT NULL, type TEXT NOT NULL, code TEXT NOT NULL, name TEXT, "
                "slug TEXT, baselined INTEGER NOT NULL DEFAULT 0, last_hits INTEGER, last_status TEXT, "
                "PRIMARY KEY (pref, type, code))")
    old.execute("INSERT INTO areas (pref, type, code) VALUES ('tokyo', 'land', '13219')")
    old.commit()
    old.close()
    reader = DB(tmp_path / "old.db", migrate=False)              # `status` never alters
    assert "missed_on" not in {r["name"] for r in reader.x("PRAGMA table_info(areas)")}
    db = DB(tmp_path / "old.db")
    db.migrate()                                                 # idempotent
    assert db.area("tokyo", "land", "13219")["missed_on"] is None


def test_saved_listing_is_kept_as_ended(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)], saved={"used_condo:2"})
    crawl(2, [rec(1)], saved={"used_condo:2"})
    assert status(db, 2) == "removed" and events(db) == [("removed", "2")]
    crawl(3, [rec(1)], saved={"used_condo:2"})                 # still saved: still kept, no second event
    assert status(db, 2) == "removed" and events(db) == [("removed", "2")]


def test_unsaved_ended_listing_is_deleted_by_purge(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)], saved={"used_condo:2"})
    p, _ = crawl(2, [rec(1)], saved={"used_condo:2"})
    p.purge()
    assert status(db, 2) == "removed"
    p, _ = crawl(3, [rec(1)])                                   # someone unsaved it
    p.purge()
    assert status(db, 2) == "deleted"


def test_reappearing_resets_the_miss_count(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)])
    crawl(2, [rec(1), rec(2)])
    crawl(3, [rec(1)])
    assert status(db, 2) == "active" and events(db) == []


def test_incomplete_crawl_never_counts_as_a_miss(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)], complete=False, hits=2)
    crawl(2, [rec(1)], complete=False, hits=2)
    assert status(db, 2) == "active" and db.listing("used_condo", "2")["missed"] == 0


def test_sudden_drop_is_suspect_and_removes_nothing(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(i) for i in range(1, 31)])
    _, out = crawl(1, [rec(i) for i in range(1, 11)])      # 30 -> 10 listings
    crawl(2, [rec(i) for i in range(1, 11)])
    assert out["status"] == "suspect"
    assert all(status(db, i) == "active" for i in range(1, 31))
    assert db.listing("used_condo", "30")["missed"] == 1               # day 2 compared against day 1's 10


def test_a_real_drop_is_distrusted_for_one_crawl_only(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(i) for i in range(1, 28)])                            # 27
    _, out = crawl(1, [rec(i) for i in range(1, 19)])                   # 18: suspect, nothing counted
    assert out["status"] == "suspect" and db.listing("used_condo", "27")["missed"] == 0
    assert db.area("tokyo", "used_condo", "13219")["last_hits"] == 18   # the new level is the reference
    _, out = crawl(2, [rec(i) for i in range(1, 19)])
    assert out["status"] == "complete" and db.listing("used_condo", "27")["missed"] == 1
    crawl(3, [rec(i) for i in range(1, 19)])
    assert status(db, 27) == "deleted" and status(db, 18) == "active"


def test_a_one_day_glitch_removes_nothing(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(i) for i in range(1, 28)])
    crawl(1, [rec(i) for i in range(1, 19)])                            # suspect
    _, out = crawl(2, [rec(i) for i in range(1, 28)])                   # back to 27
    assert out["status"] == "complete" and events(db) == []
    assert all(db.listing("used_condo", str(i))["missed"] == 0 for i in range(1, 28))


def test_small_areas_may_swing_without_being_suspect(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2), rec(3)])
    crawl(1, [rec(1)])
    crawl(2, [rec(1)])
    assert status(db, 2) == status(db, 3) == "deleted"


def test_price_change_is_an_event_and_the_stored_page_is_not_fetched_again(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    db.x("UPDATE listings SET detail_json='{}'")
    db.x("DELETE FROM queue")
    crawl(1, [rec(1, price=48_000_000, layout="3LDK")])
    e = db.x("SELECT payload FROM events WHERE kind='price_changed'").fetchone()
    assert json.loads(e["payload"])["old_price"] == 50_000_000
    assert json.loads(db.listing("used_condo", "1")["list_json"])["price"] == 48_000_000   # list fields refreshed
    assert db.x("SELECT COUNT(*) n FROM queue").fetchone()["n"] == 0


def test_unstored_page_is_queued_until_fetched(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    db.x("DELETE FROM queue")
    crawl(1, [rec(1)])
    assert db.x("SELECT reason FROM queue WHERE id='1'").fetchone()["reason"] == "backfill"


def test_back_on_suumo(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2), rec(3)])
    for day in (1, 2):
        crawl(day, [rec(1)], saved={"used_condo:2"})
    crawl(3, [rec(1), rec(2), rec(3)], saved={"used_condo:2"})
    assert status(db, 2) == status(db, 3) == "active"
    assert events(db)[-2:] == [("relisted", "2"), ("new", "3")]   # kept one relisted; deleted one is new again


def test_export_is_deterministic_and_skips_volatile_fields(env):
    db, _, targets, crawl, tmp = env
    crawl(0, [rec(2), rec(10), rec(1)])
    export(db, targets, tmp / "data")
    first = (tmp / "data/tokyo/used_condo/13219.jsonl").read_text()
    crawl(1, [rec(2), rec(10), rec(1)])                      # same listings, later day
    export(db, targets, tmp / "data")
    assert (tmp / "data/tokyo/used_condo/13219.jsonl").read_text() == first
    ids = [json.loads(ln)["id"] for ln in first.splitlines()]
    assert ids == ["1", "2", "10"]
    assert "last_seen" not in first and "missed" not in first


def test_export_one_file_per_area_and_cleans_up(env):
    db, _, _, crawl, tmp = env
    db.upsert_area("tokyo", "used_condo", {"code": "13208", "name": "調布市", "slug": "sc_chofu"})
    targets = [Target("tokyo", "used_condo", None), Target("tokyo", "land", None)]
    crawl(0, [rec(1)])
    db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen) "
         "VALUES ('used_condo', '5', 'tokyo', '13208', '調布市', ?, '2026-10-05', 'x')", json.dumps(rec(5)))
    db.commit()
    data = tmp / "data"
    export(db, targets, data)
    files = sorted(str(p.relative_to(data)) for p in data.rglob("*.jsonl"))
    assert files == ["tokyo/used_condo/13208.jsonl", "tokyo/used_condo/13219.jsonl"]   # no empty files
    mtime = (data / "tokyo/used_condo/13219.jsonl").stat().st_mtime_ns
    export(db, targets, data)
    assert (data / "tokyo/used_condo/13219.jsonl").stat().st_mtime_ns == mtime          # unchanged: not rewritten
    db.x("DELETE FROM listings WHERE id='5'")
    db.commit()
    export(db, targets, data)
    assert not (data / "tokyo/used_condo/13208.jsonl").exists()                          # area emptied: file removed
    crawl(1, [], saved={"used_condo:1"})
    crawl(2, [], saved={"used_condo:1"})
    export(db, targets, data)
    assert [json.loads(x)["id"] for x in (data / "tokyo/removed/used_condo.jsonl").read_text().splitlines()] == ["1"]
    export(db, [Target("tokyo", "land", None)], data)
    assert not (data / "tokyo/used_condo").exists()                                      # type left the scope


class EmptyAreaPage:
    """Client whose area-selection page lists no areas (the area dropped off SUUMO's list)."""
    def get(self, path):
        return "<html><body></body></html>"


def test_area_that_drops_off_the_area_page_is_reconciled_as_empty(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    for day in (1, 2):
        now = T0 + timedelta(days=day)
        p = Pipeline(db, archive, EmptyAreaPage(), targets, now, f"r{day}", log=QUIET)
        p.crawl_lists()
        db.commit()
    assert status(db, 1) == status(db, 2) == "deleted"
    assert sorted(events(db, "removed")) == [("removed", "1"), ("removed", "2")]


class ZeroAreaPage:
    """Client whose area page lists the area as "(0)" without a link."""
    def get(self, path):
        return '<html><body><input name="sc" value="13219"><label>狛江市(0)</label></body></html>'


def test_area_listed_as_zero_without_a_link_is_reconciled_as_empty(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    for day in (1, 2):
        now = T0 + timedelta(days=day)
        Pipeline(db, archive, ZeroAreaPage(), targets, now, f"r{day}", log=QUIET).crawl_lists()
        if day == 1:
            assert db.listing("used_condo", "1")["missed"] == 1
    assert status(db, 1) == status(db, 2) == "deleted"


def test_area_listed_as_zero_with_nothing_held_is_just_empty(env):
    db, archive, targets, _, _ = env
    p = Pipeline(db, archive, ZeroAreaPage(), targets, T0, "r0", log=QUIET)
    p.crawl_lists()
    assert p.report["targets"][0]["areas"] == [{"code": "13219", "name": "狛江市", "status": "empty"}]


def test_listing_that_moves_area_keeps_its_history(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    other = {"code": "13218", "name": "福生市", "slug": "sc_fussa", "expected": 0}
    db.upsert_area("tokyo", "used_condo", other)
    p = Pipeline(db, None, None, [], T0 + timedelta(days=1), "r1", log=QUIET)
    p.reconcile("tokyo", "used_condo", other, 1, {"1": rec(1)}, True)
    row = db.listing("used_condo", "1")
    assert row["area_code"] == "13218" and row["first_seen"] == T0.date().isoformat() and events(db) == []


def test_purge_drops_old_event_and_run_rows(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    db.x("INSERT INTO events (run_id, kind, type, id, pref, area_code, payload) "
         "VALUES ('20200101T000000', 'new', 'used_condo', '1', 'tokyo', '13219', '{}')")
    p, _ = crawl(1, [rec(1)])
    p.purge()
    assert db.x("SELECT COUNT(*) FROM events WHERE run_id='20200101T000000'").fetchone()[0] == 0



def test_area_crawl_requests_allowed_paths_and_archives_every_page(env, monkeypatch):
    db, archive, targets, _, _ = env
    page = Archive.read(FIXTURES / "list_used_condo_komae.html.gz")  # 67 hits, all on page 1

    class Client:
        paths = []

        def get(self, path):
            self.paths.append(path)
            return page

    monkeypatch.setattr(pipeline, "page_count", lambda hits: 2)  # pretend there is a second page
    p = Pipeline(db, archive, Client(), targets, T0, "r0", log=QUIET)
    out = p.crawl_area("tokyo", "used_condo", AREA)
    assert Client.paths == ["/ms/chuko/tokyo/sc_komae/?pc=100", "/ms/chuko/tokyo/sc_komae/?pc=100&page=2"]
    assert [f.name for f in archive.list_pages("tokyo", "20261005", "used_condo")] == \
        ["sc_komae_p1.html.gz", "sc_komae_p2.html.gz"]
    assert (out["status"], out["hits"], out["parsed"]) == ("complete", 67, 67)


def test_only_one_listing_per_property_is_queued(env):
    db, _, _, crawl, _ = env
    same = {"name": "狛江ハイツ", "built": "2005-04"}   # the same property listed by several agents (dup_key)
    queued = lambda: [r["id"] for r in db.x("SELECT id FROM queue ORDER BY id")]  # noqa: E731
    crawl(0, [rec(1, **same), rec(2, **same), rec(4)])
    assert queued() == ["1", "4"]                                       # 2 is another agent's copy of 1
    db.x("UPDATE listings SET detail_json='{}' WHERE id='1'")
    db.dequeue("used_condo", "1")
    crawl(1, [rec(1, **same), rec(2, **same), rec(3, **same), rec(4)])
    assert queued() == ["4"] and ("new", "3") in events(db)             # a new copy: an event, no page fetch
    crawl(2, [rec(2, **same), rec(3, **same), rec(4)])
    crawl(3, [rec(2, **same), rec(3, **same), rec(4)])                  # 1 ended (2 misses): a copy takes over
    assert status(db, 1) == "deleted" and queued() == ["2", "4"]


def _list_client(pages):
    """A client serving search-result pages in turn (the last one repeats); records the paths."""
    class Client:
        paths = []

        def get(self, path):
            self.paths.append(path)
            return pages[min(len(self.paths), len(pages)) - 1]
    return Client()


def test_a_page_without_a_hit_count_is_an_error_and_counts_no_misses(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    page = Archive.read(FIXTURES / "list_used_condo_komae.html.gz")
    broken = page.replace("pagination_set-hit", "x").replace("hitbox", "x")
    for day in (1, 2):
        p = Pipeline(db, archive, _list_client([broken]), targets, T0 + timedelta(days=day), f"r{day}", log=QUIET)
        out = p.crawl_area("tokyo", "used_condo", AREA)
        assert out["status"] == "error" and "no hit count" in out["error"]
    assert db.area("tokyo", "used_condo", "13219")["last_status"] == "error"
    assert status(db, 1) == status(db, 2) == "active" and db.listing("used_condo", "1")["missed"] == 0


def test_an_incomplete_first_pass_is_crawled_again_and_unioned(env, monkeypatch):
    db, archive, targets, _, _ = env
    page = Archive.read(FIXTURES / "list_used_condo_komae.html.gz")  # 67 hits on one page
    monkeypatch.setattr(pipeline, "page_count", lambda hits: 2)
    client = _list_client([page, "<html><body><div class='pagination_set-hit'>67件</div></body></html>", page, page])
    out = Pipeline(db, archive, client, targets, T0, "r0", log=QUIET).crawl_area("tokyo", "used_condo", AREA)
    assert len(client.paths) == 4                                       # page 2 came back empty: a second pass
    assert (out["status"], out["parsed"]) == ("complete", 67)
