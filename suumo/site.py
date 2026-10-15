"""Build the static search site (GitHub Pages) from data/ and geo/towns.json.

  <out>/index.html, app.js, filter.js, i18n.js, style.css   copied from site/
  <out>/data/index.json                            manifest: prefectures (slug, name, count, detail share), saved list
  <out>/data/p/<pref>.json                         one prefecture's properties, search fields only, stored by column
  <out>/data/l/<type>/<id>.json                    one listing's full record, price history, other agents

The same property listed by several agents (same dup_key: same building/address, size and price) appears once,
as its best-documented listing; the others are linked from its record. The tricky fields (plan rank, sizes, age,
walk times, flags) are computed here, so site/filter.js only compares values. The output is not committed:
GitHub Actions builds it on every push.
"""
import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote_plus
from zoneinfo import ZoneInfo

from .catalog import READY_TAGS, SOUTH_TAGS, TOP_TAG, load, load_events, run_date, station_key
from .export import prefectures
from .geo import load_cache, town_of
from .parse import TYPES
from .rail import simplify
from .scope import PREFS
from .stations import names as station_names

JST = ZoneInfo("Asia/Tokyo")
SPLIT_KM = 3  # same-named stations further apart than this are different stations
STATIC = Path(__file__).resolve().parent.parent / "site"
IMAGE_PREFIX = "https://img01.suumo.com/jj/resizeImage?src="
RAIL_COARSE = 0.001  # degrees (~100 m): the lines' shape for the map zoomed out (below zoom 11)
FLAGS = {"leasehold": 1, "conditional": 2, "post1981": 4, "new": 8, "dropped": 16, "saved": 32, "gone": 64,
         "relisted": 128, "south": 256, "top": 512, "upper": 1024, "nofee": 2048, "ready": 4096, "tower": 8192,
         "parking": 16384}

# tags a condition covers (南向き, 最上階, 即入居, 建築条件なし, 土地): not offered as tags (filter.js Filter.upgrade
# turns old links' tags into the conditions); listing pages still show them
HIDDEN_TAGS = {*SOUTH_TAGS, TOP_TAG, *READY_TAGS, "建築条件なし", "土地50坪以上", "土地100坪以上"}

B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_image_path = re.compile(r"^gazo/bukken/([^/]+)/([^/]+)/img/([^/]+)/(\d+)/\4_([^/]+)$")
_ad_copy = re.compile(r"万円|[【】●◆◇★☆■□♪！!※]")  # agents' slogans and generated "town price" names


def _image(url, lid):
    """'…?src=gazo/bukken/030/N010000/img/670/<id>/<id>_0004.jpg&w=…' -> '030/N010000/670/0004.jpg' (app.js
    rebuilds it with the id), or just 4 for the usual 030/N010000/<last 3 digits of the id> (filter.js expands it);
    other forms keep their path, or the whole URL."""
    if not url or not url.startswith(IMAGE_PREFIX):
        return url
    path = url[len(IMAGE_PREFIX):].partition("&")[0]
    if "%" in path or "+" in path:
        path = unquote_plus(path)
    m = _image_path.match(path)
    if not m or m.group(4) != lid:
        return path
    if m.group(1, 2, 3) == ("030", "N010000", lid[-3:]) and re.fullmatch(r"\d{4}\.jpg", m.group(5)):
        return int(m.group(5)[:4])
    return "/".join(m.group(1, 2, 3, 5))


def _name(rec):
    """A building or development name worth showing, or None (many 'names' are ad copy):
    '『MAC目黒コート』TVモニター…' -> 'MAC目黒コート', '柿の木坂２ 4億8000万円' -> None."""
    name = (rec.get("name") or "").rstrip("…").strip()
    if m := re.search(r"『(.+?)』", name):
        name = m.group(1)
    return name if name and not _ad_copy.search(name) else None


def _access(walk, bus, car_km):
    if walk is not None:
        return walk
    if bus:
        return -bus
    return 1000 + round(car_km * 10) if car_km else None


def _area_group(code, name):
    """The filter panel's group for an area: 'ku' (Tokyo's 23 wards), the city for a designated city's ward
    (横浜市西区 -> 横浜市), 'shi' (cities), 'gun' (towns and villages)."""
    if code.startswith("131"):
        return "ku"
    if code[2] == "1":
        m = re.match(r"(.+?市)", name)
        return m.group(1) if m else "shi"
    return "shi" if code[2] == "2" else "gun"


def _date_int(s):
    return int(s.replace("-", "")) if s else 0


def _mask(numbers):
    """A set of small numbers as a bit string, 6 bits per character, lowest first: {0, 7} -> 'BC'."""
    bits, out = sum(1 << n for n in set(numbers)), ""
    while bits:
        out += B64[bits & 63]
        bits >>= 6
    return out


class Table:
    """value -> small integer, so repeated strings are stored once (None stays None)."""

    def __init__(self, values=()):
        self.index, self.values = {}, []
        for v in values:
            self(v)

    @classmethod
    def by_count(cls, values):
        """The values numbered most common first: small numbers are short."""
        n = Counter(v for v in values if v is not None)
        return cls(sorted(n, key=lambda v: (-n[v], v)))

    def __call__(self, value):
        if value is None:
            return None
        if value not in self.index:
            self.index[value] = len(self.values)
            self.values.append(value)
        return self.index[value]


def representatives(snap):
    """One listing per property: in each duplicate group the one with the fullest record (page fetched, most
    tags), then the lowest id. Returns {item: [the other listings]}, those without a commission first."""
    reps = {}
    for i in snap.items:
        group = snap.groups.get(i.dup)
        if not group or i.saved or i.gone:  # saved / ended listings aren't grouped: each stands for itself
            reps[i] = []
        elif i is min(group, key=lambda g: (not g.rec.get("has_detail"), -len(g.features), g.idn)):
            reps[i] = sorted((g for g in group if g is not i), key=lambda g: (not g.nofee, g.price_lo or 0, g.idn))
    return reps


def station_uses(reps, into=None):
    """station name -> the prefectures whose listings name it, added to `into` (the build collects it prefecture
    by prefecture)."""
    used = defaultdict(set) if into is None else into
    for i in reps:
        for name, _ in i.stations:
            used[name].add(i.pref)
    return used


def listing_spelling(rail, used):
    """Rail stations renamed to how the listings write them (MLIT 阿佐ヶ谷, SUUMO 阿佐ケ谷), so the site matches names
    exactly: a chosen station's lines on the map, two same-named stations. Only when one listing name fits.
    used: station_uses()."""
    spelled = defaultdict(set)
    for name in used:
        if "（" not in name:  # SUUMO's own 藤沢（長野県） stays as it is
            spelled[station_key(name)].add(name)
    for s in rail["stations"]:
        fits = spelled.get(station_key(s[0]), ())
        if s[0] not in used and len(fits) == 1:
            s[0] = next(iter(fits))
    return rail


def station_prefectures(used, rail_stations=()):
    """station name -> the prefectures to tell apart, for a name that is really two stations (小川町 in Tokyo and
    in Saitama: "小川町（埼玉）"). A name in listings of two prefectures is usually one station near the border
    (二子玉川); it's two only when the railway data has stations of that name more than SPLIT_KM apart.
    used: station_uses()."""
    where = defaultdict(list)
    for name, lat, lng, _ in rail_stations:
        if len(used.get(name, ())) > 1:
            where[name].append((lat, lng))
    far = {name for name, pts in where.items()
           if max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in pts for b in pts) * 111 > SPLIT_KM}
    return {name: prefs for name, prefs in used.items() if name in far}


def build_index(snap, reps, meta, towns_cache, readings, station_prefs):
    """The search index of these listings (one prefecture's, on the site). meta: item -> (_name, snap.new)."""

    def station(name, pref):
        return f"{name}（{re.sub('[都道府県]$', '', PREFS.get(pref, pref))}）" if name in station_prefs else name

    types, areas, towns = Table(TYPES), Table(), Table()
    stations = Table.by_count(station(name, i.pref) for i in reps for name, _ in i.stations)
    shown = {i: [f for f in i.features if f not in HIDDEN_TAGS] for i in reps}
    features = Table.by_count(f for fs in shown.values() for f in fs)  # so each tag set is a short bit string
    names = Table.by_count(meta[i][0] for i in reps)
    layouts = Table.by_count(i.rec.get("layout") for i in reps)
    cols = {k: [] for k in ("id", "type", "area", "price", "priceMax", "plan", "size", "land", "age", "stations",
                            "access", "stop", "features", "flags", "others", "newAt", "drop", "firstSeen", "unit",
                            "town", "name", "layout", "image", "fees")}
    last_id = 0
    for i in sorted(reps, key=lambda i: (i.type, i.idn)):
        (name, new), r, drop = meta[i], i.rec, snap.drop(i)
        town = town_of(r.get("address"), i.pref)
        flags = {"leasehold": i.leasehold, "conditional": i.conditional, "post1981": i.post_1981,
                 "new": snap.is_new_at(new), "dropped": drop, "saved": i.saved, "gone": i.gone,
                 "relisted": new and new[1] == "relisted", "south": i.south, "top": i.top, "upper": i.upper,
                 # no commission when any agent listing the property sells it without one
                 "nofee": i.nofee or any(o.nofee for o in reps[i]), "ready": i.ready, "tower": i.tower,
                 "parking": i.parking}
        row = {
            "id": i.idn - last_id, "type": types(i.type), "area": areas((i.area, i.pref)), "price": i.price_lo,
            "priceMax": i.price_hi if i.price_hi != i.price_lo else None, "plan": i.plan, "size": i.size,
            "land": i.land, "age": i.age_months,  # months
            # access to each station: minutes on foot; -minutes by bus; 1000 + km × 10 by car; null
            "stations": [stations(station(name, i.pref)) for name, _ in i.stations],
            "access": [_access(walk, i.bus.get(name), i.car.get(name)) for name, walk in i.stations],
            "stop": list(min(i.stops, key=lambda s: s[1] if s[1] is not None else 999)) if i.stops else None,
            "features": _mask(features(f) for f in shown[i]),
            "flags": sum(FLAGS[k] for k, on in flags.items() if on),
            # when it was new (YYYYMMDDHH), and its latest drop [YYYYMMDDHH, old price]: for 新着, 値下げ and
            # "since your last visit"
            "others": len(reps[i]), "newAt": new[0] if new else None, "drop": list(drop[:2]) if drop else None,
            "firstSeen": _date_int(i.first_seen),
            "unit": round(i.unit_price / 1000) if i.unit_price else None,   # 千円/㎡
            "town": towns(town or None), "name": names(name), "layout": layouts(r.get("layout")),
            "image": _image(r.get("image"), r["id"]),
            "fees": round(i.fees / 100) if i.fees is not None else None,   # condos' monthly fees, ¥100
        }
        last_id = i.idn
        for k, v in row.items():
            cols[k].append(v)
    return {
        "flags": FLAGS, "columns": cols, "types": types.values,
        "areas": [[code, snap.areas.get(code, code), PREFS.get(pref, pref), _area_group(code, snap.areas.get(code, ""))]
                  for code, pref in areas.values],
        "stations": stations.values,
        # [kana, English] per station (null when unknown: bus stops), so either finds it
        "stationNames": [readings.get(station_key(s)) for s in stations.values],
        "features": features.values, "names": names.values, "layouts": layouts.values,
        "towns": [[t, *(towns_cache.get(t) or [None, None])] for t in towns.values],
    }


def listing_record(snap, item, others, name, new):
    stops = {stop for stop, _ in item.stops}
    stations = [{**s, "bus_stop": True} if s.get("name") in stops else s for s in item.rec.get("stations") or []]
    return {**item.rec, "stations": stations or None, "history": snap.history.get(item.key, []),
            "new_date": run_date(str(new[0])) if new else None,
            "name": name,
            "others": [{"price": o.price_lo, "agent": o.rec.get("agent"), "url": o.rec.get("url"),
                        "deal_type": o.rec.get("deal_type")} for o in others]}


def _updated(data_dir):
    """When the data last changed: the last commit touching data/ (file times are checkout times in CI)."""
    for args in (["--", str(data_dir)], []):
        out = subprocess.run(["git", "-C", str(data_dir), "log", "-1", "--format=%ct", *args],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return datetime.fromtimestamp(int(out.stdout), JST).strftime("%Y-%m-%d %H:%M")
    return ""


def _detail_share(items):
    return round(sum(1 for i in items if i.rec.get("has_detail")) / len(items), 3) if items else 0


def _chains(parts):
    """A line's sections joined end to end (the railway data splits lines at every station and junction): a few
    long paths for the map to draw rather than thousands of short ones. Where lines branch, a path takes one way."""
    parts = [[tuple(p) for p in part] for part in parts if len(part) > 1]
    ends, used, out = defaultdict(list), [False] * len(parts), []
    for k, part in enumerate(parts):
        ends[part[0]].append(k)
        ends[part[-1]].append(k)

    def take(at):  # an unused section starting or ending at this point, oriented to start there
        for k in ends[at]:
            if not used[k]:
                used[k] = True
                return parts[k] if parts[k][0] == at else parts[k][::-1]
        return None

    for k, part in enumerate(parts):
        if used[k]:
            continue
        used[k], chain = True, list(part)
        for _ in range(2):  # grow at the end, then (reversed) at the start
            while (more := take(chain[-1])) is not None:
                chain += more[1:]
            chain.reverse()
        out.append(chain)
    return out


def rail_lines(lines):
    """[operator, name, English, colour, sections] -> [..., paths, coarse paths]: sections joined into paths and
    simplified again, plus a coarser copy for the map zoomed out (the page uses the paths when it's missing)."""
    out = []
    for *head, parts in lines:
        chains = _chains(parts)
        out.append([*head, [[list(p) for p in simplify(c)] for c in chains],
                    [[list(p) for p in simplify(c, RAIL_COARSE)] for c in chains]])
    return out


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build(data_dir, geo_cache, out_dir, today=None, log=print, stations_cache=None, saved=(), rail_path=None):
    """saved: the shared saved list ("type:id" keys); rail_path: geo/rail.json (stations' places, the map's lines).
    One prefecture in memory at a time: a first pass collects what the prefectures share (which station names
    they use: two same-named stations, the railway's spelling; their sizes; the saved list), a second builds and
    writes each. Returns the manifest."""
    rail = json.loads(Path(rail_path).read_text()) if rail_path and Path(rail_path).exists() else None
    places = [s[:] for s in rail["stations"]] if rail else ()  # as read: listing_spelling renames rail's
    today, events = today or datetime.now(JST).date(), load_events(data_dir)

    def properties(pref):
        snap = load(data_dir, today, saved, places, prefs=[pref], events=events)
        return snap, representatives(snap)

    used, counts, saved_at, n_listings = defaultdict(set), {}, {}, 0
    for pref in prefectures(data_dir):
        snap, reps = properties(pref)
        n_listings += len(snap.items)
        if reps:
            station_uses(reps, used)
            counts[pref] = (len(reps), _detail_share(reps))
            saved_at.update((i.key, i.pref) for i in reps if i.saved)
    rail = rail and listing_spelling(rail, used)
    station_prefs = station_prefectures(used, rail["stations"] if rail else ())

    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(STATIC, out)
    # the service worker's version: the page's files, so a new page never reads data cached for an old one
    version = hashlib.sha1(b"".join(f.read_bytes() for f in sorted(STATIC.glob("*")) if f.is_file())).hexdigest()[:12]
    (out / "sw.js").write_text((STATIC / "sw.js").read_text().replace("__VERSION__", version))
    (out / ".nojekyll").write_text("")
    readings = station_names(stations_cache) if stations_cache else {}
    towns, updated = load_cache(geo_cache), _updated(Path(data_dir))
    (out / "data" / "p").mkdir(parents=True)
    listings = out / "data" / "l"
    # one index per prefecture, loaded as chosen; the manifest says what there is (Tokyo first, then by size)
    built = sorted(counts, key=lambda p: (p != "tokyo", -counts[p][0]))
    for pref in built:
        snap, reps = properties(pref)
        meta = {i: (_name(i.rec), snap.new(i)) for i in reps}  # each worked out once: the index and the record use them
        (out / "data" / "p" / f"{pref}.json").write_text(
            _dump(build_index(snap, reps, meta, towns, readings, station_prefs)), encoding="utf-8")
        for type_ in {i.type for i in reps}:
            (listings / type_).mkdir(parents=True, exist_ok=True)
        for item, others in reps.items():
            with open(f"{listings}/{item.type}/{item.rec['id']}.json", "w", encoding="utf-8") as f:
                f.write(_dump(listing_record(snap, item, others, *meta[item])))
        del snap, reps, meta  # before the next prefecture is loaded
    manifest = {"updated": updated,
                # [slug, name, properties, share of them with their listing page fetched]
                "prefs": [[p, PREFS.get(p, p), *counts[p]] for p in built],
                "saved": saved_at}
    fx = Path(data_dir) / "fx.json"  # the yen-dollar rate (fx.py): the page shows prices in USD too
    if fx.exists():
        manifest["fx"] = json.loads(fx.read_text())
    (out / "data" / "index.json").write_text(_dump(manifest), encoding="utf-8")
    if rail:
        (out / "data" / "rail.json").write_text(_dump({**rail, "lines": rail_lines(rail["lines"])}), encoding="utf-8")
    sizes = ", ".join(f"{p} {(out / 'data' / 'p' / f'{p}.json').stat().st_size / 1e6:.1f} MB" for p in built)
    log(f"site: {sum(n for n, _ in counts.values()):,} properties ({n_listings:,} listings); indexes: {sizes} → {out}")
    return manifest
