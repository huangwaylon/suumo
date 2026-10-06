"""Parse a listing's own (detail) page into normalized fields.

The page has a spec table of <th>label</th><td>value</td> rows (labels carry a trailing 'ヒント' link),
a 特徴ピックアップ feature-tag list, and the agent block with 取引態様 (deal type).
"""
import re

from bs4 import BeautifulSoup

from .parse import clean, parse_built, parse_m2, parse_prices, parse_station, text, yen

_num = re.compile(r"\d+(?:\.\d+)?")


def spec_table(soup):
    """{label: (value, td)} from every th/td pair; first occurrence wins (the summary and full tables repeat)."""
    spec = {}
    for th in soup.find_all("th"):
        td = th.find_next_sibling("td")
        k = re.sub(r"ヒント|\s", "", th.get_text())
        if td and k and len(k) < 20 and k not in spec:
            spec[k] = (re.sub(r"\[[^\]]*\]", "", text(td)).strip(), td)  # drop "[ 乗り換え案内 ]" style links
    return spec


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
        p = {"status": re.split(r"[（(]", s)[0].strip(), "fee_min": lo, "fee_max": hi if hi != lo else None}
        return clean(p)
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
    elif "：" not in head and not head.startswith("無"):  # leading private-road area, e.g. '23.73m2'
        r["private_m2"] = parse_m2(head)[0]
    if m := re.search(r"セットバック：\s*(\d+(?:\.\d+)?)\s*m", s):
        r["setback_m2"] = float(m.group(1))
    r["text"] = s
    return clean(r)


def _features(soup):
    h = soup.find(lambda t: t.name in ("h3", "h2") and "特徴ピックアップ" in t.get_text())
    box = h.find_parent(class_="secTitleOuterK") if h else None
    body = box.find_next_sibling("div") if box else None
    return sorted({f.strip() for f in text(body).split(" / ") if f.strip()}) if body else []


def parse_detail(html):
    soup = BeautifulSoup(html, "lxml")
    spec = spec_table(soup)

    def g(*keys):
        """First non-empty value among the labels (SUUMO uses '-' for 'not stated')."""
        return next((spec[k][0] for k in keys if k in spec and spec[k][0] not in ("", "-")), None)

    notes = g("その他概要・特記事項")
    r = {}

    if (v := g("交通")):  # one station per <div> in the cell
        divs = spec["交通"][1].find_all("div")
        r["stations"] = [st for st in map(parse_station, [d.get_text(" ").strip() for d in divs] or [v]) if st]
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
    if (v := g("向き")):
        r["direction"] = v
    if (v := g("総戸数", "総区画数")):
        r["total_units"] = _int(v)
    if (v := g("販売戸数", "販売区画数")):
        r["units_for_sale"] = _int(v)
    if (v := g("敷地の権利形態", "土地の権利形態")):
        r["land_rights"] = _tenure(v)
        if v != r["land_rights"]:
            r["land_rights_note"] = v
    if (v := g("用途地域")):
        r["zoning"] = v
    if (v := g("建ぺい率・容積率", "建ぺい率･容積率")):
        r["coverage_pct"], r["far_pct"] = _ratios(v)
    r["parking"] = _parking(g("駐車場"), notes)
    if (v := g("敷地面積")):
        r["site_m2"] = parse_m2(v)[0]
    r["road"] = _road(g("私道負担・道路"))
    if (v := g("土地状況")):
        r["land_status"] = v
    if (v := g("建築条件")):
        r["build_condition"] = v.startswith("付")
    if (v := g("地目")):
        r["land_category"] = v
    if (v := g("施工")):
        r["builder"] = v
    if (v := g("リフォーム")):
        r["reform"] = clean({"date": parse_built(v), "text": v.split("※")[0].strip()})
    if (v := g("完成時期(築年月)", "完成時期（築年月）")):
        r["built"] = parse_built(v)
        if "予定" in v:
            r["built_planned"] = True
    if (v := g("引渡可能時期", "引き渡し時期")):
        r["handover"] = v
    if (v := g("エネルギー消費性能")):
        r["energy"] = v
    if (v := g("断熱性能")):
        r["insulation"] = v
    if (v := g("その他制限事項")):
        r["restrictions"] = v
    if notes:
        m = re.search(r"設備：([^、]+(?:、(?!担当者|駐車場|建築確認)[^、：]+)*)", notes)
        if m:
            r["utilities"] = m.group(1)
    if (v := g("販売スケジュール")) and len(v) < 120:
        r["sale_schedule"] = v
    if (v := g("最多価格帯")):
        r["top_price_band"] = v
    r["features"] = _features(soup)
    m = re.search(r"取引態様：\s*＜([^＞]+)＞", text(soup))
    r["deal_type"] = m.group(1) if m else None
    return clean(r)
