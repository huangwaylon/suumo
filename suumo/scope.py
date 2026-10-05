"""What to crawl: scope.toml -> [(pref, type, area_codes or None)]."""
import tomllib
from dataclasses import dataclass

from .parse import TYPES

# SUUMO's prefecture path slugs
PREFS = {
    "hokkaido", "aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima", "ibaraki", "tochigi", "gunma",
    "saitama", "chiba", "tokyo", "kanagawa", "niigata", "toyama", "ishikawa", "fukui", "yamanashi", "nagano",
    "gifu", "shizuoka", "aichi", "mie", "shiga", "kyoto", "osaka", "hyogo", "nara", "wakayama", "tottori",
    "shimane", "okayama", "hiroshima", "yamaguchi", "tokushima", "kagawa", "ehime", "kochi", "fukuoka", "saga",
    "nagasaki", "kumamoto", "oita", "miyazaki", "kagoshima", "okinawa",
}


@dataclass(frozen=True)
class Target:
    pref: str
    type: str
    areas: frozenset | None  # None = every area in the prefecture

    def includes(self, area_code):
        return self.areas is None or area_code in self.areas


def load(path):
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    targets = []
    for i, s in enumerate(cfg.get("scope", []), 1):
        pref = s.get("pref")
        if pref not in PREFS:
            raise SystemExit(f"scope.toml entry {i}: unknown pref {pref!r} (use SUUMO slugs like 'tokyo', 'chiba')")
        types = s.get("types", list(TYPES))
        bad = [t for t in types if t not in TYPES]
        if bad:
            raise SystemExit(f"scope.toml entry {i}: unknown types {bad}; choose from {list(TYPES)}")
        areas = frozenset(str(a) for a in s["areas"]) if s.get("areas") else None
        targets += [Target(pref, t, areas) for t in types]
    seen = {}
    for t in targets:
        if (t.pref, t.type) in seen:
            raise SystemExit(f"scope.toml: {t.pref}/{t.type} listed twice; merge the entries")
        seen[(t.pref, t.type)] = t
    return targets


def in_scope(targets, pref, type_key, area_code):
    return any(t.pref == pref and t.type == type_key and t.includes(area_code) for t in targets)
