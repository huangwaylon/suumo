"""The canary: a few requests (about 20) before a crawl, to tell a SUUMO layout change from a bad run.

Per type: the area page (parse_areas), one search page of the first area with hits (hits > 0, every record has an
id and a path, and a price on PRICE_SHARE of them: new_condo only needs one, 価格未定 is common there), and listing
pages (the newest fetched listings of that type in data/<pref>; 404s skipped) through extract + derive with the
record keys their template must have: a type is bad only when 2 of up to 3 pages fail (one odd page isn't a layout
change). Plus new_house listings under /tochi/ (land sold with a build condition).

The verdict: search_ok and list_ok (parse failures), and reachable. SUUMO down or our network down (http.Unavailable,
NetworkError) stops the canary at once with reachable=false and the flags it had so far: the run's pipeline handles
outages, so only parse failures gate the shards. Written GITHUB_OUTPUT-style; the workflow decides (crawl.yml).
Pure apart from the client.
"""
import json
from pathlib import Path

from .detail import derive
from .extract import extract
from .http import NetworkError, Unavailable
from .parse import TYPES, parse_areas, parse_list_page

PREF = "tokyo"
DELAY = 1.0          # seconds between requests (+ jitter), the floor for any crawler
RETRIES = 2          # attempts per request
JUDGED = 3           # listing pages judged per type, at most; bad when FAILS of them fail
FAILS = 2
MAX_FETCHES = 6      # listing pages requested per type, at most (ended listings are 404s)
PRICE_SHARE = {"new_condo": 0.0}   # share of a search page's records with a price (> 0: at least one)
DEFAULT_PRICE_SHARE = 0.9          # (land with a build condition may have none)
REQUIRED = {"kr": ("spec", "ll", "img", "agent"), "ms": ("spec", "ll", "img"), "mg": ("spec",)}
BASE = "https://suumo.jp"


class Outage(Exception):
    """SUUMO or the network is down: the canary stops (not a parse failure)."""


def candidates(data, type_, pref=PREF, tochi=False):
    """Fetched listings of a type in data/<pref>/<type>/, newest first: [(id, path)]. For new_house, tochi picks
    those whose page is under /tochi/ (land sold with a build condition), else those that aren't."""
    rows = []
    for f in sorted((Path(data) / pref / type_).glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            url = r.get("url") or ""
            if r.get("has_detail") and url.startswith(BASE) and (type_ != "new_house" or ("/tochi/" in url) == tochi):
                rows.append((r.get("page_date") or r.get("first_seen") or "", r["id"], url[len(BASE):]))
    rows.sort(reverse=True)
    return [(i, p) for _, i, p in rows]


def _get(client, path):
    try:
        return client.get(path)
    except (Unavailable, NetworkError) as e:
        raise Outage(f"{type(e).__name__}: {e}") from e


def check_search(client, type_, pref=PREF):
    """The area page and one search page: (ok, message)."""
    path = TYPES[type_]
    areas = parse_areas(_get(client, f"/{path}/{pref}/city/"), path, pref)
    hit = next((a for a in areas if a["expected"] and a["slug"]), None)
    if not hit:
        return False, f"area page: {len(areas)} areas, none with hits and a link"
    hits, recs = parse_list_page(_get(client, f"/{path}/{pref}/{hit['slug']}/?pc=100"), type_, hit)
    where = f"{len(areas)} areas; {hit['slug']}"
    if not hits or not recs:
        return False, f"{where}: {hits} hits, {len(recs)} records"
    bad = [r.get("id", "?") for r in recs if not (r.get("id") and r.get("path"))]
    if bad:
        return False, f"{where}: records without id/path: {bad[:5]}"
    priced = sum("price" in r for r in recs)
    if not priced or priced < PRICE_SHARE.get(type_, DEFAULT_PRICE_SHARE) * len(recs):
        return False, f"{where}: only {priced} of {len(recs)} records have a price"
    return True, f"{where}: {hits} hits, {len(recs)} records"


def judge(html, type_, id_):
    """One listing page through extract + derive: None when fine, else what's wrong."""
    try:
        rec = extract(html, type_, id_)
        derive(rec)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    missing = [k for k in REQUIRED.get(rec.get("tpl"), REQUIRED["kr"]) if not rec.get(k)]
    return f"{rec.get('tpl')}: missing {', '.join(missing)}" if missing else None


def check_listing(client, type_, cands):
    """Listing pages until FAILS of them pass or fail (JUDGED at most); 404s (ended) skipped: (ok, message)."""
    if not cands:
        return False, "no fetched listing in data/"
    good, failed, gone = [], [], []
    for id_, path in cands[:MAX_FETCHES]:
        if len(good) >= FAILS or len(failed) >= FAILS or len(good) + len(failed) >= JUDGED:
            break
        try:
            html = _get(client, path)
        except FileNotFoundError:
            gone.append(id_)
            continue
        why = judge(html, type_, id_)
        if why:
            failed.append(f"{id_} ({why})")
        else:
            good.append(id_)
    ok = len(failed) < FAILS and len(good) >= len(failed) and bool(good)
    msg = f"ok {good}" + (f"; failed {failed}" if failed else "") + (f"; gone {gone}" if gone else "")
    return ok, msg


def _try(fn, *args):
    try:
        return fn(*args)
    except Outage:
        raise
    except Exception as e:  # a parser exception, a request that kept failing: the check failed, the canary goes on
        return False, f"{type(e).__name__}: {e}"


def run(client, data, pref=PREF, log=print):
    """Every check; returns {"search_ok", "list_ok", "reachable": bool, "lines": [str]}."""
    flags = {"search_ok": True, "list_ok": True, "reachable": True}
    lines = []
    checks = []
    for type_ in TYPES:
        checks.append(("search_ok", f"search  {type_}", check_search, (client, type_, pref)))
        checks.append(("list_ok", f"listing {type_}", check_listing, (client, type_, candidates(data, type_, pref))))
    checks.append(("list_ok", "listing new_house /tochi/", check_listing,
                   (client, "new_house", candidates(data, "new_house", pref, tochi=True))))
    for flag, name, fn, args in checks:
        try:
            ok, msg = _try(fn, *args)
        except Outage as e:
            flags["reachable"] = False
            lines.append(f"DOWN {name}: {e} (the rest skipped; the run handles outages)")
            log(lines[-1])
            break
        flags[flag] &= ok
        lines.append(f"{'ok ' if ok else 'BAD'} {name}: {msg}")
        log(lines[-1])
    return {**flags, "lines": lines}


def write_output(result, path):
    """search_ok=, list_ok=, reachable= lines (true|false), appended (GITHUB_OUTPUT)."""
    with open(path, "a", encoding="utf-8") as f:
        for k in ("search_ok", "list_ok", "reachable"):
            f.write(f"{k}={'true' if result[k] else 'false'}\n")
