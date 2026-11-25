"""What to crawl: scope.toml -> [(pref, type, area_codes or None)]."""
import tomllib
from dataclasses import dataclass

from .parse import TYPES

# SUUMO's prefecture path slugs -> names as written in addresses
PREFS = {
    "hokkaido_": "北海道",  # SUUMO's slug has the underscore
    "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県", "akita": "秋田県",
    "yamagata": "山形県", "fukushima": "福島県", "ibaraki": "茨城県", "tochigi": "栃木県", "gumma": "群馬県",
    "saitama": "埼玉県", "chiba": "千葉県", "tokyo": "東京都", "kanagawa": "神奈川県", "niigata": "新潟県",
    "toyama": "富山県", "ishikawa": "石川県", "fukui": "福井県", "yamanashi": "山梨県", "nagano": "長野県",
    "gifu": "岐阜県", "shizuoka": "静岡県", "aichi": "愛知県", "mie": "三重県", "shiga": "滋賀県", "kyoto": "京都府",
    "osaka": "大阪府", "hyogo": "兵庫県", "nara": "奈良県", "wakayama": "和歌山県", "tottori": "鳥取県",
    "shimane": "島根県", "okayama": "岡山県", "hiroshima": "広島県", "yamaguchi": "山口県", "tokushima": "徳島県",
    "kagawa": "香川県", "ehime": "愛媛県", "kochi": "高知県", "fukuoka": "福岡県", "saga": "佐賀県",
    "nagasaki": "長崎県", "kumamoto": "熊本県", "oita": "大分県", "miyazaki": "宮崎県", "kagoshima": "鹿児島県",
    "okinawa": "沖縄県",
}

# Each prefecture's bounding box (lat_min, lng_min, lat_max, lng_max): geo/towns.json's towns plus 0.1°
# (2026-11-15). A listing page's coordinates outside it aren't exported (a wrong point is worse than none).
PREF_BBOX = {
    "hokkaido_": (41.33, 139.72, 45.50, 145.70),
    "aomori": (40.25, 139.91, 41.40, 141.75),
    "iwate": (38.68, 140.79, 40.49, 142.08),
    "miyagi": (37.75, 140.35, 39.02, 141.77),
    "akita": (38.85, 139.75, 40.50, 140.92),
    "yamagata": (37.72, 139.47, 39.19, 140.62),
    "fukushima": (36.76, 139.48, 37.99, 141.12),
    "ibaraki": (35.67, 139.59, 36.99, 140.90),
    "tochigi": (36.11, 139.28, 37.23, 140.35),
    "gumma": (36.05, 138.43, 37.01, 139.75),
    "saitama": (35.66, 138.87, 36.37, 139.99),
    "chiba": (34.81, 139.66, 36.18, 140.96),
    "tokyo": (33.03, 139.02, 35.94, 140.01),
    "kanagawa": (35.03, 138.90, 35.75, 139.85),
    "niigata": (36.76, 137.55, 38.61, 139.73),
    "toyama": (36.41, 136.74, 37.06, 137.79),
    "ishikawa": (36.13, 136.19, 37.55, 137.37),
    "fukui": (35.37, 135.52, 36.39, 136.62),
    "yamanashi": (35.16, 138.12, 36.02, 139.21),
    "nagano": (35.16, 137.45, 36.99, 138.74),
    "gifu": (35.06, 136.36, 36.32, 137.70),
    "shizuoka": (34.50, 137.38, 35.52, 139.23),
    "aichi": (34.50, 136.58, 35.49, 137.70),
    "mie": (33.75, 135.95, 35.32, 136.99),
    "shiga": (34.72, 135.73, 35.61, 136.51),
    "kyoto": (34.61, 134.83, 35.83, 136.12),
    "osaka": (34.21, 135.02, 35.10, 135.84),
    "hyogo": (34.13, 134.21, 35.76, 135.56),
    "nara": (33.94, 135.56, 34.85, 136.31),
    "wakayama": (33.37, 134.98, 34.47, 136.06),
    "tottori": (35.05, 133.11, 35.68, 134.43),
    "shimane": (34.29, 131.67, 35.66, 133.37),
    "okayama": (34.34, 133.29, 35.41, 134.40),
    "hiroshima": (34.02, 131.99, 35.14, 133.55),
    "yamaguchi": (33.75, 130.78, 34.52, 132.53),
    "tokushima": (33.49, 133.70, 34.34, 134.79),
    "kagawa": (33.94, 133.49, 34.59, 134.50),
    "ehime": (32.83, 132.10, 34.38, 133.74),
    "kochi": (32.63, 132.59, 33.89, 134.34),
    "fukuoka": (32.90, 129.95, 34.06, 131.28),
    "saga": (32.89, 129.73, 33.63, 130.64),
    "nagasaki": (32.48, 128.74, 33.94, 130.47),
    "kumamoto": (32.06, 129.91, 33.22, 131.30),
    "oita": (32.67, 130.77, 33.78, 132.08),
    "miyazaki": (31.35, 130.65, 32.86, 131.80),
    "kagoshima": (27.29, 128.55, 32.22, 131.19),
    "okinawa": (24.22, 123.78, 26.83, 128.29),
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
    seen = set()
    for t in targets:
        if (t.pref, t.type) in seen:
            raise SystemExit(f"scope.toml: {t.pref}/{t.type} listed twice; merge the entries")
        seen.add((t.pref, t.type))
    return targets


def by_key(targets):
    """{(pref, type): Target}: one lookup per listing instead of a scan over every target."""
    return {(t.pref, t.type): t for t in targets}


def in_scope(scope, pref, type_key, area_code):
    """scope: by_key(targets)."""
    t = scope.get((pref, type_key))
    return t is not None and t.includes(area_code)
