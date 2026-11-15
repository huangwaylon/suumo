"""A listing's page record (suumo.extract) -> normalized fields (derive): what detail_json, the export and the site use.

Pure: reads only the record. Spec values are read in their one-line form (`flat`: whitespace collapsed,
'[ … ]' removed), station lines and features as written apart from surrounding whitespace, which is what the
listing-page parser before page records produced. PARSE_VERSION is bumped when derive's output changes for the same
record; runs re-derive every stored record then (no requests).
"""
import re

from .parse import clean, parse_built, parse_m2, parse_prices, parse_station, yen

PARSE_VERSION = 1


_num = re.compile(r"\d+(?:\.\d+)?")
_ws = re.compile(r"\s+")
_brk = re.compile(r"\[[^\[\]\n]{0,200}\]")  # bounded: linear time on any input
_TEXT_FIELDS = (  # field, spec labels: values kept as written (one-line form)
    ("direction", ("向き",)), ("zoning", ("用途地域",)), ("land_status", ("土地状況",)), ("land_category", ("地目",)),
    ("builder", ("施工",)), ("handover", ("引渡可能時期", "引き渡し時期")), ("energy", ("エネルギー消費性能",)),
    ("insulation", ("断熱性能",)), ("restrictions", ("その他制限事項",)), ("top_price_band", ("最多価格帯",)),
)


# Every key derive can return (a test checks the fixtures against it): what detail_json may hold.
FIELDS = frozenset({
    "lat", "lng", "photos",
    "stations", "price_excludes_building", "mgmt_fee", "mgmt_form", "repair_fee", "repair_fund_once",
    "other_monthly", "other_fees", "floor", "structure", "floors_above", "floors_below", "total_units",
    "units_for_sale", "land_rights", "land_rights_note", "coverage_pct", "far_pct", "parking", "site_m2", "road",
    "build_condition", "reform", "built", "built_planned", "utilities", "sale_schedule", "features", "deal_type",
    *(key for key, _ in _TEXT_FIELDS),
})


def flat(s):
    """Record text -> the single-line form the fields use: every whitespace run (U+3000 and no-break spaces too) one
    space, '[ … ]' groups removed. Changing it changes derive's output: bump PARSE_VERSION."""
    return _brk.sub("", _ws.sub(" ", s[:20_000]).strip()).strip()


def _int(s):
    m = _num.search(s or "")
    return int(float(m.group())) if m else None


def _monthly_sum(s):
    """'町会費：100円／月、インターネット：770円／月' -> 870 (only per-month amounts)."""
    total = 0
    for part in re.split(r"[、,]", s):
        if "／月" in part or "/月" in part:
            total += yen(part) or 0
    return total or None


def _structure(s):
    """'RC22階地下1階建' / '木造2階建（軸組工法）' / '11階/SRC15階建' / '2階建' -> (structure, above, below)."""
    s = s.split("/")[-1]
    m = re.match(r"\s*([^\d\s（(]*?)\s*(?:地上)?(\d+)階", s)
    if m:
        structure, above = m.group(1) or None, int(m.group(2))
    else:
        m = re.match(r"\s*([^\d\s（(]+)", s)
        structure, above = (m.group(1) if m else None), None
    b = re.search(r"地下(\d+)階", s)
    return structure, above, int(b.group(1)) if b else (0 if above else None)


def _tenure(s):
    """Land rights category for filtering; the full text goes to land_rights_note.

    '一部地上権（旧）、借地期間…' -> 地上権, '賃借権（旧）' -> 旧法借地権, '賃借権（普）' -> 普通借地権.
    """
    head = s.split("、")[0]
    if "定期借地" in head:
        return "定期借地権"
    if "地上権" in head:
        return "地上権"
    if "借地権" in head or "賃借権" in head:
        return "旧法借地権" if "旧" in head else "普通借地権" if "普" in head else "借地権"
    if "所有権" in head:
        return "所有権"
    return head[:20]


def _floor(s):
    m = re.search(r"(地下)?(\d+)階", s or "")
    return (-int(m.group(2)) if m.group(1) else int(m.group(2))) if m else None


def _ratios(s):
    """'60％・150％' / '建ペい率：50％、容積率：100％' / '50～60％ 200％' -> (coverage, far)."""
    vals = [int(x) for x in re.findall(r"(\d+)(?:～\d+)?\s*[％%]", s)]
    if len(vals) < 2:
        vals = [int(x) for x in re.findall(r"\d+", s)][:2]
    return (vals[0] if vals else None), (vals[1] if len(vals) > 1 else None)


def _parking(s, notes):
    if s:
        lo, hi = parse_prices(s)
        return clean({"status": re.split(r"[（(]", s)[0].strip(), "fee_min": lo, "fee_max": hi if hi != lo else None})
    m = re.search(r"駐車場：([^、]+)", notes or "")
    return {"status": m.group(1).strip()} if m else None


def _road(s):
    """Private-road share and frontage road:
    '無、北4ｍ幅（接道幅8ｍ）' -> {dir: 北, width_m: 4}; '23.73m2、北西4ｍ幅' -> {private_m2: 23.73, ...};
    '道路幅：10ｍ、アスファルト舗装、セットバック：48.24m2' -> {width_m: 10, setback_m2: 48.24}."""
    if not s:
        return None
    r = {}
    m = re.search(r"([北南東西]{1,2})\s*(\d+(?:\.\d+)?)\s*[ｍm]幅", s)
    if m:
        r["dir"], r["width_m"] = m.group(1), float(m.group(2))
    elif m := re.search(r"道路幅：\s*(\d+(?:\.\d+)?)\s*[ｍm]", s):
        r["width_m"] = float(m.group(1))
    head = s.split("、")[0]
    if m := re.search(r"私道部分\s*(\d+(?:\.\d+)?)\s*m", s):
        r["private_m2"] = float(m.group(1))
    elif "：" not in head and not head.startswith("無"):
        r["private_m2"] = parse_m2(head)[0]  # leading private-road area, e.g. '23.73m2'
    if m := re.search(r"セットバック：\s*(\d+(?:\.\d+)?)\s*m", s):
        r["setback_m2"] = float(m.group(1))
    r["text"] = s
    return clean(r)


def derive(page):
    """A page record -> normalized fields (keys in FIELDS; empty values omitted). lat/lng are the page's map point
    rounded to 4 decimals (about 10 m); photos is the gallery's length."""
    spec = page.get("spec", {})

    def g(*keys):
        """The first label's value in its one-line form (the record holds real values only)."""
        return next((flat(spec[k]) for k in keys if k in spec), None)

    notes = flat(page["texts"]["notes"]) if "notes" in page.get("texts", {}) else None
    notes = notes if notes not in ("", "-") else None
    r = {}
    if (lines := spec.get("交通")):
        r["stations"] = [st for st in (parse_station(s.strip()) for s in lines) if st]
    if (v := g("価格", "販売価格")) and ("土地のみの価格" in v or "建物価格は含みません" in v):
        r["price_excludes_building"] = True
    if (v := g("管理費")):
        r["mgmt_fee"] = yen(v)
        m = re.search(r"（(.+)）", v)
        r["mgmt_form"] = m.group(1) if m else None
    if (v := g("修繕積立金")):
        r["repair_fee"] = yen(v)
    if (v := g("修繕積立基金")):
        r["repair_fund_once"] = yen(v)
    if (v := g("諸費用")):
        r["other_monthly"] = _monthly_sum(v)
        r["other_fees"] = v
    if (v := g("所在階", "所在階/構造・階建")):
        r["floor"] = _floor(v)
    if (v := g("構造・階建て", "構造・工法", "所在階/構造・階建")):
        r["structure"], r["floors_above"], r["floors_below"] = _structure(v)
    if (v := g("総戸数", "総区画数")):
        r["total_units"] = _int(v)
    if (v := g("販売戸数", "販売区画数")):
        r["units_for_sale"] = _int(v)
    if (v := g("敷地の権利形態", "土地の権利形態")):
        r["land_rights"] = _tenure(v)
        if v != r["land_rights"]:
            r["land_rights_note"] = v
    if (v := g("建ぺい率・容積率", "建ぺい率･容積率")):
        r["coverage_pct"], r["far_pct"] = _ratios(v)
    r["parking"] = _parking(g("駐車場"), notes)
    if (v := g("敷地面積")):
        r["site_m2"] = parse_m2(v)[0]
    r["road"] = _road(g("私道負担・道路"))
    if (v := g("建築条件")):
        r["build_condition"] = v.startswith("付")
    if (v := g("リフォーム")):
        r["reform"] = clean({"date": parse_built(v), "text": v.split("※")[0].strip()})
    if (v := g("完成時期(築年月)", "完成時期（築年月）")):
        r["built"] = parse_built(v)
        if "予定" in v:
            r["built_planned"] = True
    if notes:
        m = re.search(r"設備：([^、]+(?:、(?!担当者|駐車場|建築確認)[^、：]+)*)", notes)
        if m:
            r["utilities"] = m.group(1)
    if (v := g("販売スケジュール")) and len(v) < 120:
        r["sale_schedule"] = v
    for key, labels in _TEXT_FIELDS:
        r[key] = g(*labels)
    r["features"] = sorted({f for f in (_ws.sub(" ", f).strip() for f in page.get("features", [])) if f})
    if (v := page.get("deal_type")):
        r["deal_type"] = _ws.sub(" ", v)
    if (ll := page.get("ll")):
        r["lat"], r["lng"] = round(ll["lat"], 4), round(ll["lng"], 4)
    if (img := page.get("img")):
        r["photos"] = len(img["l"])
    return clean(r)
