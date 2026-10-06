"""Lifecycle rules, offline: reconcile() is driven with synthetic list records, no network."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from suumo.archive import Archive
from suumo.db import DB
from suumo.export import export
from suumo.pipeline import Pipeline
from suumo.scope import Target
from tests.helpers import QUIET

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

    def crawl(day, recs, complete=True, hits=None):
        now = T0 + timedelta(days=day)
        p = Pipeline(db, archive, None, targets, now, now.strftime("%Y%m%dT%H%M%S"), log=QUIET)
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
    return db.listing("used_condo", str(lid))["status"]


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


def test_removed_only_after_two_complete_misses(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)])
    assert status(db, 2) == "active" and db.listing("used_condo", "2")["missed"] == 1
    crawl(2, [rec(1)])
    assert status(db, 2) == "removed" and events(db) == [("removed", "2")]


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
    assert db.area("tokyo", "used_condo", "13219")["last_hits"] == 30  # reference count kept


def test_small_areas_may_swing_without_being_suspect(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2), rec(3)])
    crawl(1, [rec(1)])
    crawl(2, [rec(1)])
    assert status(db, 2) == status(db, 3) == "removed"


def test_price_change_event_and_detail_refresh(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1)])
    db.x("DELETE FROM queue")
    crawl(1, [rec(1, price=48_000_000)])
    e = db.x("SELECT payload FROM events WHERE kind='price_changed'").fetchone()
    assert json.loads(e["payload"])["old_price"] == 50_000_000
    assert db.x("SELECT reason FROM queue WHERE id='1'").fetchone()["reason"] == "changed"


def test_relist_after_removal(env):
    db, _, _, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    crawl(1, [rec(1)])
    crawl(2, [rec(1)])
    crawl(3, [rec(1), rec(2)])
    assert status(db, 2) == "active" and events(db)[-1] == ("relisted", "2")


def test_purge_after_retention_deletes_row_and_archived_page(env):
    db, archive, targets, crawl, _ = env
    crawl(0, [rec(1), rec(2)])
    archive.save_detail("tokyo", "used_condo", "2", "<html></html>")
    crawl(1, [rec(1)])
    p, _ = crawl(2, [rec(1)])
    p.purge()
    assert status(db, 2) == "removed"                          # within retention
    p, _ = crawl(33, [rec(1)])
    p.purge()
    assert db.listing("used_condo", "2") is None
    assert not archive.detail_path("tokyo", "used_condo", "2").exists()


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
    crawl(1, [])
    crawl(2, [])
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
    assert status(db, 1) == status(db, 2) == "removed"
    assert sorted(events(db, "removed")) == [("removed", "1"), ("removed", "2")]


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
