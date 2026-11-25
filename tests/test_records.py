"""Page records in the state: store_page, the queue's extract/derive error paths, the debug sample, refetch triggers,
the slow refresh, twins and re-typed ids, re-deriving, the backfilled guard, seed, migrate-state (no network)."""
import argparse
import gzip
import json
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from suumo import cli, detail, pipeline
from suumo.cli import Ctx
from suumo.db import DB
from suumo.export import export
from suumo.extract import EXTRACT_VERSION, extract
from suumo.maintenance import seed
from suumo.migrate import migrate_state
from suumo.pipeline import P_BACKFILL, P_CHANGED, P_NEW, Pipeline, store_page
from suumo.scope import Target
from tests.helpers import FIXTURES, QUIET, fixture_html, rec, write

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 11, 20, 6, 0, tzinfo=JST)
AREA = {"code": "13219", "name": "狛江市", "slug": "sc_komae", "expected": 0}
TARGETS = [Target("tokyo", t, None) for t in ("used_condo", "used_house", "new_house", "land")]
CONDO = fixture_html("detail_used_condo_20205670.html.gz")
RECORD = extract(CONDO, "used_condo", "20205670")


def condo(lid):
    """The condo page's record as stored for listing lid (photo paths are compacted against the id)."""
    return extract(CONDO, "used_condo", str(lid))


def _db(tmp_path, ids=(), type_="used_condo", queue=True):
    db = DB(tmp_path / "s.db")
    for lid in ids:
        _insert(db, type_, lid)
        if queue:
            db.enqueue(type_, lid, P_BACKFILL, "x")
    db.x("INSERT INTO runs (run_id, started) VALUES ('r', 'x')")
    db.commit()
    return db


def _insert(db, type_, lid, path=None, **cols):
    db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen) "
         "VALUES (?, ?, 'tokyo', '13219', '狛江市', ?, '2026-10-05', 'x')", type_, str(lid),
         json.dumps({"id": str(lid), "path": path or f"/ms/chuko/tokyo/sc_komae/nc_{lid}/", "price": 1}))
    for k, v in cols.items():
        db.x(f"UPDATE listings SET {k}=? WHERE type=? AND id=?", v, type_, str(lid))


class Pages:
    """A client serving one page (or raising) for every listing page; records the paths."""

    def __init__(self, reply=CONDO):
        self.reply, self.delay, self.paths = reply, 1.0, []

    def get(self, path):
        self.paths.append(path)
        reply = self.reply(path) if callable(self.reply) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply


def _run(db, client, **kw):
    p = Pipeline(db, client, TARGETS, NOW, "r", log=QUIET, **kw)
    p.process_queue(3600)
    return p


def test_store_page_sets_the_record_its_version_time_and_derived_fields(tmp_path):
    db = _db(tmp_path, ["1"], queue=False)
    fields = store_page(db, "used_condo", "1", RECORD, "2026-11-20T06:00:00+09:00")
    row = db.listing("used_condo", "1")
    assert json.loads(row["page_json"]) == RECORD and row["extract_v"] == EXTRACT_VERSION
    assert row["fetched_at"] == "2026-11-20T06:00:00+09:00"
    assert json.loads(row["detail_json"]) == fields == detail.derive(RECORD)
    assert fields["land_rights"] == "定期借地権" and "id" not in RECORD and "type" not in RECORD


def test_the_queue_stores_records_and_reports_coverage(tmp_path):
    db = _db(tmp_path, ["1", "2"])
    p = _run(db, Pages())
    rows = db.x("SELECT id, page_json, fetched_at, detail_json FROM listings ORDER BY id").fetchall()
    assert all(json.loads(r["page_json"]) == condo(r["id"]) and r["fetched_at"] and r["detail_json"] for r in rows)
    assert p.report["detail"]["fetched"] == 2 and db.x("SELECT COUNT(*) FROM queue").fetchone()[0] == 0
    kr = p.report["pages"]["templates"]["kr"]
    assert kr["n"] == 2 and kr["ll"] == kr["img"] == kr["agent"] == kr["features"] == kr["spec10"] == 2
    assert p.report["flags"] == [] and p.report["pages"]["extract_errors"] == 0


def test_an_extract_error_keeps_the_old_record_gives_up_and_goes_to_the_debug_sample(tmp_path, monkeypatch):
    db = _db(tmp_path, ["1"])
    store_page(db, "used_condo", "1", RECORD, "2026-10-01T00:00:00+09:00")
    before = tuple(db.listing("used_condo", "1"))
    monkeypatch.setattr(pipeline.extract_mod, "extract", lambda *a: 1 / 0)
    p = _run(db, Pages("<html>changed</html>"), debug_dir=tmp_path / "debug")
    assert tuple(db.listing("used_condo", "1")) == before                     # never nulled or overwritten
    attempts, error = db.x("SELECT attempts, last_error FROM queue").fetchone()
    assert attempts == pipeline.MAX_DETAIL_ATTEMPTS and error.startswith("extract: ZeroDivisionError")
    assert p.report["pages"]["extract_errors"] == 1 and p.report["flags"] == []   # 1 page: too few to judge
    assert gzip.decompress((tmp_path / "debug/used_condo_1.html.gz").read_bytes()) == b"<html>changed</html>"
    assert "ZeroDivisionError" in json.loads((tmp_path / "debug/used_condo_1.json").read_text())["error"]


def test_an_extract_error_streak_stops_the_queue_without_using_attempts(tmp_path, monkeypatch):
    n = pipeline.EXTRACT_STREAK + 5
    db = _db(tmp_path, [str(i) for i in range(n)])
    db.x("UPDATE queue SET attempts=1 WHERE id='0'")
    db.commit()
    monkeypatch.setattr(pipeline.extract_mod, "extract", lambda *a: 1 / 0)
    client = Pages()
    p = _run(db, client)
    assert len(client.paths) == pipeline.EXTRACT_STREAK                     # stopped: the rest not fetched
    assert "extract_streak" in p.report["flags"]
    attempts = dict(db.x("SELECT id, attempts FROM queue").fetchall())
    assert attempts["0"] == 1 and set(attempts.values()) == {0, 1}           # attempts as before the streak
    assert db.x("SELECT COUNT(*) FROM listings WHERE page_json IS NOT NULL").fetchone()[0] == 0


def test_a_derive_error_stores_the_record_and_flags_the_run(tmp_path, monkeypatch):
    db = _db(tmp_path, ["1"])
    monkeypatch.setattr(pipeline, "derive", lambda record: 1 / 0)
    p = _run(db, Pages())
    row = db.listing("used_condo", "1")
    assert json.loads(row["page_json"]) == condo(1) and row["detail_json"] is None
    assert p.report["flags"] == ["derive_errors"] and p.report["pages"]["derive_errors"] == 1
    assert db.x("SELECT COUNT(*) FROM queue").fetchone()[0] == 0              # stored: re-derived after a fix


def test_the_debug_sample_is_a_deterministic_half_percent(tmp_path):
    picked = [i for i in range(20000) if pipeline.debug_sampled("used_condo", str(i))]
    assert 70 <= len(picked) <= 130                                          # ~0.5%
    assert picked == [i for i in range(20000) if pipeline.debug_sampled("used_condo", str(i))]
    lid, other = str(picked[0]), next(str(i) for i in range(20000) if i not in picked)
    db = _db(tmp_path, [lid, other])
    _run(db, Pages(), debug_dir=tmp_path / "debug")
    assert sorted(f.name for f in (tmp_path / "debug").iterdir()) == [f"used_condo_{lid}.html.gz",
                                                                      f"used_condo_{lid}.json"]
    assert json.loads((tmp_path / "debug" / f"used_condo_{lid}.json").read_text())["record"] == condo(lid)


def test_extract_command_reproduces_a_sampled_page(tmp_path, capsys):
    page = tmp_path / "used_condo_20205670.html.gz"
    page.write_bytes((FIXTURES / "detail_used_condo_20205670.html.gz").read_bytes())
    cli.cmd_extract(argparse.Namespace(file=str(page), type=None, id=None))
    out = json.loads(capsys.readouterr().out)
    assert (out["type"], out["id"]) == ("used_condo", "20205670")
    assert out["record"] == RECORD and out["derived"] == detail.derive(RECORD)


def test_refetch_errors_requeues_extract_failures_at_their_priority(tmp_path, capsys):
    db = _db(tmp_path, ["1", "2", "3"])
    db.x("UPDATE queue SET attempts=3, last_error='extract: KeyError()' WHERE id IN ('1', '2')")
    db.x("UPDATE queue SET priority=? WHERE id='1'", P_NEW)
    db.x("UPDATE queue SET attempts=3, last_error='gone: 404' WHERE id='3'")
    db.commit()
    c = Ctx(argparse.Namespace(db=str(tmp_path / "s.db"), data=str(tmp_path / "data"), scope="scope.toml",
                               errors=True, limit=1))
    cli.cmd_refetch(c)
    c.args.limit = None
    cli.cmd_refetch(c)
    rows = [tuple(r) for r in db.x("SELECT id, priority, attempts, last_error FROM queue ORDER BY id")]
    assert rows == [("1", P_NEW, 0, None), ("2", P_BACKFILL, 0, None), ("3", P_BACKFILL, 3, "gone: 404")]
    assert "queued 1 " in capsys.readouterr().out


def _reconcile(db, recs, day=0, saved=()):
    now = NOW + timedelta(days=day)
    p = Pipeline(db, None, TARGETS, now, now.strftime("%Y%m%dT%H%M%S"), log=QUIET, saved=saved)
    db.x("INSERT OR IGNORE INTO runs (run_id, started) VALUES (?, ?)", p.run_id, p.ts)
    out = p.reconcile("tokyo", "used_condo", AREA, len(recs), {r["id"]: r for r in recs}, True)
    db.commit()
    return out


def _lrec(i, price=50_000_000, **kw):
    return {"id": str(i), "path": f"/ms/chuko/tokyo/sc_komae/nc_{i}/", "price": price, **kw}


def test_a_saved_ended_lead_back_on_suumo_is_fetched_again_as_changed(tmp_path):
    db = _db(tmp_path)
    db.upsert_area("tokyo", "used_condo", AREA)
    _reconcile(db, [_lrec(1)])
    store_page(db, "used_condo", "1", RECORD, NOW.isoformat())
    db.dequeue("used_condo", "1")
    saved = {"used_condo:1"}
    _reconcile(db, [], 1, saved)
    _reconcile(db, [], 2, saved)
    assert db.listing("used_condo", "1")["status"] == "removed" and db.listing("used_condo", "1")["page_json"]
    _reconcile(db, [_lrec(1)], 3, saved)
    assert db.x("SELECT priority FROM queue WHERE id='1'").fetchone()[0] == P_CHANGED


def test_new_listings_outrank_changed_ones_and_changed_outrank_backfill(tmp_path):
    db = _db(tmp_path)
    for lid, priority in (("1", P_BACKFILL), ("2", P_CHANGED), ("3", P_NEW)):
        _insert(db, "used_condo", lid)
        db.enqueue("used_condo", lid, priority, "x")
    client = Pages()
    _run(db, client)
    assert [p.rstrip("/").split("nc_")[1] for p in client.paths][:3] == ["3", "2", "1"]


def test_slow_refresh_takes_old_versions_then_the_oldest_records_after_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "REFRESH_PER_RUN", 3)
    db = _db(tmp_path, ["q"])
    old = lambda days: (NOW - timedelta(days=days)).isoformat()  # noqa: E731
    page = json.dumps(RECORD)
    _insert(db, "used_condo", "fresh", page_json=page, extract_v=EXTRACT_VERSION, fetched_at=old(1))
    _insert(db, "used_condo", "old", page_json=page, extract_v=EXTRACT_VERSION, fetched_at=old(200))
    _insert(db, "used_condo", "older", page_json=page, extract_v=EXTRACT_VERSION, fetched_at=old(300))
    _insert(db, "used_condo", "v0", page_json=page, extract_v=EXTRACT_VERSION - 1, fetched_at=old(2))
    _insert(db, "used_condo", "oldest", page_json=page, extract_v=EXTRACT_VERSION, fetched_at=old(400),
            status="removed")                                                # ended (saved): never refreshed
    _insert(db, "used_condo", "copy")                                        # no record: not a lead
    _insert(db, "used_condo", "queued", page_json=page, extract_v=EXTRACT_VERSION, fetched_at=old(500))
    db.enqueue("used_condo", "queued", P_BACKFILL, "x")
    db.x("UPDATE queue SET attempts=? WHERE id='queued'", pipeline.MAX_DETAIL_ATTEMPTS)  # given up: not again
    db.commit()
    client = Pages()
    p = _run(db, client)
    ids = [x.rstrip("/").split("nc_")[1] for x in client.paths]
    assert ids[0] == "q" and sorted(ids[1:]) == ["old", "older", "v0"]     # the queue first; never fresh/ended/copies
    assert p.report["detail"]["refresh"] == 3
    assert db.listing("used_condo", "older")["fetched_at"] != old(300)                 # fetched again


def test_slow_refresh_waits_while_the_budget_is_spent_on_the_queue(tmp_path, monkeypatch):
    db = _db(tmp_path, ["q"])
    _insert(db, "used_condo", "old", page_json="{}", extract_v=0, fetched_at="2020-01-01")
    db.commit()
    p = Pipeline(db, Pages(), TARGETS, NOW, "r", log=QUIET)
    p.process_queue(0)
    assert p.report["detail"]["refresh"] == 0 and db.x("SELECT COUNT(*) FROM queue").fetchone()[0] == 1


def test_a_land_twin_copies_the_record_and_a_retyped_id_is_fetched(tmp_path):
    db = _db(tmp_path)
    tochi = "/tochi/tokyo/sc_komae/nc_6/"
    land = extract(fixture_html("detail_land_20862637.html.gz"), "land", "6")
    _insert(db, "new_house", "6", path=tochi)
    store_page(db, "new_house", "6", land, "2026-11-01T05:00:00+09:00", extract_v=EXTRACT_VERSION)
    _insert(db, "land", "6", path=tochi)
    _insert(db, "used_house", "7")                                           # re-typed from new_house:
    _insert(db, "new_house", "7", path="/ikkodate/tokyo/sc_komae/nc_7/")     # another page, held under the old type
    store_page(db, "new_house", "7", RECORD, "2026-11-01T05:00:00+09:00")
    for type_, lid in (("land", "6"), ("used_house", "7")):
        db.enqueue(type_, lid, P_BACKFILL, "x")
    db.commit()
    house = Pages(fixture_html("detail_used_house_20779514.html.gz"))
    p = _run(db, house)
    assert house.paths == ["/ms/chuko/tokyo/sc_komae/nc_7/"]                # the re-typed id's own page
    copy, twin = db.listing("land", "6"), db.listing("new_house", "6")
    assert all(copy[k] == twin[k] for k in ("page_json", "extract_v", "fetched_at", "detail_json"))
    assert p.report["detail"]["copied"] == 1
    retyped = json.loads(db.listing("used_house", "7")["page_json"])
    assert retyped == extract(house.reply, "used_house", "7") != RECORD


def test_delete_and_purge_drop_the_records_with_the_rows(tmp_path):
    db = _db(tmp_path)
    db.upsert_area("tokyo", "used_condo", AREA)
    _reconcile(db, [_lrec(1), _lrec(2), _lrec(3)])
    for lid in "123":
        store_page(db, "used_condo", lid, RECORD, NOW.isoformat())
    db.commit()
    saved = {"used_condo:3"}
    _reconcile(db, [_lrec(1)], 1, saved)
    _reconcile(db, [_lrec(1)], 2, saved)                                    # 2 deleted, 3 kept (saved) with its record
    assert db.listing("used_condo", "2") is None and db.listing("used_condo", "3")["page_json"]
    p = Pipeline(db, None, TARGETS, NOW + timedelta(days=3), "r3", log=QUIET, saved=set())
    p.purge()                                                               # 3 no longer saved
    assert [r[0] for r in db.x("SELECT id FROM listings WHERE page_json IS NOT NULL")] == ["1"]


def test_rederive_runs_on_a_parse_version_bump_only(tmp_path, monkeypatch):
    db = _db(tmp_path, ["1", "2"], queue=False)
    for lid in "12":
        store_page(db, "used_condo", lid, RECORD, NOW.isoformat())
    db.x("UPDATE listings SET detail_json='{\"stale\":1}'")
    db.set_meta("parse_v", detail.PARSE_VERSION)
    db.commit()
    assert pipeline.rederive(db, log=QUIET) is None                          # up to date: nothing done
    monkeypatch.setattr(pipeline, "PARSE_VERSION", detail.PARSE_VERSION + 1)
    assert pipeline.rederive(db, log=QUIET) == (2, 0)
    assert {r[0] for r in db.x("SELECT detail_json FROM listings")} == {json.dumps(
        detail.derive(RECORD), ensure_ascii=False, separators=(",", ":"), sort_keys=True)}
    assert db.meta("parse_v") == str(detail.PARSE_VERSION + 1) and pipeline.rederive(db, log=QUIET) is None


def _run_ctx(tmp_path, **kw):
    (tmp_path / "mine.toml").write_text('[[scope]]\npref = "tokyo"\ntypes = ["used_condo"]\n')
    args = dict(db=str(tmp_path / "s.db"), data=str(tmp_path / "data"), debug_dir=None,
                scope=str(tmp_path / "mine.toml"), delay=0.0, push=False, commit=False, crawl_at=None,
                no_crawl=True, no_geocode=True, budget=0.0, until_minute=None)
    c = Ctx(argparse.Namespace(**{**args, **kw}))
    c.db.migrate()
    return c


def test_a_run_refuses_a_state_without_records_and_re_derives_at_start(tmp_path, monkeypatch):
    c = _run_ctx(tmp_path)
    with pytest.raises(SystemExit, match="backfilled"):
        cli.cmd_run(c)
    assert c.db.x("SELECT COUNT(*) FROM runs").fetchone()[0] == 0           # nothing started
    _insert(c.db, "used_condo", "1")
    store_page(c.db, "used_condo", "1", RECORD, NOW.isoformat())
    c.db.x("UPDATE listings SET detail_json='{}'")
    c.db.set_meta("backfilled", "x")
    c.db.commit()
    cli.cmd_run(c)
    assert c.db.listing("used_condo", "1")["detail_json"] != "{}" and c.db.meta("parse_v") == str(detail.PARSE_VERSION)
    report = json.loads(c.db.x("SELECT report FROM runs").fetchone()[0])
    assert report["rederived"] == {"rows": 1, "errors": 0} and report["flags"] == []


def test_seed_leaves_a_runnable_state_with_every_lead_queued(tmp_path):
    data = tmp_path / "data"
    same = {"name": "狛江ハイツ"}
    write(data, [rec(1, has_detail=True, lat=35.6, lng=139.5, photos=3, page_date="2026-11-01", **same),
                 rec(2, **same), rec(3), rec(4, removed_at="2026-10-02")])
    db = DB(tmp_path / "s.db")
    seed(db, [Target("tokyo", "used_condo", None)], data, "2026-11-20T06:00:00+09:00", log=QUIET)
    assert db.meta("backfilled") and db.meta("parse_v") == str(detail.PARSE_VERSION)
    assert [tuple(r) for r in db.x("SELECT id, priority FROM queue ORDER BY id")] == [("1", P_BACKFILL),
                                                                                      ("3", P_BACKFILL)]
    row = db.listing("used_condo", "1")
    for key in ("lat", "lng", "photos", "page_date"):                       # export-time fields: in neither
        assert key not in json.loads(row["list_json"]) and key not in json.loads(row["detail_json"])
    pipeline.check_backfilled(db)                                            # a run accepts it


def test_migrate_state_adds_columns_and_meta_and_remaps_priorities_once(tmp_path):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE listings (type TEXT NOT NULL, id TEXT NOT NULL, pref TEXT NOT NULL, area_code TEXT NOT NULL,
            area_name TEXT, list_json TEXT NOT NULL, detail_json TEXT, status TEXT NOT NULL DEFAULT 'active',
            first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, missed INTEGER NOT NULL DEFAULT 0, removed_at TEXT,
            PRIMARY KEY (type, id));
        CREATE TABLE queue (type TEXT NOT NULL, id TEXT NOT NULL, priority INTEGER NOT NULL, reason TEXT NOT NULL,
            enqueued_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, PRIMARY KEY (type, id));
        INSERT INTO queue (type, id, priority, reason, enqueued_at) VALUES ('land', '1', 1, '', 'x'),
            ('land', '2', 0, '', 'x');
    """)
    c.commit()
    c.close()
    db = DB(path, migrate=False)
    assert migrate_state(db, log=QUIET) == 1
    cols = {r["name"] for r in db.x("PRAGMA table_info(listings)")}
    assert {"page_json", "extract_v", "fetched_at"} <= cols and db.meta("queue_v") == "2"
    assert dict(db.x("SELECT id, priority FROM queue").fetchall()) == {"1": P_NEW, "2": P_BACKFILL}
    db.enqueue("land", "3", P_CHANGED, "x")                                  # a new-code entry at priority 1
    db.commit()
    assert migrate_state(db, log=QUIET) == 0                                 # idempotent: nothing remapped again
    assert dict(db.x("SELECT id, priority FROM queue").fetchall()) == {"1": P_NEW, "2": P_BACKFILL, "3": P_CHANGED}
    assert not db.meta("backfilled")                                         # the backfill's to set


def test_status_shows_records_queue_priorities_and_extract_errors(tmp_path, capsys):
    db = _db(tmp_path, ["1", "2", "3"])
    store_page(db, "used_condo", "1", condo(1), NOW.isoformat())
    db.dequeue("used_condo", "1")
    db.x("UPDATE queue SET priority=? WHERE id='2'", P_NEW)
    db.x("UPDATE queue SET attempts=3, last_error='extract: KeyError()' WHERE id='3'")
    db.commit()
    (tmp_path / "mine.toml").write_text('[[scope]]\npref = "tokyo"\ntypes = ["used_condo"]\n')
    cli.cmd_status(Ctx(argparse.Namespace(db=str(tmp_path / "s.db"), data=str(tmp_path / "data"),
                                          scope=str(tmp_path / "mine.toml"))))
    out = capsys.readouterr().out
    assert "records" in out and " 33%" in out                                # 1 of 3 active listings has a record
    assert "queue: 1 listing pages (new 1)" in out and "extract errors: 1" in out


def test_stale_land_twins_are_fetched_once_and_both_get_the_new_record(tmp_path):
    db = _db(tmp_path)
    tochi = "/tochi/tokyo/sc_komae/nc_6/"
    land_html = fixture_html("detail_land_20862637.html.gz")
    stale = {"tpl": "kr", "spec": {"価格": "1円"}}
    for type_ in ("new_house", "land"):                       # both hold the same old record, both queued (refresh)
        _insert(db, type_, "6", path=tochi)
        store_page(db, type_, "6", stale, "2026-09-01T05:00:00+09:00")
        db.enqueue(type_, "6", P_BACKFILL, "x")
    db.commit()
    client = Pages(land_html)
    p = _run(db, client)
    assert client.paths == [tochi]                            # one request: the second copies the first's new page
    rows = [db.listing(t, "6") for t in ("new_house", "land")]
    assert rows[0]["fetched_at"] == rows[1]["fetched_at"] > "2026-09-01"
    assert json.loads(rows[0]["page_json"]) == json.loads(rows[1]["page_json"]) == extract(land_html, "land", "6")
    assert p.report["detail"]["copied"] == 1
    again = Pages(land_html)
    assert _run(db, again).report["detail"]["refresh"] == 0 and again.paths == []   # not selected again


def test_a_twin_with_an_older_record_is_not_copied_over_a_newer_one(tmp_path):
    db = _db(tmp_path)
    tochi = "/tochi/tokyo/sc_komae/nc_6/"
    _insert(db, "new_house", "6", path=tochi)
    store_page(db, "new_house", "6", {"tpl": "kr"}, "2026-09-01T05:00:00+09:00")
    _insert(db, "land", "6", path=tochi)
    store_page(db, "land", "6", {"tpl": "kr", "features": ["x"]}, "2026-10-01T05:00:00+09:00")
    db.enqueue("land", "6", P_CHANGED, "x")
    db.commit()
    client = Pages(fixture_html("detail_land_20862637.html.gz"))
    _run(db, client)
    assert client.paths == [tochi] and db.listing("land", "6")["fetched_at"] > "2026-10-01"


def test_extract_errors_below_the_streak_flag_the_run_from_a_count_or_a_share(tmp_path, monkeypatch):
    def flaky(bad):
        return lambda html, type_, lid: 1 / 0 if lid in bad else {"tpl": "kr"}
    db = _db(tmp_path, [str(i) for i in range(150)])
    monkeypatch.setattr(pipeline.extract_mod, "extract", flaky({"7"}))
    p = _run(db, Pages())                                     # 1 of 150 (< 1%, < 5): not flagged
    assert p.report["pages"]["extract_errors"] == 1 and p.report["flags"] == []
    (tmp_path / "b").mkdir()
    db2 = _db(tmp_path / "b", [str(i) for i in range(400)])
    monkeypatch.setattr(pipeline.extract_mod, "extract", flaky({str(i) for i in range(0, 400, 50)}))
    p = _run(db2, Pages())                                    # 8 of 400 (2%, >= 5): flagged, no streak
    assert p.report["pages"]["extract_errors"] == 8 and p.report["flags"] == ["extract_errors"]


def test_rows_left_without_derived_fields_are_re_derived_at_the_next_start(tmp_path):
    db = _db(tmp_path, ["1", "2"], queue=False)
    for lid in "12":
        store_page(db, "used_condo", lid, condo(lid), NOW.isoformat())
    db.x("UPDATE listings SET detail_json=NULL WHERE id='2'")    # a derive error on its first fetch
    db.x("UPDATE listings SET detail_json='{\"kept\":1}' WHERE id='1'")
    db.set_meta("parse_v", detail.PARSE_VERSION)
    db.commit()
    assert pipeline.rederive(db, log=QUIET) == (1, 0)
    assert json.loads(db.listing("used_condo", "2")["detail_json"]) == detail.derive(condo(2))
    assert db.listing("used_condo", "1")["detail_json"] == '{"kept":1}'   # no version bump: only the missing one
    assert pipeline.rederive(db, log=QUIET) is None


def test_seed_sets_meta_only_on_a_state_without_listings(tmp_path):
    data = tmp_path / "data"
    write(data, [rec(1)], pref="chiba", areas={"12218": "x"})
    db = _db(tmp_path, ["5"])                                  # holds tokyo data, not backfilled yet
    db.x("DELETE FROM meta")
    db.commit()
    seed(db, [Target(p, "used_condo", None) for p in ("tokyo", "chiba")], data, "2026-11-20T06:00:00+09:00",
         log=QUIET)
    assert db.listing("used_condo", "1") is not None           # chiba seeded...
    assert not db.meta("backfilled") and not db.meta("parse_v") and not db.meta("queue_v")  # ...tokyo still unfilled


def test_a_new_or_seeded_state_never_gets_its_priorities_remapped(tmp_path):
    assert DB(tmp_path / "new.db").meta("queue_v") == "2"
    data = tmp_path / "data"
    write(data, [rec(1)])
    path = tmp_path / "seeded.db"
    sqlite3.connect(path).executescript(           # an empty state made by older code: no meta
        "CREATE TABLE listings (type TEXT NOT NULL, id TEXT NOT NULL, pref TEXT NOT NULL, area_code TEXT NOT NULL,"
        " area_name TEXT, list_json TEXT NOT NULL, detail_json TEXT, status TEXT NOT NULL DEFAULT 'active',"
        " first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, missed INTEGER NOT NULL DEFAULT 0, removed_at TEXT,"
        " PRIMARY KEY (type, id));")
    db = DB(path)
    assert not db.meta("queue_v")
    seed(db, [Target("tokyo", "used_condo", None)], data, "2026-11-20T06:00:00+09:00", log=QUIET)
    db.x("UPDATE queue SET priority=? WHERE id='1'", P_CHANGED)
    db.commit()
    assert migrate_state(db, log=QUIET) == 0
    assert db.x("SELECT priority FROM queue WHERE id='1'").fetchone()[0] == P_CHANGED


def test_has_detail_means_a_page_record_or_seeded_listing_page_fields(tmp_path):
    db = _db(tmp_path, ["1", "2", "3", "4"], queue=False)
    store_page(db, "used_condo", "1", condo(1), NOW.isoformat())              # fetched
    db.x("UPDATE listings SET page_json='{}', detail_json=NULL WHERE id='2'")    # fetched, derive failed
    db.x("UPDATE listings SET detail_json='{\"floor\":3}' WHERE id='3'")         # seeded from data/, not fetched
    db.commit()
    export(db, [Target("tokyo", "used_condo", None)], tmp_path / "data")
    lines = (tmp_path / "data/tokyo/used_condo/13219.jsonl").read_text().splitlines()
    assert {r["id"]: r.get("has_detail") for r in map(json.loads, lines)} == {"1": True, "2": True, "3": True,
                                                                               "4": None}
