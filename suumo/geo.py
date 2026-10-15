"""Map coordinates for listings, at town (丁目) level: MLIT 位置参照情報 in bulk, else 国土地理院's address search.

Listings carry addresses, not coordinates, and SUUMO shows most only to the 丁目 ("東京都狛江市岩戸北３"). Each
distinct town is looked up once and cached in geo/towns.json (git-tracked, sorted): {town: [lat, lng] or null}.
Later runs only look up towns they haven't seen: first in MLIT's 大字・町丁目レベル位置参照情報 (one file per
prefecture with every 丁目's representative point; the address search gives the same points), then the few left
in the address search, one request at a time, GEO_DELAY apart. The towns MLIT's files don't place wait for the
address search under "_bulk" in the cache ({"url": PLACES_URL, "missed": [town, ...]}), so their prefectures' files
aren't downloaded again every run while the address search works through them; a new PLACES_URL tries them again.
"""
import csv
import io
import json
import re
import time
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

import requests

from .archive import write_atomic
from .db import flock
from .http import USER_AGENT
from .scope import PREFS

API = "https://msearch.gsi.go.jp/address-search/AddressSearch"
GEO_DELAY = 1.0
SAVE_EVERY = 100  # towns between cache saves (an interrupted fill keeps its progress)
# 大字・町丁目レベル位置参照情報 (MLIT, free): a new version every spring; 'b' is the 大字・町丁目 level
PLACES_URL = "https://nlftp.mlit.go.jp/isj/dls/data/19.0b/{code:02d}000-19.0b.zip"
BULK_MISSES = "_bulk"  # the cache's entry of towns PLACES_URL's files don't place (not a town: towns start 都道府県)

_block = re.compile(r"[0-9][0-9\-－]*.*$")   # 番地 / 号 in ASCII digits after the 丁目 (full-width digit)
_wide_block = re.compile(r"([０-９])[\-－‐番].*$")  # ...or in full-width digits: 下連雀３-４１－１２ -> 下連雀３
_wide_number = re.compile(r"[０-９]{2,}.*$")          # a 番地 without 丁目: 上代継６０－１ -> 上代継


def town_of(address, pref):
    """'東京都狛江市東和泉２-20-20' -> '東京都狛江市東和泉２'; prefixes the prefecture when SUUMO omits it."""
    if not address:
        return None
    town = _wide_number.sub("", _wide_block.sub(r"\1", _block.sub("", address))).rstrip("-－ ")
    name = PREFS.get(pref, "")
    if name and not town.startswith(name):
        town = name + town
    return town or None


def _municipality(town):
    """'東京都狛江市岩戸北３' -> '東京都狛江市' (to check that a result is in the right place)."""
    m = re.match(r"(.+?[都道府県].+?[市区町村])", town)
    return _same_spelling(m.group(1) if m else town)


def _same_spelling(s):
    """The geocoder's titles drop the 郡 (神奈川県足柄下郡箱根町 -> 神奈川県箱根町) and write ケ for ヶ."""
    return re.sub(r"^(.+?[都道府県])[^都道府県市区町村]+?郡", r"\1", s).replace("ヶ", "ケ")


def load_cache(path):
    """The cache, or {} when there is none yet. A file that can't be read or parsed raises (rather than being
    treated as empty and then overwritten)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return json.loads(text)


def save_cache(path, cache):
    write_atomic(path, (json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True) + "\n").encode())


def lookup(session, town):
    """[lat, lng] of the first result in the same municipality, or None (not found / elsewhere)."""
    r = session.get(API, params={"q": town}, timeout=30)
    r.raise_for_status()
    for hit in r.json():
        if _same_spelling(hit["properties"]["title"]).startswith(_municipality(town)):
            lng, lat = hit["geometry"]["coordinates"]
            return [round(lat, 6), round(lng, 6)]
    return None


def towns_in(data_dir):
    """Every town of the active listings in data/."""
    towns = set()
    for pref_dir in Path(data_dir).iterdir():
        if not pref_dir.is_dir() or pref_dir.name == "events":
            continue
        for path in pref_dir.glob("*/*.jsonl"):
            if path.parent.name == "removed":
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                town = town_of(json.loads(line).get("address"), pref_dir.name)
                if town:
                    towns.add(town)
    return towns


def geocode(data_dir, cache_path, budget_seconds=None, log=print):
    """Look up the towns not in the cache yet. Errors are logged and retried on the next call. One geocoder at a
    time: while another holds the cache (a long `geocode` next to the runs), this one skips."""
    with flock(f"{cache_path}.lock") as held:
        if not held:
            log("geocoding: another geocoder is running; skipped")
            return 0
        return _geocode(data_dir, cache_path, budget_seconds, log)


_KANJI = {c: i for i, c in enumerate("〇一二三四五六七八九")}


def _number(kanji):
    """'三' -> 3, '十二' -> 12, '二十' -> 20, '一一' -> 11 (digit by digit)."""
    if "十" not in kanji:
        return int("".join(str(_KANJI[c]) for c in kanji))
    tens, _, ones = kanji.partition("十")
    return (_KANJI[tens] if tens else 1) * 10 + (_KANJI[ones] if ones else 0)


def place_key(town):
    """The form towns are matched in: SUUMO's '千葉県印旛郡酒々井町飯積２' and MLIT's '千葉県' + '印旛郡酒々井町' +
    '飯積二丁目' -> '千葉県酒々井町飯積2'; '大字' / '字' before the name dropped, ヶ as ケ."""
    s = unicodedata.normalize("NFKC", town).replace("ヶ", "ケ").replace("ヵ", "ケ")
    s = re.sub(r"^(.+?[都道府県]).+?郡", r"\1", s)
    s = re.sub(r"^(.+?[都道府県].+?[市区町村])大?字", r"\1", s)
    s = re.sub(r"([〇一二三四五六七八九十]+)丁目$", lambda m: str(_number(m.group(1))), s)
    return re.sub(r"(\d+)丁目$", r"\1", s)


class Places:
    """MLIT's 丁目 points by place_key; a name without its 丁目 ('赤塚', with 赤塚一〜三丁目) is their middle."""

    def __init__(self):
        self.points, self.chome = {}, defaultdict(list)

    def add(self, csv_text):
        for r in csv.DictReader(io.StringIO(csv_text)):
            key = place_key(r["都道府県名"] + r["市区町村名"] + r["大字町丁目名"])
            self.points[key] = point = [float(r["緯度"]), float(r["経度"])]
            self.chome[re.sub(r"\d+$", "", key)].append(point)

    def _at(self, key):
        if key in self.points:
            return self.points[key]
        ps = [] if re.search(r"\d$", key) else self.chome.get(key)
        return [round(sum(p[0] for p in ps) / len(ps), 6), round(sum(p[1] for p in ps) / len(ps), 6)] if ps else None

    def find(self, town):
        """[lat, lng] of the town, else of the longest place its name starts with (SUUMO adds 小字, lot notes:
        '静岡県裾野市須山主要地' -> 須山); None when nothing past the municipality matches."""
        key = place_key(town)
        city = re.match(r".+?[都道府県].+?[市区町村]", key)
        for n in range(len(key), city.end() if city else len(key) - 1, -1):
            if point := self._at(key[:n]):
                return point
        return None


def bulk(towns, log=print):
    """{town: [lat, lng], or None when MLIT's file doesn't place it} for the towns of the prefectures whose files
    were read (one download per prefecture they're in); a file that couldn't be read leaves its towns out."""
    codes = {name: i for i, name in enumerate(PREFS.values(), 1)}  # PREFS is in JIS order
    places, read = Places(), []
    for name in sorted({n for t in towns for n in codes if t.startswith(n)}):
        try:
            r = requests.get(PLACES_URL.format(code=codes[name]), timeout=120)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                places.add(z.read(next(n for n in z.namelist() if n.endswith(".csv"))).decode("cp932"))
            read.append(name)
        except (requests.RequestException, zipfile.BadZipFile, StopIteration) as e:
            log(f"  ! 位置参照情報 for {name}: {e!r} (the address search covers it)")
    return {t: places.find(t) for t in towns if any(t.startswith(n) for n in read)}


def _geocode(data_dir, cache_path, budget_seconds, log):
    cache = load_cache(cache_path)
    misses = cache.pop(BULK_MISSES, None) or {}
    todo = sorted(towns_in(data_dir) - set(cache))
    missed = set(misses.get("missed", ())) & set(todo) if misses.get("url") == PLACES_URL else set()

    def save():  # the misses still waiting for the address search with it
        left = sorted(missed - cache.keys())
        save_cache(cache_path, {**cache, BULK_MISSES: {"url": PLACES_URL, "missed": left}} if left else cache)

    if not todo:
        if misses:
            save()  # every miss found since, or gone from the listings
        return 0
    placed = bulk([t for t in todo if t not in missed], log)
    found = {t: p for t, p in placed.items() if p}
    missed |= placed.keys() - found.keys()
    if placed:
        cache.update(found)
        save()
        log(f"位置参照情報: {len(found):,} of {len(placed):,} new towns")
        todo = [t for t in todo if t not in found]
        if not todo:
            return len(found)
    log(f"geocoding {len(todo):,} new towns (~{len(todo) * GEO_DELAY / 60:.0f} min)")
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    deadline = time.monotonic() + budget_seconds if budget_seconds else None
    done = failures = 0
    for town in todo:
        if deadline and time.monotonic() > deadline:
            break
        try:
            cache[town] = lookup(session, town)
            done += 1
        except (requests.RequestException, ValueError, KeyError) as e:
            failures += 1
            log(f"  ! {town}: {e!r}")
            if failures >= 10:
                log("  geocoding stopped after 10 errors; the rest is retried next time")
                break
        if done and done % SAVE_EVERY == 0:
            save()
            log(f"  geocoded {done:,}/{len(todo):,}")
        time.sleep(GEO_DELAY)
    save()
    missing = sum(1 for t in todo if t in cache and cache[t] is None)
    log(f"geocoded {done:,} towns ({missing} not found)")
    return len(found) + done
