"""Crawl search results, reconcile each area's listings, fetch listing pages from the queue, clean up.

A run crawls the search results (at the `--crawl-at` hours) and diffs them against the DB. Lifecycle of a listing:
  first seen -> stored; its listing page is fetched (one per property: the group's lead) and kept as a record
    (page_json, store_page); fetched again when it's relisted, when its price changes and the record is older than
    REFETCH_DAYS, and by the slow refresh (REFRESH_PER_RUN oldest records a run). Later crawls refresh the
    search-result fields, so price changes always show
  missing from REMOVE_AFTER_MISSES consecutive complete crawls of its area -> `removed` event, then
    saved (on the shared list, saved.json): kept as status 'removed' (the site shows it as ended)
    not saved: deleted (row with its record, queue entry; git history keeps the data)
  a kept listing seen again -> active (relisted event); one no longer saved -> deleted by `purge`
  a deleted listing seen again within TOMBSTONE_DAYS -> back with its first_seen (relisted event, not new)
  an id first seen under another type (re-typed, or land also listed as new_house) -> that row's first_seen, no
    new event (unless the other type's got one in the same run), queued at backfill
"""
import gzip
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import extract as extract_mod
from .db import dumps
from .detail import PARSE_VERSION, derive
from .export import dup_key
from .http import NetworkError, Unavailable
from .parse import PAGE_SIZE, TYPES, page_count, parse_areas, parse_list_page
from .scope import by_key, in_scope
from .util import write_atomic

REMOVE_AFTER_MISSES = 2  # consecutive complete crawls, on different crawl days, that don't see a listing
DAY_STARTS = 4           # hour SUUMO's daily update lands; a crawl day runs from it to the next day's
EVENT_DAYS = 90          # events/runs rows kept this long in the DB (data/events/ in git keeps all)
SUSPECT_DROP = 0.30      # an area losing more than this share at once is distrusted (no removals)
SUSPECT_MIN_HITS = 20    # ...but only when it had at least this many (small areas swing naturally)
TOMBSTONE_DAYS = 14      # a deleted listing's tombstone (ended table) is kept this long
MAX_DETAIL_ATTEMPTS = 3
NET_STREAK = 2           # targets (or areas of one) in a row failing because SUUMO or the network is down stop the
                         # crawl (or the target): http.NetworkError (never reached) or http.Unavailable (5xx only)
DOWN = (NetworkError, Unavailable)
PROGRESS_EVERY = 200      # listing pages between progress lines (also saved to runs.progress for `status`)
CHECKPOINT_SECONDS = 3600  # long runs export data/ this often, so the site shows what's fetched so far
OUTAGE_STREAK = 5          # this many failed listing pages in a row = SUUMO (or the network) is down...
OUTAGE_PAUSE = 600         # ...pause this long; those failures don't count toward MAX_DETAIL_ATTEMPTS
EXTRACT_STREAK = 10        # this many extract errors in a row = SUUMO changed its layout: the queue stops, and those
                           # pages keep their attempts (fix extract, then they're fetched again)
REFETCH_DAYS = 14          # a price change on a lead whose record is older than this fetches its page again
REFRESH_PER_RUN = 20       # slow refresh: after the queue, this many leads with the oldest records (or records of an
REFRESH_MIN_DAYS = 30      # older EXTRACT_VERSION) are fetched again; never one fetched within REFRESH_MIN_DAYS
DEBUG_PER_MILLE = 5        # debug sample: this share of fetched pages (by a hash of type:id) + every extract failure
EXTRACT_ERRORS_MAX = 5     # extract errors below the streak that still flag the run (extract_errors): this many,
EXTRACT_ERRORS_SHARE = 0.01  # ...or more than this share of the pages fetched, from EXTRACT_ERRORS_MIN pages on
EXTRACT_ERRORS_MIN = 200     # (one odd page in a small run isn't a layout change)
REDERIVE_BATCH = 1000      # rows per batch when re-deriving
SPEC_ROWS = (5, 10)        # run report: records with at least this many spec rows (ms pages have ~8, kr ~20+)

# priorities in the detail queue: listings new since the area's baseline first, then changed ones
P_NEW, P_CHANGED, P_BACKFILL = 2, 1, 0


class DeriveError(Exception):
    """derive() failed on a record that store_page stored anyway (detail_json kept; re-derived after a fix)."""


def store_page(db, type_key, lid, record, fetched_at, extract_v=extract_mod.EXTRACT_VERSION):
    """The one write path of a listing's page record: page_json, extract_v, fetched_at and detail_json =
    derive(record). Used by the queue, twin copies, the migration's backfill. No commit (the caller's transaction).
    A derive error stores the record, keeps the old detail_json and raises DeriveError."""
    page = extract_mod.dumps(record)
    try:
        fields = derive(record)
    except Exception as e:
        db.x("UPDATE listings SET page_json=?, extract_v=?, fetched_at=? WHERE type=? AND id=?",
             page, extract_v, fetched_at, type_key, lid)
        raise DeriveError(f"derive: {e!r}") from e
    db.x("UPDATE listings SET page_json=?, extract_v=?, fetched_at=?, detail_json=? WHERE type=? AND id=?",
         page, extract_v, fetched_at, dumps(fields), type_key, lid)
    return fields


def rederive(db, log=print):
    """Rebuild detail_json from page_json, no requests: every record when detail.PARSE_VERSION is newer than the
    state's (meta parse_v), else only records without derived fields (a derive error on a first fetch, fixed since).
    Batches of REDERIVE_BATCH rows in one transaction; rows whose derive fails keep their detail_json (counted).
    Returns (rows, errors), or None when nothing was due."""
    due = int(db.meta("parse_v") or 0) < PARSE_VERSION
    where = "page_json IS NOT NULL" + ("" if due else " AND detail_json IS NULL")
    ids = [r[0] for r in db.x(f"SELECT rowid FROM listings WHERE {where}")]
    if not due and not ids:
        return None
    t0 = time.monotonic()
    done = errors = 0
    for n in range(0, len(ids), REDERIVE_BATCH):
        chunk = ids[n:n + REDERIVE_BATCH]
        out = []
        for r in db.x(f"SELECT rowid, type, id, page_json FROM listings WHERE rowid IN ({','.join('?' * len(chunk))})",
                      *chunk):
            try:
                out.append((dumps(derive(json.loads(r["page_json"]))), r["rowid"]))
            except Exception as e:
                errors += 1
                if errors <= 5:
                    log(f"  ! derive failed for {r['type']}/{r['id']}: {e!r}")
        db.conn.executemany("UPDATE listings SET detail_json=? WHERE rowid=?", out)
        done += len(out)
    if due:
        db.set_meta("parse_v", PARSE_VERSION)
    db.commit()
    log(f"re-derived {done:,} records ({f'parse_v {PARSE_VERSION}' if due else 'without derived fields'}) in "
        f"{time.monotonic() - t0:.1f}s" + (f"; {errors} derive errors" if errors else ""))
    return done, errors


def check_backfilled(db):
    """A state whose records were never filled (meta backfilled) would queue a whole shard's pages at once: stop."""
    if not db.meta("backfilled"):
        raise SystemExit("this state has no page records yet (meta 'backfilled' is unset): migrate it (`migrate-state` "
                         "and the backfill) or rebuild it with `seed`; a run won't queue every listing page at once")


def debug_sampled(type_key, lid):
    """The deterministic DEBUG_PER_MILLE share of listings whose fetched page goes to the debug sample."""
    return int(hashlib.sha1(f"{type_key}:{lid}".encode()).hexdigest()[:8], 16) % 1000 < DEBUG_PER_MILLE


class AreaPageError(ValueError):
    """An area page that can't be right: the target is an error, nothing of it is reconciled."""


class Pipeline:
    def __init__(self, db, client, targets, now: datetime, run_id: str, log=print, saved=(), debug_dir=None):
        self.db, self.client, self.targets = db, client, targets
        self.debug_dir = Path(debug_dir) if debug_dir else None  # the debug sample's folder (None: off)
        self.scope = by_key(targets)
        self.saved = set(saved)  # "type:id" keys on the shared saved list: kept after they're gone
        self.now, self.run_id, self.log = now, run_id, log
        self.ts = now.isoformat(timespec="seconds")
        self.today = now.date().isoformat()
        self.crawl_day = (now - timedelta(hours=DAY_STARTS)).date().isoformat()  # one miss per area per crawl day
        self.report = {"targets": [], "detail": {}, "pages": _page_stats(), "flags": []}

    def save_progress(self, phase, text):
        """Put progress in the run's row (runs.progress only, a few bytes) so `status`, another process, can show
        it while the run goes on. The report is written once, at the end."""
        at = datetime.now(self.now.tzinfo).isoformat(timespec="seconds")
        self.db.x("UPDATE runs SET progress=? WHERE run_id=?",
                  json.dumps({"phase": phase, "text": text, "at": at}, ensure_ascii=False), self.run_id)
        self.db.commit()

    # ---------- search results ----------

    def crawl_lists(self, skip=()):
        """Crawl every target's search results, except prefectures in skip (already crawled today). A target that
        failed (its area page, or some of its areas) is crawled again once at the end (only the failed areas when
        its area page was fine). A prefecture's crawled_at is written once all its targets are done, retry included
        (success or not), so a crawl that stops part-way resumes with the next prefecture (`run --crawl-at`).
        NET_STREAK targets in a row failing because SUUMO or our network is down (DOWN: never reached, or nothing but
        5xx, e.g. SUUMO's maintenance page) stop the crawl: returns False, and the prefectures not done are left to
        the next run."""
        targets = [t for t in self.targets if t.pref not in skip]
        if len(targets) < len(self.targets):
            self.log(f"search results: {len({t.pref for t in self.targets} & set(skip))} prefectures already "
                     "crawled since the daily update; skipped")
        left = Counter(t.pref for t in targets)
        retry, offline = [(t, None) for t in targets], 0
        for last in (False, True):  # every target, then the failed ones again
            jobs, retry = retry, []
            for t, areas in jobs:
                self.log(f"\n=== {t.pref} / {t.type}{' (again)' if last else ''} ===")
                rep, failed, again = self._target(t, areas)
                if last:
                    rep["retried"] = True
                self.report["targets"].append(rep)
                offline = offline + 1 if rep.get("network") else 0
                if offline >= NET_STREAK:
                    self.db.commit()
                    self.log(f"\n! {NET_STREAK} targets in a row failed: SUUMO or the network is down; "
                             "the search results not crawled yet are left to the next run")
                    self.report["offline"] = True
                    return False
                if failed and not last:
                    retry.append((t, again))
                else:
                    left[t.pref] -= 1
                    if not left[t.pref]:
                        self.db.x("INSERT OR REPLACE INTO prefs (pref, crawled_at) VALUES (?, ?)", t.pref, self.ts)
                self.db.commit()
        return True

    def _target(self, t, areas=None):
        """Crawl a target (or only these areas of it, a retry): (report, failed, the areas to crawl again: None for
        the whole target)."""
        try:
            rep = self._crawl_target(t, areas)
        except Exception as e:  # e.g. the area page itself failed; other targets still run
            self.log(f"  ERROR {e}" if isinstance(e, AreaPageError) else f"  ERROR {e!r}")
            rep = {"pref": t.pref, "type": t.type, "error": repr(e)}
            if isinstance(e, DOWN):
                rep["network"] = True
            return rep, True, None
        again = rep.pop("again")
        return rep, bool(again), again

    def _crawl_target(self, t, retry=None):
        """One target's area page, then its areas (or only the areas in retry, without the area page). The report's
        "again": the areas that failed or weren't reached (NET_STREAK network errors in a row end the target)."""
        rep = {"pref": t.pref, "type": t.type, "areas": [], "again": []}
        areas = self._area_page(t, rep) if retry is None else retry
        offline = 0
        for k, a in enumerate(areas):
            self.save_progress("search results", f"{t.pref}/{t.type}: area {k + 1}/{len(areas)} ({a['name']})")
            out = self.crawl_area(t.pref, t.type, a)
            rep["areas"].append(out)
            self.db.commit()
            if out["status"] == "error":
                rep["again"].append(a)
            offline = offline + 1 if out.get("network") else 0
            if offline >= NET_STREAK:
                self.log(f"  ! {NET_STREAK} areas in a row failed (SUUMO or the network is down); stopping "
                         f"{t.pref}/{t.type}")
                rep["network"] = True
                rep["again"] += areas[k + 1:]
                break
        return rep

    def _area_page(self, t, rep):
        """Fetch and check the target's area page, reconcile the areas it shows without a link (or doesn't show);
        returns the areas to crawl. A page that can't be right (no areas but for new_condo, or far fewer listings in
        the target's areas than held there) raises AreaPageError before anything is reconciled (target = error)."""
        html = self.client.get(f"/{TYPES[t.type]}/{t.pref}/city/")
        parsed = parse_areas(html, TYPES[t.type], t.pref)
        if not parsed and t.type != "new_condo":  # new_condo lists only areas with developments: maybe none
            raise AreaPageError("area page: no areas on it (a changed or broken page?)")
        areas = [a for a in parsed if t.includes(a["code"])]
        held = {}  # area code -> (name, active listings)
        for r in self.db.x("""SELECT area_code, MAX(area_name) name, COUNT(*) n FROM listings
                              WHERE pref=? AND type=? AND status='active' GROUP BY area_code""", t.pref, t.type):
            if t.includes(r["area_code"]):
                held[r["area_code"]] = (r["name"], r["n"])
        n_held, expected = sum(n for _, n in held.values()), sum(a["expected"] for a in areas)
        if n_held >= SUSPECT_MIN_HITS and expected < n_held * (1 - SUSPECT_DROP):
            raise AreaPageError(f"area page: {expected:,} listings in its areas, {n_held:,} held there "
                                "(a broken page? nothing reconciled)")

        # An area can drop off the area page entirely (e.g. new_condo lists only areas that currently have
        # developments). Treat it as crawled with zero listings so what we hold there is reconciled too. The same for
        # a new_condo area the other types know: without developments now, so its first one later is 新着.
        listed = {a["code"] for a in areas}
        known = {}
        if t.type == "new_condo":
            known = {r["code"]: r["name"] for r in self.db.x(
                "SELECT code, MAX(name) name FROM areas WHERE pref=? AND type!=? GROUP BY code", t.pref, t.type)
                if t.includes(r["code"])}
        empty = 0
        for code in sorted((set(held) | set(known) | (t.areas or set())) - listed):
            if code in held or code in known:
                a = {"code": code, "name": held[code][0] if code in held else known[code], "slug": None, "expected": 0}
                self.db.upsert_area(t.pref, t.type, a)
                rep["areas"].append(self.reconcile(t.pref, t.type, a, 0, {}, True))
                empty += code not in held
            else:
                self.log(f"  {code:<10} not on the area page (no listings, or a wrong code)")
                rep["areas"].append({"code": code, "status": "not_listed"})

        crawl = []
        for a in areas:
            self.db.upsert_area(t.pref, t.type, a)
            if a["slug"]:
                crawl.append(a)
            elif a["expected"] == 0:  # listed as "(0)" without a link: crawled, 0 hits (a baseline if nothing held)
                rep["areas"].append(self.reconcile(t.pref, t.type, a, 0, {}, True))
                empty += a["code"] not in held
            else:
                rep["areas"].append({"code": a["code"], "name": a["name"], "status": "no_slug"})
        if empty:
            self.log(f"  {empty} areas without listings: recorded as crawled with 0")
        self.db.commit()
        return crawl

    def _fetch_area(self, pref, type_key, a):
        path = f"/{TYPES[type_key]}/{pref}/{a['slug']}/?pc={PAGE_SIZE}"

        def page(n):
            html = self.client.get(path if n == 1 else f"{path}&page={n}")
            hits, recs = parse_list_page(html, type_key, a)
            return hits, {r["id"]: r for r in recs}

        hits, recs = page(1)
        if hits is None:  # without the count nothing says the crawl is complete: an error (no misses)
            raise ValueError("no hit count on page 1")
        errors = []
        for n in range(2, page_count(hits) + 1):
            try:
                more = page(n)[1]
                recs.update(more)
                if not more:
                    errors.append(f"page {n} empty")
            except Exception as e:  # the area is just marked incomplete
                errors.append(f"page {n}: {e!r}")
        return hits, recs, errors

    def crawl_area(self, pref, type_key, a):
        try:
            hits, recs, errors = self._fetch_area(pref, type_key, a)
            if len(recs) < hits or errors:
                # listings shift between pages while paginating; a second pass (unioned) closes the gap
                hits, recs2, errors = self._fetch_area(pref, type_key, a)
                recs.update(recs2)  # union; the second pass is fresher
        except Exception as e:
            self.log(f"  {a['name']:<10} ERROR {e!r}")
            self.db.x("UPDATE areas SET last_status='error' WHERE pref=? AND type=? AND code=?",
                      pref, type_key, a["code"])
            out = {"code": a["code"], "name": a["name"], "status": "error", "error": repr(e)}
            if isinstance(e, DOWN):
                out["network"] = True
            return out
        complete = not errors and len(recs) >= hits
        return self.reconcile(pref, type_key, a, hits, recs, complete, errors)

    def _leads(self, type_key, recs, rows):
        """wanted(id): whether a listing seen in this crawl should get its page. One listing page per property (the
        only place this rule lives): a group of listings with the same dup_key has one lead, and the other agents'
        copies aren't queued (by the owner's decision, copies are never fetched). Only listings seen in this crawl
        count (a missing one may be ending, and a copy should take over).
          - a member has its page record (page_json): it's the lead; no other member is queued
          - else the member already queued (and not given up on) stays the lead, so the group keeps its place in
            line when SUUMO shows the members in another order; else the first one on the page not given up on
          - an agent appears twice in the group: not one property (an agent doesn't list the same one twice),
            every listing in it is its own lead
        """
        keys = {lid: dup_key({**rec, "type": type_key}) for lid, rec in recs.items()}
        groups = defaultdict(list)
        for lid, k in keys.items():
            if k:
                groups[k].append(lid)
        groups = {k: ids for k, ids in groups.items() if len(ids) > 1}
        members = [i for ids in groups.values() for i in ids]
        attempts = {}  # queue entries of the members: id -> attempts
        for n in range(0, len(members), 500):
            chunk = members[n:n + 500]
            attempts.update((r["id"], r["attempts"]) for r in self.db.x(
                f"SELECT id, attempts FROM queue WHERE type=? AND id IN ({','.join('?' * len(chunk))})",
                type_key, *chunk))
        live = lambda i: attempts.get(i, 0) < MAX_DETAIL_ATTEMPTS  # noqa: E731  (not given up: 404, parse error)
        lead = {}
        for k, ids in groups.items():
            names = [recs[i]["agent"] for i in ids if recs[i].get("agent")]
            if len(names) != len(set(names)):
                continue
            if any(rows[i] and rows[i]["fetched"] for i in ids):
                lead[k] = None
            else:
                lead[k] = (next((i for i in sorted(ids) if i in attempts and live(i)), None)
                           or next((i for i in ids if live(i)), ids[0]))
        return lambda lid: keys[lid] not in lead or lead[keys[lid]] == lid

    def reconcile(self, pref, type_key, a, hits, recs, complete, errors=()):
        """Apply one area's crawl result to the DB: inserts, events, queue, misses/removals, area state."""
        area_row = self.db.area(pref, type_key, a["code"])
        baselined = bool(area_row["baselined"])
        prev_hits = area_row["last_hits"]
        suspect = (complete and prev_hits is not None and prev_hits >= SUSPECT_MIN_HITS
                   and hits < prev_hits * (1 - SUSPECT_DROP))
        counts = {"new": 0, "retyped": 0, "price_changed": 0, "relisted": 0, "removed": 0, "missing": 0}
        held = {r["id"]: r for r in self.db.area_listings(pref, type_key, a["code"])}
        rows = {lid: held.get(lid) or self.db.listing(type_key, lid, light=True) for lid in recs}  # or moved here
        wanted = self._leads(type_key, recs, rows)

        for lid, rec in recs.items():
            row = rows[lid]
            ident = {"type": type_key, "id": lid, "pref": pref, "area_code": a["code"]}
            if row is None:
                tomb = self.db.x("SELECT first_seen, price FROM ended WHERE type=? AND id=?", type_key, lid).fetchone()
                # the same id under another type (by primary key: no scan): land also listed under new_house, or a
                # listing re-typed (new_house -> used_house). Not new; the old row ends after its misses.
                other = None if tomb else next(
                    (r for t in TYPES if t != type_key and (r := self.db.listing(t, lid, light=True))), None)
                first = tomb["first_seen"] if tomb else other["first_seen"] if other else self.today
                self.db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen,
                             last_seen) VALUES (?,?,?,?,?,?,?,?)""",
                          type_key, lid, pref, a["code"], a["name"], dumps(rec), first, self.ts)
                if tomb:  # deleted lately and back: relisted (its record was deleted with it: fetched again)
                    self.db.x("DELETE FROM ended WHERE type=? AND id=?", type_key, lid)
                    if baselined:
                        self.db.add_event(self.run_id, "relisted", ident, {"price": rec.get("price")})
                        if tomb["price"] != rec.get("price"):
                            counts["price_changed"] += 1
                            self.db.add_event(self.run_id, "price_changed", ident,
                                              {"price": rec.get("price"), "old_price": tomb["price"]})
                    if wanted(lid):
                        self.db.enqueue(type_key, lid, P_CHANGED, self.ts)
                    counts["relisted"] += 1
                elif other:
                    if baselined and self.db.x(
                            "SELECT 1 FROM events WHERE run_id=? AND type=? AND id=? AND kind IN ('new', 'relisted')",
                            self.run_id, other["type"], lid).fetchone():
                        self.db.add_event(self.run_id, "new", ident, {"price": rec.get("price")})  # both new today
                    if wanted(lid):
                        self.db.enqueue(type_key, lid, P_BACKFILL, self.ts)
                    counts["retyped"] += 1
                elif baselined:
                    self.db.add_event(self.run_id, "new", ident, {"price": rec.get("price")})
                    if wanted(lid):
                        self.db.enqueue(type_key, lid, P_NEW, self.ts)
                    counts["new"] += 1
                elif wanted(lid):
                    self.db.enqueue(type_key, lid, P_BACKFILL, self.ts)
                continue

            old = json.loads(row["list_json"])
            relisted = row["status"] == "removed"  # kept because saved, and back on SUUMO
            if relisted:
                counts["relisted"] += 1
                self.db.add_event(self.run_id, "relisted", ident, {"price": rec.get("price")})
            repriced = old.get("price") != rec.get("price")
            if repriced:
                counts["price_changed"] += 1
                self.db.add_event(self.run_id, "price_changed", ident,
                                  {"price": rec.get("price"), "old_price": old.get("price")})
            if not row["fetched"]:  # not fetched yet (a failed or interrupted fetch), unless it's a copy
                if wanted(lid):
                    self.db.enqueue(type_key, lid, P_CHANGED if relisted else P_BACKFILL, self.ts)
                else:
                    self.db.dequeue(type_key, lid)
            elif relisted or (repriced and self._stale(row["fetched_at"])):  # the lead: its page may have changed
                self.db.enqueue(type_key, lid, P_CHANGED, self.ts)
            self.db.x("""UPDATE listings SET list_json=?, area_code=?, area_name=?, pref=?, status='active',
                         removed_at=NULL, missed=0, last_seen=? WHERE type=? AND id=?""",
                      dumps(rec), a["code"], a["name"], pref, self.ts, type_key, lid)

        # misses count once per crawl day: a re-crawl the same day (a restarted crawl) saw the same SUUMO data.
        # A suspect crawl uses up the day too, so a glitch must outlast it by two crawl days to remove anything.
        counted = complete and area_row["missed_on"] == self.crawl_day
        if complete and not suspect and not counted:
            for lid, row in held.items():
                if lid in recs or row["status"] != "active":
                    continue
                missed = row["missed"] + 1
                if missed >= REMOVE_AFTER_MISSES:
                    price = json.loads(row["list_json"]).get("price")
                    self.db.add_event(self.run_id, "removed", row, {"price": price})
                    self.db.dequeue(type_key, lid)
                    if f"{type_key}:{lid}" in self.saved:
                        self.db.x("UPDATE listings SET status='removed', removed_at=?, missed=? WHERE type=? AND id=?",
                                  self.ts, missed, type_key, lid)
                    else:
                        self.db.x("INSERT OR REPLACE INTO ended (type, id, pref, first_seen, price, ended_at) "
                                  "VALUES (?,?,?,?,?,?)", type_key, lid, pref, row["first_seen"], price, self.ts)
                        self.delete(pref, type_key, lid)
                    counts["removed"] += 1
                else:
                    self.db.x("UPDATE listings SET missed=? WHERE type=? AND id=?", missed, type_key, lid)
                    counts["missing"] += 1

        status = "suspect" if suspect else ("complete" if complete else "incomplete")
        # last_hits is always the latest count: a real drop is distrusted for one crawl, then the new level holds
        self.db.x("""UPDATE areas SET last_hits=?, last_status=?, baselined=?, missed_on=?
                     WHERE pref=? AND type=? AND code=?""",
                  hits, status, 1 if (baselined or (complete and not suspect)) else 0,
                  self.crawl_day if complete else area_row["missed_on"], pref, type_key, a["code"])
        line = f"  {a['name']:<10} hits={hits:>5} parsed={len(recs):>5} {status}"
        if not baselined and complete:
            line += " (baseline)"
        elif counted and not suspect:
            line += " (misses already counted this crawl day)"
        line += "".join(f" {k}={v}" for k, v in counts.items() if v)
        if errors:
            line += f" errors={list(errors)[:2]}"
        if hits or held or status != "complete":  # an empty area with nothing held: counted by _area_page
            self.log(line)
        return {"code": a["code"], "name": a["name"], "status": status, "hits": hits, "parsed": len(recs),
                "baseline": not baselined and complete, **counts}

    def _stale(self, fetched_at):
        """A record older than REFETCH_DAYS (or of unknown age)."""
        if not fetched_at:
            return True
        at, now = datetime.fromisoformat(fetched_at), self.now
        if (at.tzinfo is None) != (now.tzinfo is None):  # compare wall clocks
            at, now = at.replace(tzinfo=None), now.replace(tzinfo=None)
        return at < now - timedelta(days=REFETCH_DAYS)

    # ---------- listing pages ----------

    def process_queue(self, budget_seconds, checkpoint=None):
        """Fetch listing pages, highest priority first, until the queue is empty or the budget is spent; then the slow
        refresh (_refresh) with what's left of the budget. checkpoint(): called every CHECKPOINT_SECONDS (the CLI
        exports data/)."""
        t0 = time.monotonic()
        self._clock = {"t0": t0, "deadline": t0 + budget_seconds, "checkpoint": t0, "fn": checkpoint}
        self._tally = Counter()
        finished = self._work(self._queued())
        refreshed = 0
        if finished and time.monotonic() < self._clock["deadline"]:
            keys = self._refresh()
            refreshed = len(keys)
            if keys:
                self.log(f"slow refresh: {refreshed} leads with the oldest records")
                self._work(self._queued(keys))
        self.db.commit()
        tally = self._tally
        remaining = self.db.x("SELECT COUNT(*) FROM queue WHERE attempts < ?", MAX_DETAIL_ATTEMPTS).fetchone()[0]
        self.report["detail"] = {"fetched": tally["done"], "failed": tally["failed"], "gone": tally["gone"],
                                 "copied": tally["copied"], "refresh": refreshed, "remaining": remaining}
        pages = self.report["pages"]
        errors = pages["extract_errors"]
        seen = tally["done"] + errors
        if errors >= EXTRACT_ERRORS_MAX or (seen >= EXTRACT_ERRORS_MIN and errors > EXTRACT_ERRORS_SHARE * seen):
            self.flag("extract_errors")
        for key in ("unknown_labels", "unknown_sections"):  # most frequent first, capped
            pages[key] = dict(Counter(pages[key]).most_common(100))
        self.log(f"\nlisting pages: fetched={tally['done']} failed={tally['failed']} gone={tally['gone']} "
                 f"extract_errors={pages['extract_errors']} derive_errors={pages['derive_errors']} "
                 f"remaining_in_queue={remaining}")

    def _queued(self, keys=None):
        """Queue entries not given up on, in fetch order (only these (type, id) keys if given)."""
        rows = self.db.x(
            """SELECT q.type, q.id, q.priority, q.attempts, l.pref, l.area_code, l.list_json, l.status
               FROM queue q LEFT JOIN listings l ON l.type=q.type AND l.id=q.id
               WHERE q.attempts < ? ORDER BY q.priority DESC, q.enqueued_at, q.id""", MAX_DETAIL_ATTEMPTS).fetchall()
        if keys is not None:
            keys = set(keys)
            rows = [r for r in rows if (r["type"], r["id"]) in keys]
        return self._queue_order(rows)

    def _refresh(self):
        """Slow refresh: queue (at P_BACKFILL) up to REFRESH_PER_RUN in-scope active leads not queued, those whose
        record is of an older EXTRACT_VERSION first, then the oldest fetched_at (never one fetched within
        REFRESH_MIN_DAYS unless its EXTRACT_VERSION is older). Keeps records from going stale and upgrades them after
        an EXTRACT_VERSION bump, without HTML kept anywhere."""
        cutoff = (self.now - timedelta(days=REFRESH_MIN_DAYS)).isoformat(timespec="seconds")
        keys = []
        for r in self.db.x(
                """SELECT l.type, l.id, l.pref, l.area_code FROM listings l
                   LEFT JOIN queue q ON q.type=l.type AND q.id=l.id
                   WHERE l.page_json IS NOT NULL AND l.status='active' AND q.id IS NULL
                     AND (l.extract_v IS NULL OR l.extract_v < ? OR l.fetched_at IS NULL OR l.fetched_at < ?)
                   ORDER BY COALESCE(l.extract_v, 0) >= ?, l.fetched_at, l.type, l.id""",
                extract_mod.EXTRACT_VERSION, cutoff, extract_mod.EXTRACT_VERSION):
            if len(keys) >= REFRESH_PER_RUN:
                break
            if in_scope(self.scope, r["pref"], r["type"], r["area_code"]):
                keys.append((r["type"], r["id"]))
                self.db.enqueue(r["type"], r["id"], P_BACKFILL, self.ts)
        self.db.commit()
        return keys

    def _work(self, rows):
        """Fetch -> extract -> store_page, one transaction per page. False when it stopped early (budget, or an
        extract error streak)."""
        clock, tally, pages = self._clock, self._tally, self.report["pages"]
        streak = []    # consecutive fetch failures
        extract = []   # consecutive extract errors: (type, id, attempts before)
        total = len(rows)
        for n, r in enumerate(rows):
            now = time.monotonic()
            if now >= clock["deadline"]:
                return False
            if n and n % PROGRESS_EVERY == 0:
                self._queue_progress(n, total, tally, now - clock["t0"], clock["deadline"] - now)
            if clock["fn"] and now - clock["checkpoint"] >= CHECKPOINT_SECONDS:
                clock["fn"]()
                clock["checkpoint"] = time.monotonic()
            if r["status"] != "active":  # ended or deleted since it was queued
                self.db.dequeue(r["type"], r["id"])
                self.db.commit()
                continue
            if not in_scope(self.scope, r["pref"], r["type"], r["area_code"]):
                continue  # left for `prune`
            if self._copy_twin(r):  # land listed under new_house too: the same page, already fetched
                tally["copied"] += 1
                continue
            try:
                html = self.client.get(json.loads(r["list_json"])["path"])
            except FileNotFoundError as e:
                # gone from SUUMO (404, or a redirect): not asked for again; the entry goes with the listing when
                # the crawls remove it (re-queueing keeps the attempts). The status and Location are kept. A record
                # held from an earlier fetch stays.
                self.log(f"  gone: {r['type']}/{r['id']}: {e}")
                self.db.x("UPDATE queue SET attempts=?, last_error=? WHERE type=? AND id=?",
                          MAX_DETAIL_ATTEMPTS, f"gone: {e}"[:300], r["type"], r["id"])
                self.db.commit()
                tally["gone"] += 1
                streak = []
                continue
            except Exception as e:
                # a network error (http.NetworkError: SUUMO was never reached) isn't the page's fault: no attempt
                counted = not isinstance(e, NetworkError)
                self.db.x("UPDATE queue SET attempts=attempts+?, last_error=? WHERE type=? AND id=?",
                          int(counted), repr(e)[:300], r["type"], r["id"])
                self.db.commit()
                tally["failed"] += 1
                streak.append((r["type"], r["id"], counted))
                if len(streak) >= OUTAGE_STREAK:
                    self._pause_for_outage(streak, clock["deadline"])
                    streak = []
                continue
            streak = []
            fetched_at = datetime.now(self.now.tzinfo).isoformat(timespec="seconds")
            try:
                record = extract_mod.extract(html, r["type"], r["id"])
            except Exception as e:
                # nothing stored (a record held from an earlier fetch stays); the page goes to the debug sample
                self.log(f"  ! extract failed for {r['type']}/{r['id']}: {e!r}")
                self.db.x("UPDATE queue SET attempts=?, last_error=? WHERE type=? AND id=?",
                          MAX_DETAIL_ATTEMPTS, f"extract: {e!r}"[:300], r["type"], r["id"])
                self.db.commit()
                tally["failed"] += 1
                pages["extract_errors"] += 1
                self._debug(r["type"], r["id"], html, fetched_at, error=repr(e))
                # A streak gives the attempts back at its 10th error. Errors of a streak cut short (the budget, an
                # interrupt, a run killed) stay given up on: `refetch --errors` queues them again after a fix.
                extract.append((r["type"], r["id"], r["attempts"]))
                if len(extract) >= EXTRACT_STREAK:
                    for type_key, lid, attempts in extract:  # a layout change, not these pages' fault
                        self.db.x("UPDATE queue SET attempts=? WHERE type=? AND id=?", attempts, type_key, lid)
                    self.db.commit()
                    self.flag("extract_streak")
                    self.log(f"  ! {len(extract)} extract errors in a row: SUUMO's listing pages changed? the queue "
                             "stops (their attempts kept); fix extract, then the next run fetches them")
                    return False
                continue
            extract = []
            try:
                store_page(self.db, r["type"], r["id"], record, fetched_at)
            except DeriveError as e:  # the record is stored; fixed code re-derives it at a run start (PARSE_VERSION)
                self.log(f"  ! {e} for {r['type']}/{r['id']}")
                pages["derive_errors"] += 1
                self.flag("derive_errors")
            self.db.dequeue(r["type"], r["id"])
            self.db.commit()
            tally["done"] += 1
            self._count(record)
            if debug_sampled(r["type"], r["id"]):
                self._debug(r["type"], r["id"], html, fetched_at, record=record)
        return True

    def flag(self, name):
        if name not in self.report["flags"]:
            self.report["flags"].append(name)

    def _count(self, record):
        """Run report: per template, records with each part (the Healthy step's coverage), unknown labels/sections."""
        pages = self.report["pages"]
        t = pages["templates"].setdefault(record.get("tpl", "?"), dict.fromkeys(_COVERAGE, 0))
        rows = len(record.get("spec", {}))
        for key, have in (("n", True), ("ll", "ll" in record), ("img", bool(record.get("img", {}).get("l"))),
                          *((f"spec{k}", rows >= k) for k in SPEC_ROWS), ("agent", bool(record.get("agent"))),
                          ("features", bool(record.get("features")))):
            t[key] += bool(have)
        unknown = record.get("unknown", {})
        for key in ("labels", "sections"):
            counts = pages[f"unknown_{key}"]
            for name in unknown.get(key, ()):
                counts[name] = counts.get(name, 0) + 1

    def _debug(self, type_key, lid, html, fetched_at, record=None, error=None):
        """Debug sample: <debug_dir>/<type>_<id>.html.gz (the page) and .json ({type, id, fetched_at, record | error};
        `python -m suumo extract FILE` reproduces it). Off without a debug dir; never fails the run."""
        if not self.debug_dir:
            return
        try:
            base = self.debug_dir / f"{type_key}_{lid}"
            write_atomic(base.with_suffix(".html.gz"), gzip.compress(html.encode(), mtime=0))
            meta = {"type": type_key, "id": lid, "fetched_at": fetched_at, "extract_v": extract_mod.EXTRACT_VERSION}
            meta.update({"record": record} if error is None else {"error": error})
            write_atomic(base.with_suffix(".json"), (json.dumps(meta, ensure_ascii=False, sort_keys=True,
                                                                indent=1) + "\n").encode())
        except OSError as e:
            self.log(f"  ! debug sample not written: {e!r}")

    def _queue_order(self, rows):
        """The queued listings in fetch order: new ones first, then by prefecture, the one with the most listings
        first (where most people look). Which listings are queued at all is reconcile's decision. Land also listed
        under new_house (one /tochi/ page) waits for its queued twin's page, then copies it (_copy_twin)."""
        size = dict(self.db.x("SELECT pref, COUNT(*) FROM listings WHERE status='active' GROUP BY pref").fetchall())
        pages, ranked = set(), []
        for n, r in enumerate(rows):
            path = _path(r)
            twin = "/tochi/" in path and path in pages  # its twin is queued before it: that page comes first
            pages.add(path)
            ranked.append((-r["priority"], twin, -size.get(r["pref"], 0), n, r))
        return [r for *_, r in sorted(ranked, key=lambda x: x[:4])]

    def _copy_twin(self, r):
        """Land sold with a build condition is listed under both new_house and land with one page (/tochi/): copy
        the other listing's record (page_json, extract_v, fetched_at; detail_json derived) instead of fetching it
        again, when it's newer than this row's (or this row has none). Otherwise False: the page is fetched, and the
        twin, when queued, copies it in turn. The record has no type or id in it: right for this row as it is."""
        twin_type = {"land": "new_house", "new_house": "land"}.get(r["type"])
        twin = twin_type and self.db.listing(twin_type, r["id"])
        if not (twin and twin["page_json"] and _path(twin) == _path(r)):
            return False
        own = self.db.listing(r["type"], r["id"], light=True)
        if own["fetched"] and (twin["fetched_at"] or "") <= (own["fetched_at"] or ""):
            return False  # the twin's record is no newer than this one: fetch
        try:
            store_page(self.db, r["type"], r["id"], json.loads(twin["page_json"]), twin["fetched_at"],
                       twin["extract_v"])
        except DeriveError as e:
            self.log(f"  ! {e} for {r['type']}/{r['id']}")
            self.db.x("UPDATE listings SET detail_json=? WHERE type=? AND id=?", twin["detail_json"], r["type"],
                      r["id"])
            self.report["pages"]["derive_errors"] += 1
            self.flag("derive_errors")
        self.db.dequeue(r["type"], r["id"])
        self.db.commit()
        return True

    def _queue_progress(self, n, total, tally, elapsed, budget_left):
        per = elapsed / n
        eta = (total - n) * per
        text = (f"{n:,}/{total:,} ({100 * n / total:.1f}%) · {per:.2f} s/page · "
                f"ETA {_hm(min(eta, budget_left))}{' (budget ends first)' if budget_left < eta else ''} · "
                f"fetched {tally['done']:,} failed {tally['failed']:,} gone {tally['gone']:,} · "
                f"delay {self.client.delay:.2f}s")
        self.log(f"  listing pages {text}")
        self.save_progress("listing pages", text)

    def _pause_for_outage(self, streak, deadline):
        """Several failures in a row: SUUMO or the network is down. Give the attempts back and wait."""
        for type_key, lid, counted in streak:
            if counted:
                self.db.x("UPDATE queue SET attempts=MAX(0, attempts-1) WHERE type=? AND id=?", type_key, lid)
        self.db.commit()
        pause = max(0, min(OUTAGE_PAUSE, deadline - time.monotonic()))
        self.log(f"  ! {len(streak)} listing pages failed in a row; pausing {pause / 60:.0f} min "
                 "(these failures don't count toward the attempt limit)")
        self.save_progress("paused", f"SUUMO or the network is down; retrying in {pause / 60:.0f} min")
        time.sleep(pause)

    # ---------- cleanup ----------

    def delete(self, pref, type_key, lid):
        """A listing's row (with its page record) and queue entry."""
        self.db.x("DELETE FROM listings WHERE type=? AND id=?", type_key, lid)
        self.db.dequeue(type_key, lid)

    def purge(self):
        """Ended listings no longer saved (deleted, leaving a tombstone dated their removal, as reconcile's deletes
        do), old events/runs rows, old tombstones."""
        unsaved = [r for r in self.db.x("SELECT type, id, pref, first_seen, list_json, removed_at FROM listings "
                                        "WHERE status='removed'")
                   if f"{r['type']}:{r['id']}" not in self.saved]
        for r in unsaved:
            self.db.x("INSERT OR REPLACE INTO ended (type, id, pref, first_seen, price, ended_at) VALUES (?,?,?,?,?,?)",
                      r["type"], r["id"], r["pref"], r["first_seen"], json.loads(r["list_json"]).get("price"),
                      r["removed_at"] or self.ts)
            self.delete(r["pref"], r["type"], r["id"])
        if unsaved:
            self.log(f"deleted {len(unsaved)} ended listings no longer saved")
        old_runs = (self.now - timedelta(days=EVENT_DAYS)).strftime("%Y%m%dT%H%M%S")
        self.db.x("DELETE FROM events WHERE run_id < ?", old_runs)
        self.db.x("DELETE FROM runs WHERE run_id < ?", old_runs)
        self.db.x("DELETE FROM ended WHERE ended_at < ?",
                  (self.now - timedelta(days=TOMBSTONE_DAYS)).isoformat(timespec="seconds"))
        self.db.commit()


def _path(row):
    """A listing's page path (from its search-result fields)."""
    return (json.loads(row["list_json"]) if row["list_json"] else {}).get("path") or ""


def _hm(seconds):
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


_COVERAGE = ("n", "ll", "img", *(f"spec{k}" for k in SPEC_ROWS), "agent", "features")


def _page_stats():
    """The run report's "pages": per template {n, ll, img, spec5, spec10, agent, features} (records fetched this run
    with each), unknown labels/sections {name: records}, extract and derive errors."""
    return {"templates": {}, "unknown_labels": {}, "unknown_sections": {}, "extract_errors": 0, "derive_errors": 0}
