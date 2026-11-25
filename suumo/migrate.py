"""`migrate-state`: an older state -> the page-record schema. Additive and idempotent (run it any number of times).

  - columns listings.page_json/extract_v/fetched_at and the meta table (db.COLUMNS/TABLES, as any writer adds them)
  - queue priorities: the old P_NEW 1 becomes 2 (1 is P_CHANGED now); once, marked by meta queue_v (a state
    created by this code, or seeded, has it already)
The records themselves come from the backfill (pipeline.store_page), which then sets meta backfilled.
"""
from .db import QUEUE_V
from .pipeline import P_NEW


def migrate_state(db, log=print):
    """Returns the number of queue entries remapped (0 when done before)."""
    db.migrate()
    n = 0
    if int(db.meta("queue_v") or 0) < QUEUE_V:
        n = db.x("UPDATE queue SET priority=? WHERE priority=1", P_NEW).rowcount
        db.set_meta("queue_v", QUEUE_V)
    db.commit()
    log(f"state migrated: columns and meta in place; {n} queue entries remapped to P_NEW"
        + ("" if db.meta("backfilled") else "; not backfilled yet (runs refuse it until then)"))
    return n
