"""Crawl search results, reconcile each area's listings, fetch listing pages from the queue, clean up.

A run crawls the search results (once a day with `--crawl-at`) and diffs them against the DB. Lifecycle of a listing:
  first seen -> stored; its listing page is fetched once (never again: later crawls refresh the search-result
    fields, so price changes still show)
  missing from REMOVE_AFTER_MISSES consecutive complete crawls of its area -> `removed` event, then
    saved (on the shared list, saved.json): kept as status 'removed' (the site shows it as ended)
    not saved: deleted (row, queue entry, archived page; git history keeps the data)
  a kept listing seen again -> active (relisted event); one no longer saved -> deleted by `purge`
  a deleted listing seen again within TOMBSTONE_DAYS -> back with its first_seen (relisted event, not new)
  an id first seen under another type (re-typed, or land also listed as new_house) -> that row's first_seen, no
    new event (unless the other type's got one in the same run), queued at backfill
"""
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from .archive import Archive
from .db import dumps
from .detail import parse_detail
from .export import dup_key
from .http import NetworkError
from .parse import PAGE_SIZE, TYPES, page_count, parse_areas, parse_list_page
from .scope import by_key, in_scope

REMOVE_AFTER_MISSES = 2  # consecutive complete crawls, on different crawl days, that don't see a listing
DAY_STARTS = 4           # hour SUUMO's daily update lands; a crawl day runs from it to the next day's
EVENT_DAYS = 90          # events/runs rows kept this long in the DB (data/events/ in git keeps all)
SUSPECT_DROP = 0.30      # an area losing more than this share at once is distrusted (no removals)
SUSPECT_MIN_HITS = 20    # ...but only when it had at least this many (small areas swing naturally)
TOMBSTONE_DAYS = 14      # a deleted listing's tombstone (ended table) is kept this long
MAX_DETAIL_ATTEMPTS = 3
NET_STREAK = 2           # targets (or areas of one) in a row failing with a network error stop the crawl (or it)
PROGRESS_EVERY = 200      # listing pages between progress lines (also saved to runs.progress for `status`)
CHECKPOINT_SECONDS = 3600  # long runs export data/ this often, so the site shows what's fetched so far
OUTAGE_STREAK = 5          # this many failed listing pages in a row = SUUMO (or the network) is down...
OUTAGE_PAUSE = 600         # ...pause this long; those failures don't count toward MAX_DETAIL_ATTEMPTS

# priorities in the detail queue: listings new since the area's baseline first
P_NEW, P_BACKFILL = 1, 0


class AreaPageError(ValueError):
    """An area page that can't be right: the target is an error, nothing of it is reconciled."""


class Pipeline:
    def __init__(self, db, archive: Archive, client, targets, now: datetime, run_id: str, log=print, saved=()):
        self.db, self.archive, self.client, self.targets = db, archive, client, targets
        self.scope = by_key(targets)
        self.saved = set(saved)  # "type:id" keys on the shared saved list: kept after they're gone
        self.now, self.run_id, self.log = now, run_id, log
        self.ts = now.isoformat(timespec="seconds")
        self.today = now.date().isoformat()
        self.day = now.strftime("%Y%m%d")
        self.crawl_day = (now - timedelta(hours=DAY_STARTS)).date().isoformat()  # one miss per area per crawl day
        self.report = {"targets": [], "detail": {}}

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
        NET_STREAK targets in a row failing with a network error (http.NetworkError: our network, not SUUMO) stop
        the crawl: returns False, and the prefectures not done are left to the next run."""
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
                    self.log(f"\n! {NET_STREAK} targets in a row failed with a network error: the network is down; "
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
            if isinstance(e, NetworkError):
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
                self.log(f"  ! {NET_STREAK} areas in a row failed with a network error; stopping {t.pref}/{t.type}")
                rep["network"] = True
                rep["again"] += areas[k + 1:]
                break
        return rep

    def _area_page(self, t, rep):
        """Fetch and check the target's area page, reconcile the areas it shows without a link (or doesn't show);
        returns the areas to crawl. A page that can't be right (no areas but for new_condo, or far fewer listings in
        the target's areas than held there) raises AreaPageError before anything is reconciled (target = error)."""
        html = self.client.get(f"/{TYPES[t.type]}/{t.pref}/city/")
        self.archive.save_list(t.pref, self.day, t.type, "_city", html)
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
            self.archive.save_list(pref, self.day, type_key, f"{a['slug']}_p{n}", html)
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
            if isinstance(e, NetworkError):
                out["network"] = True
            return out
        complete = not errors and len(recs) >= hits
        return self.reconcile(pref, type_key, a, hits, recs, complete, errors)

    def _leads(self, type_key, recs, rows):
        """wanted(id): whether a listing seen in this crawl should get its page. One listing page per property (the
        only place this rule lives): a group of listings with the same dup_key has one lead, and the other agents'
        copies aren't queued (by the owner's decision, copies are never fetched). Only listings seen in this crawl
        count (a missing one may be ending, and a copy should take over).
          - a member has its page stored: it's the lead; no other member is queued
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
            if any(rows[i] and rows[i]["detail_json"] for i in ids):
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
        rows = {lid: held.get(lid) or self.db.listing(type_key, lid) for lid in recs}  # or moved from another area
        wanted = self._leads(type_key, recs, rows)

        for lid, rec in recs.items():
            row = rows[lid]
            ident = {"type": type_key, "id": lid, "pref": pref, "area_code": a["code"]}
            if row is None:
                tomb = self.db.x("SELECT first_seen, price FROM ended WHERE type=? AND id=?", type_key, lid).fetchone()
                # the same id under another type (by primary key: no scan): land also listed under new_house, or a
                # listing re-typed (new_house -> used_house). Not new; the old row ends after its misses.
                other = None if tomb else next(
                    (r for t in TYPES if t != type_key and (r := self.db.listing(t, lid))), None)
                first = tomb["first_seen"] if tomb else other["first_seen"] if other else self.today
                self.db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen,
                             last_seen) VALUES (?,?,?,?,?,?,?,?)""",
                          type_key, lid, pref, a["code"], a["name"], dumps(rec), first, self.ts)
                if tomb:  # deleted lately and back: relisted (its page was deleted with it: fetched again)
                    self.db.x("DELETE FROM ended WHERE type=? AND id=?", type_key, lid)
                    if baselined:
                        self.db.add_event(self.run_id, "relisted", ident, {"price": rec.get("price")})
                        if tomb["price"] != rec.get("price"):
                            counts["price_changed"] += 1
                            self.db.add_event(self.run_id, "price_changed", ident,
                                              {"price": rec.get("price"), "old_price": tomb["price"]})
                    if wanted(lid):
                        self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
                    counts["relisted"] += 1
                elif other:
                    if baselined and self.db.x(
                            "SELECT 1 FROM events WHERE run_id=? AND type=? AND id=? AND kind IN ('new', 'relisted')",
                            self.run_id, other["type"], lid).fetchone():
                        self.db.add_event(self.run_id, "new", ident, {"price": rec.get("price")})  # both new today
                    if wanted(lid):
                        self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
                    counts["retyped"] += 1
                elif baselined:
                    self.db.add_event(self.run_id, "new", ident, {"price": rec.get("price")})
                    if wanted(lid):
                        self.db.enqueue(type_key, lid, P_NEW, "new", self.ts)
                    counts["new"] += 1
                elif wanted(lid):
                    self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
                continue

            old = json.loads(row["list_json"])
            if row["status"] == "removed":  # kept because saved, and back on SUUMO
                counts["relisted"] += 1
                self.db.add_event(self.run_id, "relisted", ident, {"price": rec.get("price")})
            if old.get("price") != rec.get("price"):
                counts["price_changed"] += 1
                self.db.add_event(self.run_id, "price_changed", ident,
                                  {"price": rec.get("price"), "old_price": old.get("price")})
            if row["detail_json"] is None:  # not stored yet (a failed or interrupted fetch), unless it's a copy
                if wanted(lid):
                    self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
                else:
                    self.db.dequeue(type_key, lid)
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

    # ---------- listing pages ----------

    def process_queue(self, budget_seconds, checkpoint=None):
        """Fetch listing pages, highest priority first, until the queue is empty or the budget is spent.
        checkpoint(): called every CHECKPOINT_SECONDS (the CLI exports data/)."""
        t0 = time.monotonic()
        deadline = t0 + budget_seconds
        last_checkpoint = t0
        done = failed = gone = 0
        streak = []  # consecutive fetch failures
        rows = self._queue_order(self.db.x(
            """SELECT q.type, q.id, q.priority, l.pref, l.area_code, l.list_json, l.status
               FROM queue q LEFT JOIN listings l ON l.type=q.type AND l.id=q.id
               WHERE q.attempts < ? ORDER BY q.priority DESC, q.enqueued_at, q.id""", MAX_DETAIL_ATTEMPTS).fetchall())
        total = len(rows)
        for n, r in enumerate(rows):
            now = time.monotonic()
            if now >= deadline:
                break
            if n and n % PROGRESS_EVERY == 0:
                self._queue_progress(n, total, done, failed, gone, now - t0, deadline - now)
            if checkpoint and now - last_checkpoint >= CHECKPOINT_SECONDS:
                checkpoint()
                last_checkpoint = time.monotonic()
            if r["status"] != "active":  # ended or deleted since it was queued
                self.db.dequeue(r["type"], r["id"])
                continue
            if not in_scope(self.scope, r["pref"], r["type"], r["area_code"]):
                continue  # left for `prune`
            if self._copy_twin(r):  # land listed under new_house too: the same page, already fetched
                done += 1
                continue
            try:
                html = self.client.get(json.loads(r["list_json"])["path"])
            except FileNotFoundError as e:
                # gone from SUUMO (404, or a redirect): not asked for again; the entry goes with the listing when
                # the crawls remove it (re-queueing keeps the attempts). The status and Location are kept.
                self.log(f"  gone: {r['type']}/{r['id']}: {e}")
                self.db.x("UPDATE queue SET attempts=?, last_error=? WHERE type=? AND id=?",
                          MAX_DETAIL_ATTEMPTS, f"gone: {e}"[:300], r["type"], r["id"])
                gone += 1
                streak = []
                continue
            except Exception as e:
                # a network error (http.NetworkError: SUUMO was never reached) isn't the page's fault: no attempt
                counted = not isinstance(e, NetworkError)
                self.db.x("UPDATE queue SET attempts=attempts+?, last_error=? WHERE type=? AND id=?",
                          int(counted), repr(e)[:300], r["type"], r["id"])
                failed += 1
                streak.append((r["type"], r["id"], counted))
                if len(streak) >= OUTAGE_STREAK:
                    self._pause_for_outage(streak, deadline)
                    streak = []
                continue
            streak = []
            self.archive.save_detail(r["pref"], r["type"], r["id"], html)
            try:
                apply_detail(self.db, r["type"], r["id"], html)
                self.db.dequeue(r["type"], r["id"])
                done += 1
            except Exception as e:
                # the page is archived: fix the parser and `reparse` (no re-fetch needed)
                self.log(f"  ! parse failed for {r['type']}/{r['id']}: {e!r}")
                self.db.x("UPDATE queue SET attempts=?, last_error=? WHERE type=? AND id=?",
                          MAX_DETAIL_ATTEMPTS, f"parse: {e!r}"[:300], r["type"], r["id"])
                failed += 1
            self.db.commit()
        self.db.commit()
        remaining = self.db.x("SELECT COUNT(*) FROM queue WHERE attempts < ?", MAX_DETAIL_ATTEMPTS).fetchone()[0]
        self.report["detail"] = {"fetched": done, "failed": failed, "gone": gone, "remaining": remaining}
        self.log(f"\nlisting pages: fetched={done} failed={failed} gone={gone} remaining_in_queue={remaining}")

    def _queue_order(self, rows):
        """The queued listings in fetch order: new ones first, then by prefecture, the one with the most listings
        first (where most people look). Which listings are queued at all is reconcile's decision. Land also listed
        under new_house (one /tochi/ page) waits for its queued twin's page, then copies it (_copy_twin)."""
        size = dict(self.db.x("SELECT pref, COUNT(*) FROM listings WHERE status='active' GROUP BY pref").fetchall())
        pages, ranked = set(), []
        for n, r in enumerate(rows):
            path = (json.loads(r["list_json"]) if r["list_json"] else {}).get("path") or ""
            twin = "/tochi/" in path and path in pages  # its twin is queued before it: that page comes first
            pages.add(path)
            ranked.append((-r["priority"], twin, -size.get(r["pref"], 0), n, r))
        return [r for *_, r in sorted(ranked, key=lambda x: x[:4])]

    def _copy_twin(self, r):
        """Land sold with a build condition is listed under both new_house and land with one page (/tochi/): copy
        the other listing's fetched fields and archived page instead of fetching it again."""
        twin_type = {"land": "new_house", "new_house": "land"}.get(r["type"])
        twin = twin_type and self.db.listing(twin_type, r["id"])
        if not (twin and twin["detail_json"]
                and json.loads(twin["list_json"]).get("path") == json.loads(r["list_json"]).get("path")):
            return False
        self.db.x("UPDATE listings SET detail_json=? WHERE type=? AND id=?", twin["detail_json"], r["type"], r["id"])
        page = self.archive.detail_path(twin["pref"], twin_type, r["id"])
        if page.exists():
            self.archive.save_detail(r["pref"], r["type"], r["id"], Archive.read(page))
        self.db.dequeue(r["type"], r["id"])
        self.db.commit()
        return True

    def _queue_progress(self, n, total, done, failed, gone, elapsed, budget_left):
        per = elapsed / n
        eta = (total - n) * per
        text = (f"{n:,}/{total:,} ({100 * n / total:.1f}%) · {per:.2f} s/page · "
                f"ETA {_hm(min(eta, budget_left))}{' (budget ends first)' if budget_left < eta else ''} · "
                f"fetched {done:,} failed {failed:,} gone {gone:,} · delay {self.client.delay:.2f}s")
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
        self.db.x("DELETE FROM listings WHERE type=? AND id=?", type_key, lid)
        self.db.dequeue(type_key, lid)
        self.archive.delete_detail(pref, type_key, lid)

    def purge(self):
        """Ended listings no longer saved (deleted, leaving a tombstone dated their removal, as reconcile's deletes
        do), old events/runs rows, old search-result snapshots."""
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
        for pref in {t.pref for t in self.targets}:
            self.archive.prune_lists(pref, self.now.date())
        self.db.commit()


def _hm(seconds):
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def apply_detail(db, type_key, lid, html):
    """Parse a listing page into the DB row (used by the queue and by `reparse`)."""
    db.x("UPDATE listings SET detail_json=? WHERE type=? AND id=?", dumps(parse_detail(html)), type_key, lid)
