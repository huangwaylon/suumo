"""data/ -> listings with the derived fields the site searches on (rooms, sizes, age, walk times, flags,
duplicate groups, 新着 and 値下げ from data/events). Pure: reads files only. The search rules themselves live in
site/filter.js.

Listing keys are "type:id" strings (e.g. "used_condo:20205670").
"""
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .parse import TYPES

LEASEHOLD = {"地上権", "定期借地権", "普通借地権", "旧法借地権", "借地権"}
NEW_TYPES = {"new_house", "new_condo"}
NEW_DAYS = 7           # 新着: a new/relisted event within this many days
DROP_DAYS = 30         # 値下げ: a price drop within this many days
QUAKE_CODE = "1981-06"  # 新耐震基準: built from June 1981

_rooms = re.compile(r"(\d+)\s*(?:SLDK|LDK|SDK|SK|LK|DK|K|R)")


def parse_rooms(layout):
    """'3LDK+S（納戸）' -> {3}, 'ワンルーム' -> {1}, '2LDK・4LDK' -> {2, 4}, '1LDK+2S～3LDK' -> {1, 2, 3}."""
    if not layout:
        return frozenset()
    s = unicodedata.normalize("NFKC", layout)
    found = {int(n) for n in _rooms.findall(s)}
    if "ワンルーム" in s:
        found.add(1)
    if found and "~" in s:  # NFKC turns ～ into ~: a range across plots or units
        found = set(range(min(found), max(found) + 1))
    return frozenset(found)


def line_name(line):
    """'小田急線（新宿～相模大野）' -> '小田急線': SUUMO sometimes names the section in brackets."""
    return re.sub(r"[（(].*?[）)]", "", line or "").strip()


def is_bus(st):
    return bool(st.get("bus_stop") or (st.get("line") and "バス" in st["line"]))


def _age_years(rec, today):
    if rec["type"] in NEW_TYPES:
        return 0
    built = rec.get("built")
    if not built:
        return None
    y, m = int(built[:4]), int(built[5:7] or 1)
    return max(0, (today.year * 12 + today.month - (y * 12 + m)) // 12)


class Item:
    """One active listing: its record plus the values the site filters and sorts on."""

    def __init__(self, rec, today, pref):
        self.rec = rec
        self.pref = pref
        self.type = rec["type"]
        self.key = f"{self.type}:{rec['id']}"
        self.idn = int(rec["id"])
        self.area = rec.get("area_code")
        self.price_lo = rec.get("price")
        self.price_hi = rec.get("price_max") or self.price_lo
        self.rooms = parse_rooms(rec.get("layout")) if self.type != "land" else frozenset()
        size = rec.get("floor_m2_max") or rec.get("floor_m2") or rec.get("building_m2_max") or rec.get("building_m2")
        self.size = size if self.type != "land" else None
        self.land = rec.get("land_m2_max") or rec.get("land_m2")
        self.age = _age_years(rec, today)
        self.built = rec.get("built")
        # (name, line, minutes on foot or None when the access is by bus); bus stops aren't stations
        self.stations = [(s["name"], line_name(s.get("line")), None if s.get("bus") else s.get("walk"))
                         for s in rec.get("stations") or [] if s.get("name") and not is_bus(s)]
        self.features = sorted(rec.get("features") or ())
        self.leasehold = rec.get("land_rights") in LEASEHOLD
        self.first_seen = rec.get("first_seen") or ""
        self.dup = rec.get("dup_key")
        base = rec.get("floor_m2") or rec.get("building_m2") or (rec.get("land_m2") if self.type == "land" else None)
        self.unit_price = round(self.price_lo / base) if self.price_lo and base and not rec.get("price_max") else None
        # land sold with a build condition (also listed under new_house, price without the building)
        self.conditional = bool(rec.get("build_condition") or rec.get("price_excludes_building")
                                or ("/tochi/" in rec.get("url", "") and self.type != "land"))
        self.post_1981 = self.type in NEW_TYPES or bool(self.built and self.built >= QUAKE_CODE)


@dataclass
class Snapshot:
    items: list                                   # active listings (Item)
    areas: dict                                   # area code -> name
    history: dict                                 # key -> [(date, old_price, new_price)], oldest first
    new_dates: dict                               # key -> date of its latest new/relisted event
    today: date
    groups: dict = field(default_factory=dict)    # dup_key -> [Item] (only groups of 2 or more)

    def __post_init__(self):
        by_dup = defaultdict(list)
        for i in self.items:
            if i.dup:
                by_dup[i.dup].append(i)
        self.groups = {k: v for k, v in by_dup.items() if len(v) > 1}

    def is_new(self, item):
        return self.new_dates.get(item.key, "") >= (self.today - timedelta(days=NEW_DAYS)).isoformat()

    def is_dropped(self, item):
        cutoff = (self.today - timedelta(days=DROP_DAYS)).isoformat()
        return any(d >= cutoff and old and new and new < old for d, old, new in self.history.get(item.key, ()))


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_date(run_id):
    return f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"


def load(data_dir, today: date):
    data_dir = Path(data_dir)
    items, areas = [], {}
    for pref_dir in sorted(p for p in data_dir.iterdir() if p.is_dir() and p.name != "events"):
        recs = {t: [r for p in sorted((pref_dir / t).glob("*.jsonl")) for r in _read_jsonl(p)] for t in TYPES}
        land_ids = {r["id"] for r in recs["land"]}
        for type_key, rs in recs.items():
            # land with a build condition is listed under both new_house and land: keep the land listing
            items += [Item(r, today, pref_dir.name) for r in rs
                      if not (type_key == "new_house" and r["id"] in land_ids and "/tochi/" in r.get("url", ""))]
        areas_file = pref_dir / "areas.json"
        if areas_file.exists():
            areas.update(json.loads(areas_file.read_text(encoding="utf-8")))
    history, new_dates = defaultdict(list), {}
    for path in sorted(data_dir.glob("events/*.json")):
        day = run_date(path.stem)
        for e in json.loads(path.read_text(encoding="utf-8")):
            key = f"{e['type']}:{e['id']}"
            if e["kind"] in ("new", "relisted"):
                new_dates[key] = day
            elif e["kind"] == "price_changed":
                p = e.get("payload") or {}
                history[key].append((day, p.get("old_price"), p.get("price")))
    return Snapshot(items, areas, dict(history), new_dates, today)
