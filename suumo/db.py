"""Local SQLite state: listings and their lifecycle, per-area crawl state, the detail queue, events, runs.

Not in git. The git-tracked JSONL files are exported from here (export.py).
"""
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    type TEXT NOT NULL, id TEXT NOT NULL,
    pref TEXT NOT NULL, area_code TEXT NOT NULL, area_name TEXT,
    list_json TEXT NOT NULL,          -- fields from the search-result page (refreshed every crawl)
    detail_json TEXT,                 -- fields from the listing's own page (fetched once, refreshed on change)
    status TEXT NOT NULL DEFAULT 'active',   -- active | removed
    first_seen TEXT NOT NULL,         -- YYYY-MM-DD
    last_seen TEXT NOT NULL,          -- ISO timestamp of the last crawl that saw it
    missed INTEGER NOT NULL DEFAULT 0,       -- consecutive complete crawls of its area that didn't see it
    removed_at TEXT,
    detail_fetched TEXT, info_date TEXT, next_update TEXT,
    PRIMARY KEY (type, id)
);
CREATE INDEX IF NOT EXISTS listings_area ON listings (pref, type, area_code, status);

CREATE TABLE IF NOT EXISTS areas (
    pref TEXT NOT NULL, type TEXT NOT NULL, code TEXT NOT NULL,
    name TEXT, slug TEXT,
    baselined INTEGER NOT NULL DEFAULT 0,   -- 1 once a complete crawl established its starting set
    last_hits INTEGER, last_status TEXT, last_crawled TEXT,
    PRIMARY KEY (pref, type, code)
);

CREATE TABLE IF NOT EXISTS queue (
    type TEXT NOT NULL, id TEXT NOT NULL,
    priority INTEGER NOT NULL,        -- 2 new in a tracked area, 1 changed listing, 0 backfill
    reason TEXT NOT NULL, enqueued_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
    PRIMARY KEY (type, id)
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, kind TEXT NOT NULL,   -- new | price_changed | removed | relisted
    type TEXT NOT NULL, id TEXT NOT NULL, pref TEXT NOT NULL, area_code TEXT NOT NULL,
    payload TEXT NOT NULL, posted INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started TEXT, finished TEXT, report TEXT
);
"""


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def x(self, sql, *args):
        return self.conn.execute(sql, args)

    def commit(self):
        self.conn.commit()

    # listings
    def listing(self, type_key, lid):
        return self.x("SELECT * FROM listings WHERE type=? AND id=?", type_key, lid).fetchone()

    def area_listings(self, pref, type_key, code, status="active"):
        return self.x("SELECT * FROM listings WHERE pref=? AND type=? AND area_code=? AND status=?",
                      pref, type_key, code, status).fetchall()

    # areas
    def area(self, pref, type_key, code):
        return self.x("SELECT * FROM areas WHERE pref=? AND type=? AND code=?", pref, type_key, code).fetchone()

    def upsert_area(self, pref, type_key, a):
        self.x("""INSERT INTO areas (pref, type, code, name, slug) VALUES (?,?,?,?,?)
                  ON CONFLICT(pref, type, code) DO UPDATE SET name=excluded.name, slug=excluded.slug""",
               pref, type_key, a["code"], a["name"], a["slug"])

    # queue
    def enqueue(self, type_key, lid, priority, reason, ts):
        """Insert, or raise the priority of an existing entry."""
        self.x("""INSERT INTO queue (type, id, priority, reason, enqueued_at) VALUES (?,?,?,?,?)
                  ON CONFLICT(type, id) DO UPDATE SET
                    priority=MAX(priority, excluded.priority),
                    reason=CASE WHEN excluded.priority > priority THEN excluded.reason ELSE reason END""",
               type_key, lid, priority, reason, ts)

    # events
    def add_event(self, run_id, kind, row_or_rec, payload):
        self.x("INSERT INTO events (run_id, kind, type, id, pref, area_code, payload) VALUES (?,?,?,?,?,?,?)",
               run_id, kind, row_or_rec["type"], row_or_rec["id"], row_or_rec["pref"], row_or_rec["area_code"],
               json.dumps(payload, ensure_ascii=False))
