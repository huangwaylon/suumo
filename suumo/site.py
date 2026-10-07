"""Build the static search site (GitHub Pages) from data/ and geo/towns.json.

  <out>/index.html, app.js, filter.js, i18n.js, style.css   copied from site/
  <out>/data/index.json                            manifest: the prefectures (slug, name, count), the saved list
  <out>/data/p/<pref>.json                         one prefecture's properties, search fields only, stored by column
  <out>/data/l/<type>/<id>.json                    one listing's full record, price history, other agents

The same property listed by several agents (same dup_key: same building/address, size and price) appears once,
as its best-documented listing; the others are linked from its record. The tricky fields (plan rank, sizes, age,
walk times, flags) are computed here, so site/filter.js only compares values. The output is not committed:
GitHub Actions builds it on every push.
"""
import json
import re
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .catalog import load
from .geo import load_cache, town_of
from .parse import TYPES
from .scope import PREFS
from .stations import key as station_key
from .stations import names as station_names

JST = ZoneInfo("Asia/Tokyo")
STATIC = Path(__file__).resolve().parent.parent / "site"
IMAGE_PREFIX = "https://img01.suumo.com/jj/resizeImage?src="
FLAGS = {"leasehold": 1, "conditional": 2, "post1981": 4, "new": 8, "dropped": 16, "saved": 32, "gone": 64,
         "relisted": 128}

_image_path = re.compile(r"^gazo/bukken/([^/]+)/([^/]+)/img/([^/]+)/(\d+)/\4_([^/]+)$")
_ad_copy = re.compile(r"万円|[【】●◆◇★☆■□♪！!※]")  # agents' slogans and generated "town price" names


def _image(url, lid):
    """'…?src=gazo/bukken/030/N010000/img/670/<id>/<id>_0004.jpg&w=…' -> '030/N010000/670/0004.jpg' (app.js
    rebuilds it with the id); other forms keep their path, or the whole URL."""
    if not url or not url.startswith(IMAGE_PREFIX):
        return url
    path = parse_qs(urlparse(url).query).get("src", [""])[0]
    m = _image_path.match(path)
    return "/".join(m.group(1, 2, 3, 5)) if m and m.group(4) == lid else path


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


def _new_date(at):
    """(2026100619, kind) -> '2026-10-06'."""
    return f"{str(at[0])[:4]}-{str(at[0])[4:6]}-{str(at[0])[6:8]}" if at else None


def _date_int(s):
    return int(s.replace("-", "")) if s else 0


class Table:
    """value -> small integer, so repeated strings are stored once."""

    def __init__(self, values=()):
        self.index, self.values = {}, []
        for v in values:
            self(v)

    def __call__(self, value):
        if value not in self.index:
            self.index[value] = len(self.values)
            self.values.append(value)
        return self.index[value]


def representatives(snap):
    """One listing per property: in each duplicate group the one with the fullest record (page fetched, most
    tags), then the lowest id. Returns {item: [the other listings]}."""
    reps = {}
    for i in snap.items:
        group = snap.groups.get(i.dup)
        if not group or i.saved or i.gone:  # saved / ended listings aren't grouped: each stands for itself
            reps[i] = []
        elif i is min(group, key=lambda g: (not g.rec.get("has_detail"), -len(g.features), g.idn)):
            reps[i] = [g for g in group if g is not i]
    return reps


def station_prefectures(reps):
    """station name -> the prefectures it's in (a name in two is two stations: "小川町（埼玉）")."""
    out = defaultdict(set)
    for i in reps:
        for name, _, _ in i.stations:
            out[name].add(i.pref)
    return out


def build_index(snap, reps, towns_cache, updated, readings=None, station_prefs=None):
    """The search index of these listings (one prefecture's, on the site)."""
    types, areas, stations, features, towns = Table(TYPES), Table(), Table(), Table(), Table()
    station_prefs = station_prefs if station_prefs is not None else station_prefectures(reps)

    def station(name, pref):
        return name if len(station_prefs[name]) < 2 else f"{name}（{re.sub('[都道府県]$', '', PREFS.get(pref, pref))}）"

    cols = {k: [] for k in ("id", "type", "area", "price", "priceMax", "plan", "size", "land", "age", "built",
                            "stations", "features", "flags", "others", "newAt", "drop", "firstSeen", "unit", "town",
                            "name", "layout", "image")}
    last_id = 0
    for i in sorted(reps, key=lambda i: (i.type, i.idn)):
        r = i.rec
        town = town_of(r.get("address"), i.pref)
        row = {
            "id": i.idn - last_id, "type": types(i.type), "area": areas((i.area, i.pref)), "price": i.price_lo,
            "priceMax": i.price_hi if i.price_hi != i.price_lo else None, "plan": i.plan, "size": i.size,
            "land": i.land, "age": i.age, "built": i.built,
            # pairs (station, access): minutes on foot; -minutes by bus; 1000 + km × 10 by car; null
            "stations": [x for name, _, walk in i.stations for x in (
                stations(station(name, i.pref)), _access(walk, i.bus.get(name), i.car.get(name)))],
            "features": [features(f) for f in i.features],
            "flags": (FLAGS["leasehold"] * i.leasehold | FLAGS["conditional"] * i.conditional
                      | FLAGS["post1981"] * i.post_1981 | FLAGS["new"] * snap.is_new(i)
                      | FLAGS["dropped"] * snap.is_dropped(i) | FLAGS["saved"] * i.saved | FLAGS["gone"] * i.gone
                      | FLAGS["relisted"] * bool((snap.new(i) or (0, ""))[1] == "relisted")),
            # when it was new (YYYYMMDDHH), and its latest drop [YYYYMMDDHH, old price]: for 新着, 値下げ and
            # "since your last visit"
            "others": len(reps[i]), "newAt": (snap.new(i) or (None,))[0],
            "drop": list(snap.drop(i)[:2]) if snap.drop(i) else None,
            "firstSeen": _date_int(i.first_seen),
            "unit": round(i.unit_price / 1000) if i.unit_price else None,   # 千円/㎡
            "town": towns(town) if town else None, "name": _name(r), "layout": r.get("layout"),
            "image": _image(r.get("image"), r["id"]),
        }
        last_id = i.idn
        for k, v in row.items():
            cols[k].append(v)
    return {
        "updated": updated, "flags": FLAGS, "columns": cols, "types": types.values,
        "areas": [[code, snap.areas.get(code, code), PREFS.get(pref, pref), _area_group(code, snap.areas.get(code, ""))]
                  for code, pref in areas.values],
        "stations": stations.values,
        # [kana, English] per station (null when unknown: bus stops), so either finds it
        "stationNames": [(readings or {}).get(station_key(s)) for s in stations.values],
        "features": features.values,
        "towns": [[t, *(towns_cache.get(t) or [None, None])] for t in towns.values],
    }


def listing_record(snap, item, others):
    return {**item.rec, "history": snap.history.get(item.key, []), "new_date": _new_date(snap.new(item)),
            "name": _name(item.rec),
            "others": [{"price": o.price_lo, "agent": o.rec.get("agent"), "url": o.rec.get("url")} for o in others]}


def _updated(data_dir):
    """When the data last changed: the last commit touching data/ (file times are checkout times in CI)."""
    for args in (["--", str(data_dir)], []):
        out = subprocess.run(["git", "-C", str(data_dir), "log", "-1", "--format=%ct", *args],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return datetime.fromtimestamp(int(out.stdout), JST).strftime("%Y-%m-%d %H:%M")
    return ""


def _dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build(data_dir, geo_cache, out_dir, today=None, log=print, stations_cache=None, saved=()):
    """saved: the shared saved list ("type:id" keys). Returns {"manifest": ..., <pref slug>: its index}."""
    snap = load(data_dir, today or datetime.now(JST).date(), saved)
    reps = representatives(snap)
    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(STATIC, out)
    (out / ".nojekyll").write_text("")
    readings, towns, updated = station_names(stations_cache) if stations_cache else {}, load_cache(geo_cache), \
        _updated(Path(data_dir))
    (out / "data" / "p").mkdir(parents=True)
    by_pref = defaultdict(dict)
    for item, others in reps.items():
        by_pref[item.pref][item] = others
    station_prefs = station_prefectures(reps)
    # one index per prefecture, loaded as chosen; the manifest says what there is (Tokyo first, then by size)
    built = {}
    for pref in sorted(by_pref, key=lambda p: (p != "tokyo", -len(by_pref[p]))):
        built[pref] = build_index(snap, by_pref[pref], towns, updated, readings, station_prefs)
        (out / "data" / "p" / f"{pref}.json").write_text(_dump(built[pref]), encoding="utf-8")
    manifest = {"updated": updated, "flags": FLAGS,
                "prefs": [[p, PREFS.get(p, p), len(by_pref[p])] for p in built],
                "saved": {i.key: i.pref for i in reps if i.saved}}
    (out / "data" / "index.json").write_text(_dump(manifest), encoding="utf-8")
    rail = Path(geo_cache).with_name("rail.json")  # the map's railway lines and stations (suumo.rail)
    if rail.exists():
        shutil.copyfile(rail, out / "data" / "rail.json")
    for item, others in reps.items():
        path = out / "data" / "l" / item.type / f"{item.rec['id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(listing_record(snap, item, others)), encoding="utf-8")
    sizes = ", ".join(f"{p} {(out / 'data' / 'p' / f'{p}.json').stat().st_size / 1e6:.1f} MB" for p in built)
    log(f"site: {len(reps):,} properties ({len(snap.items):,} listings); indexes: {sizes} → {out}")
    return {"manifest": manifest, **built}
