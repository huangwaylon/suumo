"""Maintenance commands, the run lock, git data commits, polite fetching and long runs (no network)."""
import fcntl
import io
import json
import os
import subprocess
import zipfile
from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from suumo import fx, geo, http, pipeline, saved
from suumo.archive import Archive
from suumo.cli import crawl_due, crawled_since, is_locked, print_progress, seconds_until_minute
from suumo.db import DB, exclusive
from suumo.export import export
from suumo.geo import _municipality, _same_spelling, geocode, town_of
from suumo.gitdata import commit_data
from suumo.maintenance import prune, reparse, seed, strip_list_fields
from suumo.pipeline import Pipeline
from suumo.scope import Target
from tests.helpers import FIXTURES, QUIET, rec, write

NOW = datetime(2026, 10, 6, 4, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def test_second_writer_is_refused(tmp_path):
    lock = tmp_path / "state.db.lock"
    with exclusive(lock), pytest.raises(SystemExit, match="another suumo process"), exclusive(lock):
        pass


def _db_with_listing(tmp_path, pref="tokyo", area="13219"):
    db = DB(tmp_path / "s.db")
    db.upsert_area(pref, "used_condo", {"code": area, "name": "狛江市", "slug": "sc_komae"})
    db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen) "
         "VALUES ('used_condo', '20205670', ?, ?, '狛江市', ?, '2026-10-05', 'x')",
         pref, area, json.dumps({"id": "20205670", "path": "/ms/chuko/tokyo/sc_komae/nc_20205670/"}))
    db.enqueue("used_condo", "20205670", 0, "backfill", "x")
    db.commit()
    return db


def test_prune_removes_out_of_scope_prefecture(tmp_path):
    db = _db_with_listing(tmp_path, pref="chiba", area="12218")
    archive = Archive(tmp_path / "archive")
    archive.save_detail("chiba", "used_condo", "20205670", "<html/>")
    (tmp_path / "data/chiba").mkdir(parents=True)
    targets = [Target("tokyo", "used_condo", None)]
    assert prune(db, archive, targets, tmp_path / "data", apply=False, log=QUIET) == 0
    assert db.listing("used_condo", "20205670") is not None          # dry run kept it
    assert prune(db, archive, targets, tmp_path / "data", apply=True, log=QUIET) == 1
    assert db.listing("used_condo", "20205670") is None
    assert not (tmp_path / "archive/chiba").exists() and not (tmp_path / "data/chiba").exists()
    assert db.x("SELECT COUNT(*) FROM queue").fetchone()[0] == 0


def test_seed_rebuilds_state_from_data_and_round_trips(tmp_path):
    data = tmp_path / "data"
    write(data, [rec(1, land_rights="所有権", has_detail=True), rec(2), rec(3, removed_at="2026-10-02")],
          areas={"13219": "狛江市", "13218": "福生市"})
    db = DB(tmp_path / "state.db")
    targets = [Target("tokyo", t, None) for t in ("used_condo", "land")]
    assert seed(db, targets, data, "2026-10-14T10:00:00+09:00", log=QUIET) == 3
    assert seed(db, targets, data, "2026-10-14T10:00:00+09:00", log=QUIET) == 0  # holds tokyo now: no-op
    out = tmp_path / "out"
    export(db, targets, out)
    lines = lambda d: {f.relative_to(d): [json.loads(x) for x in f.read_text().splitlines()]  # noqa: E731
                       for f in d.rglob("*.jsonl")}
    assert lines(out) == lines(data) and json.loads((out / "tokyo/areas.json").read_text()) == {
        "13218": "福生市", "13219": "狛江市"}
    area = db.area("tokyo", "used_condo", "13219")
    assert (area["baselined"], area["last_hits"], area["last_status"]) == (1, 2, "complete")
    assert not db.area("tokyo", "used_condo", "13218")["baselined"]  # no listings: its first crawl is the baseline
    assert db.listing("used_condo", "3")["status"] == "removed"
    # the next crawl records changes since the export: listing 2 is gone (a miss), 4 is new and its page queued
    p = Pipeline(db, None, None, targets, NOW, "r1", log=QUIET)
    a = {"code": "13219", "name": "狛江市", "slug": "sc_komae", "expected": 2}
    p.reconcile("tokyo", "used_condo", a, 2, {"1": rec(1), "4": rec(4)}, True)
    assert db.listing("used_condo", "2")["missed"] == 1
    assert [r["kind"] for r in db.x("SELECT kind FROM events")] == ["new"]
    assert {r["id"] for r in db.x("SELECT id FROM queue")} == {"4"}


def test_seed_keeps_only_listing_page_fields_as_detail(tmp_path):
    data = tmp_path / "data"
    write(data, [rec(1, land_rights="所有権", price_max=60_000_000, has_detail=True), rec(2, has_detail=True)])
    db = DB(tmp_path / "state.db")
    seed(db, [Target("tokyo", "used_condo", None)], data, "2026-10-14T10:00:00+09:00", log=QUIET)
    assert json.loads(db.listing("used_condo", "1")["detail_json"]) == {"land_rights": "所有権", "built": "2005-04",
                                                                       "stations": rec(1)["stations"]}
    assert json.loads(db.listing("used_condo", "1")["list_json"])["price_max"] == 60_000_000
    assert db.listing("used_condo", "2")["detail_json"] is not None    # built, stations


def test_strip_list_fields_fixes_rows_seeded_by_older_code_once(tmp_path):
    db = DB(tmp_path / "state.db")
    rows = {"1": {"price": 1, "price_max": 2, "title": "x", "mgmt_fee": 100},   # seeded: list + detail fields
            "2": {"price": 1, "title": "x"},                                    # seeded: list fields only
            "3": {"mgmt_fee": 100, "features": ["南向き"]}}                      # fetched: untouched
    for lid, detail in rows.items():
        db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, detail_json, first_seen, "
             "last_seen) VALUES ('used_condo', ?, 'tokyo', '13219', '狛江市', '{}', ?, '2026-10-05', 'x')",
             lid, json.dumps(detail))
    db.commit()
    assert strip_list_fields(db, log=QUIET) == 2
    detail = {r["id"]: r["detail_json"] for r in db.x("SELECT id, detail_json FROM listings")}
    assert detail == {"1": '{"mgmt_fee":100}', "2": None, "3": json.dumps(rows["3"])}
    db.x("UPDATE listings SET detail_json='{\"price\":1}' WHERE id='3'")
    assert strip_list_fields(db, log=QUIET) == 0                         # once only (PRAGMA user_version)


def test_reparse_rebuilds_from_archive(tmp_path):
    db = _db_with_listing(tmp_path)
    archive = Archive(tmp_path / "archive")
    archive.save_detail("tokyo", "used_condo", "20205670",
                        Archive.read(FIXTURES / "detail_used_condo_20205670.html.gz"))
    archive.save_list("tokyo", "20261005", "used_condo", "sc_komae_p1",
                      Archive.read(FIXTURES / "list_used_condo_komae.html.gz"))
    n_list, n_detail = reparse(db, archive, log=QUIET)
    row = db.listing("used_condo", "20205670")
    assert (n_list, n_detail) == (1, 1)
    assert json.loads(row["list_json"])["price"] == 12_000_000
    assert json.loads(row["detail_json"])["land_rights"] == "定期借地権"


def test_commit_data_commits_only_data(tmp_path):
    git = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)  # noqa: E731
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/x.jsonl").write_text("{}\n")
    (tmp_path / "geo").mkdir()
    (tmp_path / "geo/towns.json").write_text("{}\n")
    (tmp_path / "other.txt").write_text("not data")
    assert commit_data(tmp_path, "r1", log=QUIET)
    assert sorted(git("log", "--name-only", "--format=").stdout.split()) == ["data/x.jsonl", "geo/towns.json"]
    assert not commit_data(tmp_path, "r2", log=QUIET)     # unchanged -> no empty commit


# ---------- polite fetching and long runs ----------

class FakeResponse:
    def __init__(self, status=200, body=b"<html/>", headers=None):
        self.status_code, self.content, self.headers = status, body, headers or {}


def test_client_slows_down_on_pushback_and_eases_back(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    c = http.Client(delay=1.0, log=QUIET)
    replies = iter([FakeResponse(429, headers={"Retry-After": "30"}), FakeResponse(200)])
    c.session.get = lambda url, timeout, allow_redirects: next(replies)
    assert c.get("/x/") == "<html/>"
    assert c.delay == pytest.approx(2.0 * 0.95) and c.slowdowns == 1   # doubled, then eased once
    assert c.pushbacks == 1                                 # a 429: SUUMO asking us to slow down
    c.session.get = lambda url, timeout, allow_redirects: FakeResponse(200)
    for _ in range(200):
        c.get("/x/")
    assert c.delay == 1.0                                   # back to the base after fast successes
    c.session.get = lambda url, timeout, allow_redirects: FakeResponse(503)
    with pytest.raises(RuntimeError):
        c.get("/x/")
    assert c.delay == http.MAX_DELAY                        # doubled per failure, capped
    assert c.pushbacks == 1                                 # 5xx: slowed down, but not counted as pushback


def test_client_404_is_gone_not_an_error(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    c = http.Client(delay=1.0, log=QUIET)
    for status in (404, 302):  # 302: an ended listing redirected to its building's page
        c.session.get = lambda url, timeout, allow_redirects, s=status: FakeResponse(s, headers={"Location": "/x"})
        with pytest.raises(FileNotFoundError):
            c.get("/x/")
    assert c.delay == 1.0


def test_client_network_errors_retry_then_raise_without_a_last_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    c = http.Client(delay=1.0, log=QUIET)
    monkeypatch.setattr(c, "_last", -1e9)

    def get(url, timeout, allow_redirects):
        c._last = -1e9  # no delay floor between attempts: only the backoff sleeps are left
        raise http.requests.ConnectionError("down")
    c.session.get = get
    with pytest.raises(RuntimeError, match="failed after 4 attempts"):
        c.get("/x/")
    assert sleeps == [5, 10, 20]                            # backoff after attempts 1-3, none after the last
    assert c.delay > 1.0 and c.pushbacks == 0               # slowed down; a network error isn't a pushback


def _queue_db(tmp_path, n):
    db = DB(tmp_path / "s.db")
    for i in range(n):
        db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen) "
             "VALUES ('used_condo', ?, 'tokyo', '13219', '狛江市', ?, '2026-10-05', 'x')",
             str(i), json.dumps({"id": str(i), "path": f"/ms/chuko/tokyo/sc_komae/nc_{i}/"}))
        db.enqueue("used_condo", str(i), 0, "backfill", "x")
    db.x("INSERT INTO runs (run_id, started) VALUES ('r', 'x')")
    db.commit()
    return db


def test_outage_pauses_without_using_up_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.time, "sleep", lambda s: None)
    db = _queue_db(tmp_path, 8)
    client = MagicMock()
    client.get.side_effect = RuntimeError("failed after 4 attempts")
    out = []
    p = Pipeline(db, Archive(tmp_path / "a"), client, [Target("tokyo", "used_condo", None)], NOW, "r",
                 log=out.append)
    p.process_queue(3600)
    attempts = [r[0] for r in db.x("SELECT attempts FROM queue ORDER BY CAST(id AS INT)")]
    assert attempts == [0, 0, 0, 0, 0, 1, 1, 1]            # the first 5 were an outage: not counted
    assert any("pausing" in line for line in out)


def test_long_queue_reports_progress_and_checkpoints(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PROGRESS_EVERY", 2)
    monkeypatch.setattr(pipeline, "CHECKPOINT_SECONDS", 0)
    monkeypatch.setattr(pipeline, "apply_detail", lambda *a, **k: None)
    db = _queue_db(tmp_path, 5)
    client = MagicMock()
    client.get.return_value = "<html/>"
    client.delay = 1.0
    out, checkpoints = [], []
    p = Pipeline(db, Archive(tmp_path / "a"), client, [Target("tokyo", "used_condo", None)], NOW, "r",
                 log=out.append)
    seen = []
    real_save = p.save_progress

    def spy(*args):
        seen.append(args)
        real_save(*args)

    p.save_progress = spy
    p.process_queue(3600, checkpoint=lambda: checkpoints.append(1))
    assert [s[1].split(" ")[0] for s in seen] == ["2/5", "4/5"]
    assert any("listing pages 4/5 (80.0%)" in line for line in out)
    assert checkpoints                                      # data/ exported during the run
    assert "progress" not in p.report                       # cleared when the queue phase ends


def test_status_shows_a_running_backfill(tmp_path, capsys):
    progress = {"phase": "listing pages", "text": "1,200/64,000 (1.9%) · ETA 22h46m", "at": NOW.isoformat()}
    print_progress({"run_id": "r1", "report": json.dumps({"progress": progress})}, running=True)
    out = capsys.readouterr().out
    assert "— running" in out and "listing pages (updated" in out and "1,200/64,000 (1.9%) · ETA 22h46m" in out
    lock = tmp_path / "state.db.lock"
    assert not is_locked(lock)
    with exclusive(lock):
        assert is_locked(lock)


@pytest.mark.parametrize("address,town", [
    ("東京都狛江市東和泉２-20-20", "東京都狛江市東和泉２"), ("東京都北区栄町47", "東京都北区栄町"),
    ("港区三田５", "東京都港区三田５"), ("東京都八王子市元本郷町", "東京都八王子市元本郷町"), (None, None),
    ("東京都三鷹市下連雀３-４１－１２", "東京都三鷹市下連雀３"),
    ("東京都あきる野市上代継６０－１", "東京都あきる野市上代継"),
])
def test_town_of_drops_block_numbers_and_adds_the_prefecture(address, town):
    assert town_of(address, "tokyo") == town


def test_archive_ignores_stray_files_in_list_days(tmp_path):
    a = Archive(tmp_path)
    (tmp_path / "tokyo/list/20260101").mkdir(parents=True)
    (tmp_path / "tokyo/list/.DS_Store").write_text("")
    assert a.list_days("tokyo") == ["20260101"]
    a.prune_lists("tokyo", date(2026, 10, 6))
    assert a.list_days("tokyo") == []


def test_saved_list_applies_issue_titles(tmp_path):
    path = tmp_path / "saved.json"
    assert saved.apply(path, "save used_condo:20205670") == "saved used_condo:20205670"
    assert saved.apply(path, "save used_condo:20205670") == "saved used_condo:20205670 (no change)"
    assert saved.apply(path, "save land:1") == "saved land:1"
    assert saved.load(path) == {"used_condo:20205670", "land:1"}
    assert saved.apply(path, "unsave land:1") == "unsaved land:1"
    for bad in ("save used_condo:1; rm -rf /", "save rental:1", "please save this", "save used_condo:abc", ""):
        assert saved.apply(path, bad) is None
    assert path.read_text() == '[\n"used_condo:20205670"\n]\n'


def test_geocoder_titles_match_district_towns_and_ke_spellings():
    assert _same_spelling("神奈川県箱根町強羅").startswith(_municipality("神奈川県足柄下郡箱根町強羅"))
    assert _same_spelling("茨城県龍ケ崎市佐貫町").startswith(_municipality("茨城県龍ヶ崎市佐貫町"))
    assert not _same_spelling("東京都府中市").startswith(_municipality("広島県府中市"))


def test_hourly_runs_stop_listing_pages_before_the_next_run():
    assert seconds_until_minute(datetime(2026, 10, 6, 21, 30, 0), 55) == 25 * 60
    assert seconds_until_minute(datetime(2026, 10, 6, 21, 58, 30), 55) == 56 * 60 + 30   # next hour's :55
    assert seconds_until_minute(datetime(2026, 10, 6, 21, 55, 0), 55) == 0


def test_search_results_are_crawled_once_a_day(tmp_path):
    jst = ZoneInfo("Asia/Tokyo")
    db = DB(tmp_path / "s.db")
    assert crawl_due(db, datetime(2026, 10, 7, 13, 0, tzinfo=jst), 4)                     # never crawled
    db.x("INSERT INTO prefs VALUES ('tokyo', '2026-10-07T04:05:00+09:00')")
    assert not crawl_due(db, datetime(2026, 10, 7, 13, 0, tzinfo=jst), 4)                 # done today
    assert not crawl_due(db, datetime(2026, 10, 8, 3, 0, tzinfo=jst), 4)                  # before tomorrow's 04:00
    assert crawl_due(db, datetime(2026, 10, 8, 4, 5, tzinfo=jst), 4)                      # after it


def test_the_daily_crawl_is_due_until_every_prefecture_is_done(tmp_path):
    jst = ZoneInfo("Asia/Tokyo")
    db = DB(tmp_path / "s.db")
    db.x("INSERT INTO prefs VALUES ('tokyo', '2026-10-07T04:05:00+09:00'), ('chiba', '2026-10-06T04:05:00+09:00')")
    now = datetime(2026, 10, 7, 13, 0, tzinfo=jst)
    assert crawl_due(db, now, 4, {"tokyo", "chiba"}) and crawl_due(db, now, 4)    # chiba is yesterday's
    assert not crawl_due(db, now, 4, {"tokyo"})
    assert crawl_due(db, now, 4, {"tokyo", "osaka"})                               # never crawled
    assert crawled_since(db, now, 4) == {"tokyo"}


class _AreaPages:
    """An area page without areas for every prefecture; stops the run (like a kill) at the one in stop_at."""
    def __init__(self, stop_at=None):
        self.stop_at, self.paths = stop_at, []

    def get(self, path):
        if self.stop_at and f"/{self.stop_at}/" in path:
            raise KeyboardInterrupt
        self.paths.append(path)
        return "<html><body></body></html>"


def test_a_daily_crawl_that_stopped_resumes_with_the_next_prefecture(tmp_path):
    jst = ZoneInfo("Asia/Tokyo")
    db = DB(tmp_path / "s.db")
    targets = [Target(p, t, None) for p in ("tokyo", "chiba") for t in ("used_condo", "land")]
    t1 = datetime(2026, 10, 7, 4, 5, tzinfo=jst)
    with pytest.raises(KeyboardInterrupt):
        Pipeline(db, Archive(tmp_path / "a"), _AreaPages(stop_at="chiba"), targets, t1, "r1", log=QUIET).crawl_lists()
    assert [tuple(r) for r in db.x("SELECT pref, crawled_at FROM prefs")] == [("tokyo", t1.isoformat())]
    t2 = datetime(2026, 10, 7, 4, 30, tzinfo=jst)
    assert crawl_due(db, t2, 4, {"tokyo", "chiba"})
    client = _AreaPages()
    Pipeline(db, Archive(tmp_path / "a"), client, targets, t2, "r2", log=QUIET).crawl_lists(
        skip=crawled_since(db, t2, 4))
    assert client.paths == ["/ms/chuko/chiba/city/", "/tochi/chiba/city/"]
    assert not crawl_due(db, t2, 4, {"tokyo", "chiba"})


def test_a_second_geocoder_skips(tmp_path):
    cache = tmp_path / "towns.json"
    with open(f"{cache}.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert geocode(tmp_path / "data", cache, log=QUIET) == 0  # would need the network otherwise
    assert not cache.exists()


PLACES_CSV = """都道府県名,市区町村名,大字町丁目名,緯度,経度
茨城県,水戸市,赤塚一丁目,36.38,140.41
茨城県,水戸市,赤塚二丁目,36.39,140.42
茨城県,東茨城郡茨城町,大字網掛,36.28,140.35
茨城県,つくば市,島名,36.06,140.04
茨城県,水戸市,南町十二丁目,36.37,140.47
茨城県,水戸市,見和一一丁目,36.40,140.44
"""  # MLIT's file has more columns; these are the ones read


def test_places_match_suumo_town_names():
    places = geo.Places()
    places.add(PLACES_CSV)
    assert places.find("茨城県水戸市赤塚２") == [36.39, 140.42]             # １ ↔ 一丁目
    assert places.find("茨城県水戸市赤塚２丁目") == [36.39, 140.42]
    assert places.find("茨城県水戸市南町１２") == [36.37, 140.47]           # 十二丁目
    assert places.find("茨城県水戸市見和１１") == [36.40, 140.44]           # 一一丁目, digit by digit
    assert places.find("茨城県東茨城郡茨城町網掛") == [36.28, 140.35]       # 郡, 大字 dropped on both sides
    assert places.find("茨城県つくば市島名字薬師台") == [36.06, 140.04]     # a 小字 after the 大字: the 大字
    assert places.find("茨城県水戸市赤塚") == [36.385, 140.415]             # no 丁目 given: the middle of its 丁目s
    assert places.find("茨城県水戸市見川３") is None and places.find("茨城県水戸市") is None


def test_geocode_takes_bulk_places_first_and_asks_only_for_the_rest(tmp_path, monkeypatch):
    data = tmp_path / "data"
    write(data, [rec(1, address="茨城県水戸市赤塚１"), rec(2, address="茨城県水戸市見川３")], pref="ibaraki")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("08000-19.0b/08_2025.csv", PLACES_CSV.encode("cp932"))
    monkeypatch.setattr(geo.requests, "get", lambda url, timeout: MagicMock(content=buf.getvalue()))
    asked = []
    monkeypatch.setattr(geo, "lookup", lambda session, town: asked.append(town) or [36.4, 140.4])
    monkeypatch.setattr(geo, "GEO_DELAY", 0)
    cache = tmp_path / "towns.json"
    assert geocode(data, cache, log=QUIET) == 2
    assert asked == ["茨城県水戸市見川３"]
    assert json.loads(cache.read_text()) == {"茨城県水戸市見川３": [36.4, 140.4], "茨城県水戸市赤塚１": [36.38, 140.41]}


def test_queue_fetches_what_is_queued_and_copies_land_twins(tmp_path):
    db = DB(tmp_path / "s.db")
    archive = Archive(tmp_path / "a")
    condo = {"name": "狛江ハイツ", "floor_m2": 60.0, "layout": "3LDK", "built": "2000-01", "price": 30_000_000}
    rows = [  # (type, id, list fields, fetched page): 1 is fetched; 2 and 3 are queued copies of it (reconcile's call)
        ("used_condo", "1", condo, '{"a":1}'), ("used_condo", "2", condo, None), ("used_condo", "3", condo, None),
        ("new_house", "6", {"path": "/tochi/tokyo/sc_komae/nc_6/"}, None),
        ("land", "6", {"path": "/tochi/tokyo/sc_komae/nc_6/"}, None)]
    for type_, lid, rec_, detail in rows:
        db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, detail_json, first_seen, "
             "last_seen) VALUES (?, ?, 'tokyo', '13219', '狛江市', ?, ?, '2026-10-05', 'x')",
             type_, lid, json.dumps({"path": f"/ms/chuko/tokyo/sc_komae/nc_{lid}/", **rec_}), detail)
        if not detail:
            db.enqueue(type_, lid, 0, "backfill", "x")
    db.x("INSERT INTO runs (run_id, started) VALUES ('r', 'x')")
    db.commit()
    fetched = []

    class Client:
        def get(self, path):
            fetched.append(path)
            return Archive.read(FIXTURES / "detail_used_condo_20205670.html.gz")
    targets = [Target("tokyo", t, None) for t in ("used_condo", "new_house", "land")]
    Pipeline(db, archive, Client(), targets, NOW, "r", log=QUIET).process_queue(3600)
    ids = [p.rstrip("/").split("nc_")[1] for p in fetched]
    assert ids == ["2", "3", "6"]   # everything queued is fetched; land 6 fetched once, for both types
    assert db.listing("land", "6")["detail_json"] == db.listing("new_house", "6")["detail_json"]
    assert archive.detail_path("tokyo", "land", "6").exists() and db.x("SELECT COUNT(*) FROM queue").fetchone()[0] == 0


def test_queue_goes_by_prefecture_size_after_new_listings(tmp_path):
    db = DB(tmp_path / "s.db")
    for pref, lid, priority in [("tottori", "1", 0), ("osaka", "2", 0), ("osaka", "3", 0), ("tottori", "4", 1)]:
        db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen) "
             "VALUES ('land', ?, ?, 'x', 'x', ?, '2026-10-05', 'x')", lid, pref,
             json.dumps({"path": f"/tochi/{pref}/x/nc_{lid}/", "address": lid, "land_m2": 1.0, "price": 1}))
        db.enqueue("land", lid, priority, "x", "x")
    db.x("INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen) "
         "VALUES ('land', '9', 'osaka', 'x', 'x', '{}', '2026-10-05', 'x')")    # osaka has more listings
    rows = db.x("SELECT q.type, q.id, q.priority, l.pref, l.area_code, l.list_json, l.status FROM queue q "
                "JOIN listings l USING (type, id) ORDER BY q.id").fetchall()
    order = [r["id"] for r in Pipeline(db, None, None, [], NOW, "r", log=QUIET)._queue_order(rows)]
    assert order == ["4", "2", "3", "1"]   # new first, then the bigger prefecture


def test_exchange_rate_is_checked_once_a_day_and_kept_on_errors(tmp_path, monkeypatch):
    calls = []

    def get(url, timeout):
        calls.append(url)
        return MagicMock(json=lambda: {"base": "USD", "date": "2026-10-13", "rates": {"JPY": 158.12}})
    monkeypatch.setattr(fx.requests, "get", get)
    path = tmp_path / "fx.json"
    assert fx.update(path, log=QUIET) == {"jpy_per_usd": 158.12, "date": "2026-10-13"}
    assert fx.update(path, log=QUIET)["jpy_per_usd"] == 158.12 and len(calls) == 1   # checked today: no request
    os.utime(path, (0, 0))                                                         # a day later
    monkeypatch.setattr(fx.requests, "get", lambda url, timeout: (_ for _ in ()).throw(fx.requests.ConnectionError()))
    assert fx.update(path, log=QUIET)["jpy_per_usd"] == 158.12                     # offline: the last rate
