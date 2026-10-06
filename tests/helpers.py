"""Shared test helpers: synthetic listing records, a data/ folder writer, and running site/filter.js in Node."""
import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

from suumo.site import build

TODAY = date(2026, 10, 20)
QUIET = lambda *_, **__: None  # noqa: E731  (a log function that prints nothing)
FIXTURES = Path(__file__).parent / "fixtures"
FILTER_JS = Path(__file__).parent.parent / "site" / "filter.js"
NODE = shutil.which("node")


def rec(i, type_="used_condo", **kw):
    """An exported listing record; keyword None removes a field."""
    r = {"id": str(i), "type": type_, "area_code": "13219", "area": "狛江市", "town": "東和泉",
         "price": 50_000_000, "layout": "3LDK", "floor_m2": 70.0, "built": "2005-04",
         "stations": [{"line": "小田急線", "name": "狛江", "walk": 5}],
         "url": f"https://suumo.jp/x/nc_{i}/", "first_seen": "2026-10-01"}
    r.update(kw)
    return {k: v for k, v in r.items() if v is not None}


def write(data, recs, areas=None, events=None, pref="tokyo"):
    """Write records as data/<pref>/<type>/<area>.jsonl (ended ones, with removed_at, as removed/<type>.jsonl),
    plus areas.json and data/events/<run_id>.json."""
    d = Path(data) / pref
    files = {}
    for r in recs:
        ended = r.get("removed_at")
        path = d / "removed" / f"{r['type']}.jsonl" if ended else d / r["type"] / f"{r['area_code']}.jsonl"
        files.setdefault(path, []).append(r)
    for path, rs in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rs), encoding="utf-8")
    d.mkdir(parents=True, exist_ok=True)
    (d / "areas.json").write_text(json.dumps(areas or {"13219": "狛江市"}, ensure_ascii=False), encoding="utf-8")
    for run_id, evs in (events or {}).items():
        (Path(data) / "events").mkdir(exist_ok=True)
        (Path(data) / "events" / f"{run_id}.json").write_text(json.dumps(evs, ensure_ascii=False), encoding="utf-8")


RUNNER = """
const Filter = require(process.argv[1]);
const fs = require("fs");
const db = Filter.load(JSON.parse(fs.readFileSync(process.argv[2], "utf8")));
const out = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).map(({query, choices, station, saved}) => {
  if (saved) return [...db.byKey.values()].filter((it) => it.flags & db.flags.saved).map((it) => it.id).sort();
  if (station) return db.index.stations.map((s) => [s, Filter.stationMatch(db, s, station)])
    .filter(([, m]) => m < 3).sort((a, b) => a[1] - b[1]).map(([s]) => s);
  const q = Object.assign(Filter.emptyQuery(), query);
  if (choices) return Filter.facets(db, q, choices);
  const hits = Filter.search(db, q);
  return {ids: hits.map((it) => it.id), others: hits.map((it) => it.others), count: Filter.count(db, q)};
});
process.stdout.write(JSON.stringify(out));
"""


def site_queries(tmp_path, recs, requests, areas=None, events=None, today=TODAY, stations=None, saved=()):
    """Build the site from recs and run requests through site/filter.js in Node: {query} -> {ids, others, count},
    {query, choices} -> the choice counts (Filter.facets), {station: typed} -> the matching stations, best first.
    stations: the station-name cache ({name: [kana, english]})."""
    data, out = tmp_path / "data", tmp_path / "site"
    write(data, recs, areas, events)
    names = tmp_path / "stations.json"
    names.write_text(json.dumps(stations or {}, ensure_ascii=False), encoding="utf-8")
    build(data, tmp_path / "no-geo.json", out, today=today, log=QUIET, stations_cache=names, saved=set(saved))
    req = tmp_path / "requests.json"
    req.write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
    res = subprocess.run([NODE, "-e", RUNNER, str(FILTER_JS), str(out / "data/index.json"), str(req)],
                         capture_output=True, text=True, check=True)
    return json.loads(res.stdout)
