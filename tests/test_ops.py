"""Discord posting, maintenance commands, the run lock and git data commits (no network)."""
import json
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from suumo import notify as notify_mod
from suumo.archive import Archive
from suumo.db import DB, exclusive
from suumo.gitdata import commit_data
from suumo.maintenance import prune, reparse
from suumo.scope import Target

NOW = datetime(2026, 10, 6, 4, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


def add_events(db, n, run_id="20261006T040000"):
    for i in range(n):
        db.x("INSERT INTO events (run_id, kind, type, id, pref, area_code, payload) VALUES (?,?,?,?,?,?,?)",
             run_id, "new", "used_condo", str(i), "tokyo", "13219",
             json.dumps({"area": "狛江市", "town": "x" * 150, "price": 10_000_000 + i, "path": f"/p/nc_{i}/"}))
    db.commit()


def pending(db):
    return db.x("SELECT COUNT(*) FROM events WHERE posted=0").fetchone()[0]


def test_failed_send_keeps_only_unsent_events_pending(tmp_path, monkeypatch):
    db = DB(tmp_path / "s.db")
    add_events(db, 15)                      # long lines -> several messages
    sent = []

    def fake_send(token, channel, text):
        if len(sent) == 1:
            raise RuntimeError("discord down")
        sent.append(text)

    monkeypatch.setattr(notify_mod, "send", fake_send)
    msgs = notify_mod.notify(db, NOW, "t", "c", log=lambda *_: None)
    assert len(msgs) > 1 and len(sent) == 1
    first_batch = len(msgs[0][1])
    assert pending(db) == 15 - first_batch
    monkeypatch.setattr(notify_mod, "send", lambda *a: sent.append(a[-1]))
    notify_mod.notify(db, NOW, "t", "c", log=lambda *_: None)
    assert pending(db) == 0


def test_unconfigured_discord_previews_and_keeps_events(tmp_path):
    db = DB(tmp_path / "s.db")
    add_events(db, 2)
    out = []
    notify_mod.notify(db, NOW, None, None, log=out.append)
    assert pending(db) == 2 and "not configured" in out[0]


def test_old_pending_events_are_skipped_not_posted(tmp_path, monkeypatch):
    db = DB(tmp_path / "s.db")
    add_events(db, 1, run_id="20261001T040000")   # 5 days old
    monkeypatch.setattr(notify_mod, "send", lambda *a: pytest.fail("should not post"))
    notify_mod.notify(db, NOW, "t", "c", log=lambda *_: None)
    assert db.x("SELECT posted FROM events").fetchone()[0] == -1


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
    assert prune(db, archive, targets, tmp_path / "data", apply=False, log=lambda *_: None) == 0
    assert db.listing("used_condo", "20205670") is not None          # dry run kept it
    assert prune(db, archive, targets, tmp_path / "data", apply=True, log=lambda *_: None) == 1
    assert db.listing("used_condo", "20205670") is None
    assert not (tmp_path / "archive/chiba").exists() and not (tmp_path / "data/chiba").exists()
    assert db.x("SELECT COUNT(*) FROM queue").fetchone()[0] == 0


def test_reparse_rebuilds_from_archive(tmp_path):
    db = _db_with_listing(tmp_path)
    archive = Archive(tmp_path / "archive")
    fixtures = __import__("pathlib").Path(__file__).parent / "fixtures"
    archive.save_detail("tokyo", "used_condo", "20205670",
                        Archive.read(fixtures / "detail_used_condo_20205670.html.gz"))
    archive.save_list("tokyo", "20261005", "used_condo", "sc_komae_p1",
                      Archive.read(fixtures / "list_used_condo_komae.html.gz"))
    n_list, n_detail = reparse(db, archive, log=lambda *_: None)
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
    (tmp_path / "other.txt").write_text("not data")
    assert commit_data(tmp_path, "r1", log=lambda *_: None)
    assert git("log", "--name-only", "--format=").stdout.split() == ["data/x.jsonl"]
    assert not commit_data(tmp_path, "r2", log=lambda *_: None)     # unchanged -> no empty commit
