"""Parse SUUMO area-selection pages and search-result (list) pages."""
import math
import re
import unicodedata

from bs4 import BeautifulSoup

# type key -> SUUMO path segment. "new_condo" lists developments (projects), not individual units.
TYPES = {
    "used_condo": "ms/chuko",
    "new_house": "ikkodate",
    "used_house": "chukoikkodate",
    "land": "tochi",
    "new_condo": "ms/shinchiku",
}
PAGE_SIZE = 100

_ws = re.compile(r"\s+")
_price = re.compile(r"(?:(\d+(?:\.\d+)?)億)?(?:(\d+(?:\.\d+)?)万)?(\d+)?円")
_m2 = re.compile(r"(\d+(?:\.\d+)?)\s*m\s*(?:2|²)")
_built = re.compile(r"(\d{4})年(\d{1,2})月")
_station = re.compile(r"(.*?)「(.+?)」")
_walk = re.compile(r"(?:徒)?歩(\d+)分")
_bus = re.compile(r"バス(\d+)分")
_car = re.compile(r"車\s*([\d.]+)\s*km(?:\s*[～~]\s*([\d.]+)\s*km)?")  # rural access: 車2.4km, 車4.8km～5.9km
_nc = re.compile(r"/nc_(\d+)/")


def text(el):
    return _ws.sub(" ", el.get_text(" ")).strip() if el else ""


def yen(s):
    """First amount: '1万5480円' -> 15480, '1億2000万円' -> 120000000, '8702万8000円' -> 87028000."""
    return parse_prices(s)[0]


def parse_prices(s):
    """All yen amounts in s as (min, max); text after ※ is a footnote and ignored. '未定' -> (None, None)."""
    vals = []
    for oku, man, en in _price.findall(s.split("※")[0]):
        if oku or man or en:
            vals.append(round(float(oku or 0) * 1e8 + float(man or 0) * 1e4 + int(en or 0)))
    return (min(vals), max(vals)) if vals else (None, None)


def parse_m2(s):
    vals = [float(v) for v in _m2.findall(s.replace(" ", ""))]
    return (min(vals), max(vals)) if vals else (None, None)


def parse_built(s):
    m = _built.search(s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else None


def parse_station(s):
    """'京王線「仙川」歩16分' / 'ＪＲ中央線「吉祥寺」バス12分停歩3分' / '小田急線/経堂 徒歩6分' /
    'ＪＲ常磐線「牛久」車2.4km' -> station dict."""
    m = _station.search(s)
    if m:
        st = {"line": m.group(1).strip() or None, "name": m.group(2)}
        rest = s[m.end():]
    else:  # new-condo list format: "line/station 徒歩N分"
        m = re.match(r"(.+?)/(\S+)", s)
        if not m:
            return None
        st = {"line": m.group(1), "name": m.group(2)}
        rest = s[m.end():]
    if st["line"] and "バス" in st["line"]:  # a bus line's stop listed as access, not a rail station
        st["bus_stop"] = True
    b = _bus.search(rest)
    if b:
        st["bus"] = int(b.group(1))
        rest = rest[b.end():]
    w = _walk.search(rest)
    if w:
        st["walk"] = int(w.group(1))
    car = _car.search(unicodedata.normalize("NFKC", rest))
    if car and not w and not b:
        st["car_km"] = float(car.group(1))
        if car.group(2):
            st["car_km_max"] = float(car.group(2))
    return st


def town(address, area_name):
    """'東京都品川区戸越３-9-16' + '品川区' -> '戸越３-9-16'."""
    a = re.sub(r"^(東京都|北海道|(?:京都|大阪)府|.{2,3}県)", "", address or "")
    return a[len(area_name):] if area_name and a.startswith(area_name) else a


def hit_count(soup):
    el = soup.select_one(".pagination_set-hit") or soup.select_one(".hitbox")
    m = re.search(r"[\d,]+", text(el)) if el else None
    return int(m.group().replace(",", "")) if m else 0


def page_count(hits):
    return max(1, math.ceil(hits / PAGE_SIZE))


def parse_areas(html, type_path, pref):
    """Area-selection page -> [{code, name, slug, expected}], zero-count areas included (slug may be None)."""
    soup = BeautifulSoup(html, "lxml")
    areas = {}
    for inp in soup.select('input[name="sc"]'):
        code = inp.get("value")
        label = inp.find_next("label")
        if not code or not label:
            continue
        m = re.search(r"\(([\d,]+)\)", text(label))
        name = re.sub(r"\([\d,]+\)", "", text(label)).strip()
        areas.setdefault(code, {"code": code, "name": name, "slug": None,
                                "expected": int(m.group(1).replace(",", "")) if m else 0})
    slug_re = re.compile(rf"^/{re.escape(type_path)}/{pref}/(sc_[a-z0-9_]+)/$")
    for a in soup.find_all("a", href=slug_re):
        slug = slug_re.match(a["href"]).group(1)
        m = re.match(r"js-linkSc(\d+)$", a.get("id", ""))
        if m:
            code = next((c for c in areas if c.endswith(m.group(1))), None)
        else:
            inp = a.find_previous("input", attrs={"name": "sc"})
            code = inp.get("value") if inp else None
        if code in areas and not areas[code]["slug"]:
            areas[code]["slug"] = slug
    return list(areas.values())


def parse_list_page(html, type_key, area):
    """Search-result page -> (hits, [record]). Records hold list-page fields only."""
    soup = BeautifulSoup(html, "lxml")
    if type_key == "new_condo":
        units, parse = soup.select("div.cassette.property_unit"), _new_condo_unit
    else:
        units, parse = soup.select("div.property_unit"), _standard_unit
    return hit_count(soup), [r for r in (parse(u, area) for u in units) if r]


def _base(u):
    a = u.find("a", href=_nc)
    if not a:
        return None
    path = a["href"].split("?")[0]
    return {"id": _nc.search(path).group(1), "path": path}


def _price_into(r, s):
    r["price"], hi = parse_prices(s)
    if hi != r["price"]:
        r["price_max"] = hi
    if "建物価格は含みません" in s or "土地のみの価格" in s:
        r["price_excludes_building"] = True


def _m2_into(r, key, s):
    lo, hi = parse_m2(s)
    r[key] = lo
    if hi != lo:
        r[key + "_max"] = hi


def _standard_unit(u, area):
    r = _base(u)
    if not r:
        return None
    r["title"] = text(u.select_one(".property_unit-title"))
    for dl in u.select(".property_unit-info dl"):
        dt, dd = dl.find("dt"), dl.find("dd")
        if not dt or not dd:
            continue
        k, v = text(dt), _ws.sub(" ", dd.get_text("")).strip()
        if k == "物件名":
            r["name"] = v
        elif k == "販売価格":
            _price_into(r, v)
        elif k == "所在地":
            r["address"] = v
            r["town"] = town(v, area["name"])
        elif k == "沿線・駅":
            st = parse_station(v)
            r["stations"] = [st] if st else []
        elif k == "専有面積":
            _m2_into(r, "floor_m2", v)
        elif k == "間取り":
            r["layout"] = v
        elif k == "バルコニー":
            _m2_into(r, "balcony_m2", v)
        elif k == "築年月":
            r["built"] = parse_built(v)
        elif k == "土地面積":
            _m2_into(r, "land_m2", v)
        elif k == "建物面積":
            _m2_into(r, "building_m2", v)
    r["agent"] = text(u.select_one(".shopmore-title"))
    r["image"] = _image(u)
    return clean(r)


def _new_condo_unit(u, area):
    r = _base(u)
    if not r:
        return None
    r["name"] = text(u.select_one(".cassette_header-title")) or text(u.find("a", href=_nc))
    for item in u.select(".cassette_basic-item"):
        k, v = text(item.select_one(".cassette_basic-title")), text(item.select_one(".cassette_basic-value"))
        if k == "所在地":
            r["address"] = v
            r["town"] = town(v, area["name"])
        elif k == "交通":
            st = parse_station(v)
            r["stations"] = [st] if st else []
        elif k == "引渡時期":
            r["handover"] = v
    price = text(u.select_one(".cassette_price"))
    _price_into(r, price)
    _m2_into(r, "floor_m2", price)
    layouts = sorted(set(re.findall(r"\d[SLDK]+(?:\+\d?S)?|ワンルーム|1R", price)))
    r["layout"] = "・".join(layouts) or None
    r["image"] = _image(u)
    return clean(r)


def _image(u):
    """The listing photo: lazy-loaded, its URL is in rel."""
    img = u.select_one("img[rel]")
    rel = img.get("rel") if img else None
    return rel[0] if isinstance(rel, list) else rel


def clean(r):
    """Drop empty values so records stay small and diffs stay quiet."""
    return {k: v for k, v in r.items() if v not in (None, "", "-", [], {})}
