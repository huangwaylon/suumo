"""crawl.yml's decisions, testable (tests/test_ci.py). Standard library only (runs before `uv sync` would matter).

  ci.py plan RELEASE_JSON [--shards JSON]
      shards=[...] for GITHUB_OUTPUT: the shards with work (crawl due, or a queue), from the state assets' labels
      (state.sh put writes them); --shards (a dispatch's input) overrides.
  ci.py params --db DB --shard N [--started ISO] [--budget B] [--delay D] [--list-ok true|false]
      budget=... and delay=... lines for GITHUB_OUTPUT: the run's --budget (time for listing pages) and --delay.
  ci.py health REPORT      a shard's run report (runs.report): areas, coverage and error flags; exit 1 when unhealthy
  ci.py exported CODE REPORT   exported=true|false: whether the run's data/ may be handed on (exit 0 or 3, and the
                           report records its export)
  ci.py watch RUNS JOBS    the watchdog's view of crawl.yml's runs (gh run list JSON; {run id: [jobs]})
  ci.py summary DIR        Markdown: every report-*/report.json under DIR summed (the combine job's step summary)

Report keys read (pipeline.py writes them into the run's report; normalize() turns them into the shape below):
  flags           a list: "extract_streak" (EXTRACT_STREAK consecutive extract errors stopped the queue: a layout
                  change), "derive_errors"
  pages           {"templates": {tpl: {"n", "ll", "img", "spec5", "spec10", "agent", "features"}}, "extract_errors",
                  "derive_errors", "unknown_labels": {label: count}, "unknown_sections": {title: count}}
Normalized: extract_streak, extract_errors, derive_errors, coverage {tpl: {"pages", "ll", "img", "spec", "agent",
"features"}} ("spec": pages with at least SPEC_ROWS[tpl] rows), unknown {"labels", "sections"}.
"""
import json
import os
import re
import sqlite3
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
CRAWL_HOURS = tuple(int(h) for h in os.environ.get("CRAWL_AT", "5,15").split(","))  # crawl.yml's --crawl-at
QUEUE_HOURS = (1, 10)     # JST hours (of the run's start) whose runs get the long queue budget (not the evening)
# 10m, 45m, 60m (a dispatch's input is clamped to MAX_BUDGET: the run's 75 min leave room for purge and export)
SHORT, LONG, MAX_BUDGET = 600, 2700, 3600
MIN_DELAY = 1.0           # a dispatch can't make a worker faster than this
BAD_AREA = {"error", "incomplete"}   # area statuses that make a crawl not whole
SPEC_ROWS = {"kr": 10, "ms": 5}   # (pipeline.py counts pages with at least this many spec rows as "spec")
# template: (minimum pages before judging, {coverage key: minimum share})
THRESHOLDS = {"kr": (20, {"ll": 0.95, "img": 0.90, "spec": 0.95}),
              "ms": (5, {"img": 0.90, "spec": 0.95})}


def seconds(s):
    """'90m', '2h', '600s', '600' -> seconds; ValueError otherwise."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smh]?)", s.strip())
    if not m:
        raise ValueError(f"not a duration: {s!r} (e.g. 45m)")
    return float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def crawl_slot(now, hours=CRAWL_HOURS):
    """The latest crawl hour that has come (the same rule as cli.crawl_slot)."""
    today = [h for h in sorted(hours) if h <= now.hour]
    since = now.replace(hour=today[-1] if today else max(hours), minute=0, second=0, microsecond=0)
    return since if today else since - timedelta(days=1)


def due_prefs(crawled, prefs, now):
    """Whether one of prefs wasn't crawled since the latest crawl hour; crawled: {pref: ISO time}. A missing or
    unreadable time (naive, malformed) counts as not crawled."""
    since = crawl_slot(now)
    for p in prefs:
        try:
            if datetime.fromisoformat(crawled[p]) < since:
                return True
        except (KeyError, TypeError, ValueError):
            return True
    return False


def crawl_due(db, prefs, now):
    """Whether the run will crawl search results (cli.crawl_due on the state file)."""
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        crawled = dict(conn.execute("SELECT pref, crawled_at FROM prefs").fetchall())
    return due_prefs(crawled, prefs, now)


def shards_of(path="shards.toml"):
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))["shards"]


def parse_label(label):
    """state.sh put's asset label 'queued=N crawled=pref@ISO,pref@ISO' -> (queued, {pref: ISO}); None when absent
    or not in that form."""
    kv = dict(part.partition("=")[::2] for part in (label or "").split())
    if not kv.get("queued", "").isdigit() or "crawled" not in kv:
        return None
    crawled = dict(item.partition("@")[::2] for item in kv["crawled"].split(",") if "@" in item)
    return int(kv["queued"]), crawled


def plan(release, shards, now, override=""):
    """The shards to run: override (a dispatch's JSON list) as given; else those whose state asset says a crawl is due
    or listing pages are queued. An asset without a readable label (an old state, a save that stopped before its
    rename) counts as work."""
    if override.strip():
        chosen = json.loads(override)
        if not isinstance(chosen, list) or not chosen:
            raise ValueError(f"shards: a non-empty JSON list, not {override!r}")
        if not all(isinstance(n, int) and 0 <= n < len(shards) for n in chosen):
            raise ValueError(f"shards: numbers 0-{len(shards) - 1}, not {override!r}")
        return chosen
    labels = {a["name"]: a.get("label") for a in release.get("assets", [])}
    out = []
    for n, prefs in enumerate(shards):
        got = parse_label(labels.get(f"state-{n}.db.zst"))
        if got is None or got[0] > 0 or due_prefs(got[1], prefs, now):
            out.append(n)
    return out


def crawl_due_in(release, shards, chosen, now):
    """Whether one of the chosen shards will crawl search results (no readable label: assumed to)."""
    labels = {a["name"]: a.get("label") for a in release.get("assets", [])}
    for n in chosen:
        got = parse_label(labels.get(f"state-{n}.db.zst"))
        if got is None or due_prefs(got[1], shards[n], now):
            return True
    return False


def canary_needed(chosen, crawl_due, started, event):
    """The canary (~20 requests) runs before a crawl, on a dispatch, and otherwise at most once an hour (runs started
    in the first half of an hour): queue-only runs every 30 min would add up, and their shards' extract flags catch
    listing-page breakage anyway."""
    return bool(chosen) and (crawl_due or event == "workflow_dispatch" or started.minute < 30)


def budget(now, due, given=None, list_ok=True):
    """Seconds for listing pages. Listing pages bad (the canary): none. A run that crawls: SHORT (the crawl takes up to
    ~35 min of the job). A dispatch: its input, at most MAX_BUDGET. Else LONG when the run started (now) at
    QUEUE_HOURS, SHORT otherwise."""
    if not list_ok:
        return 0
    b = min(seconds(given), MAX_BUDGET) if given else LONG if now.hour in QUEUE_HOURS else SHORT
    return min(b, SHORT) if due else b


def params(db, shard, given_budget, given_delay, list_ok, now=None, started=None, shards_file="shards.toml"):
    """now: the shard's start (whether its run crawls); started: the run's start (the canary job's), the same for
    every wave of shards, which picks the queue budget."""
    now = now or datetime.now(JST)
    started = started.astimezone(JST) if started else now
    prefs = shards_of(shards_file)[shard]
    try:
        due = crawl_due(db, prefs, now)
    except sqlite3.Error as e:
        print(f"::warning::couldn't tell whether shard {shard} crawls ({e}); assuming it does", file=sys.stderr)
        due = True
    b = budget(started, due, given_budget, list_ok)
    delay = max(MIN_DELAY, float(given_delay or MIN_DELAY))
    print(f"shard {shard}: crawl {'due' if due else 'done'}, budget {b:.0f}s, delay {delay}s", file=sys.stderr)
    return {"budget": f"{b:.0f}s", "delay": f"{delay:g}"}


def area_statuses(targets):
    """The run's search-result report (targets[], in order; a retry comes after the first try) -> (each area's last
    status {(pref, type, code): status}, the targets whose area page still failed {pref/type: error})."""
    status, failed = {}, {}
    for t in targets or []:
        key = f"{t.get('pref')}/{t.get('type')}"
        if "error" in t:
            failed[key] = t["error"]
            continue
        failed.pop(key, None)
        for a in t.get("areas", []):
            status[(t.get("pref"), t.get("type"), a.get("code"))] = a.get("status")
    return status, failed


def normalize(raw):
    """The pipeline's run report -> the shape the checks below read."""
    pages = raw.get("pages") or {}
    status, failed = area_statuses(raw.get("targets"))
    cov = {}
    for tpl, c in (pages.get("templates") or {}).items():
        rows = SPEC_ROWS.get(tpl, 10)
        cov[tpl] = {"pages": c.get("n", 0), "ll": c.get("ll", 0), "img": c.get("img", 0),
                    "spec": c.get(f"spec{rows}", 0), "agent": c.get("agent", 0), "features": c.get("features", 0)}
    flags = raw.get("flags") or []
    return {"extract_streak": "extract_streak" in flags, "extract_flag": "extract_errors" in flags,
            "extract_errors": pages.get("extract_errors", 0),
            "derive_errors": pages.get("derive_errors", 0) or ("derive_errors" in flags),
            "coverage": cov,
            "areas": {"all": len(status), "bad": sum(s in BAD_AREA for s in status.values()), "failed": failed},
            "unknown": {"labels": pages.get("unknown_labels") or {}, "sections": pages.get("unknown_sections") or {}}}


def problems(report):
    """Why a shard's run is unhealthy (empty: healthy), from its report."""
    out = []
    areas = report.get("areas") or {}
    for key, err in sorted((areas.get("failed") or {}).items()):
        out.append(f"{key}: the area page failed ({err})")
    if areas.get("bad", 0) * 20 > areas.get("all", 0):   # more than 5% of the areas this run crawled
        out.append(f"{areas['bad']} of {areas['all']} areas crawled this run not complete")
    if report.get("extract_streak"):
        out.append("the listing-page queue stopped: consecutive extract errors (a layout change?)")
    elif report.get("extract_flag"):
        out.append(f"{report.get('extract_errors', 0)} extract errors (pages that no longer extract)")
    if report.get("derive_errors"):
        out.append(f"{report['derive_errors']} derive errors")
    for tpl, (least, mins) in THRESHOLDS.items():
        c = report.get("coverage", {}).get(tpl, {})
        n = c.get("pages", 0)
        if n < least:
            continue
        for key, share in mins.items():
            if c.get(key, 0) < share * n:
                out.append(f"{tpl}: {key} on {c.get(key, 0)} of {n} pages (< {share:.0%})")
    return out


def coverage_lines(cov):
    lines = []
    for tpl in sorted(cov):
        c = cov[tpl]
        n = c.get("pages", 0)
        cells = ", ".join(f"{k} {c.get(k, 0) / n:.0%}" for k in ("ll", "img", "spec", "agent", "features") if n)
        lines.append(f"{tpl}: {n} pages" + (f" ({cells})" if cells else ""))
    return lines


def exported(code, report):
    """Whether a run's data/ may be handed to the combine job: it exited 0 (or OFFLINE_EXIT 3: an outage, after the
    export), and its report records the export. A crash (1), a timeout (124/130) or no report: no."""
    return str(code) in ("0", "3") and "export" in (report or {})


FAILED = {"failure", "timed_out", "startup_failure"}


def watch(runs, jobs, now, window=43200):
    """crawl.yml's runs on main (newest first: databaseId, createdAt, conclusion, url) and their jobs ({id: [{name,
    conclusion}]}) -> {"failed": [urls of runs created within window that failed], "run_age": seconds since the newest
    run, "crawl_age": since the newest run whose shards ran (not a plan with nothing to do)}."""
    def age(r):
        return (now - datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00"))).total_seconds()

    def shards_ran(r):
        shard = [j for j in jobs.get(str(r["databaseId"]), []) if j.get("name", "").startswith("shard")]
        return any(j.get("conclusion") != "skipped" for j in shard)

    never = 999999999
    return {"failed": [r["url"] for r in runs if age(r) < window and r.get("conclusion") in FAILED],
            "run_age": int(min((age(r) for r in runs), default=never)),
            "crawl_age": int(min((age(r) for r in runs if shards_ran(r)), default=never))}


def health(path):
    report = normalize(json.loads(Path(path).read_text(encoding="utf-8") or "{}"))
    areas = report["areas"]
    if areas["all"] or areas["failed"]:
        print(f"areas crawled: {areas['all']}, not complete: {areas['bad']}, area pages failed: {len(areas['failed'])}")
    for line in coverage_lines(report.get("coverage", {})):
        print(line)
    print(f"extract errors {report.get('extract_errors', 0)}, derive errors {report.get('derive_errors', 0)}")
    bad = problems(report)
    for p in bad:
        print(f"::error::{p}")
    return 1 if bad else 0


def summary(root, top=30):
    cov, labels, sections = {}, {}, {}
    errors = {"extract_errors": 0, "derive_errors": 0}
    streaks, n = [], 0
    for f in sorted(Path(root).glob("report-*/report.json")):
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        r, n = normalize(json.loads(text)), n + 1
        for tpl, c in r.get("coverage", {}).items():
            for k, v in c.items():
                cov.setdefault(tpl, {}).setdefault(k, 0)
                cov[tpl][k] += v
        for kind, into in (("labels", labels), ("sections", sections)):
            for k, v in r.get("unknown", {}).get(kind, {}).items():
                into[k] = into.get(k, 0) + v
        for k in errors:
            errors[k] += r.get(k, 0)
        if r.get("extract_streak"):
            streaks.append(f.parent.name.removeprefix("report-"))
    out = [f"### listing pages ({n} shard reports)", "```", *coverage_lines(cov),
           f"extract errors {errors['extract_errors']}, derive errors {errors['derive_errors']}"
           + (f"; queue stopped by an extract streak: shards {', '.join(streaks)}" if streaks else ""), "```"]
    for title, d in (("unknown labels", labels), ("unknown sections", sections)):
        if d:
            out += [f"**{title}** ({len(d)})", "```",
                    *(f"{v:6} {k}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:top]), "```"]
    return "\n".join(out) + "\n"


def main(argv):
    cmd, rest = argv[0], argv[1:]
    if cmd == "params":
        import argparse
        ap = argparse.ArgumentParser(prog="ci.py params")
        ap.add_argument("--db", required=True)
        ap.add_argument("--shard", type=int, required=True)
        ap.add_argument("--budget", default="")
        ap.add_argument("--delay", default="")
        ap.add_argument("--list-ok", default="true")
        ap.add_argument("--started", default="", help="the run's start (ISO); default now")
        a = ap.parse_args(rest)
        started = datetime.fromisoformat(a.started) if a.started else None
        for k, v in params(a.db, a.shard, a.budget, a.delay, a.list_ok == "true", started=started).items():
            print(f"{k}={v}")
        return 0
    if cmd == "plan":
        import argparse
        ap = argparse.ArgumentParser(prog="ci.py plan")
        ap.add_argument("release", help="the state release's JSON (gh api repos/.../releases/tags/TAG)")
        ap.add_argument("--shards", default="")
        ap.add_argument("--started", default="", help="the run's start (ISO); default now")
        ap.add_argument("--event", default="schedule", help="github.event_name")
        a = ap.parse_args(rest)
        release = json.loads(Path(a.release).read_text(encoding="utf-8"))
        try:
            chosen = plan(release, shards_of(), datetime.now(JST), a.shards)
        except ValueError as e:
            print(f"::error::{e}", file=sys.stderr)
            return 2
        due = crawl_due_in(release, shards_of(), chosen, datetime.now(JST))
        started = datetime.fromisoformat(a.started) if a.started else datetime.now(JST)
        canary = canary_needed(chosen, due, started, a.event)
        print(f"shards={json.dumps(chosen, separators=(',', ':'))}")
        print(f"crawl_due={'true' if due else 'false'}")
        print(f"canary={'true' if canary else 'false'}")
        print(f"shards with work: {chosen or 'none'}; a crawl due: {due}; canary: {canary}", file=sys.stderr)
        return 0
    if cmd == "exported":
        text = Path(rest[1]).read_text(encoding="utf-8").strip() if Path(rest[1]).exists() else ""
        print(f"exported={'true' if exported(rest[0], json.loads(text) if text else {}) else 'false'}")
        return 0
    if cmd == "watch":
        runs = json.loads(Path(rest[0]).read_text(encoding="utf-8"))
        jobs = json.loads(Path(rest[1]).read_text(encoding="utf-8"))
        w = watch(runs, jobs, datetime.now(JST))
        print(f"run_age={w['run_age']}\ncrawl_age={w['crawl_age']}\nfailed={' '.join(w['failed'])}")
        return 0
    if cmd == "health":
        return health(rest[0])
    if cmd == "summary":
        sys.stdout.write(summary(rest[0]))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
