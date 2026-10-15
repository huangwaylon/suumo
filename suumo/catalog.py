"""data/ -> listings with the derived fields the site searches on (rooms, sizes, age, walk times, flags,
duplicate groups, 新着 and 値下げ from data/events). Pure: reads files only (the railway stations are passed in).
The search rules themselves live in site/filter.js.

Listing keys are "type:id" strings (e.g. "used_condo:20205670").
"""
import functools
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .parse import TYPES

LEASEHOLD = {"地上権", "定期借地権", "普通借地権", "旧法借地権", "借地権"}
NEW_TYPES = {"new_house", "new_condo"}
NEW_DAYS = 7           # 新着: a new/relisted event within this many days
DROP_DAYS = 30         # 値下げ: a price drop within this many days
RETURN_DAYS = 14       # a listing seen again this soon after its removal is a return, not 新着
QUAKE_CODE = "1981-06"  # 新耐震基準: built from June 1981
CONDOS = {"used_condo", "new_condo"}
SOUTH = {"南", "南東", "南西"}  # direction (condos)
SOUTH_TAGS = {"南向き", "全室南向き", "東南向き", "南西向き", "全室東南向き", "全室南西向き"}
TOP_TAG = "最上階・上階なし"
READY_TAGS = {"即引渡可", "即引渡し可"}
TOWER_FLOORS = 20
_no_fee = re.compile(r"売主|事業主|代理")      # the seller or its agent: no commission
_fee = re.compile(r"媒介|仲介(?!手数料)")       # a broker ('売主・仲介手数料0' is still no commission)
_parking = re.compile(r"カースペース|車庫|カーポート|ビルトイン|駐車|台")

_plan = re.compile(r"(\d+)\s*(SLDK|LDK|SDK|SK|LK|DK|K|R)")
_KIND = {"R": 0, "K": 0, "SK": 0, "DK": 1, "SDK": 1, "LK": 1, "LDK": 2, "SLDK": 2}


def plan_rank(layout):
    """The largest plan in a layout as one comparable number, rooms * 3 + (K 0, DK 1, LDK 2):
    'ワンルーム' -> 3, '2DK' -> 7, '3LDK+S（納戸）' -> 11, '2LDK・4LDK' -> 14. None when no plan is stated."""
    if not layout:
        return None
    s = unicodedata.normalize("NFKC", layout)
    ranks = [int(n) * 3 + _KIND[k] for n, k in _plan.findall(s)]
    if "ワンルーム" in s:
        ranks.append(3)
    return max(ranks) if ranks else None


@functools.cache
def station_key(name):
    """The form station names are matched in: '市ヶ谷', '市ケ谷駅', '市が谷' -> '市ケ谷'; '浦安駅 (千葉県)' -> '浦安';
    '江曾島' -> '江曽島'; '江の島' -> '江ノ島'."""
    name = unicodedata.normalize("NFKC", name)
    for a, b in (("ヶ", "ケ"), ("ヵ", "ケ"), ("が", "ケ"), ("澤", "沢"), ("曾", "曽"), ("の", "ノ")):
        name = name.replace(a, b)
    return re.sub(r"\s*\(.*\)$", "", name).removesuffix("駅").removesuffix("停留場").strip()


AREA_MARGIN = 0.15  # degrees: stations just over a prefecture's border are named in its listings too


def station_area(points):
    """[west, south, east, north] around (lat, lng) points, leaving out those over 1° from the median (a same-named
    station far away, a far island); None without points. A point listed n times weighs n times."""
    if not points:
        return None
    mid = (sorted(p[0] for p in points)[len(points) // 2], sorted(p[1] for p in points)[len(points) // 2])
    near = [p for p in points if abs(p[0] - mid[0]) < 1 and abs(p[1] - mid[1]) < 1]
    lats, lngs = [p[0] for p in near], [p[1] for p in near]
    return [min(lngs) - AREA_MARGIN, min(lats) - AREA_MARGIN, max(lngs) + AREA_MARGIN, max(lats) + AREA_MARGIN]


def listing_station_keys(recs):
    """Counter of the station keys the listings mention (a bus stop is mentioned by a few, a station by many)."""
    return Counter(station_key(s["name"]) for r in recs for s in r.get("stations") or [] if s.get("name"))


def railway_near(keys, rail_stations):
    """The keys of the railway stations in the area the listings' stations (a Counter of keys) span, weighed by
    mentions so bus stops named like stations elsewhere don't move it: what counts as a station there.
    rail_stations: rail.json's [[name, lat, lng, lines], ...]."""
    area = station_area([(lat, lng) for name, lat, lng, _ in rail_stations for _ in range(keys[station_key(name)])])
    if not area:
        return set()
    west, south, east, north = area
    return {station_key(name) for name, lat, lng, _ in rail_stations if west <= lng <= east and south <= lat <= north}


def is_bus(st, railway=None):
    """A bus stop: SUUMO says so (a バス line), or, with the railway near the listing known, no station has its name
    there (SUUMO often gives only the bus company or a route number: 'しずてつジャストライン「丸子橋入口」')."""
    if st.get("bus_stop") or (st.get("line") and "バス" in st["line"]):
        return True
    return railway is not None and station_key(st["name"]) not in railway


def unify_station_names(items):
    """One spelling per station: every name is replaced by the most common spelling of its station_key among the
    listings of the same prefecture (荻窪駅 -> 荻窪, 市ヶ谷 -> 市ケ谷, 駒澤大学 -> 駒沢大学 when those are the usual
    ones; ties: the first in code-point order). Per prefecture, because the same key can be two stations: Tokyo's 霞ケ関
    and Saitama's 霞ヶ関, 自由が丘 and Fukuoka's 自由ケ丘. SUUMO's own disambiguated names (藤沢（長野県）) stay as they
    are. Rewrites the items' stations, bus and car access, and the station names in their records."""
    seen = Counter((i.pref, name) for i in items for name, _ in i.stations if "（" not in name)
    best = {}
    for pref, name in sorted(seen, key=lambda pn: (-seen[pn], pn)):
        best.setdefault((pref, station_key(name)), name)
    rename = {pn: best[pn[0], station_key(pn[1])] for pn in seen if best[pn[0], station_key(pn[1])] != pn[1]}
    for i in items:
        new = {name: rename[i.pref, name] for name, _ in i.stations if (i.pref, name) in rename}
        if not new:
            continue
        stops = {name for name, _ in i.stops}
        i.stations = [(new.get(name, name), walk) for name, walk in i.stations]
        i.bus = {new.get(k, k): v for k, v in i.bus.items()}
        i.car = {new.get(k, k): v for k, v in i.car.items()}
        i.rec = {**i.rec, "stations": [{**s, "name": new[s["name"]]} if s.get("name") in new and s["name"] not in stops
                                       else s for s in i.rec["stations"]]}


def _age_months(rec, today):
    """Whole months since it was built (new homes: 0)."""
    if rec["type"] in NEW_TYPES:
        return 0
    built = rec.get("built")
    if not built:
        return None
    y, m = int(built[:4]), int(built[5:7] or 1)
    return max(0, today.year * 12 + today.month - (y * 12 + m))


class Item:
    """One listing: its record plus the values the site filters and sorts on. `gone`: ended on SUUMO but kept
    because it's saved; `saved`: on the shared saved list."""

    def __init__(self, rec, today, pref, railway=None):
        self.rec = rec
        self.pref = pref
        self.type = rec["type"]
        self.key = f"{self.type}:{rec['id']}"
        self.gone = bool(rec.get("removed_at"))
        self.saved = False
        self.idn = int(rec["id"])
        self.area = rec.get("area_code")
        self.price_lo = rec.get("price")
        self.price_hi = rec.get("price_max") or self.price_lo
        self.plan = plan_rank(rec.get("layout")) if self.type != "land" else None
        size = rec.get("floor_m2_max") or rec.get("floor_m2") or rec.get("building_m2_max") or rec.get("building_m2")
        self.size = size if self.type != "land" else None
        self.land = rec.get("land_m2_max") or rec.get("land_m2")
        self.age_months = _age_months(rec, today)
        built = rec.get("built")
        # (name, minutes on foot or None when the access is by bus); bus stops aren't stations
        named = [s for s in rec.get("stations") or [] if s.get("name")]
        rail = [s for s in named if not is_bus(s, railway)]
        self.stations = [(s["name"], None if s.get("bus") else s.get("walk")) for s in rail]
        self.bus = {s["name"]: s["bus"] for s in rail if s.get("bus")}  # minutes
        self.car = {s["name"]: s["car_km"] for s in rail if s.get("car_km")}
        self.stops = [(s["name"], s.get("walk")) for s in named if s not in rail]  # bus stops (name, minutes on foot)
        self.features = sorted(rec.get("features") or ())
        self.leasehold = rec.get("land_rights") in LEASEHOLD
        self.first_seen = rec.get("first_seen") or ""
        self.dup = rec.get("dup_key")
        base = rec.get("floor_m2") or rec.get("building_m2") or (rec.get("land_m2") if self.type == "land" else None)
        self.unit_price = round(self.price_lo / base) if self.price_lo and base and not rec.get("price_max") else None
        # land sold with a build condition (also listed under new_house, price without the building)
        self.conditional = bool(rec.get("build_condition") or rec.get("price_excludes_building")
                                or ("/tochi/" in rec.get("url", "") and self.type != "land"))
        self.post_1981 = self.type in NEW_TYPES or bool(built and built >= QUAKE_CODE)
        tags, condo = set(self.features), self.type in CONDOS
        floor, floors = rec.get("floor"), rec.get("floors_above")
        floor = floor if isinstance(floor, int) else None
        self.south = rec.get("direction") in SOUTH or bool(tags & SOUTH_TAGS)
        self.top = condo and ((floor is not None and floor == floors) or TOP_TAG in tags)
        self.upper = condo and floor is not None and floor >= 2
        deal = rec.get("deal_type") or ""
        self.nofee = bool(_no_fee.search(deal)) and not _fee.search(deal)   # this listing's own deal type
        self.ready = (rec.get("handover") or "").startswith("即") or bool(tags & READY_TAGS)
        self.tower = isinstance(floors, int) and floors >= TOWER_FLOORS
        parking = rec.get("parking")
        status = (parking.get("status") or "") if isinstance(parking, dict) else ""
        self.parking = not condo and (bool(_parking.search(status) and not status.startswith("無"))
                                      or any("駐車" in t for t in tags))
        # monthly fees of a condo (yen): management + repair fund (+ other monthly costs); None unless both known
        mgmt, repair = rec.get("mgmt_fee"), rec.get("repair_fee")
        known = condo and mgmt is not None and repair is not None
        self.fees = mgmt + repair + (rec.get("other_monthly") or 0) if known else None


@dataclass
class Snapshot:
    items: list                                   # listings (Item): active, and ended ones kept because saved
    areas: dict                                   # area code -> name
    history: dict                                 # key -> [(date, old_price, new_price)], oldest first
    new_at: dict                                  # key -> (YYYYMMDDHH, "new" | "relisted"), latest; returns skipped
    drops: dict                                   # key -> (YYYYMMDDHH, old_price, new_price) of its latest drop
    today: date
    groups: dict = field(default_factory=dict)    # dup_key -> [Item] (only groups of 2 or more)

    def __post_init__(self):
        by_dup = defaultdict(list)
        for i in self.items:
            if i.dup and not (i.saved or i.gone):  # a saved listing stays itself (its key is what was saved)
                by_dup[i.dup].append(i)
        self.groups = {k: v for k, v in by_dup.items() if len(v) > 1 and one_property(v)}

    def new(self, item):
        """(YYYYMMDDHH, kind) when the listing is new, else None. A property another agent already listed (an
        older listing in its duplicate group) is a re-post, not new."""
        at = self.new_at.get(item.key)
        if not at:
            return None
        for other in self.groups.get(item.dup, ()):
            if other is not item and self.new_at.get(other.key, (0,))[0] < at[0]:
                return None
        return at

    def is_new_at(self, at):
        """Whether new(item) == at makes the listing 新着 (within NEW_DAYS)."""
        return bool(at) and at[0] >= hour(self.today - timedelta(days=NEW_DAYS))

    def drop(self, item):
        """(YYYYMMDDHH, old_price, new_price) of the latest price drop within DROP_DAYS, else None."""
        d = self.drops.get(item.key)
        return d if d and d[0] >= hour(self.today - timedelta(days=DROP_DAYS)) else None


def one_property(group):
    """Whether a dup_key group is one property listed by several agents. Not when an agent lists two of them (lots of
    one development, units of one building), nor condo units on two different floors."""
    agents = [i.rec["agent"] for i in group if i.rec.get("agent")]
    if len(agents) != len(set(agents)):
        return False
    floors = {i.rec.get("floor") for i in group if i.type in CONDOS and isinstance(i.rec.get("floor"), int)}
    return len(floors) < 2


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_date(run_id):
    return f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"


def run_hour(run_id):
    """'20261006T194539' -> 2026100619 (runs are hourly: the hour says when a change was seen)."""
    return int(run_id[:8] + run_id[9:11])


def _hour_time(at):
    """2026100619 -> datetime(2026, 10, 6, 19)."""
    return datetime.strptime(str(at), "%Y%m%d%H")


def hour(day):
    """A date as YYYYMMDD00, comparable with run_hour."""
    return int(day.strftime("%Y%m%d")) * 100


def prefectures(data_dir):
    """The prefectures data/ holds, in name order."""
    return sorted(p.name for p in Path(data_dir).iterdir() if p.is_dir() and p.name != "events")


def load_events(data_dir):
    """data/events/ -> (history, new_at, drops), as Snapshot holds them (one read shared by every prefecture)."""
    history, new_at, drops, removed = defaultdict(list), {}, {}, {}
    for path in sorted(Path(data_dir).glob("events/*.json")):
        day, at = run_date(path.stem), run_hour(path.stem)
        for e in json.loads(path.read_text(encoding="utf-8")):
            key = f"{e['type']}:{e['id']}"
            if e["kind"] == "removed":
                removed[key] = at
            elif e["kind"] in ("new", "relisted"):
                # back soon after it ended (a missed crawl, a re-post): not new; an earlier new event still counts
                if key not in removed or _hour_time(at) - _hour_time(removed[key]) > timedelta(days=RETURN_DAYS):
                    new_at[key] = (at, e["kind"])
            elif e["kind"] == "price_changed":
                p = e.get("payload") or {}
                old, new = p.get("old_price"), p.get("price")
                history[key].append((day, old, new))
                if old and new and new < old:
                    drops[key] = (at, old, new)
    return dict(history), new_at, drops


def load(data_dir, today: date, saved=(), rail_stations=(), prefs=None, events=None):
    """rail_stations (rail.json's): tell bus stops from stations by the railway near each prefecture. prefs: only
    these prefectures (the site builds one at a time); events: load_events(data_dir), when already read. Nothing
    crosses prefectures: duplicate groups and station spellings are per prefecture."""
    data_dir = Path(data_dir)
    items, areas = [], {}
    for pref in prefectures(data_dir) if prefs is None else prefs:
        pref_dir = data_dir / pref
        recs = {t: [r for p in sorted((pref_dir / t).glob("*.jsonl")) for r in _read_jsonl(p)] for t in TYPES}
        for path in sorted((pref_dir / "removed").glob("*.jsonl")):  # ended, kept because saved
            recs[path.stem] += _read_jsonl(path)
        land_ids = {r["id"] for r in recs["land"]}
        railway = railway_near(listing_station_keys(r for rs in recs.values() for r in rs),
                               rail_stations) if rail_stations else None
        for type_key, rs in recs.items():
            # land with a build condition is listed under both new_house and land: keep the land listing
            items += [Item(r, today, pref, railway) for r in rs
                      if not (type_key == "new_house" and r["id"] in land_ids and "/tochi/" in r.get("url", ""))]
        areas_file = pref_dir / "areas.json"
        if areas_file.exists():
            areas.update(json.loads(areas_file.read_text(encoding="utf-8")))
    history, new_at, drops = events or load_events(data_dir)
    for i in items:
        i.saved = i.key in saved
    unify_station_names(items)
    return Snapshot(items, areas, history, new_at, drops, today)
