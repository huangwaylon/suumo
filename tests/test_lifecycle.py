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
from suumo.http import NetworkError, Unavailable
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
    q = db.x("SELECT id, priority FROM queue ORDER BY id").fetchall()
    assert [(r["id"], r["priority"]) for r in q] == [("1", pipeline.P_BACKFILL), ("2", pipeline.P_BACKFILL)]


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
    top = db.x("SELECT id, priority FROM queue ORDER BY priority DESC, enqueued_at LIMIT 1").fetchone()
    assert (top["id"], top["priority"]) == ("2", pipeline.P_NEW)


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
    assert db.x("SELECT priority FROM queue WHERE id='1'").fetchone()[0] == pipeline.P_BACKFILL


def test_back_on_suumo(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2), rec(3)])
    for day in (1, 2):
        crawl(day, [rec(1)], saved={"used_condo:2"})
    crawl(3, [rec(1), rec(2), rec(3)], saved={"used_condo:2"})
    assert status(db, 2) == status(db, 3) == "active"
    assert events(db)[-2:] == [("relisted", "2"), ("relisted", "3")]  # the kept one, and the deleted one (tombstone)


def test_a_deleted_listing_back_within_the_tombstone_days_is_relisted(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)])
    crawl(2, [rec(1)])                                          # 2 deleted, its tombstone kept
    assert status(db, 2) == "deleted" and db.x("SELECT first_seen FROM ended").fetchone()[0] == "2026-10-05"
    db.x("DELETE FROM queue")
    _, out = crawl(5, [rec(1), rec(2, price=45_000_000)])
    assert out["relisted"] == 1 and out["new"] == 0 and out["price_changed"] == 1
    assert events(db)[-2:] == [("relisted", "2"), ("price_changed", "2")]
    payload = json.loads(db.x("SELECT payload FROM events WHERE kind='price_changed'").fetchone()[0])
    assert payload == {"price": 45_000_000, "old_price": 50_000_000}
    assert db.listing("used_condo", "2")["first_seen"] == "2026-10-05"
    assert db.x("SELECT COUNT(*) FROM ended").fetchone()[0] == 0
    assert db.x("SELECT priority FROM queue WHERE id='2'").fetchone()[0] == pipeline.P_BACKFILL  # its page went with it


def test_a_deleted_listing_back_after_the_tombstone_is_purged_is_new(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)])
    crawl(2, [rec(1)])
    day = 3 + pipeline.TOMBSTONE_DAYS
    p, _ = crawl(day, [rec(1)])
    p.purge()
    assert db.x("SELECT COUNT(*) FROM ended").fetchone()[0] == 0
    crawl(day + 1, [rec(1), rec(2)])
    assert events(db)[-1] == ("new", "2")
    assert db.listing("used_condo", "2")["first_seen"] == (T0 + timedelta(days=day + 1)).date().isoformat()


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
    """Client whose area-selection page lists only another area (ours dropped off SUUMO's list)."""
    def get(self, path):
        return '<html><body><input name="sc" value="13218"><label>福生市(3)</label></body></html>'


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


def test_area_listed_as_zero_with_nothing_held_is_a_baseline(env):
    db, archive, targets, crawl, _ = env
    p = Pipeline(db, archive, ZeroAreaPage(), targets, T0, "r0", log=QUIET)
    p.crawl_lists()
    out = p.report["targets"][0]["areas"][0]
    assert (out["status"], out["hits"], out["baseline"]) == ("complete", 0, True)
    area = db.area("tokyo", "used_condo", "13219")
    assert (area["baselined"], area["last_hits"], area["last_status"]) == (1, 0, "complete")
    crawl(1, [rec(1)])                                          # its first listing later is 新着
    assert events(db) == [("new", "1")]


def test_a_new_condo_area_the_other_types_know_is_a_baseline_when_missing(env):
    db, archive, _, _, _ = env                                  # env knows 13219 as a used_condo area
    client = _list_client(['<html><body><input name="sc" value="13218"><label><a href="/ms/shinchiku/tokyo/'
                           'sc_fussa/">福生市</a><span>(2)</span></label></body></html>'])
    targets = [Target("tokyo", "new_condo", frozenset({"13219"}))]
    Pipeline(db, archive, client, targets, T0, "r0", log=QUIET).crawl_lists()
    assert client.paths == ["/ms/shinchiku/tokyo/city/"]        # no request for it
    area = db.area("tokyo", "new_condo", "13219")
    assert (area["name"], area["baselined"], area["last_hits"], area["last_status"]) == ("狛江市", 1, 0, "complete")
    assert db.area("tokyo", "new_condo", "13218") is None       # not in the target's areas


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


def _fetch_queue(db, archive, targets):
    """Work the queue with a client serving a fixture page; returns the fetched listing ids."""
    page = Archive.read(FIXTURES / "detail_used_condo_20205670.html.gz")
    fetched = []

    class Client:
        delay = 1.0

        def get(self, path):
            fetched.append(path.rstrip("/").split("nc_")[1])
            return page
    db.x("INSERT OR IGNORE INTO runs (run_id, started) VALUES ('q', 'x')")
    Pipeline(db, archive, Client(), targets, T0, "q", log=QUIET).process_queue(3600)
    return fetched


SAME = {"name": "狛江ハイツ", "built": "2005-04"}   # the same property listed by several agents (dup_key)


def test_a_copy_is_fetched_once_the_fetched_listing_goes_missing(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1, **SAME), rec(2, **SAME)])
    assert _fetch_queue(db, archive, targets) == ["1"]
    crawl(1, [rec(2, **SAME)])                                  # 1 missing (maybe ending): 2 takes over
    assert db.listing("used_condo", "1")["missed"] == 1
    assert _fetch_queue(db, archive, targets) == ["2"]


def test_a_copy_of_a_saved_ended_listing_gets_its_page(env):
    db, archive, targets, crawl, _ = env
    saved = {"used_condo:1"}
    crawl(0, [rec(1, **SAME)], saved=saved)
    assert _fetch_queue(db, archive, targets) == ["1"]
    crawl(1, [], saved=saved)
    crawl(2, [], saved=saved)
    assert status(db, 1) == "removed"
    crawl(3, [rec(2, **SAME)], saved=saved)                     # another agent's listing of it, later
    assert _fetch_queue(db, archive, targets) == ["2"]


def test_a_group_with_a_repeated_agent_is_not_one_property(env):
    db, _, _, crawl, _ = env
    queued = lambda: [r["id"] for r in db.x("SELECT id FROM queue ORDER BY id")]  # noqa: E731
    crawl(0, [rec(1, agent="A不動産", **SAME), rec(2, agent="A不動産", **SAME), rec(3, agent="B不動産", **SAME)])
    assert queued() == ["1", "2", "3"]                          # the same agent twice: separate units, all pages
    db.x("DELETE FROM queue")
    db.x("DELETE FROM listings")
    crawl(1, [rec(4, agent="A不動産", **SAME), rec(5, agent="B不動産", **SAME)])
    assert queued() == ["4"]                                    # different agents: one property, one page


def test_the_queued_member_keeps_its_place_when_the_page_order_changes(env):
    db, _, _, crawl, _ = env
    entry = lambda: [tuple(r) for r in db.x("SELECT id, priority, enqueued_at FROM queue")]  # noqa: E731
    crawl(0, [rec(1, **SAME), rec(2, **SAME)])
    before = entry()
    assert [e[0] for e in before] == ["1"]
    crawl(1, [rec(2, **SAME), rec(1, **SAME)])                  # SUUMO shows the other agent's listing first
    crawl(2, [rec(3, **SAME), rec(2, **SAME), rec(1, **SAME)])  # and a third agent's, new, first of all
    assert entry() == before                                    # 1 is still the lead, queued since day 0


def test_a_lead_given_up_on_hands_over(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1, **SAME), rec(2, **SAME)])
    db.x("UPDATE queue SET attempts=?, last_error='gone: 404' WHERE id='1'", pipeline.MAX_DETAIL_ATTEMPTS)
    crawl(1, [rec(1, **SAME), rec(2, **SAME)])
    assert _fetch_queue(db, archive, targets) == ["2"]


def _held_as_new_condo(db, lid, first_seen="2026-10-01"):
    db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen)
            VALUES ('new_condo', ?, 'tokyo', '13219', '狛江市', '{}', ?, ?)""", str(lid), first_seen, first_seen)


def test_an_id_held_under_another_type_is_not_new(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    _held_as_new_condo(db, 7)                                   # SUUMO re-typed it: new_condo -> used_condo
    _, out = crawl(1, [rec(1), rec(7)])
    row = db.listing("used_condo", "7")
    assert events(db) == [] and out["retyped"] == 1 and row["first_seen"] == "2026-10-01"
    assert db.x("SELECT priority FROM queue WHERE id='7'").fetchone()[0] == pipeline.P_BACKFILL


def test_an_id_new_under_two_types_in_one_run_is_new_under_both(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    _held_as_new_condo(db, 7, "2026-10-06")
    run_id = (T0 + timedelta(days=1)).strftime("%Y%m%dT%H%M%S")  # the run that crawls day 1
    db.add_event(run_id, "new", {"type": "new_condo", "id": "7", "pref": "tokyo", "area_code": "13219"}, {})
    crawl(1, [rec(1), rec(7)])
    rows = db.x("SELECT kind, type FROM events WHERE id='7' ORDER BY seq").fetchall()
    assert [tuple(r) for r in rows] == [("new", "new_condo"), ("new", "used_condo")]


def area_page(*areas, path="ms/chuko"):
    """An area-selection page: areas are (code, name, count, slug or None)."""
    return "<html><body>" + "".join(
        f'<input name="sc" value="{code}"><label>'
        + (f'<a href="/{path}/tokyo/{slug}/">{name}</a>' if slug else name) + f"({n})</label>"
        for code, name, n, slug in areas) + "</body></html>"


class _Pages:
    """A client answering by path: replies {path: html, or an exception, or a list of them in turn}, else default."""
    def __init__(self, replies, default=None):
        self.replies, self.default, self.paths = replies, default, []

    def get(self, path):
        self.paths.append(path)
        reply = self.replies.get(path, self.default)
        if isinstance(reply, list):
            reply = reply.pop(0) if len(reply) > 1 else reply[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


CITY = "/ms/chuko/tokyo/city/"
KOMAE = "/ms/chuko/tokyo/sc_komae/?pc=100"


def test_an_area_page_without_areas_is_an_error_and_reconciles_nothing(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    client = _Pages({}, default="<html><body>メンテナンス中</body></html>")
    p = Pipeline(db, archive, client, targets, T0 + timedelta(days=1), "r1", log=QUIET)
    assert p.crawl_lists()
    assert client.paths == [CITY, CITY]                          # tried again once at the end
    assert all("area page" in r["error"] for r in p.report["targets"])
    assert db.listing("used_condo", "1")["missed"] == 0
    assert db.x("SELECT pref FROM prefs").fetchone()[0] == "tokyo"   # given up on for today: crawled_at written


def test_an_area_page_with_far_fewer_listings_than_held_is_an_error(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(i) for i in range(1, 26)])                     # 25 held
    client = _Pages({CITY: area_page(("13219", "狛江市", 5, "sc_komae"))})
    p = Pipeline(db, archive, client, targets, T0 + timedelta(days=1), "r1", log=QUIET)
    p.crawl_lists()
    assert client.paths == [CITY, CITY] and "5 listings in its areas, 25 held" in p.report["targets"][0]["error"]
    assert all(db.listing("used_condo", str(i))["missed"] == 0 for i in range(1, 26))
    client = _Pages({CITY: area_page(("13219", "狛江市", 18, "sc_komae"))}, default="<html></html>")
    Pipeline(db, archive, client, targets, T0 + timedelta(days=1), "r2", log=QUIET).crawl_lists()
    assert client.paths[:2] == [CITY, KOMAE]                     # 18 of 25: within the suspect margin, crawled


def test_a_failed_target_is_tried_again_once_at_the_end(env):
    db, archive, _, _, _ = env
    targets = [Target("tokyo", "used_condo", frozenset({"13219"})), Target("tokyo", "land", frozenset({"13219"}))]
    empty = area_page(("13219", "狛江市", 0, None))
    client = _Pages({CITY: [RuntimeError("failed after 4 attempts"), empty]}, default=empty)
    p = Pipeline(db, archive, client, targets, T0, "r0", log=QUIET)
    written = []
    p.log = lambda line: written.append((len(client.paths), db.x("SELECT COUNT(*) FROM prefs").fetchone()[0]))
    assert p.crawl_lists()
    assert client.paths == [CITY, "/tochi/tokyo/city/", CITY]
    assert (3, 0) in written and db.x("SELECT COUNT(*) FROM prefs").fetchone()[0] == 1  # written after the retry
    assert [("error" in r, r.get("retried", False)) for r in p.report["targets"]] == [
        (True, False), (False, False), (False, True)]


def test_only_the_failed_areas_are_crawled_again(env):
    db, archive, targets, _, _ = env
    page = Archive.read(FIXTURES / "list_used_condo_komae.html.gz")
    client = _Pages({CITY: area_page(("13219", "狛江市", 67, "sc_komae")), KOMAE: [RuntimeError("HTTP 503"), page]})
    p = Pipeline(db, archive, client, targets, T0, "r0", log=QUIET)
    p.crawl_lists()
    assert client.paths == [CITY, KOMAE, KOMAE]                  # not the area page again
    assert p.report["targets"][-1]["areas"][0]["status"] == "complete"
    assert db.area("tokyo", "used_condo", "13219")["last_status"] == "complete"


def test_network_errors_in_a_row_stop_the_crawl(env):
    db, archive, _, _, _ = env
    targets = [Target("tokyo", "used_condo", None), Target("tokyo", "land", None), Target("chiba", "land", None)]
    city = area_page(*[(f"1321{i}", f"市{i}", 5, f"sc_{i}") for i in range(3)])
    client = _Pages({CITY: city}, default=NetworkError("network unreachable"))
    p = Pipeline(db, archive, client, targets, T0, "r0", log=QUIET)
    assert not p.crawl_lists()
    # two areas in a row end the target; the next target's area page fails too: the crawl stops (chiba not asked)
    assert client.paths == [CITY, "/ms/chuko/tokyo/sc_0/?pc=100", "/ms/chuko/tokyo/sc_1/?pc=100", "/tochi/tokyo/city/"]
    assert p.report["offline"] and db.x("SELECT COUNT(*) FROM prefs").fetchone()[0] == 0


def test_suumo_down_stops_the_crawl(env):
    db, archive, _, _, _ = env
    targets = [Target("tokyo", "used_condo", None), Target("tokyo", "land", None), Target("chiba", "land", None)]
    client = _Pages({}, default=Unavailable("SUUMO unavailable (4 attempts, HTTP 503)"))   # its maintenance page
    p = Pipeline(db, archive, client, targets, T0, "r0", log=QUIET)
    assert not p.crawl_lists()
    assert client.paths == [CITY, "/tochi/tokyo/city/"]          # two area pages, then it stops: no retry pass
    assert p.report["offline"] and db.x("SELECT COUNT(*) FROM prefs").fetchone()[0] == 0   # all left for later


def test_purge_leaves_a_tombstone_for_an_unsaved_ended_listing(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)], saved={"used_condo:2"})
    crawl(2, [rec(1)], saved={"used_condo:2"})                   # ended, kept: saved
    removed_at = db.listing("used_condo", "2")["removed_at"]
    p, _ = crawl(3, [rec(1)])                                    # unsaved
    p.purge()
    tomb = db.x("SELECT first_seen, price, ended_at FROM ended WHERE id='2'").fetchone()
    assert status(db, 2) == "deleted" and tuple(tomb) == ("2026-10-05", 50_000_000, removed_at)
    crawl(4, [rec(1), rec(2)])
    assert events(db)[-1] == ("relisted", "2") and db.listing("used_condo", "2")["first_seen"] == "2026-10-05"
