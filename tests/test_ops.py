"""Maintenance commands, the run lock, git data commits, polite fetching and long runs (no network)."""
import json
import subprocess
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from suumo import http, pipeline
from suumo.archive import Archive
from suumo.cli import is_locked, print_progress
from suumo.db import DB, exclusive
from suumo.geo import town_of
from suumo.gitdata import commit_data
from suumo.maintenance import prune, reparse
from suumo.pipeline import Pipeline
from suumo.scope import Target
from tests.helpers import FIXTURES, QUIET

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
    c.session.get = lambda url, timeout: next(replies)
    assert c.get("/x/") == "<html/>"
    assert c.delay == pytest.approx(2.0 * 0.95) and c.slowdowns == 1   # doubled, then eased once
    c.session.get = lambda url, timeout: FakeResponse(200)
    for _ in range(200):
        c.get("/x/")
    assert c.delay == 1.0                                   # back to the base after fast successes
    c.session.get = lambda url, timeout: FakeResponse(503)
    with pytest.raises(RuntimeError):
        c.get("/x/")
    assert c.delay == http.MAX_DELAY                        # doubled per failure, capped


def test_client_404_is_gone_not_an_error(monkeypatch):
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    c = http.Client(delay=1.0, log=QUIET)
    c.session.get = lambda url, timeout: FakeResponse(404)
    with pytest.raises(FileNotFoundError):
        c.get("/x/")
    assert c.delay == 1.0


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
])
def test_town_of_drops_block_numbers_and_adds_the_prefecture(address, town):
    assert town_of(address, "tokyo") == town


def test_archive_ignores_stray_files_in_list_days(tmp_path):
    from datetime import date

    from suumo.archive import Archive
    a = Archive(tmp_path)
    (tmp_path / "tokyo/list/20260101").mkdir(parents=True)
    (tmp_path / "tokyo/list/.DS_Store").write_text("")
    assert a.list_days("tokyo") == ["20260101"]
    a.prune_lists("tokyo", date(2026, 10, 6))
    assert a.list_days("tokyo") == []
