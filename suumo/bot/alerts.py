"""Which run events each person hears about (pure: no Discord).

Saved searches: `new` and `relisted` listings that match, and price drops of listings that match.
Favorites (when on): any price change, removal or relisting of a starred listing.
Conditions that need the listing page (こだわり) count as met while the page isn't fetched yet, so a
new listing isn't missed for good because its page came a day later.
"""
from dataclasses import replace

from ..catalog import key_of

SEARCH_KINDS = ("new", "relisted", "price_changed")
FAVORITE_KINDS = ("price_changed", "removed", "relisted")
ORDER = {"new": 0, "relisted": 1, "price_changed": 2, "removed": 3}


def pending_runs(user, snap):
    return [r for r in snap.runs if user.last_run is None or r > user.last_run]


def collect(user, snap, run_events, heading_for, fav_heading):
    """run_events: [(run_id, [event])] newer than the person's last_run -> [(heading, [event])]."""
    events = [e for _, evs in run_events for e in evs]
    sections = []
    for s in user.searches:
        q = replace(s.query, new_only=False, drops_only=False)  # the alert itself is the "new" / "dropped"
        hits, seen = [], set()
        for e in events:
            if e.get("kind") not in SEARCH_KINDS:
                continue
            p = e.get("payload") or {}
            if e["kind"] == "price_changed" and not (p.get("old_price") and p.get("price")
                                                     and p["price"] < p["old_price"]):
                continue
            item = snap.by_key.get(key_of(e))
            if item is None or not snap.matches(item, q, lenient=True):
                continue
            ident = item.dup or item.key
            if ident in seen:
                continue
            seen.add(ident)
            hits.append(e)
        if hits:
            sections.append((heading_for(s.query), sorted(hits, key=lambda e: ORDER[e["kind"]])))
    if user.notify_favorites and user.favorites:
        favs = [e for e in events if e.get("kind") in FAVORITE_KINDS and key_of(e) in user.favorites]
        if favs:
            sections.append((fav_heading, sorted(favs, key=lambda e: ORDER[e["kind"]])))
    return sections
