""".github/scripts: state.sh's asset names, ci.py's budget, health and summary."""
import gzip
import importlib.util
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / ".github" / "scripts"
STATE_SH = (SCRIPTS / "state.sh").read_text()
spec = importlib.util.spec_from_file_location("ci", SCRIPTS / "ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


def names(n):
    """state.sh's asset names for shard n: base, prev, an upload before its rename."""
    base = re.search(r'^base="(.*)"$', STATE_SH, re.M).group(1).replace("$n", str(n))
    prev = re.search(r'^prev="(.*)"$', STATE_SH, re.M).group(1).replace("$n", str(n))
    return base, prev, f"{base}.123456-1"


def assets_filter(n):
    """assets()'s jq program for shard n, as state.sh runs it."""
    body = re.search(r"^assets\(\) \{\n(.*?)\n\}", STATE_SH, re.M | re.S).group(1)
    prog = re.search(r'--jq "(.*)"', body).group(1).replace('\\"', '"')
    return prog.replace("$base", names(n)[0])


@pytest.mark.skipif(not shutil.which("jq"), reason="needs jq")
def test_get_and_put_never_match_a_prev_copy_or_another_shard():
    shards = [0, 1, 2, 10, 11, 19, 100]
    release = {"assets": [{"name": name} for n in shards for name in names(n)]}
    for n in shards:
        got = json.loads(subprocess.run(["jq", "-c", assets_filter(n)], input=json.dumps(release),
                                        capture_output=True, text=True, check=True).stdout)
        base, prev, upload = names(n)
        assert {a["name"] for a in got} == {base, upload}, n
    assert names(1)[1] == "state-1.prev.db.zst" and names(1)[0] == "state-1.db.zst"


def test_put_frees_the_prev_name_before_each_rename():
    put = STATE_SH[STATE_SH.index("  put)"):]
    steps = [put.index(s) for s in ('DELETE "repos/$GH_REPO/releases/assets/$oldprev"',
                                    '"repos/$GH_REPO/releases/assets/$old" -f name="$prev"',
                                    '"repos/$GH_REPO/releases/assets/$new" -f name="$base"')]
    assert steps == sorted(steps)
    # the leftovers deleted afterwards are from assets(): never the prev copy just made
    assert "select(.name != $u and .name != $b)" in put


JST = ci.JST


def at(hour, minute=7):
    return datetime(2026, 11, 16, hour, minute, tzinfo=JST)


@pytest.mark.parametrize("now, due, given, list_ok, want", [
    (at(5), True, "", True, 600),        # a crawl run
    (at(5, 37), False, "", True, 600),   # the backstop finds the slot crawled
    (at(1, 37), False, "", True, 2700),  # queue runs at 01 and 10 JST
    (at(10, 7), False, "", True, 2700),
    (at(10, 7), True, "", True, 600),    # ...unless the run crawls (a missed crawl slot)
    (at(20), False, "", True, 600),      # any other run
    (at(20), False, "290m", True, 3600), # a dispatch, clamped to 60m
    (at(20), False, "30m", True, 1800),
    (at(20), False, "0", True, 0),
    (at(20), True, "70m", True, 600),
    (at(1), False, "", False, 0),        # the canary found listing pages bad
])
def test_budget(now, due, given, list_ok, want):
    assert ci.budget(now, due, given, list_ok) == want


def test_crawl_slot_and_due(tmp_path):
    assert ci.crawl_slot(at(4)) == datetime(2026, 11, 15, 15, tzinfo=JST)
    assert ci.crawl_slot(at(15, 37)) == at(15, 0)
    import sqlite3
    db = tmp_path / "s.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE prefs (pref TEXT PRIMARY KEY, crawled_at TEXT)")
        c.execute("INSERT INTO prefs VALUES ('tokyo', '2026-11-16T05:30:00+09:00'), ('chiba', '2026-11-15T20:21:00Z')")
    assert not ci.crawl_due(db, ["tokyo"], at(10))
    assert ci.crawl_due(db, ["tokyo"], at(15, 7))
    assert ci.crawl_due(db, ["tokyo", "kagawa"], at(10))   # never crawled
    assert not ci.crawl_due(db, ["chiba"], at(10))           # 05:21 JST, written in UTC


def test_crawl_due_counts_unreadable_times_as_not_crawled():
    assert ci.due_prefs({"tokyo": "2026-11-16T05:30:00"}, ["tokyo"], at(10))   # naive: TypeError
    assert ci.due_prefs({"tokyo": "garbage"}, ["tokyo"], at(10))
    assert not ci.due_prefs({"tokyo": "2026-11-16T05:30:00+09:00"}, ["tokyo"], at(10))


def test_crawl_hours_come_from_crawl_at(monkeypatch):
    monkeypatch.setenv("CRAWL_AT", "4,16")
    other = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(other)
    assert other.CRAWL_HOURS == (4, 16) and ci.CRAWL_HOURS == (5, 15)
    wf = (Path(__file__).parent.parent / ".github" / "workflows" / "crawl.yml").read_text()
    assert "CRAWL_AT: '5,15'" in wf and '--crawl-at "$CRAWL_AT"' in wf


def test_the_queue_budget_follows_the_run_start_not_the_wave(tmp_path):
    (tmp_path / "shards.toml").write_text('shards = [["tokyo"]]\n')
    db = tmp_path / "s.db"
    import sqlite3
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE prefs (pref TEXT PRIMARY KEY, crawled_at TEXT)")
        c.execute("INSERT INTO prefs VALUES ('tokyo', '2026-11-16T05:30:00+09:00')")
    second_wave = at(11, 50)   # a 10:37 run's later shards start after 11:00
    out = ci.params(db, 0, "", "", True, now=second_wave, started=at(10, 37), shards_file=tmp_path / "shards.toml")
    assert out["budget"] == "2700s"
    out = ci.params(db, 0, "", "", True, now=at(11, 50), started=at(11, 37), shards_file=tmp_path / "shards.toml")
    assert out["budget"] == "600s"


SHARDS = [["tokyo"], ["chiba", "kagawa"], ["saitama"], ["aichi"], ["osaka"]]


def asset(n, label):
    return {"name": f"state-{n}.db.zst", "label": label}


def test_plan_runs_only_shards_with_work():
    fresh = "2026-11-16T05:31:00+09:00"
    release = {"assets": [
        asset(0, f"queued=0 crawled=tokyo@{fresh}"),                          # nothing to do
        asset(1, f"queued=0 crawled=chiba@{fresh},kagawa@2026-11-15T15:20:00+09:00"),   # kagawa not since 05:00
        asset(2, f"queued=12 crawled=saitama@{fresh}"),                       # listing pages queued
        asset(3, ""),                                                         # no label (an old state)
        {"name": "state-4.prev.db.zst", "label": f"queued=0 crawled=osaka@{fresh}"},    # only a .prev: no state
        {"name": "state-0.db.zst.99-1", "label": ""},                         # a leftover upload: ignored
    ]}
    assert ci.plan(release, SHARDS, at(10)) == [1, 2, 3, 4]
    assert ci.plan(release, SHARDS, at(15, 7)) == [0, 1, 2, 3, 4]   # the 15:00 crawl is due everywhere
    assert ci.plan(release, SHARDS, at(10), "[0, 3]") == [0, 3]     # a dispatch's choice
    for bad in ("[]", "[7]", '"0"', "[0.5]"):
        with pytest.raises(ValueError):
            ci.plan(release, SHARDS, at(10), bad)


def test_plan_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shards.toml").write_text('shards = [["tokyo"], ["chiba"]]\n')
    (tmp_path / "r.json").write_text(json.dumps({"assets": [asset(0, "queued=3 crawled=")]}))
    assert ci.main(["plan", "r.json"]) == 0
    assert "shards=[0,1]\n" in capsys.readouterr().out
    assert ci.main(["plan", "r.json", "--shards", "[]"]) == 2


def test_state_labels_read_back_from_a_real_state(tmp_path):
    """state.sh put's label queries, on a state the code made, parsed by ci.plan's reader."""
    from suumo.db import DB
    from suumo.pipeline import MAX_DETAIL_ATTEMPTS
    assert re.search(r"^MAX_ATTEMPTS=(\d+)", STATE_SH, re.M).group(1) == str(MAX_DETAIL_ATTEMPTS)
    db = DB(tmp_path / "s.db")
    db.x("INSERT INTO prefs (pref, crawled_at) VALUES ('tokyo', '2026-11-16T05:31:00+09:00')")
    for i, attempts in enumerate((0, 1, MAX_DETAIL_ATTEMPTS)):
        db.x("INSERT INTO queue (type, id, priority, reason, enqueued_at, attempts) VALUES ('land', ?, 2, '', '', ?)",
             str(i), attempts)
    db.commit()
    sql = dict(re.findall(r'^\s+(queued|crawled)=\$\(sqlite3 "\$db" "(.*)"\)$', STATE_SH, re.M))
    sql = {k: v.replace("$MAX_ATTEMPTS", str(MAX_DETAIL_ATTEMPTS)) for k, v in sql.items()}
    import sqlite3
    with sqlite3.connect(tmp_path / "s.db") as c:
        label = f"queued={c.execute(sql['queued']).fetchone()[0]} crawled={c.execute(sql['crawled']).fetchone()[0]}"
    assert ci.parse_label(label) == (2, {"tokyo": "2026-11-16T05:31:00+09:00"})
    assert re.search(r'-f label="\$label"', STATE_SH)


@pytest.mark.parametrize("n", ["x", "1;rm", "", "-1"])
def test_state_sh_rejects_a_shard_that_isnt_a_number(n):
    r = subprocess.run(["bash", str(SCRIPTS / "state.sh"), "get", "tag", n, "/tmp/x.db"],
                       capture_output=True, text=True)
    assert r.returncode == 2 and "not a number" in r.stdout


def test_params_clamp_the_delay_and_read_the_shard(tmp_path):
    (tmp_path / "shards.toml").write_text('shards = [["tokyo"]]\n')
    out = ci.params(tmp_path / "missing.db", 0, "", "0.3", True, now=at(10), shards_file=tmp_path / "shards.toml")
    assert out == {"budget": "600s", "delay": "1"}   # no state readable: assumed to crawl
    out = ci.params(tmp_path / "missing.db", 0, "", "2.5", True, now=at(10), shards_file=tmp_path / "shards.toml")
    assert out["delay"] == "2.5"


def report(**kw):
    r = {"coverage": {"kr": {"pages": 100, "ll": 100, "img": 95, "spec": 99, "agent": 100, "features": 80},
                      "ms": {"pages": 6, "ll": 6, "img": 6, "spec": 6}},
         "extract_errors": 0, "derive_errors": 0}
    r.update(kw)
    return r


def raw(r):
    """A normalized report back in the shape pipeline.py writes (what the files hold)."""
    tpls = {t: {"n": c.get("pages", 0), "ll": c.get("ll", 0), "img": c.get("img", 0),
                f"spec{ci.SPEC_ROWS.get(t, 10)}": c.get("spec", 0), "agent": c.get("agent", 0),
                "features": c.get("features", 0)} for t, c in r.get("coverage", {}).items()}
    u = r.get("unknown", {})
    flags = ["extract_streak"] * bool(r.get("extract_streak")) + ["derive_errors"] * bool(r.get("derive_errors"))
    return {"flags": flags,
            "pages": {"templates": tpls, "extract_errors": r.get("extract_errors", 0),
                      "derive_errors": r.get("derive_errors", 0), "unknown_labels": u.get("labels", {}),
                      "unknown_sections": u.get("sections", {})}}


def test_normalize_reads_the_pipeline_report():
    r = report(extract_streak=True, unknown={"labels": {"新ラベル": 2}, "sections": {}})
    n = ci.normalize(raw(r))
    assert n["coverage"]["kr"] == r["coverage"]["kr"] and n["extract_streak"]
    assert n["unknown"]["labels"] == {"新ラベル": 2} and ci.normalize({})["coverage"] == {}


def test_health_on_reports_the_pipeline_built():
    """Coverage counted by pipeline.Pipeline._count on real records, read through normalize."""
    from types import SimpleNamespace

    from suumo.extract import extract
    from suumo.pipeline import Pipeline, _page_stats
    from tests.helpers import FIXTURES

    def page(name):
        return gzip.decompress((FIXTURES / f"{name}.html.gz").read_bytes()).decode("utf-8")

    records = [extract(page(f"detail_{t}_{i}"), t, i) for t, i in
               (("used_condo", "20205670"), ("used_house", "20779514"), ("land", "20862637"))]

    def built(recs):
        p = SimpleNamespace(report={"pages": _page_stats(), "flags": []})
        for r in recs:
            Pipeline._count(p, r)
        return ci.normalize(p.report)

    good = built(records * 7)   # 21 kr pages: judged
    assert good["coverage"]["kr"]["pages"] == 21 and ci.problems(good) == []
    no_ll = [{k: v for k, v in r.items() if k != "ll"} for r in records]
    bad = built(records * 6 + no_ll)   # 3 of 21 without coordinates
    assert ci.problems(bad) == ["kr: ll on 18 of 21 pages (< 95%)"]
    assert ci.problems(built(no_ll)) == []   # 3 pages: too few to judge


def test_health():
    assert ci.problems(report()) == []
    assert ci.problems(report(extract_streak=True))[0].startswith("the listing-page queue stopped")
    assert ci.problems(report(derive_errors=2)) == ["2 derive errors"]
    low = report(coverage={"kr": {"pages": 100, "ll": 94, "img": 89, "spec": 95}, "ms": {"pages": 5, "img": 4}})
    assert ci.problems(low) == ["kr: ll on 94 of 100 pages (< 95%)", "kr: img on 89 of 100 pages (< 90%)",
                                "ms: img on 4 of 5 pages (< 90%)", "ms: spec on 0 of 5 pages (< 95%)"]
    few = report(coverage={"kr": {"pages": 19}, "ms": {"pages": 4}})   # too few pages to judge
    assert ci.problems(few) == []
    assert ci.problems({}) == []   # a run without listing pages


def test_health_command(tmp_path, capsys):
    f = tmp_path / "report.json"
    f.write_text(json.dumps(raw(report(derive_errors=1))))
    assert ci.main(["health", str(f)]) == 1
    out = capsys.readouterr().out
    assert "kr: 100 pages (ll 100%, img 95%, spec 99%, agent 100%, features 80%)" in out
    assert "::error::1 derive errors" in out


def test_summary_sums_the_shards(tmp_path):
    reports = [report(unknown={"labels": {"新ラベル": 2}, "sections": {"新章": 1}}),
               report(unknown={"labels": {"新ラベル": 1, "別": 5}}, extract_errors=3, extract_streak=True)]
    for n, r in enumerate(reports):
        (tmp_path / f"report-{n}").mkdir()
        (tmp_path / f"report-{n}" / "report.json").write_text(json.dumps(raw(r), ensure_ascii=False))
    (tmp_path / "report-2").mkdir()
    (tmp_path / "report-2" / "report.json").write_text("")   # a run that left no report
    s = ci.summary(tmp_path)
    assert "(2 shard reports)" in s and "kr: 200 pages" in s and "ms: 12 pages" in s
    assert "extract errors 3, derive errors 0; queue stopped by an extract streak: shards 1" in s
    assert "     5 別\n     3 新ラベル" in s and "     1 新章" in s


def test_workflow_steps_fail_on_a_failed_pipe():
    for wf in ("crawl.yml", "watch.yml"):
        text = (Path(__file__).parent.parent / ".github" / "workflows" / wf).read_text()
        assert re.search(r"^defaults:\n  run:\n    shell: bash\b", text, re.M), wf


def areas_report(*targets):
    return {"targets": list(targets)}


def target(pref, type_, *statuses, start=0, error=None):
    if error:
        return {"pref": pref, "type": type_, "error": error}
    areas = [{"code": str(start + i), "status": s} for i, s in enumerate(statuses)]
    return {"pref": pref, "type": type_, "areas": areas}


def test_health_counts_only_the_areas_this_run_crawled():
    ok = ["complete"] * 19
    assert ci.problems(ci.normalize(areas_report(target("tokyo", "land", *ok, "error")))) == []   # 1 of 20: 5%
    two = ci.normalize(areas_report(target("tokyo", "land", *ok, "error", "incomplete")))
    assert ci.problems(two) == ["2 of 21 areas crawled this run not complete"]
    # a retry (only the failed areas, after the first try) has the last word
    retried = areas_report(target("tokyo", "land", *ok, "error", "incomplete"),
                           target("tokyo", "land", "complete", "complete", start=19))
    assert ci.problems(ci.normalize(retried)) == []
    # an area page that failed twice; once is fine when its retry worked
    failed = areas_report(target("tokyo", "land", error="E1"), target("tokyo", "land", error="E2"))
    assert ci.problems(ci.normalize(failed)) == ["tokyo/land: the area page failed (E2)"]
    healed = areas_report(target("tokyo", "land", error="E1"), target("tokyo", "land", *ok))
    assert ci.problems(ci.normalize(healed)) == []
    assert ci.problems(ci.normalize({})) == []   # a queue-only run crawls no areas
    # suspect / not_listed / no_slug areas are reported, not unhealthy
    assert ci.problems(ci.normalize(areas_report(target("tokyo", "land", "suspect", "not_listed", "no_slug")))) == []


@pytest.mark.parametrize("code, report, want", [
    ("0", {"export": {}}, True),
    ("3", {"export": {}, "offline": True}, True),   # an outage, after the export
    ("1", {"export": {}}, False),                   # a crash (even after the export: geocoding, the summary)
    ("0", {}, False),                               # no report of this run
    ("124", {"export": {}}, False), ("130", {"interrupted": True}, False), ("", {}, False),
])
def test_exported(code, report, want):
    assert ci.exported(code, report) is want


def test_exported_command(tmp_path, capsys):
    (tmp_path / "r.json").write_text(json.dumps({"export": {"files": 3}}))
    ci.main(["exported", "3", str(tmp_path / "r.json")])
    ci.main(["exported", "0", str(tmp_path / "missing.json")])
    assert capsys.readouterr().out == "exported=true\nexported=false\n"


def test_canary_only_before_a_crawl_on_a_dispatch_or_once_an_hour():
    assert ci.canary_needed([1], True, at(15, 37), "schedule")
    assert ci.canary_needed([1], False, at(11, 7), "schedule")        # the first half hour
    assert not ci.canary_needed([1], False, at(11, 37), "schedule")
    assert ci.canary_needed([1], False, at(11, 37), "workflow_dispatch")
    assert not ci.canary_needed([], True, at(5, 7), "schedule")       # nothing to run
    fresh = "2026-11-16T05:31:00+09:00"
    release = {"assets": [asset(0, f"queued=4 crawled=tokyo@{fresh}"), asset(1, "")]}
    assert not ci.crawl_due_in(release, SHARDS, [0], at(10))
    assert ci.crawl_due_in(release, SHARDS, [0], at(15, 7)) and ci.crawl_due_in(release, SHARDS, [1], at(10))


def run(i, hours_ago, conclusion="success"):
    created = (at(12) - timedelta(hours=hours_ago)).astimezone(ci.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"databaseId": i, "createdAt": created, "conclusion": conclusion, "url": f"u/{i}"}


def jobs(skipped):
    shard = "skipped" if skipped else "success"
    return [{"name": "plan", "conclusion": "success"}, {"name": "shard (0)", "conclusion": shard},
            {"name": "combine", "conclusion": shard}]


def test_watch_sees_failures_behind_no_op_runs():
    runs = [run(5, 0.2), run(4, 0.7), run(3, 3, "failure"), run(2, 11, "cancelled"), run(1, 13, "timed_out")]
    j = {"5": jobs(True), "4": jobs(True), "3": jobs(False), "2": jobs(False), "1": jobs(False)}
    w = ci.watch(runs, j, at(12))
    assert w["failed"] == ["u/3"]                    # within 12 h; cancelled ignored; 13 h ago is out of the window
    assert w["run_age"] == 720 and w["crawl_age"] == 3 * 3600   # no-op plans don't count as the crawl being alive
    j["3"] = [{"name": "plan", "conclusion": "failure"}, {"name": "shard", "conclusion": "skipped"}]
    w = ci.watch(runs, j, at(12))
    assert w["failed"] == ["u/3"] and w["crawl_age"] == 11 * 3600   # a failed canary still counts as a failure
    assert ci.watch([], {}, at(12)) == {"failed": [], "run_age": 999999999, "crawl_age": 999999999}


def test_prune_keeps_the_newest_and_the_pointed_snapshot():
    snaps = [{"id": i, "name": f"data-2026112{i}T0507-1-1.tar.zst", "created_at": f"2026-11-2{i}T00:00:00Z"}
             for i in range(6)]
    release = {"assets": [*snaps, {"id": 99, "name": "state-1.db.zst", "created_at": "2026-01-01T00:00:00Z"}]}
    assert ci.prune(release, 3) == [2, 1, 0]                                      # never anything else
    assert ci.prune(release, 3, pointed=snaps[1]["name"]) == [2, 0]               # the pointer's stays, however old
    assert ci.prune(release, 10) == []


def test_data_sh_rejects_an_unknown_command():
    r = subprocess.run(["bash", str(SCRIPTS / "data.sh"), "nope"], capture_output=True, text=True)
    assert r.returncode == 2 and "usage" in r.stdout
