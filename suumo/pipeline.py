"""Crawl search results, reconcile each area's listings, fetch listing pages from the queue, clean up.

Lifecycle of a listing:
  new (enqueued for its detail page) -> active
  active -> missing from REMOVE_AFTER_MISSES consecutive complete crawls of its area -> removed (event)
  removed -> seen again -> active (relisted event)
  removed for PURGE_DAYS -> purged (row, queue entry and archived page deleted; git history keeps it)
"""
import json
import time
from datetime import datetime, timedelta

from .archive import Archive
from .detail import parse_detail
from .parse import TYPES, PAGE_SIZE, page_count, parse_areas, parse_list_page
from .scope import in_scope

REMOVE_AFTER_MISSES = 2
PURGE_DAYS = 30
SUSPECT_DROP = 0.30      # an area losing more than this share at once is distrusted (no removals)
SUSPECT_MIN_HITS = 20    # ...but only when it had at least this many (small areas swing naturally)
MAX_DETAIL_ATTEMPTS = 3

# priorities in the detail queue
P_NEW, P_CHANGED, P_BACKFILL = 2, 1, 0

# list-page fields whose change means the listing page is worth re-fetching
CHANGE_FIELDS = ("price", "price_max", "title", "layout", "floor_m2", "land_m2", "building_m2")


def summary(type_key, rec, area_name):
    """Small snapshot stored with events so a notification can be written even after the row is purged."""
    keys = ("name", "title", "price", "price_max", "layout", "floor_m2", "land_m2", "building_m2", "built",
            "town", "stations", "path")
    return {"area": area_name, **{k: rec[k] for k in keys if k in rec}}


class Pipeline:
    def __init__(self, db, archive: Archive, client, targets, now: datetime, run_id: str, log=print):
        self.db, self.archive, self.client, self.targets = db, archive, client, targets
        self.now, self.run_id, self.log = now, run_id, log
        self.ts = now.isoformat(timespec="seconds")
        self.today = now.date().isoformat()
        self.day = now.strftime("%Y%m%d")
        self.report = {"targets": [], "detail": {}, "purged": 0}

    # ---------- search results ----------

    def crawl_lists(self):
        for t in self.targets:
            self.log(f"\n=== {t.pref} / {t.type} ===")
            html = self.client.get(f"/{TYPES[t.type]}/{t.pref}/city/")
            self.archive.save_list(t.pref, self.day, t.type, "_city", html)
            areas = [a for a in parse_areas(html, TYPES[t.type], t.pref) if t.includes(a["code"])]
            if t.areas:
                missing = t.areas - {a["code"] for a in areas}
                if missing:
                    self.log(f"  ! area codes not found for {t.pref}/{t.type}: {sorted(missing)}")
            rep = {"pref": t.pref, "type": t.type, "areas": []}
            for a in areas:
                self.db.upsert_area(t.pref, t.type, a)
                if not a["slug"]:
                    status = "empty" if a["expected"] == 0 else "no_slug"
                    rep["areas"].append({"code": a["code"], "name": a["name"], "status": status})
                    continue
                rep["areas"].append(self.crawl_area(t.pref, t.type, a))
                self.db.commit()
            self.report["targets"].append(rep)

    def _fetch_area(self, pref, type_key, a):
        path = f"/{TYPES[type_key]}/{pref}/{a['slug']}/?pc={PAGE_SIZE}"
        recs, errors = {}, []
        html = self.client.get(path)
        self.archive.save_list(pref, self.day, type_key, f"{a['slug']}_p1", html)
        hits, first = parse_list_page(html, type_key, a)
        recs.update((r["id"], r) for r in first)
        for page in range(2, page_count(hits) + 1):
            try:
                html = self.client.get(f"{path}&page={page}")
                self.archive.save_list(pref, self.day, type_key, f"{a['slug']}_p{page}", html)
                _, more = parse_list_page(html, type_key, a)
                recs.update((r["id"], r) for r in more)
                if not more:
                    errors.append(f"page {page} empty")
            except Exception as e:  # the area is just marked incomplete
                errors.append(f"page {page}: {e!r}")
        return hits, recs, errors

    def crawl_area(self, pref, type_key, a):
        try:
            hits, recs, errors = self._fetch_area(pref, type_key, a)
            if len(recs) < hits or errors:
                # listings shift between pages while paginating; a second pass (unioned) closes the gap
                h2, recs2, errors = self._fetch_area(pref, type_key, a)
                recs2.update(recs)
                hits, recs = h2, recs2
        except Exception as e:
            self.log(f"  {a['name']:<10} ERROR {e!r}")
            self.db.x("UPDATE areas SET last_status='error', last_crawled=? WHERE pref=? AND type=? AND code=?",
                      self.ts, pref, type_key, a["code"])
            return {"code": a["code"], "name": a["name"], "status": "error", "error": repr(e)}
        complete = not errors and len(recs) >= hits
        return self.reconcile(pref, type_key, a, hits, recs, complete, errors)

    def reconcile(self, pref, type_key, a, hits, recs, complete, errors=()):
        area_row = self.db.area(pref, type_key, a["code"])
        baselined = bool(area_row["baselined"])
        prev_hits = area_row["last_hits"]
        suspect = (complete and prev_hits is not None and prev_hits >= SUSPECT_MIN_HITS
                   and hits < prev_hits * (1 - SUSPECT_DROP))
        counts = {"new": 0, "price_changed": 0, "relisted": 0, "removed": 0, "missing": 0}

        for lid, rec in recs.items():
            row = self.db.listing(type_key, lid)
            ident = {"type": type_key, "id": lid, "pref": pref, "area_code": a["code"]}
            if row is None:
                self.db.x("""INSERT INTO listings (type, id, pref, area_code, area_name, list_json, first_seen, last_seen)
                             VALUES (?,?,?,?,?,?,?,?)""",
                          type_key, lid, pref, a["code"], a["name"], _dumps(rec), self.today, self.ts)
                if baselined:
                    self.db.add_event(self.run_id, "new", ident, summary(type_key, rec, a["name"]))
                    self.db.enqueue(type_key, lid, P_NEW, "new", self.ts)
                    counts["new"] += 1
                else:
                    self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
                continue

            old = json.loads(row["list_json"])
            if row["status"] == "removed":
                counts["relisted"] += 1
                self.db.add_event(self.run_id, "relisted", ident, summary(type_key, rec, a["name"]))
            if old.get("price") != rec.get("price"):
                counts["price_changed"] += 1
                self.db.add_event(self.run_id, "price_changed", ident,
                                  {**summary(type_key, rec, a["name"]), "old_price": old.get("price")})
            if any(old.get(k) != rec.get(k) for k in CHANGE_FIELDS):
                self.db.enqueue(type_key, lid, P_CHANGED, "changed", self.ts)
            elif row["detail_json"] is None:
                self.db.enqueue(type_key, lid, P_BACKFILL, "backfill", self.ts)
            self.db.x("""UPDATE listings SET list_json=?, area_code=?, area_name=?, pref=?, status='active',
                         removed_at=NULL, missed=0, last_seen=? WHERE type=? AND id=?""",
                      _dumps(rec), a["code"], a["name"], pref, self.ts, type_key, lid)

        if complete and not suspect:
            for row in self.db.area_listings(pref, type_key, a["code"]):
                if row["id"] in recs:
                    continue
                missed = row["missed"] + 1
                if missed >= REMOVE_AFTER_MISSES:
                    self.db.x("UPDATE listings SET status='removed', removed_at=?, missed=? WHERE type=? AND id=?",
                              self.ts, missed, type_key, row["id"])
                    self.db.x("DELETE FROM queue WHERE type=? AND id=?", type_key, row["id"])
                    self.db.add_event(self.run_id, "removed", row,
                                      summary(type_key, json.loads(row["list_json"]), row["area_name"]))
                    counts["removed"] += 1
                else:
                    self.db.x("UPDATE listings SET missed=? WHERE type=? AND id=?", missed, type_key, row["id"])
                    counts["missing"] += 1

        status = "suspect" if suspect else ("complete" if complete else "incomplete")
        self.db.x("""UPDATE areas SET last_hits=?, last_status=?, last_crawled=?, baselined=?
                     WHERE pref=? AND type=? AND code=?""",
                  hits if not suspect else prev_hits, status, self.ts,
                  1 if (baselined or (complete and not suspect)) else 0, pref, type_key, a["code"])
        line = f"  {a['name']:<10} hits={hits:>5} parsed={len(recs):>5} {status}"
        if not baselined and complete:
            line += " (baseline)"
        line += "".join(f" {k}={v}" for k, v in counts.items() if v)
        if errors:
            line += f" errors={list(errors)[:2]}"
        self.log(line)
        return {"code": a["code"], "name": a["name"], "status": status, "hits": hits, "parsed": len(recs),
                "baseline": not baselined and complete, **counts}

    # ---------- listing pages ----------

    def process_queue(self, budget_seconds):
        """Fetch listing pages, highest priority first, until the queue is empty or the budget is spent."""
        deadline = time.monotonic() + budget_seconds
        done = failed = gone = 0
        rows = self.db.x("""SELECT q.type, q.id, l.pref, l.area_code, l.list_json, l.status
                            FROM queue q LEFT JOIN listings l ON l.type=q.type AND l.id=q.id
                            WHERE q.attempts < ? ORDER BY q.priority DESC, q.enqueued_at, q.id""",
                         MAX_DETAIL_ATTEMPTS).fetchall()
        for r in rows:
            if time.monotonic() >= deadline:
                break
            if r["status"] != "active":  # removed (or purged) since it was queued
                self.db.x("DELETE FROM queue WHERE type=? AND id=?", r["type"], r["id"])
                continue
            if not in_scope(self.targets, r["pref"], r["type"], r["area_code"]):
                continue  # left for `prune`
            try:
                html = self.client.get(json.loads(r["list_json"])["path"])
            except FileNotFoundError:
                # gone from SUUMO; the next search-result crawls will mark it removed
                self.db.x("DELETE FROM queue WHERE type=? AND id=?", r["type"], r["id"])
                gone += 1
                continue
            except Exception as e:
                self.db.x("UPDATE queue SET attempts=attempts+1, last_error=? WHERE type=? AND id=?",
                          repr(e)[:300], r["type"], r["id"])
                failed += 1
                continue
            self.archive.save_detail(r["pref"], r["type"], r["id"], html)
            try:
                self._apply_detail(r["type"], r["id"], html)
            except Exception as e:
                # the page is archived; fix the parser and `reparse` (no re-fetch needed)
                self.log(f"  ! parse failed for {r['type']}/{r['id']}: {e!r}")
                self.db.x("UPDATE queue SET attempts=?, last_error=? WHERE type=? AND id=?",
                          MAX_DETAIL_ATTEMPTS, f"parse: {e!r}"[:300], r["type"], r["id"])
                self.db.commit()
                failed += 1
                continue
            self.db.x("DELETE FROM queue WHERE type=? AND id=?", r["type"], r["id"])
            self.db.commit()
            done += 1
        self.db.commit()
        remaining = self.db.x("SELECT COUNT(*) FROM queue WHERE attempts < ?", MAX_DETAIL_ATTEMPTS).fetchone()[0]
        self.report["detail"] = {"fetched": done, "failed": failed, "gone": gone, "remaining": remaining}
        self.log(f"\nlisting pages: fetched={done} failed={failed} gone={gone} remaining_in_queue={remaining}")

    def _apply_detail(self, type_key, lid, html):
        detail, meta = parse_detail(html)
        self.db.x("""UPDATE listings SET detail_json=?, detail_fetched=?, info_date=?, next_update=?
                     WHERE type=? AND id=?""",
                  _dumps(detail), self.today, meta.get("info_date"), meta.get("next_update"), type_key, lid)

    # ---------- cleanup ----------

    def purge(self):
        cutoff = (self.now - timedelta(days=PURGE_DAYS)).isoformat(timespec="seconds")
        rows = self.db.x("SELECT type, id, pref FROM listings WHERE status='removed' AND removed_at < ?",
                         cutoff).fetchall()
        for r in rows:
            self.db.x("DELETE FROM listings WHERE type=? AND id=?", r["type"], r["id"])
            self.db.x("DELETE FROM queue WHERE type=? AND id=?", r["type"], r["id"])
            self.archive.delete_detail(r["pref"], r["type"], r["id"])
        for pref in {t.pref for t in self.targets}:
            self.archive.prune_lists(pref, self.now.date())
        self.db.commit()
        self.report["purged"] = len(rows)
        if rows:
            self.log(f"purged {len(rows)} listings removed more than {PURGE_DAYS} days ago")


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
