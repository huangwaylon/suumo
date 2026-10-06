"""What to crawl: scope.toml -> [(pref, type, area_codes or None)]."""
import tomllib
from dataclasses import dataclass

from .parse import TYPES

# SUUMO's prefecture path slugs -> names as written in addresses
PREFS = {
    "hokkaido": "北海道", "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県", "akita": "秋田県",
    "yamagata": "山形県", "fukushima": "福島県", "ibaraki": "茨城県", "tochigi": "栃木県", "gunma": "群馬県",
    "saitama": "埼玉県", "chiba": "千葉県", "tokyo": "東京都", "kanagawa": "神奈川県", "niigata": "新潟県",
    "toyama": "富山県", "ishikawa": "石川県", "fukui": "福井県", "yamanashi": "山梨県", "nagano": "長野県",
    "gifu": "岐阜県", "shizuoka": "静岡県", "aichi": "愛知県", "mie": "三重県", "shiga": "滋賀県", "kyoto": "京都府",
    "osaka": "大阪府", "hyogo": "兵庫県", "nara": "奈良県", "wakayama": "和歌山県", "tottori": "鳥取県",
    "shimane": "島根県", "okayama": "岡山県", "hiroshima": "広島県", "yamaguchi": "山口県", "tokushima": "徳島県",
    "kagawa": "香川県", "ehime": "愛媛県", "kochi": "高知県", "fukuoka": "福岡県", "saga": "佐賀県",
    "nagasaki": "長崎県", "kumamoto": "熊本県", "oita": "大分県", "miyazaki": "宮崎県", "kagoshima": "鹿児島県",
    "okinawa": "沖縄県",
}


@dataclass(frozen=True)
class Target:
    pref: str
    type: str
    areas: frozenset | None  # None = every area in the prefecture
    rotate: bool = False     # crawled in turn with the other rotating prefectures, not every run

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
        targets += [Target(pref, t, areas, bool(s.get("rotate"))) for t in types]
    seen = set()
    for t in targets:
        if (t.pref, t.type) in seen:
            raise SystemExit(f"scope.toml: {t.pref}/{t.type} listed twice; merge the entries")
        seen.add((t.pref, t.type))
    return targets


def in_scope(targets, pref, type_key, area_code):
    return any(t.pref == pref and t.type == type_key and t.includes(area_code) for t in targets)
