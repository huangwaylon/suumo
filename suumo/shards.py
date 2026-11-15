"""Crawl workers in parallel (GitHub Actions): shards.toml gives each worker a fixed set of prefectures; its state is
its own SQLite file holding only those (state-<n>.db). Nothing a run does crosses prefectures (listings, areas, the
queue, events, tombstones are all one prefecture's; duplicates are grouped by address), so a worker needs no other
worker's state, and its data/ files (data/<pref>/..., data/events/<run_id>-sNN.json) are its own.

  split: one state.db -> state-<n>.db per shard (the move from one crawler to many)
  join:  state-<n>.db files -> one state.db (back to one crawler)
"""
import sqlite3
import tomllib
from pathlib import Path

# tables with a pref column, split by it; queue rows follow their listing; runs go to every shard
BY_PREF = ("listings", "areas", "events", "prefs", "ended")


def load(path, scope_prefs=None):
    """shards.toml -> [[pref, ...], ...]; with scope_prefs, checks every one is in exactly one shard."""
    with open(path, "rb") as f:
        shards = tomllib.load(f)["shards"]
    seen = {}
    for n, prefs in enumerate(shards):
        for p in prefs:
            if p in seen:
                raise SystemExit(f"{path}: {p} is in shards {seen[p]} and {n}")
            seen[p] = n
    if scope_prefs is not None:
        missing, extra = set(scope_prefs) - set(seen), set(seen) - set(scope_prefs)
        if missing or extra:
            raise SystemExit(f"{path}: not in any shard: {sorted(missing)}; not in scope: {sorted(extra)}")
    return shards


def suffix(n):
    """A worker's run ids end in this, so its data/events files never collide with another's."""
    return f"-s{n:02d}"


def _copy_schema(src, dst):
    for (sql,) in src.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
                              "ORDER BY type = 'index'"):  # tables first
        dst.execute(sql)
    dst.execute(f"PRAGMA user_version = {src.execute('PRAGMA user_version').fetchone()[0]}")


def _tables(conn, schema="main"):
    return [r[0] for r in conn.execute(f"SELECT name FROM {schema}.sqlite_master WHERE type='table' "
                                       "AND name NOT LIKE 'sqlite_%'")]


def split(db_path, shards, out_dir, log=print):
    """state.db -> out_dir/state-<n>.db. Runs that never finished are marked finished in the copies: their events
    were exported already, and a worker re-exporting one would write only its own prefectures' part of the file."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path)
    tables = _tables(src)
    for n, prefs in enumerate(shards):
        path = out_dir / f"state-{n}.db"
        path.unlink(missing_ok=True)
        dst = sqlite3.connect(path)
        _copy_schema(src, dst)
        dst.commit()
        dst.close()
        src.execute("ATTACH ? AS out", (str(path),))
        marks = ",".join("?" * len(prefs))
        for t in tables:
            if t in BY_PREF:
                src.execute(f"INSERT INTO out.{t} SELECT * FROM main.{t} WHERE pref IN ({marks})", prefs)
            elif t == "queue":
                src.execute(f"""INSERT INTO out.queue SELECT q.* FROM main.queue q JOIN main.listings l
                                ON l.type = q.type AND l.id = q.id WHERE l.pref IN ({marks})""", prefs)
            else:
                src.execute(f"INSERT INTO out.{t} SELECT * FROM main.{t}")
        src.execute("UPDATE out.runs SET finished = COALESCE(finished, started) WHERE finished IS NULL")
        src.commit()
        src.execute("DETACH out")
        dst = sqlite3.connect(path)
        dst.execute("VACUUM")
        n_listings = dst.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        dst.close()
        log(f"shard {n}: {', '.join(prefs)}: {n_listings} listings -> {path}")
    src.close()


def join(paths, out_path, log=print):
    """state-<n>.db files -> one state.db. Events are renumbered (seq) in run and shard order; runs every shard
    copied from the original are kept once."""
    out_path = Path(out_path)
    out_path.unlink(missing_ok=True)
    first = sqlite3.connect(paths[0])
    dst = sqlite3.connect(out_path)
    _copy_schema(first, dst)
    dst.commit()
    first.close()
    for p in paths:
        dst.execute("ATTACH ? AS s", (str(p),))
        for t in _tables(dst, "s"):
            if t == "events":
                cols = [r[1] for r in dst.execute("PRAGMA s.table_info(events)") if r[1] != "seq"]
                c = ", ".join(cols)
                dst.execute(f"INSERT INTO main.events ({c}) SELECT {c} FROM s.events ORDER BY seq")
            else:
                dst.execute(f"INSERT OR IGNORE INTO main.{t} SELECT * FROM s.{t}")
        dst.commit()
        dst.execute("DETACH s")
        log(f"joined {p}")
    dst.execute("VACUUM")
    dst.close()
