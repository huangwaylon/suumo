"""In-memory index over the exported data/ for the search bot: load, filter, sort, facets, price history.

Pure: reads files, no Discord, no network, never touches state.db. A `Snapshot` is immutable once built;
`Catalog.refresh()` builds a new one when any file under data/ changed and swaps it in.

Listing keys are "type:id" strings (e.g. "used_condo:20205670").
"""
import contextlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, timedelta
from pathlib import Path

from .parse import TYPES

LEASEHOLD = {"地上権", "定期借地権", "普通借地権", "旧法借地権", "借地権"}
NEW_TYPES = {"new_house", "new_condo"}
NEW_DAYS = 7         # 新着のみ: a new/relisted event within this many days
DROP_DAYS = 30       # 値下げのみ: a price drop within this many days
SORTS = ("new", "price_asc", "price_desc", "size_desc", "walk_asc", "age_asc", "unit_asc")
ROOM_BUCKETS = (1, 2, 3, 4)  # 4 = 4 rooms or more
QUAKE_CODE = "1981-06"       # 新耐震基準: built from June 1981
DETAIL_FIELDS = ("features", "freehold_only")  # conditions only a fetched listing page can answer

_rooms = re.compile(r"(\d+)\s*(?:SLDK|LDK|SDK|SK|LK|DK|K|R)")


def key_of(rec):
    return f"{rec['type']}:{rec['id']}"


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




@dataclass(frozen=True)
class Query:
    """Search conditions. Empty tuples / None mean "any". Tuples are kept sorted so equal queries are equal."""
    types: tuple = ()
    areas: tuple = ()
    stations: tuple = ()          # station names (the same station on several lines is one choice)
    walk_max: int | None = None   # minutes on foot to some (chosen) station
    price_min: int | None = None  # yen
    price_max: int | None = None
    rooms: tuple = ()             # ROOM_BUCKETS
    size_min: int | None = None   # m², floor (condo) or building (house)
    land_min: int | None = None   # m², land (house, land)
    age_max: int | None = None    # years since built; new types count as 0
    post_1981: bool = False       # 新耐震基準
    features: tuple = ()          # SUUMO tags, all required
    freehold_only: bool = False
    no_condition: bool = False    # 建築条件なし (land)
    new_only: bool = False
    drops_only: bool = False
    sort: str = "new"

    def set(self, **kw):
        for k, v in kw.items():
            if isinstance(v, (list, set, frozenset)):
                kw[k] = tuple(sorted(v))
        return replace(self, **kw)

    def is_empty(self):
        return self.set(sort="new") == Query()

    def to_dict(self):
        """Only non-default fields, JSON-friendly."""
        default = Query()
        return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(self).items()
                if v != getattr(default, k)}

    @classmethod
    def from_dict(cls, d):
        """Tolerant of unknown keys and bad values (state written by an older version)."""
        known = {f.name: f for f in fields(cls)}
        allowed = {"types": lambda x: x in TYPES, "rooms": lambda x: x in ROOM_BUCKETS and isinstance(x, int)}
        q = cls()
        for k, v in (d or {}).items():
            if k not in known:
                continue
            default = getattr(q, k)
            if isinstance(default, tuple) and isinstance(v, list):
                ok = allowed.get(k, lambda x: isinstance(x, str))
                q = q.set(**{k: [x for x in v if ok(x)]})
            elif isinstance(default, bool) and isinstance(v, bool):
                q = replace(q, **{k: v})
            elif k == "sort" and v in SORTS:
                q = replace(q, sort=v)
            elif default is None and isinstance(v, int) and not isinstance(v, bool):
                q = replace(q, **{k: v})
        return q


class Item:
    """One listing, with the values filters and sorts need precomputed. The record itself is kept as its JSON
    line and parsed on access (a page shows a handful), which keeps a 50k-listing catalog small."""
    __slots__ = ("key", "idn", "idx", "_raw", "type", "area", "price_lo", "price_hi", "rooms", "size", "land",
                 "age", "stations", "walk", "features", "leasehold", "first_seen", "dup", "unit_price", "removed",
                 "built", "has_detail", "conditional")

    def __init__(self, rec, today, removed=False, raw=None):
        self._raw = raw or json.dumps(rec, ensure_ascii=False)
        self.key = key_of(rec)
        self.idn = int(rec["id"])
        self.idx = -1
        self.type = rec["type"]
        self.area = rec.get("area_code")
        self.price_lo = rec.get("price")
        self.price_hi = rec.get("price_max") or self.price_lo
        self.rooms = parse_rooms(rec.get("layout")) if self.type != "land" else frozenset()
        size = rec.get("floor_m2_max") or rec.get("floor_m2") or rec.get("building_m2_max") or rec.get("building_m2")
        self.size = size if self.type != "land" else None
        self.land = rec.get("land_m2_max") or rec.get("land_m2")
        self.age = _age_years(rec, today)
        # (name, line, minutes on foot or None when the access is by bus)
        self.stations = tuple((s["name"], line_name(s.get("line")), None if s.get("bus") else s.get("walk"))
                              for s in rec.get("stations") or [] if s.get("name") and not is_bus(s))
        walks = [w for _, _, w in self.stations if w is not None]
        self.walk = min(walks) if walks else None
        self.features = frozenset(rec.get("features") or ())
        self.leasehold = rec.get("land_rights") in LEASEHOLD
        self.first_seen = rec.get("first_seen") or ""
        self.dup = rec.get("dup_key")
        base = rec.get("floor_m2") or rec.get("building_m2") or (rec.get("land_m2") if self.type == "land" else None)
        self.unit_price = self.price_lo / base if self.price_lo and base and not rec.get("price_max") else None
        self.removed = removed
        self.built = rec.get("built")
        self.has_detail = bool(rec.get("has_detail"))
        # land sold with a build condition (also listed under new_house, price without the building)
        self.conditional = bool(rec.get("build_condition") or rec.get("price_excludes_building")
                                or ("/tochi/" in rec.get("url", "") and self.type != "land"))

    @property
    def rec(self):
        return json.loads(self._raw)

    def walk_to(self, chosen):
        """Shortest walk to one of the chosen stations (all stations when none chosen)."""
        walks = [w for n, _, w in self.stations if w is not None and (not chosen or n in chosen)]
        return min(walks) if walks else None


def _age_years(rec, today):
    if rec["type"] in NEW_TYPES:
        return 0
    built = rec.get("built")
    if not built:
        return None
    y, m = int(built[:4]), int(built[5:7] or 1)
    return max(0, (today.year * 12 + today.month - (y * 12 + m)) // 12)


@dataclass
class Hit:
    item: Item
    others: list  # other agents' listings of the same property (same dup_key)

    @property
    def key(self):
        return self.item.key


# ---------- bitsets: a Python int with bit i set for items[i] ----------

_BYTE_BITS = [tuple(j for j in range(8) if b >> j & 1) for b in range(256)]


def to_bits(idxs):
    idxs = list(idxs)
    if not idxs:
        return 0
    buf = bytearray(max(idxs) // 8 + 1)
    for i in idxs:
        buf[i >> 3] |= 1 << (i & 7)
    return int.from_bytes(buf, "little")


def indices(mask):
    out = []
    for n, byte in enumerate(mask.to_bytes((mask.bit_length() + 7) // 8, "little")):
        if byte:
            out.extend(n * 8 + j for j in _BYTE_BITS[byte])
    return out


def _any(table, keys):
    m = 0
    for k in keys:
        m |= table.get(k, 0)
    return m


def _index(items, keys_of):
    table = defaultdict(list)
    for i in items:
        for k in keys_of(i):
            table[k].append(i.idx)
    return {k: to_bits(v) for k, v in table.items()}


class Snapshot:
    """Immutable view of data/ at one moment. Filtering is bitset arithmetic: every choice a screen offers has
    a precomputed set, thresholds (price, size, age, walk) are computed once per value and cached."""

    def __init__(self, items, removed, areas, history, runs, today, new_dates=None):
        self.items = items                    # active listings
        for n, i in enumerate(items):
            i.idx = n
        self.by_key = {i.key: i for i in items}
        self.removed = {i.key: i for i in removed}
        self.areas = areas                    # code -> name
        self.history = history                # key -> [(date, old_price, new_price)], oldest first
        self.runs = runs                      # run ids with an events file, sorted
        self.today = today
        self.new_dates = new_dates or {}      # key -> date of its latest new/relisted event (baselines have none)
        self.new_cutoff = (today - timedelta(days=NEW_DAYS)).isoformat()
        cutoff = (today - timedelta(days=DROP_DAYS)).isoformat()
        self.dropped = {k for k, h in history.items()
                        if any(d >= cutoff and old and new and new < old for d, old, new in h)}
        self.groups = defaultdict(list)
        for i in items:
            if i.dup:
                self.groups[i.dup].append(i)

        self.all = (1 << len(items)) - 1
        self.b_type = _index(items, lambda i: (i.type,))
        self.b_area = _index(items, lambda i: (i.area,))
        self.b_station = _index(items, lambda i: {n for n, _, _ in i.stations})
        self.b_line = _index(items, lambda i: {ln for _, ln, w in i.stations if w is not None and ln})
        self.b_room = _index(items, lambda i: {min(r, 4) for r in i.rooms})
        self.b_feature = _index(items, lambda i: i.features)
        self.b_land = self.b_type.get("land", 0)
        self.b_leasehold = to_bits(i.idx for i in items if i.leasehold)
        self.b_conditional = to_bits(i.idx for i in items if i.conditional)
        self.b_detail = to_bits(i.idx for i in items if i.has_detail)
        self.b_new = to_bits(i.idx for i in items if self.new_dates.get(i.key, "") >= self.new_cutoff)
        self.b_dropped = to_bits(i.idx for i in items if i.key in self.dropped)
        self.b_multi = to_bits(i.idx for g in self.groups.values() if len(g) > 1 for i in g)
        self.station_walks = defaultdict(list)    # name -> [(idx, minutes on foot)]
        self.line_stations = defaultdict(set)     # line -> station names
        for i in items:
            for n, ln, w in i.stations:
                if w is not None:
                    self.station_walks[n].append((i.idx, w))
                    if ln:
                        self.line_stations[ln].add(n)
        self._cache = {}

    def get(self, key):
        """Active listing, else a recently removed one, else None."""
        return self.by_key.get(key) or self.removed.get(key)

    # ---------- filtering ----------

    def _where(self, name, value, pred):
        k = (name, value)
        if k not in self._cache:
            self._cache[k] = to_bits(i.idx for i in self.items if pred(i))
        return self._cache[k]

    def _walk_bits(self, limit, stations):
        k = ("walk", limit, stations)
        if k not in self._cache:
            if stations:
                idxs = {n for s in stations for n, w in self.station_walks.get(s, ()) if w <= limit}
            else:
                idxs = {i.idx for i in self.items if i.walk is not None and i.walk <= limit}
            self._cache[k] = to_bits(idxs)
        return self._cache[k]

    def mask(self, q: Query, skip=(), lenient=False):
        """Bitset of the listings matching q (the same rules as `matches`)."""
        m = self.all
        land_ok = self.b_land if "land" in q.types else 0  # building conditions don't exclude chosen land
        if q.types and "types" not in skip:
            m &= _any(self.b_type, q.types)
        if q.areas and "areas" not in skip:
            m &= _any(self.b_area, q.areas)
        if q.stations and "stations" not in skip:
            m &= _any(self.b_station, q.stations)
        if q.walk_max is not None:
            m &= self._walk_bits(q.walk_max, () if "stations" in skip else q.stations)
        if q.price_max is not None:
            v = q.price_max
            m &= self._where("price_max", v, lambda i: i.price_lo is not None and i.price_lo <= v)
        if q.price_min is not None:
            v = q.price_min
            m &= self._where("price_min", v, lambda i: i.price_hi is not None and i.price_hi >= v)
        if q.rooms and "rooms" not in skip:
            m &= _any(self.b_room, q.rooms) | land_ok
        if q.size_min is not None:
            v = q.size_min
            m &= self._where("size_min", v, lambda i: i.size is not None and i.size >= v) | land_ok
        if q.land_min is not None:
            v = q.land_min
            m &= self._where("land_min", v, lambda i: i.land is not None and i.land >= v)
        if q.age_max is not None:
            v = q.age_max
            m &= self._where("age_max", v, lambda i: i.age is not None and i.age <= v) | land_ok
        if q.post_1981:
            m &= self._where("post_1981", True, lambda i: i.type in NEW_TYPES
                             or bool(i.built and i.built >= QUAKE_CODE)) | land_ok
        if q.features and "features" not in skip:
            tags = self.all
            for f in q.features:
                tags &= self.b_feature.get(f, 0)
            m &= tags | (self.all & ~self.b_detail if lenient else 0)
        if q.freehold_only:
            m &= ~self.b_leasehold
        if q.no_condition:
            m &= ~self.b_conditional
        if q.new_only:
            m &= self.b_new
        if q.drops_only:
            m &= self.b_dropped
        return m

    def matches(self, i: Item, q: Query, lenient=False):
        """Does one listing match? The reference for `mask`, used for alerts.
        lenient: conditions that need the listing page pass when it hasn't been fetched yet."""
        if q.types and i.type not in q.types:
            return False
        if q.areas and i.area not in q.areas:
            return False
        if q.stations and not any(n in q.stations for n, _, _ in i.stations):
            return False
        if q.walk_max is not None:
            w = i.walk_to(q.stations)
            if w is None or w > q.walk_max:
                return False
        if q.price_max is not None and (i.price_lo is None or i.price_lo > q.price_max):
            return False
        if q.price_min is not None and (i.price_hi is None or i.price_hi < q.price_min):
            return False
        # building conditions imply a building, except for land the user explicitly asked for
        building = i.type != "land" or "land" not in q.types
        if building and q.rooms and not any(min(r, 4) in q.rooms for r in i.rooms):
            return False
        if building and q.size_min is not None and (i.size is None or i.size < q.size_min):
            return False
        if q.land_min is not None and (i.land is None or i.land < q.land_min):
            return False
        if building and q.age_max is not None and (i.age is None or i.age > q.age_max):
            return False
        if building and q.post_1981 and i.type not in NEW_TYPES and (not i.built or i.built < QUAKE_CODE):
            return False
        unknown = lenient and not i.has_detail
        if q.features and not unknown and not i.features.issuperset(q.features):
            return False
        if q.freehold_only and i.leasehold:
            return False
        if q.no_condition and i.conditional:
            return False
        if q.new_only and self.new_dates.get(i.key, "") < self.new_cutoff:
            return False
        return not (q.drops_only and i.key not in self.dropped)

    def fold_mask(self, m):
        """Keep one listing per property (dup_key group), for counting."""
        dup = m & self.b_multi
        if not dup:
            return m
        seen, reps = set(), []
        for n in indices(dup):
            d = self.items[n].dup
            if d not in seen:
                seen.add(d)
                reps.append(n)
        return (m & ~self.b_multi) | to_bits(reps)

    def search(self, q: Query):
        """Matching listings, one Hit per property (duplicates by other agents folded in), sorted."""
        matched = [self.items[n] for n in indices(self.mask(q))]
        return sort_hits(fold(matched, self.groups), q, self)

    def count(self, q: Query):
        return self.fold_mask(self.mask(q)).bit_count()

    def undetailed(self, q: Query):
        """How many more would match if every listing page had been fetched (0 unless a page-only condition)."""
        if not q.features:
            return 0
        return self.fold_mask(self.mask(q, lenient=True)).bit_count() - self.count(q)

    def loosen(self, q: Query):
        """For 0 results: [(field, count)] — how many each condition would give back if cleared, best first."""
        default, out = Query(), []
        for f in fields(Query):
            if f.name == "sort" or getattr(q, f.name) == getattr(default, f.name):
                continue
            n = self.count(replace(q, **{f.name: getattr(default, f.name)}))
            if n:
                out.append((f.name, n))
        return sorted(out, key=lambda x: -x[1])

    # ---------- facets: choices with counts, given the rest of the query ----------

    def _counts(self, q, dim, table, keys):
        pool = self.fold_mask(self.mask(q, skip=(dim,)))
        return {k: (pool & table.get(k, 0)).bit_count() for k in keys}

    def facet_types(self, q):
        """Every type present, counted as if it were the only one chosen (building conditions treat a chosen
        土地 differently, so the type dimension can't simply be skipped)."""
        return [(t, self.count(replace(q, types=(t,)))) for t in TYPES if t in self.b_type]

    def facet_areas(self, q):
        c = self._counts(q, "areas", self.b_area, set(self.b_area) | set(q.areas))
        return sorted(((a, n) for a, n in c.items() if n or a in q.areas), key=lambda x: x[0])

    def facet_lines(self, q):
        """Rail lines by number of matching listings near them (stations chosen elsewhere don't narrow)."""
        c = self._counts(q, "stations", self.b_line, self.b_line)
        return sorted(((ln, n) for ln, n in c.items() if n), key=lambda x: (-x[1], x[0]))

    def facet_stations(self, q, line):
        """Stations on a line, by number of matching listings within the walk limit of that station."""
        pool = self.fold_mask(self.mask(q, skip=("stations",)))
        out = []
        for n in self.line_stations.get(line, ()):
            limit = q.walk_max if q.walk_max is not None else _BIG
            count = (pool & self._walk_bits(limit, (n,))).bit_count()
            if count or n in q.stations:
                out.append((n, count))
        return sorted(out, key=lambda x: (-x[1], x[0]))

    def facet_features(self, q, limit=25):
        """The most common tags among matching listings; chosen tags always included."""
        c = self._counts(q, "features", self.b_feature, self.b_feature)
        rest = [f for f, n in sorted(c.items(), key=lambda x: (-x[1], x[0])) if n and f not in q.features]
        picked = list(q.features) + rest[:max(0, limit - len(q.features))]
        return sorted(((f, c.get(f, 0)) for f in picked), key=lambda x: (-x[1], x[0]))


def fold(items, groups):
    """One Hit per property: the cheapest (then lowest id) matching listing represents its dup_key group."""
    seen, hits = set(), []
    matched = {i.key for i in items}
    for i in items:
        if i.dup and i.dup in seen:
            continue
        if i.dup and len(groups.get(i.dup, ())) > 1:
            seen.add(i.dup)
            group = sorted((g for g in groups[i.dup] if g.key in matched),
                           key=lambda g: (g.price_lo is None, g.price_lo or 0, g.idn))
            hits.append(Hit(group[0], [g for g in groups[i.dup] if g is not group[0]]))
        else:
            hits.append(Hit(i, []))
    return hits


_BIG = float("inf")


def sort_hits(hits, q, snap=None):
    def by(h):
        i = h.item
        idn = i.idn
        return {
            "new": (_neg_date(snap.new_dates.get(i.key, "") if snap else ""), _neg_date(i.first_seen), -idn),
            "price_asc": (i.price_lo if i.price_lo is not None else _BIG, idn),
            "price_desc": (-(i.price_hi or 0), idn),
            "size_desc": (-(i.size or i.land or 0), idn),
            "walk_asc": (_none_last(i.walk_to(q.stations)), idn),
            "age_asc": (_none_last(i.age), _neg_date(i.built or ""), idn),
            "unit_asc": (_none_last(i.unit_price), idn),
        }[q.sort if q.sort in SORTS else "new"]
    return sorted(hits, key=by)


def _none_last(v):
    return _BIG if v is None else v


def _neg_date(s):
    """Sort key for newest-first on 'YYYY-MM-DD' / 'YYYY-MM' strings; missing dates last."""
    return (0, *(-ord(c) for c in s)) if s else (1,)


# ---------- loading ----------

def signature(data_dir):
    """Cheap fingerprint of everything the catalog reads; changes whenever the export rewrites a file."""
    data_dir = Path(data_dir)
    sig = []
    for p in sorted(data_dir.glob("*/*/*.jsonl")) + sorted(data_dir.glob("*/areas.json")):
        st = p.stat()
        sig.append((str(p), st.st_mtime_ns, st.st_size))
    sig.append(tuple(sorted(p.name for p in data_dir.glob("events/*.json"))))
    return tuple(sig)


def _read_jsonl(path):
    """[(record, its JSON line)]."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append((json.loads(line), line))
    return out


def read_events(path):
    """Events of one run file, or None if the file is missing or not fully written yet."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def run_date(run_id):
    return f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}"


def load(data_dir, today: date):
    data_dir = Path(data_dir)
    items, removed, areas = [], [], {}
    for pref_dir in sorted(p for p in data_dir.iterdir() if p.is_dir() and p.name != "events"):
        recs = {t: [x for p in sorted((pref_dir / t).glob("*.jsonl")) for x in _read_jsonl(p)] for t in TYPES}
        land_ids = {r["id"] for r, _ in recs.get("land", [])}
        for type_key, rs in recs.items():
            # land with a build condition is listed under both new_house and land: keep the land listing
            items += [Item(r, today, raw=raw) for r, raw in rs
                      if not (type_key == "new_house" and r["id"] in land_ids and "/tochi/" in r.get("url", ""))]
        for p in sorted((pref_dir / "removed").glob("*.jsonl")):
            removed += [Item(r, today, removed=True, raw=raw) for r, raw in _read_jsonl(p)]
        with contextlib.suppress(OSError, ValueError):
            areas.update(json.loads((pref_dir / "areas.json").read_text(encoding="utf-8")))
    history, runs, new_dates = defaultdict(list), [], {}
    for path in sorted(data_dir.glob("events/*.json")):
        events = read_events(path)
        if events is None:
            continue
        runs.append(path.stem)
        for e in events:
            if e.get("kind") in ("new", "relisted"):
                new_dates[f"{e['type']}:{e['id']}"] = run_date(path.stem)
            if e.get("kind") == "price_changed":
                p = e.get("payload") or {}
                history[f"{e['type']}:{e['id']}"].append((run_date(path.stem), p.get("old_price"), p.get("price")))
    return Snapshot(items, removed, areas, dict(history), runs, today, new_dates)


class Catalog:
    """Holds the current Snapshot; `refresh()` reloads when data/ changed."""

    def __init__(self, data_dir, today_fn):
        self.data_dir = Path(data_dir)
        self.today_fn = today_fn
        self.sig = None
        self.snap = Snapshot([], [], {}, {}, [], today_fn())

    def changed(self):
        return signature(self.data_dir) != self.sig or self.snap.today != self.today_fn()

    def refresh(self):
        """Reload if anything changed. Returns True when a new snapshot was swapped in."""
        sig = signature(self.data_dir)
        today = self.today_fn()
        if sig == self.sig and today == self.snap.today:
            return False
        self.snap = load(self.data_dir, today)
        self.sig = sig
        return True
