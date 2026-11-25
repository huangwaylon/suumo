"""Local SQLite state: listings and their lifecycle, per-area crawl state, the detail queue, events, runs.

Not in git. The git-tracked JSONL files are exported from here (export.py).
"""
import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    type TEXT NOT NULL, id TEXT NOT NULL,
    pref TEXT NOT NULL, area_code TEXT NOT NULL, area_name TEXT,
    list_json TEXT NOT NULL,          -- fields from the search-result page (refreshed every crawl)
    detail_json TEXT,                 -- fields derived from page_json (detail.derive; a cache, re-derived on a
                                      -- PARSE_VERSION bump)
    page_json TEXT,                   -- the listing page's record (extract.extract); set only by pipeline.store_page
    extract_v INTEGER,                -- extract.EXTRACT_VERSION that made page_json
    fetched_at TEXT,                  -- ISO timestamp the page was fetched
    status TEXT NOT NULL DEFAULT 'active',   -- active | removed (ended on SUUMO, kept because saved)
    first_seen TEXT NOT NULL,         -- YYYY-MM-DD
    last_seen TEXT NOT NULL,          -- ISO timestamp of the last crawl that saw it
    missed INTEGER NOT NULL DEFAULT 0,       -- consecutive complete crawls of its area that didn't see it
    removed_at TEXT,
    PRIMARY KEY (type, id)
);
CREATE INDEX IF NOT EXISTS listings_area ON listings (pref, type, area_code, status);

CREATE TABLE IF NOT EXISTS areas (
    pref TEXT NOT NULL, type TEXT NOT NULL, code TEXT NOT NULL,
    name TEXT, slug TEXT,
    baselined INTEGER NOT NULL DEFAULT 0,   -- 1 once a complete crawl established its starting set
    last_hits INTEGER, last_status TEXT,
    missed_on TEXT,                         -- the crawl day (YYYY-MM-DD) whose misses were counted
    PRIMARY KEY (pref, type, code)
);

CREATE TABLE IF NOT EXISTS queue (
    type TEXT NOT NULL, id TEXT NOT NULL,
    priority INTEGER NOT NULL,        -- 2 new since the area's baseline, 1 changed (relisted, or a price change
                                      -- on a stale page), 0 backfill and slow refresh (pipeline.P_*)
    reason TEXT NOT NULL, enqueued_at TEXT NOT NULL,  -- reason: unused (older code wrote new | backfill)
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
    PRIMARY KEY (type, id)
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL, kind TEXT NOT NULL,   -- new | price_changed | removed | relisted
    type TEXT NOT NULL, id TEXT NOT NULL, pref TEXT NOT NULL, area_code TEXT NOT NULL,
    payload TEXT NOT NULL                       -- {"price"} (+ "old_price" for price_changed)
);

CREATE TABLE IF NOT EXISTS prefs (           -- when each prefecture's search results were last crawled
    pref TEXT PRIMARY KEY, crawled_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started TEXT, finished TEXT, report TEXT
);
"""


def dumps(obj):
    """Canonical compact JSON (sorted keys) so equal data is equal text."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@contextmanager
def flock(lock_path):
    """Try to hold an exclusive lock on lock_path for the duration, without waiting: yields whether it's held."""
    with open(lock_path, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


@contextmanager
def exclusive(lock_path):
    """Hold an exclusive lock for the duration; a second writer exits instead of interleaving."""
    with flock(lock_path) as held:
        if not held:
            raise SystemExit(f"another suumo process is running ({lock_path} is locked)")
        yield


def locked(lock_path):
    """True while another process holds lock_path (a writing command holds the DB's lock)."""
    if not Path(lock_path).exists():
        return False
    with flock(lock_path) as held:
        return not held


# Additions to databases created by older code, applied by `migrate` (writers only): tables, then columns.
TABLES = """
CREATE TABLE IF NOT EXISTS ended (           -- tombstones of deleted listings, so a return is relisted, not new
    type TEXT NOT NULL, id TEXT NOT NULL, pref TEXT NOT NULL,
    first_seen TEXT NOT NULL, price INTEGER, ended_at TEXT NOT NULL,   -- ended_at: ISO timestamp of the removal
    PRIMARY KEY (type, id)
);
CREATE INDEX IF NOT EXISTS events_run ON events (run_id, type, id);   -- a run's events (export, the retyped check)
CREATE TABLE IF NOT EXISTS meta (            -- parse_v: detail.PARSE_VERSION of detail_json; backfilled: set once
    key TEXT PRIMARY KEY, value TEXT         -- every lead's page record was filled (migration or seed)
);
"""
COLUMNS = [("areas", "missed_on", "TEXT"),
           ("runs", "progress", "TEXT"),  # (table, column, type). progress: JSON {phase, text, at} while running
           ("listings", "page_json", "TEXT"),
           ("listings", "extract_v", "INTEGER"),
           ("listings", "fetched_at", "TEXT")]


QUEUE_V = 2  # meta queue_v: the queue's priorities are P_NEW 2 / P_CHANGED 1 / P_BACKFILL 0 (migrate.migrate_state)
# a listing's columns without the page record: what lifecycle code reads (fetched = holds a page record)
LIGHT = ("type, id, pref, area_code, area_name, list_json, status, first_seen, last_seen, missed, removed_at, "
         "extract_v, fetched_at, page_json IS NOT NULL AS fetched")


class DB:
    def __init__(self, path, migrate=True):
        """migrate=False for readers (`status`): they never alter a database a run may be writing; writing commands
        call migrate() once they hold the lock."""
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        fresh = not self.x("SELECT 1 FROM sqlite_master WHERE type='table' AND name='listings'").fetchone()
        self.conn.executescript(SCHEMA)
        if fresh:  # a new state: its queue priorities are the current ones (migrate-state must never remap them)
            self.conn.executescript(TABLES)
            self.set_meta("queue_v", QUEUE_V)
            self.commit()
        if migrate:
            self.migrate()

    def migrate(self):
        """Bring an older database up to date: additive and idempotent (old code keeps working on the result)."""
        self.conn.executescript(TABLES)
        for table, column, kind in COLUMNS:
            if column not in {r["name"] for r in self.x(f"PRAGMA table_info({table})")}:
                self.x(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        self.commit()

    def x(self, sql, *args):
        return self.conn.execute(sql, args)

    def commit(self):
        self.conn.commit()

    # meta
    def meta(self, key):
        row = self.x("SELECT value FROM meta WHERE key=?", key).fetchone()
        return row["value"] if row else None

    def set_meta(self, key, value):
        self.x("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", key, str(value))

    # listings
    def listing(self, type_key, lid, light=False):
        """The whole row, or with light only LIGHT's columns (no page record or derived fields)."""
        return self.x(f"SELECT {LIGHT if light else '*'} FROM listings WHERE type=? AND id=?", type_key, lid).fetchone()

    def area_listings(self, pref, type_key, code):
        """An area's listings, LIGHT columns."""
        return self.x(f"SELECT {LIGHT} FROM listings WHERE pref=? AND type=? AND area_code=?",
                      pref, type_key, code).fetchall()

    # areas
    def area(self, pref, type_key, code):
        return self.x("SELECT * FROM areas WHERE pref=? AND type=? AND code=?", pref, type_key, code).fetchone()

    def upsert_area(self, pref, type_key, a):
        self.x("""INSERT INTO areas (pref, type, code, name, slug) VALUES (?,?,?,?,?)
                  ON CONFLICT(pref, type, code) DO UPDATE SET name=excluded.name, slug=excluded.slug""",
               pref, type_key, a["code"], a["name"], a["slug"])

    # queue
    def enqueue(self, type_key, lid, priority, ts):
        """Insert, or raise the priority of an existing entry. (reason is unused: older code wrote new/backfill.)"""
        self.x("""INSERT INTO queue (type, id, priority, reason, enqueued_at) VALUES (?,?,?,'',?)
                  ON CONFLICT(type, id) DO UPDATE SET priority=MAX(priority, excluded.priority)""",
               type_key, lid, priority, ts)

    def dequeue(self, type_key, lid):
        self.x("DELETE FROM queue WHERE type=? AND id=?", type_key, lid)

    # events
    def add_event(self, run_id, kind, ident, payload):
        """ident: anything with type, id, pref, area_code (a listings row or a dict)."""
        self.x("INSERT INTO events (run_id, kind, type, id, pref, area_code, payload) VALUES (?,?,?,?,?,?,?)",
               run_id, kind, ident["type"], ident["id"], ident["pref"], ident["area_code"], dumps(payload))
