"""A listing's own page -> its page record: everything worth keeping from the page, as plain JSON, no HTML.

`extract(html, type_, id_)` is pure and deterministic (`dumps` of the same page is the same text). The record holds no
id, type or version: the state's columns carry them (`listings.type`, `id`, `extract_v`). `detail.derive` turns a
record into the normalized fields; it reads nothing else.

Record (v1). Empty values are omitted everywhere (`parse.clean`):

    tpl       "kr" (the four resale/new-house/land types and about half of new_condo), "ms" (new_condo development
              top pages: overview table, rich photos and copy), "mg" (new_condo outline pages: 物件共通情報/販売期概要)
    spec      {label: value} from every th/td pair, the value as written. The first occurrence of a label wins (the
              summary and full tables repeat it); a later one with a different value is kept as "label#2",
              "label#3" (sale periods on project pages repeat 価格 etc.). "交通" is a list: one line per station.
              Only rows with a real value (not empty or "-"); rows stored elsewhere (MOVED) and form fields left out.
    features  特徴ピックアップ, in page order
    deal_type 取引態様 (仲介, 売主, ...)
    ll        {"lat", "lng", "kind"}: the page's map point (kind "map", or the map script's point type)
    dates     {"info", "next", "terms_until"}: 情報提供日, 次回更新予定日, 取引条件有効期限 as YYYY-MM-DD
    img       {"b": "030/N010000", "l": [item, ...]}: the gallery in page order, one entry per distinct photo.
              item = n (photo n under the base b, .jpg; no caption or comment), [ref, caption] or
              [ref, caption, comment]; ref = n, [b2, n] / [b2, n, ext] (another base or extension) or the whole
              "gazo/bukken/..." path when it doesn't follow the usual layout. `img_src` turns an item back into
              its path.
    texts     {"catch", "pr", "event", "staff", "seller", "present", "notes"}: free text with its line breaks
    env       [[category, text]]: 周辺環境
    agent     {"name", "tel", "license", "address", "hours", "about"}
    unknown   {"labels": [...], "sections": [...]}: spec labels and section headings not recognised here; a
              non-empty list means the page has something this module doesn't know about yet

Text keeps the page's characters: runs of ASCII whitespace (HTML source formatting) become one space, or one line
break in free text, while U+3000 and no-break spaces stay as written. Link widgets inside a cell ("[ 乗り換え案内 ]",
"[ □ 支払シミュレーション ]", "地図を見る") are not part of its value; neither is link text in photo comments.
Labels and section headings are identifiers: whitespace and the "ヒント" suffix are removed from labels.
"""
import json
import re
from collections import Counter
from urllib.parse import unquote

from bs4 import BeautifulSoup, NavigableString

from .parse import clean, text

EXTRACT_VERSION = 1  # bump when extract() output changes for the same page

_ASCII_WS = " \t\n\r\f\v"
_ascii_ws = re.compile(r"[ \t\n\r\f\v]+")
_ascii_blank = re.compile(r"[ \t\r\f\v]+")  # inside one line of free text
_ws = re.compile(r"\s+")  # any whitespace, U+3000 and no-break space included
_brk = re.compile(r"\[[^\[\]\n]{0,200}\]")  # '[ … ]' (bounded: linear time on any input)
_widget = re.compile(r"\[[^\[\]]*\]")  # one bracket group: a link widget's whole text
_MAP_LINK = "地図を見る"


MAX_TEXT = 20_000  # characters kept of any one text (a value, a free text): bounds every regex's work


def _real(v):
    """A real value: not empty or '-' in the one-line form (as detail.flat reads it)."""
    return _brk.sub("", _ws.sub(" ", v)).strip() not in ("", "-")


def _text(el):
    """An element's text on one line: ASCII whitespace runs collapsed, U+3000/NBSP kept."""
    return _ascii_ws.sub(" ", el.get_text(" ")[:MAX_TEXT]).strip(_ASCII_WS) if el else ""


def _text_without_links(el, sep):
    """el.get_text(sep) without the text inside <a> elements."""
    out = []
    for s in el.find_all(string=True):
        if type(s) is not NavigableString:  # comments, scripts, styles: not text (get_text skips them too)
            continue
        p = s.parent
        while p is not el and p.name != "a":
            p = p.parent
        if p is el:
            out.append(s)
    return sep.join(out)


def _lines(el, links=True):
    """Free text with its line breaks: one line per text node or source line, blank lines dropped, ASCII spacing
    collapsed. 'm<sup>2</sup>' stays on its line as 'm 2' (as in the one-line form)."""
    if not el:
        return ""
    raw = (el.get_text("\n") if links else _text_without_links(el, "\n"))[:MAX_TEXT]
    lines = (_ascii_blank.sub(" ", x).strip(_ASCII_WS) for x in raw.split("\n"))
    s = "\n".join(x for x in lines if x.strip())
    return re.sub(r"m\n2(?:\n|$)", "m 2 ", s).rstrip(_ASCII_WS)


# ---------------------------------------------------------------- spec rows

DROP = {  # boilerplate: inquiry-form fields, links, model-room calendar weekday headers
    "お名前必須", "電話番号半角必須", "ご住所必須", "お問い合わせ内容", "メールアドレス", "半角英数",
    "メールアドレス※PCメール推奨半角英数必須", "関連リンク", "不動産会社ガイド",
    "月", "火", "水", "木", "金", "土", "日",
}
MOVED = {  # rows stored elsewhere in the record
    "会社概要": "agent", "会社情報": "agent", "問い合わせ先": "agent", "お問い合せ先": "agent",
    "お問い合わせ先": "agent",
    "担当者より": "texts.staff", "イベント情報": "texts.event", "その他概要・特記事項": "texts.notes",
    "情報提供日": "dates", "次回更新予定日": "dates", "取引条件有効期限": "dates",
}
FORM_OTHER = "この情報を他の問い合わせにも自動で反映"  # the inquiry form's 'その他' checkbox row
ALIASES = {  # dropped when equal to the canonical rows (which hold real values): no information lost
    "住所": ("所在地",),
    "建ぺい率･容積率": ("建ぺい率・容積率",),
    "完成時期（築年月）": ("完成時期(築年月)",),
    "所在階/構造・階建": ("所在階", "構造・階建て"),  # 'floor/structure' == f'{所在階}/{構造・階建て}'
}
KNOWN_LABELS = {  # recognised spec labels (anything else is reported under unknown.labels)
    "価格", "販売価格", "管理費", "修繕積立金", "修繕積立基金", "諸費用", "所在階", "構造・階建て", "構造・工法",
    "総戸数", "総区画数", "販売戸数", "販売区画数", "敷地の権利形態", "土地の権利形態", "建ぺい率・容積率", "駐車場",
    "敷地面積", "私道負担・道路", "建築条件", "リフォーム", "完成時期(築年月)", "販売スケジュール", "交通", "所在地",
    "向き", "用途地域", "土地状況", "地目", "施工", "引渡可能時期", "引き渡し時期", "エネルギー消費性能", "断熱性能",
    "その他制限事項", "最多価格帯", "間取り", "専有面積", "その他面積", "土地面積", "建物面積", "物件名", "目安光熱費",
    "造成完了時期", "間取り補足", "物件への交通", "予定価格帯", "予定価格", "その他", "建築面積", "建築延床面積",
    "駐輪場", "バイク置場", "ミニバイク置場", "物件種別", "管理形態", "その他概要", "管理", "今回販売戸数",
    "管理準備金", "その他諸経費", "制限事項", "完成時期", "プラン間取り", "土地価格", "プラン建物価格",
    "プラン建物面積",
    "サポート概要", "サポートの詳細説明", "サポートを受けられる期間", "料金発生有無", "料金",
    "サポートを受ける場合の注意点・制約事項",
    *ALIASES, *MOVED,
}
KNOWN_SECTIONS = {  # recognised section headings (anything else, unless SECTION_PATTERN matches, is unknown)
    "物件の特徴", "特徴ピックアップ", "イベント情報", "物件詳細情報", "周辺環境・地図", "周辺環境", "周辺施設",
    "物件概要",
    "共通概要", "会社情報", "情報提供日・更新日", "その他の情報", "売主コメント", "プレゼント情報", "住宅ローン情報",
    "見学可能な日程", "他の注目ポイントを見る", "設備・構造", "設備仕様", "購入サポート制度", "構造の特徴",
    "区画・住戸情報", "区画図", "現地案内図", "間取り図", "注目の物件ピックアップ", "路線図", "物件共通情報",
    "お問い合わせ", "販売期概要", "あなたにオススメの物件", "条件を変更して物件を探す", "交通アクセス図",
    "こんなパンフレットが届きます",
    # ms
    "立地・アクセス", "周辺環境の特徴", "交通アクセス", "お問い合わせ先", "建物の特徴", "間取り",
    "モデルルームスケジュール",
    "見学予約メニュー", "見学予約スケジュール", "室内の特徴", "室内空間のポイント", "この物件の魅力をもっと詳しく",
    "SUUMOオリジナル取材レポート", "コンセプト", "モデルルーム360°パノラマ",
}
SECTION_PATTERN = re.compile(
    r"(^|\s)物件概要(\s|$)|【(マンション|一戸建て|土地)】$|^モデルルームの見どころポイント|期$|期\d+次")


MAX_UNKNOWN = 20  # unknown labels/sections listed per record
OTHER = "#other"  # stands for unknown names too long or mostly digits (data in a th/heading, not a name)


def _unknown(names):
    """Unknown names for the record: at most MAX_UNKNOWN, implausible names folded into OTHER."""
    out = []
    for n in names:
        n = OTHER if len(n) > 30 or 2 * sum(c.isdigit() for c in n) > len(n) else n
        if n not in out:
            out.append(n)
    return out[:MAX_UNKNOWN]


def _label(th):
    return re.sub(r"ヒント|\s", "", th.get_text())


def _drop_widgets(td):
    """Remove link widgets from a cell: '[ <a>乗り換え案内</a> ]' style elements and 地図を見る links."""
    out = []
    for a in td.find_all("a"):
        p = a.parent
        if a.get_text(strip=True) == _MAP_LINK:
            out.append(a)
        elif p is not td and _widget.fullmatch(_ws.sub("", p.get_text())):
            out.append(p)
    for el in out:
        if not any(el in x.descendants for x in out if x is not el):
            el.decompose()


def _value(td):
    _drop_widgets(td)
    return _text(td)


def _spec(soup):
    """-> (spec, first values, first tds, unknown labels). See the module docstring for the rules."""
    first, tds, spec, unknown = {}, {}, {}, []
    for th in soup.find_all("th"):
        td = th.find_next_sibling(["th", "td"])
        k = _label(th)
        if td is None or td.name != "td" or not k or k in DROP:
            continue
        v = _value(td)
        if k == "その他" and v.startswith(FORM_OTHER):
            continue
        if k not in first:
            first[k], tds[k] = v, td
            if k not in KNOWN_LABELS:
                unknown.append(k)
            if k not in MOVED and _real(v):
                spec[k] = v
        elif k not in MOVED and _real(v) and v != first[k]:
            same = [x for x in spec if x == k or x.startswith(k + "#")]
            if v not in (spec[x] for x in same):
                spec[f"{k}#{len(same) + 1 + (k not in spec)}"] = v
    for alias, canon in ALIASES.items():
        if alias in spec and all(c in first and _real(first[c]) for c in canon) \
                and spec[alias] == "/".join(first[c] for c in canon):
            del spec[alias]
    if "交通" in spec:  # one station per <div>; else the cell's text
        lines = [s for s in map(_text, tds["交通"].find_all("div")) if s]
        spec["交通"] = lines or [spec["交通"]]
    return spec, first, tds, unknown


# ---------------------------------------------------------------- the rest of the page

def _jdate(s):
    m = re.search(r"(\d{4})\s*[年/]\s*(\d{1,2})\s*[月/]\s*(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


_NUM = r"(\d{1,3}(?:\.\d{1,15})?)"
_INIT = re.compile(rf"initIdo\s*:\s*'{_NUM}'\s*,\s*initKeido\s*:\s*'{_NUM}'")
_MARKER = re.compile(r'\{"lat":[^{}]{0,300}"type":"\w+"[^{}]{0,300}\}|\{"lng":[^{}]{0,300}"type":"\w+"[^{}]{0,300}\}')
_JAPAN = ((20.0, 46.0), (122.0, 154.0))  # lat, lng ranges


def _point(lat, lng, kind):
    """{lat, lng, kind} if the point is in Japan (swapped if given the wrong way round), else None."""
    lat, lng = float(lat), float(lng)
    (lat0, lat1), (lng0, lng1) = _JAPAN
    for a, b in ((lat, lng), (lng, lat)):
        if lat0 <= a <= lat1 and lng0 <= b <= lng1:
            return {"lat": round(a, 6), "lng": round(b, 6), "kind": kind}
    return None


def _latlng(html):
    """The listing's point: the kr map's initial point, else the first marker in the map script whose type contains
    'local' (the site; a model room elsewhere is not). None when absent or outside Japan."""
    if m := _INIT.search(html):
        return _point(m.group(1), m.group(2), "map")
    for obj in _MARKER.findall(html):
        lat, lng = re.search(rf'"lat":{_NUM}', obj), re.search(rf'"lng":{_NUM}', obj)
        kind = re.search(r'"type":"(\w+)"', obj).group(1)
        if lat and lng and "local" in kind and (p := _point(lat.group(1), lng.group(1), kind)):
            return p
    return None


def _sec_body(soup, title):
    """kr: <div class=secTitleOuterK><h3>title</h3></div><div>body</div> -> body."""
    h = soup.find(lambda t: t.name in ("h3", "h2") and t.get_text(strip=True) == title)
    box = h.find_parent(class_="secTitleOuterK") if h else None
    return box.find_next_sibling() if box else None


def _features(soup):
    """特徴ピックアップ: 'A / B / C' -> [A, B, C] in page order, without repeats."""
    h = soup.find(lambda t: t.name in ("h3", "h2") and "特徴ピックアップ" in t.get_text())
    box = h.find_parent(class_="secTitleOuterK") if h else None
    body = box.find_next_sibling("div") if box else None
    out = []
    for f in re.split(r"\s+/\s+", _text(body)) if body else []:
        f = f.strip(_ASCII_WS)
        if f.strip() and f not in out:
            out.append(f)
    return out


def _deal_type(soup):
    m = re.search(r"取引態様：\s*＜([^＞]+)＞", _ascii_ws.sub(" ", soup.get_text(" ")))
    return m.group(1) if m else None


_GAZO = re.compile(r"gazo(?:%2F|/)bukken(?:%2F|/)[^\"'&\s<>,]+", re.I)
_PATH = re.compile(r"gazo/bukken/(\w+)/(N\d+)/img/(\w+)/(\w+)/(\w+)_(\d+)\.(\w+)$")
_PLAIN_PATH = re.compile(r"gazo/bukken/[\w./-]+")
_CAPTION = re.compile(r"-caption\b|floorplan_item-content|accessitem-caption")


def _src(v):
    """The listing photo path in an attribute value, else None (placeholders under gazo/bukken/system/ too)."""
    m = _GAZO.search(v or "")
    src = unquote(m.group(0)) if m else None
    return src if src and not src.lower().startswith("gazo/bukken/system/") else None


def _photo_of(img):
    """The listing photo path of an <img> (rel, else src), or None."""
    for attr in ("rel", "src"):
        v = img.get(attr)
        if src := _src(" ".join(v) if isinstance(v, list) else v):
            return src
    return None


def _photos(soup, tpl, name):
    """[(path, caption, comment)] in page order, one per distinct photo (a repeat fills in a missing caption or
    comment). kr: the gallery's hidden inputs (path, caption) with their comment boxes, then every other <img>;
    ms: every <img>, the comment from the nearest caption element around it."""
    out, idx = [], {}

    def add(src, cap, com):
        cap, com = ((x or "").strip(_ASCII_WS) for x in (cap, com))
        cap = "" if cap == "-" else cap
        com = "" if com in ("-", cap) else com
        if src in idx:
            i = idx[src]
            out[i] = (src, out[i][1] or cap, out[i][2] or com)
        else:
            idx[src] = len(out)
            out.append((src, cap, com))

    if tpl != "ms":
        for inp in soup.select('input[id^="img"][id$="orgn"]'):
            v = inp.get("value") or ""
            src = _src(v)
            if not src:
                continue  # no photo, or a placeholder
            cap = v.split(",", 1)[1] if "," in v else ""
            box = inp.find_parent(class_="jsiKrThickbox")
            if box is not None and not cap:
                cap = _text(box.select_one(".secTitleInnerK"))
            com = _lines(box.select_one("p.bw"), links=False) if box is not None else ""
            add(src, cap, com)
    for img in soup.find_all("img"):
        src = _photo_of(img)
        if not src:
            continue
        cap = _ascii_ws.sub(" ", img.get("alt") or "")
        if name and cap.startswith(name + "の"):
            cap = cap[len(name) + 1:]
        com = ""
        if tpl == "ms":
            node = img
            for _ in range(4):
                node = node.parent
                if node is None or any(o is not img and _photo_of(o) for o in node.find_all("img")):
                    break  # stop before an ancestor holding another photo: its caption is that photo's
                c = node.find(class_=_CAPTION)
                if c is not None and img not in c.descendants:
                    com = _lines(c, links=False)
                    break
        add(src, cap, com)
    return out


def img_src(id_, img, item):
    """One gallery item of a listing (its id, the record's img, an entry of img["l"]) -> 'gazo/bukken/…' path.
    Photo URL: https://img01.suumo.com/jj/resizeImage?src=<path, URL-encoded>&w=<width>&h=<height>."""
    ref = item if isinstance(item, int) else item[0]
    if isinstance(ref, str):
        return ref
    if isinstance(ref, int):
        b, n, ext = img["b"], ref, "jpg"
    else:
        b, n, ext = ref[0], ref[1], ref[2] if len(ref) > 2 else "jpg"
    ar, kind = b.split("/")
    d, w = (id_[-2:], 6) if kind == "N001000" else (id_[-3:], 4)
    return f"gazo/bukken/{ar}/{kind}/img/{d}/{id_}/{id_}_{n:0{w}d}.{ext}"


def _gallery(photos, id_):
    """[(path, caption, comment)] -> the record's img: the commonest base as b, each photo as the shortest ref that
    img_src turns back into exactly its path."""
    parsed = [_PATH.match(src) for src, _, _ in photos]
    bases = Counter(f"{m.group(1)}/{m.group(2)}" for m in parsed if m)
    img = {"b": bases.most_common(1)[0][0] if bases else None, "l": []}
    for (src, cap, com), m in zip(photos, parsed, strict=True):
        ref = src if _PLAIN_PATH.fullmatch(src) else None  # an odd path is kept whole only if it's a plain path
        if m:
            b, n, ext = f"{m.group(1)}/{m.group(2)}", int(m.group(6)), m.group(7)
            short = n if (b, ext) == (img["b"], "jpg") else [b, n] if ext == "jpg" else [b, n, ext]
            if img_src(id_, img, [short]) == src:
                ref = short
        if ref is None:
            continue
        img["l"].append([ref, cap, com] if com else [ref, cap] if cap or not isinstance(ref, int) else ref)
    return clean(img) if img["l"] else None


_LICENCE = re.compile(  # '東京都知事（2）第123456号' (bounded: linear time on any input)
    r"(国土交通大臣|\S{1,20}?知事\S{0,10}?)\s{0,3}[（(]\s{0,3}\d{1,3}\s{0,3}[)）]\s{0,3}第\s{0,3}\d{1,9}\s{0,3}号")


def _agent(soup, tpl, tds):
    a = {}
    if tpl == "ms":
        box = soup.select_one(".inquiry_info")
        if box:
            a["name"] = _text(box.select_one(".inquiry_info-name"))
            a["tel"] = _text(box.select_one(".inquiry_info-number_item"))
            a["hours"] = " ／ ".join(t for t in map(_text, box.select(".inquiry_info-basic p")) if t)
        return clean(a)
    about = next((tds[k] for k in ("会社概要", "会社情報") if k in tds), None)
    if about is not None:
        a["about"] = _lines(about)
        m = re.search(r"〒\d{3}-\d{4}[^\n]*", a["about"])
        a["address"] = m.group(0).strip(_ASCII_WS) if m else None
    contact = next((tds[k] for k in ("問い合わせ先", "お問い合わせ先", "お問い合せ先") if k in tds), None)
    if contact is not None:
        t = _text(contact)
        link = contact.find("a")
        a["name"] = _text(link) if link else t.split(" TEL")[0].strip(_ASCII_WS)
        m = re.search(r"TEL：\s*([\d\-]+)", t)
        a["tel"] = m.group(1) if m else None
        m = re.search(r"免許番号：\s*(.+?号)", t)
        a["license"] = m.group(1) if m else None
        m = re.search(r"営業時間：(.+)$", t)
        a["hours"] = m.group(1).strip(_ASCII_WS) if m else None
    if not a.get("license") and a.get("about"):
        m = _LICENCE.search(a["about"])
        a["license"] = m.group(0) if m else None
    return clean(a)


def _texts(soup, tpl, first, tds):
    t = {}
    if tpl != "ms":
        h2 = soup.find(lambda x: x.name == "h2" and x.get_text(strip=True) == "物件の特徴")
        sec = h2.find_parent(class_=re.compile("section_h2")) if h2 else None
        if sec:
            t["catch"] = _lines(sec.select_one("h3.fs16"))
            t["pr"] = _lines(sec.select_one("p.fs14"))
        for title, key in (("イベント情報", "event"), ("売主コメント", "seller"), ("プレゼント情報", "present")):
            t[key] = _lines(_sec_body(soup, title))
        if not t.get("event") and "イベント情報" in first and _real(first["イベント情報"]):
            t["event"] = _lines(tds["イベント情報"])
        if "担当者より" in tds:
            t["staff"] = _lines(tds["担当者より"])
    else:
        t["catch"] = _lines(soup.select_one("p.catchcopy"))
        parts = []  # 建物/室内/周辺環境/立地 write-ups: title + text, in page order
        for x in soup.select(".cassette_basicreport_read-title, .cassette_basicreport_read-txt"):
            s = _lines(x)
            if s and s not in parts:
                parts.append(s)
        t["pr"] = "\n\n".join(parts)
    if "その他概要・特記事項" in first and _real(first["その他概要・特記事項"]):
        t["notes"] = _lines(tds["その他概要・特記事項"])
    return clean(t)


def _env(soup):
    env = []
    for li in soup.select("li.cf.dibz"):
        divs = li.find_all("div", recursive=False)
        if len(divs) >= 2:
            env.append([_text(divs[0]), _text(divs[1])])
    return env


def _sections(soup):
    out = []
    for h in soup.select("h2, h3.secTitleInnerK, h3.section_h3-title"):
        s = text(h)
        if s and s not in KNOWN_SECTIONS and not SECTION_PATTERN.search(s) and s not in out:
            out.append(s)
    return out


def template(soup):
    """'ms' (new_condo development page), 'mg' (new_condo outline page) or 'kr' (everything else)."""
    if soup.select_one("table.overview_table"):
        return "ms"
    if soup.select_one(".detailtable"):
        return "mg"
    return "kr"


def extract(html, type_, id_):
    """A listing page (its type and id) -> its record (see the module docstring). ValueError for a page that isn't a
    listing page (no spec rows; kr without a title and a price): the caller keeps the record it has."""
    soup = BeautifulSoup(html, "lxml")
    tpl = template(soup)
    spec, first, tds, unknown_labels = _spec(soup)
    h1 = soup.select_one(".section_h1-titlebox")
    if not spec:
        raise ValueError("no spec rows")  # a maintenance, error or captcha page: never stored over a good record
    if tpl == "kr" and h1 is None and not {"価格", "販売価格"} & spec.keys():
        raise ValueError("no title and no price")
    name = re.sub(r"\s*（[^（）]*）$", "", _text(h1)) if h1 else None
    upd = soup.select_one("p.overview-update")
    dates = {"info": _jdate(first.get("情報提供日")) or _jdate(_text(upd)),
             "next": _jdate(first.get("次回更新予定日")), "terms_until": _jdate(first.get("取引条件有効期限"))}
    return clean({
        "tpl": tpl,
        "spec": spec,
        "features": _features(soup),
        "deal_type": _deal_type(soup),
        "ll": _latlng(html),
        "dates": clean(dates),
        "img": _gallery(_photos(soup, tpl, name), id_),
        "texts": _texts(soup, tpl, first, tds),
        "env": _env(soup),
        "agent": _agent(soup, tpl, tds),
        "unknown": clean({"labels": _unknown(unknown_labels), "sections": _unknown(_sections(soup))}),
    })


def dumps(record):
    """Canonical text of a record (sorted keys, compact): equal records are equal text."""
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
