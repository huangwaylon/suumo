"""Crawl workers: shards.toml, splitting one state.db into a file per worker and joining them back, a worker's run."""
import argparse
import json
import sqlite3
import tomllib
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from suumo import cli, shards
from suumo.cli import Ctx, cmd_run
from suumo.db import DB
from suumo.pipeline import Pipeline
from tests.helpers import QUIET

ROOT = Path(__file__).resolve().parent.parent


def test_shards_toml_puts_every_prefecture_in_scope_in_exactly_one_shard():
    with open(ROOT / "scope.toml", "rb") as f:
        scope = {s["pref"] for s in tomllib.load(f)["scope"]}
    got = shards.load(ROOT / "shards.toml", scope)
    assert sorted(p for s in got for p in s) == sorted(scope)


def test_load_rejects_a_prefecture_twice_or_missing(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('shards = [["tokyo"], ["tokyo", "chiba"]]\n')
    with pytest.raises(SystemExit, match="tokyo is in shards 0 and 1"):
        shards.load(p)
    p.write_text('shards = [["tokyo"]]\n')
    with pytest.raises(SystemExit, match="not in any shard: \\['chiba'\\]"):
        shards.load(p, {"tokyo", "chiba"})


def _state(path):
    db = DB(path)
    for i, pref in [(1, "tokyo"), (2, "tokyo"), (3, "chiba")]:
        db.x("INSERT INTO listings (type, id, pref, area_code, list_json, first_seen, last_seen) "
             "VALUES ('used_condo', ?, ?, '1', '{}', '2026-11-01', 'x')", str(i), pref)
        db.x("INSERT INTO queue (type, id, priority, reason, enqueued_at) VALUES ('used_condo', ?, 1, '', 'x')",
             str(i))
        db.add_event("20261101T050000", "new", {"type": "used_condo", "id": str(i), "pref": pref,
                                                 "area_code": "1"}, {"price": i})
    db.x("INSERT INTO areas (pref, type, code, baselined) VALUES ('tokyo', 'used_condo', '1', 1), "
         "('chiba', 'used_condo', '1', 1)")
    db.x("INSERT INTO prefs VALUES ('tokyo', 'x'), ('chiba', 'x')")
    db.x("INSERT INTO ended VALUES ('land', '9', 'chiba', '2026-10-01', 5, 'x')")
    db.x("INSERT INTO runs (run_id, started, finished) VALUES ('20261101T050000', 's', 'f'), "
         "('20261101T060000', 's', NULL), ('20261102T050000-s01', 's', 'f')")
    db.x("PRAGMA user_version = 3")
    db.commit()
    db.conn.close()


def _counts(path):
    c = sqlite3.connect(path)
    out = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
           for t in ("listings", "queue", "events", "areas", "prefs", "ended", "runs")}
    out["user_version"] = c.execute("PRAGMA user_version").fetchone()[0]
    c.close()
    return out


def test_split_gives_each_shard_its_prefectures_and_join_puts_them_back(tmp_path):
    _state(tmp_path / "state.db")
    shards.split(tmp_path / "state.db", [["tokyo"], ["chiba"]], tmp_path / "out", log=QUIET)
    a, b = tmp_path / "out/state-0.db", tmp_path / "out/state-1.db"
    assert _counts(a) == dict(listings=2, queue=2, events=2, areas=1, prefs=1, ended=0, runs=3, user_version=3)
    assert _counts(b) == dict(listings=1, queue=1, events=1, areas=1, prefs=1, ended=1, runs=3, user_version=3)
    c = sqlite3.connect(a)
    assert {r[0] for r in c.execute("SELECT DISTINCT pref FROM listings")} == {"tokyo"}
    # a run that never finished: its events were exported whole; a shard must not re-export its part of them
    assert c.execute("SELECT COUNT(*) FROM runs WHERE finished IS NULL").fetchone()[0] == 0
    # an earlier layout's worker run: not this layout's shard 1's, so never re-exported by it
    assert c.execute("SELECT COUNT(*) FROM runs WHERE run_id LIKE '%-s01'").fetchone()[0] == 0
    c.close()

    shards.join([a, b], tmp_path / "joined.db", log=QUIET)
    assert _counts(tmp_path / "joined.db") == _counts(tmp_path / "state.db")


def _args(tmp_path, **kw):
    (tmp_path / "scope.toml").write_text('[[scope]]\npref = "tokyo"\ntypes = ["used_condo"]\n'
                                         '[[scope]]\npref = "chiba"\ntypes = ["used_condo"]\n')
    args = dict(db=str(tmp_path / "s.db"), archive=str(tmp_path / "a"), data=str(tmp_path / "data"),
                scope=str(tmp_path / "scope.toml"), delay=0.0, push=False, commit=False, crawl_at=None,
                no_crawl=False, no_geocode=True, budget=0.0, until_minute=None, shard=1)
    return argparse.Namespace(**{**args, **kw})


def test_a_worker_runs_its_prefectures_with_its_own_run_ids_and_re_exports_its_events(tmp_path, monkeypatch):
    (tmp_path / "shards.toml").write_text('shards = [["tokyo"], ["chiba"]]\n')
    monkeypatch.setattr(cli, "SHARDS", str(tmp_path / "shards.toml"))
    seen = []

    def crawl(self, skip=()):
        seen.append({t.pref for t in self.targets})
        self.db.add_event(self.run_id, "new", {"type": "used_condo", "id": "1", "pref": "chiba",
                                               "area_code": "1"}, {"price": 1})
        self.db.commit()
        return True
    monkeypatch.setattr(Pipeline, "crawl_lists", crawl)
    c = Ctx(_args(tmp_path))
    c.db.migrate()
    cmd_run(c)
    assert seen == [{"chiba"}]
    run_id = c.db.x("SELECT run_id FROM runs").fetchone()[0]
    assert run_id.endswith("-s01")
    assert [p.name for p in (tmp_path / "data/events").iterdir()] == [f"{run_id}.json"]

    # the push of that run's data was lost: the next run, on a fresh data/, writes its events file again
    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(seconds=5)
    monkeypatch.setattr(cli, "datetime", Later)
    monkeypatch.setattr(Pipeline, "crawl_lists", lambda self, skip=(): True)
    c = Ctx(_args(tmp_path, data=str(tmp_path / "data2")))
    cmd_run(c)
    files = [p.name for p in (tmp_path / "data2/events").iterdir()]
    assert files == [f"{run_id}.json"]
    assert json.loads((tmp_path / "data2/events" / files[0]).read_text())[0]["pref"] == "chiba"


def test_shard_out_of_range(tmp_path, monkeypatch):
    (tmp_path / "shards.toml").write_text('shards = [["tokyo"], ["chiba"]]\n')
    monkeypatch.setattr(cli, "SHARDS", str(tmp_path / "shards.toml"))
    with pytest.raises(SystemExit, match="has shards 0-1"):
        Ctx(_args(tmp_path, shard=2))
