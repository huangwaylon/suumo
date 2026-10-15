"""Crawl search results, reconcile each area's listings, fetch listing pages from the queue, clean up.

A run crawls the search results (once a day with `--crawl-at`) and diffs them against the DB. Lifecycle of a listing:
  first seen -> stored; its listing page is fetched once (never again: later crawls refresh the search-result
    fields, so price changes still show)
  missing from REMOVE_AFTER_MISSES consecutive complete crawls of its area -> `removed` event, then
    saved (on the shared list, saved.json): kept as status 'removed' (the site shows it as ended)
    not saved: deleted (row, queue entry, archived page; git history keeps the data)
  a kept listing seen again -> active (relisted event); one no longer saved -> deleted by `purge`
  a deleted listing seen again within TOMBSTONE_DAYS -> back with its first_seen (relisted event, not new)
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timedelta

from .archive import Archive
from .db import dumps
from .detail import parse_detail
from .export import dup_key
from .parse import PAGE_SIZE, TYPES, page_count, parse_areas, parse_list_page
from .scope import by_key, in_scope

REMOVE_AFTER_MISSES = 2  # consecutive complete crawls, on different crawl days, that don't see a listing
DAY_STARTS = 4           # hour SUUMO's daily update lands; a crawl day runs from it to the next day's
EVENT_DAYS = 90          # events/runs rows kept this long in the DB (data/events/ in git keeps all)
SUSPECT_DROP = 0.30      # an area losing more than this share at once is distrusted (no removals)
SUSPECT_MIN_HITS = 20    # ...but only when it had at least this many (small areas swing naturally)
TOMBSTONE_DAYS = 14      # a deleted listing's tombstone (ended table) is kept this long
MAX_DETAIL_ATTEMPTS = 3
PROGRESS_EVERY = 200      # listing pages between progress lines (also saved to the run's report for `status`)
CHECKPOINT_SECONDS = 3600  # long runs export data/ this often, so the site shows what's fetched so far
OUTAGE_STREAK = 5          # this many failed listing pages in a row = SUUMO (or the network) is down...
OUTAGE_PAUSE = 600         # ...pause this long; those failures don't count toward MAX_DETAIL_ATTEMPTS

# priorities in the detail queue: listings new since the area's baseline first
P_NEW, P_BACKFILL = 1, 0


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
        """Put progress in the run's report so `status` (another process) can show it while the run goes on."""
        at = datetime.now(self.now.tzinfo).isoformat(timespec="seconds")
        self.report["progress"] = {"phase": phase, "text": text, "at": at}
        self.db.x("UPDATE runs SET report=? WHERE run_id=?", json.dumps(self.report, ensure_ascii=False), self.run_id)
        self.db.commit()

    # ---------- search results ----------

    def crawl_lists(self):
        for t in self.targets:
            self.log(f"\n=== {t.pref} / {t.type} ===")
            try:
                rep = self._crawl_target(t)
            except Exception as e:  # e.g. the area page itself failed; other targets still run
                self.log(f"  ERROR {e!r}")
                rep = {"pref": t.pref, "type": t.type, "error": repr(e)}
            self.report["targets"].append(rep)
            self.db.commit()
        for pref in dict.fromkeys(t.pref for t in self.targets):
            self.db.x("INSERT OR REPLACE INTO prefs (pref, crawled_at) VALUES (?, ?)", pref, self.ts)
        self.db.commit()

    def _crawl_target(self, t):
        html = self.client.get(f"/{TYPES[t.type]}/{t.pref}/city/")
        self.archive.save_list(t.pref, self.day, t.type, "_city", html)
        areas = [a for a in parse_areas(html, TYPES[t.type], t.pref) if t.includes(a["code"])]
        rep = {"pref": t.pref, "type": t.type, "areas": []}

        # An area can drop off the area page entirely (e.g. new_condo lists only areas that currently have
        # developments). Treat it as crawled with zero listings so what we hold there is reconciled too.
        listed = {a["code"] for a in areas}
        held = {r["area_code"]: r["area_name"] for r in self.db.x(
            "SELECT DISTINCT area_code, area_name FROM listings WHERE pref=? AND type=? AND status='active'",
            t.pref, t.type) if t.includes(r["area_code"])}
        for code in sorted((set(held) | (t.areas or set())) - listed):
            a = {"code": code, "name": held.get(code, code), "slug": None, "expected": 0}
            if code in held:
                self.db.upsert_area(t.pref, t.type, a)
                rep["areas"].append(self.reconcile(t.pref, t.type, a, 0, {}, True))
            else:
                self.log(f"  {code:<10} not on the area page (no listings, or a wrong code)")
                rep["areas"].append({"code": code, "status": "not_listed"})

        for k, a in enumerate(areas):
            self.save_progress("search results", f"{t.pref}/{t.type}: area {k + 1}/{len(areas)} ({a['name']})")
            self.db.upsert_area(t.pref, t.type, a)
            if not a["slug"]:
                if a["code"] in held and a["expected"] == 0:  # listed as "(0)" without a link: crawled, 0 hits
                    rep["areas"].append(self.reconcile(t.pref, t.type, a, 0, {}, True))
                    self.db.commit()
                    continue
                status = "empty" if a["expected"] == 0 else "no_slug"
                rep["areas"].append({"code": a["code"], "name": a["name"], "status": status})
                continue
            rep["areas"].append(self.crawl_area(t.pref, t.type, a))
            self.db.commit()
        return rep

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
            return {"code": a["code"], "name": a["name"], "status": "error", "error": repr(e)}
        complete = not errors and len(recs) >= hits
        return self.reconcile(pref, type_key, a, hits, recs, complete, errors)

    def reconcile(self, pref, type_key, a, hits, recs, complete, errors=()):
        """Apply one area's crawl result to the DB: inserts, events, queue, misses/removals, area state."""
        area_row = self.db.area(pref, type_key, a["code"])
        baselined = bool(area_row["baselined"])
        prev_hits = area_row["last_hits"]
        suspect = (complete and prev_hits is not None and prev_hits >= SUSPECT_MIN_HITS
                   and hits < prev_hits * (1 - SUSPECT_DROP))
        counts = {"new": 0, "price_changed": 0, "relisted": 0, "removed": 0, "missing": 0}
        held = {r["id"]: r for r in self.db.area_listings(pref, type_key, a["code"])}
        # One listing page per property (the only place this rule lives): another agent's listing of one that has
        # (or is getting) its page isn't queued. Only listings seen in this crawl count (a missing one may be
        # ending, and a copy should take over). A group in which an agent appears twice isn't one property
        # (an agent doesn't list the same one twice): every listing in it gets its page.
        agents = defaultdict(list)
        for rec in recs.values():
            if (k := dup_key({**rec, "type": type_key})) and rec.get("agent"):
                agents[k].append(rec["agent"])
        distinct = {k for k, names in agents.items() if len(names) != len(set(names))}
        claimed = {dup_key({**json.loads(r["list_json"]), "type": type_key})
                   for r in held.values() if r["detail_json"] and r["id"] in recs} - {None}

        def wanted(rec):
            k = dup_key({**rec, "type": type_key})
            if k in distinct:
                return True
            if k in claimed:
                return False
            if k:
                claimed.add(k)
            return True

        for lid, rec in recs.items():
            row = held.get(lid) or self.db.listing(type_key, lid)  # fallback: moved from another area
            ident = {"type": type_key, "id": lid, "pref": pref, "area_code": a["code"]}
            if row is None:
                tomb = self.db.x("SELECT first_seen, price FROM ended WHERE type=? AND id=?", type_key, lid).fetchone()
                self.db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen,
                             last_seen) VALUES (?,?,?,?,?,?,?,?)""",
                          type_key, lid, pref, a["code"], a["name"], dumps(rec),
                          tomb["first_seen"] if tomb else self.today, self.ts)
                if tomb:  # deleted lately and back: relisted (its page was deleted with it: fetched again)
                    self.db.x("DELETE FROM ended WHERE type=? AND id=?", type_key, lid)
                    if baselined:
                        self.db.add_event(self.run_id, "relisted", ident, {"price": rec.get("price")})
                        if tomb["price"] != rec.get("price"):
                            counts["price_changed"] += 1
                            self.db.add_event(self.run_id, "price_changed", ident,
                                              {"price": rec.get("price"), "old_price": tomb["price"]})
                    if wanted(rec):
                        self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
                    counts["relisted"] += 1
                elif baselined:
                    self.db.add_event(self.run_id, "new", ident, {"price": rec.get("price")})
                    if wanted(rec):
                        self.db.enqueue(type_key, lid, P_NEW, "new", self.ts)
                    counts["new"] += 1
                elif wanted(rec):
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
                if wanted(rec):
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
            except FileNotFoundError:
                # gone from SUUMO; the next search-result crawls will delete it
                self.db.dequeue(r["type"], r["id"])
                gone += 1
                streak = []
                continue
            except Exception as e:
                self.db.x("UPDATE queue SET attempts=attempts+1, last_error=? WHERE type=? AND id=?",
                          repr(e)[:300], r["type"], r["id"])
                failed += 1
                streak.append((r["type"], r["id"]))
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
        self.report.pop("progress", None)
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
        for type_key, lid in streak:
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
        """Ended listings no longer saved, old events/runs rows, old search-result snapshots."""
        unsaved = [r for r in self.db.x("SELECT type, id, pref FROM listings WHERE status='removed'")
                   if f"{r['type']}:{r['id']}" not in self.saved]
        for r in unsaved:
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
