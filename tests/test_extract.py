"""Listing pages -> page records (extract) -> fields (derive), on real pages in tests/fixtures (detail_*.html.gz and
pages/<type>_<id>.html.gz: kr pages of every type, ms and mg new-condo pages, bus stops, a /tochi/ new_house land
listing, a second photo base, pages without photos or with a placeholder) and one synthetic page
(pages/synthetic_77123456.html) with U+3000 and no-break spaces in every field."""
import gzip
import json
import re
from urllib.parse import unquote

import pytest

from suumo import extract as X
from suumo import pipeline
from suumo.detail import FIELDS, derive, flat
from tests import legacy_detail
from tests.helpers import FIXTURES

SYNTHETIC = FIXTURES / "pages" / "synthetic_77123456.html"
REAL = sorted(FIXTURES.glob("detail_*.html.gz")) + sorted((FIXTURES / "pages").glob("*.html.gz"))
PAGES = REAL + [SYNTHETIC]
NO_PHOTOS = {"used_house_22069621", "new_condo_21886249", "new_condo_67723259", "new_condo_67733706"}
KEYS = {"tpl", "spec", "features", "deal_type", "ll", "dates", "img", "texts", "env", "agent", "unknown"}


def html(path):
    return gzip.decompress(path.read_bytes()).decode() if path.suffix == ".gz" else path.read_text()


def ident(path):
    """'detail_used_condo_20205670.html.gz' / 'used_condo_20205670.html.gz' -> ('used_condo', '20205670')."""
    m = re.match(r"(?:detail_)?(\w+?)_(\d+)\.html", path.name)
    return m.group(1), m.group(2)


def record(path):
    return X.extract(html(path), *ident(path))


_cache = {}


def page(path):
    """(html, record, fields) of a fixture, derived from the record's stored text (as the pipeline does)."""
    if path not in _cache:
        h = html(path)
        rec = X.extract(h, *ident(path))
        _cache[path] = h, rec, derive(json.loads(X.dumps(rec)))
    return _cache[path]


def by_name(name):
    return next(p for p in PAGES if p.name.startswith(name))


# ---------------------------------------------------------------- the gate and record invariants

@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name.split(".")[0])
def test_derive_equals_the_html_parser(path):
    """The equality gate: every field the parser before page records produced, unchanged."""
    h, _, fields = page(path)
    assert {k: v for k, v in fields.items() if k in legacy_detail.FIELDS} == legacy_detail.parse_detail(h)
    assert fields.keys() <= FIELDS


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name.split(".")[0])
def test_records_are_deterministic_and_carry_no_identity(path):
    _, rec, fields = page(path)
    assert X.dumps(record(path)) == X.dumps(rec)
    assert rec.keys() <= KEYS and not {"id", "type", "v", "version"} & rec.keys()
    assert derive(rec) == fields
    assert rec.get("texts", {}).keys() <= {"catch", "pr", "event", "staff", "seller", "present", "notes"}
    assert rec.get("agent", {}).keys() <= {"name", "tel", "license", "address", "hours", "about"}
    assert rec.get("dates", {}).keys() <= {"info", "next", "terms_until"}


@pytest.mark.parametrize("path", PAGES, ids=lambda p: p.name.split(".")[0])
def test_every_photo_on_the_page_comes_back_from_the_record(path):
    """img_src rebuilds each photo's path from the record: every listing photo on the page, once, in page order."""
    h, rec, fields = page(path)
    on_page = {unquote(m.group(0)) for m in X._GAZO.finditer(h)}
    on_page = {p for p in on_page if not p.startswith("gazo/bukken/system/")}  # placeholders
    img = rec.get("img")
    back = [X.img_src(ident(path)[1], img, item) for item in img["l"]] if img else []
    assert len(back) == len(set(back)) and set(back) == on_page
    assert fields.get("photos") == (len(back) or None)
    assert (path.name.split(".")[0] in NO_PHOTOS) == (not back)


@pytest.mark.parametrize("path", REAL, ids=lambda p: p.name.split(".")[0])
def test_real_pages_have_what_their_template_promises(path):
    _, rec, fields = page(path)
    assert "unknown" not in rec  # every label and section on the fixtures is known
    rows = len(rec["spec"])
    if rec["tpl"] == "kr":
        assert rows >= 10 and rec["ll"] and rec["agent"].get("name") and rec["deal_type"] and rec["dates"]["info"]
        assert fields["lat"] == round(rec["ll"]["lat"], 4) and fields["lng"] == round(rec["ll"]["lng"], 4)
    elif rec["tpl"] == "ms":
        assert rows >= 5 and rec["img"] and rec["ll"] and rec["texts"]["catch"] and rec["agent"]["name"]
        assert not rec["spec"]["所在地"].endswith("地図を見る")
    else:
        assert rec["tpl"] == "mg" and rows >= 5


def test_fixture_coverage():
    """The corpus keeps its edges: each kr type, ms, mg, bus stops, a /tochi/ new_house page, a second base."""
    kinds = {(ident(p)[0], page(p)[1]["tpl"]) for p in REAL}
    assert {(t, "kr") for t in ("used_condo", "used_house", "new_house", "land", "new_condo")} <= kinds
    assert {("new_condo", "ms"), ("new_condo", "mg")} <= kinds
    assert sum(any(s.get("bus_stop") for s in page(p)[2].get("stations", [])) for p in REAL) >= 3
    assert any("/tochi/" in html(p) and ident(p)[0] == "new_house" and page(p)[2].get("price_excludes_building")
               for p in REAL)
    items = [i for p in REAL for i in (page(p)[1].get("img") or {}).get("l", [])]
    assert any(isinstance(i, list) and isinstance(i[0], list) for i in items)  # [[b2, n], caption]
    assert any(isinstance(i, int) for i in items)  # a photo without caption: bare n


# ---------------------------------------------------------------- U+3000, no-break spaces, widgets

def test_stored_text_keeps_the_pages_characters():
    _, rec, _ = page(SYNTHETIC)
    spec, texts, agent = rec["spec"], rec["texts"], rec["agent"]
    assert spec["価格"] == "3980万円　(税込)"  # no '[ □ 支払シミュレーション ]'
    assert spec["修繕積立金"] == "1万\xa05000円／月"
    assert spec["交通"] == ["ＪＲ　中央線「吉祥寺」歩5分", "関東バス「東町　バス停」歩3分\xa0",
                          "京王井の頭線「[井]　三鷹台」歩12分"]  # no '[ 乗り換え案内 ]'
    assert "住所" not in spec  # equal to 所在地 once 地図を見る is gone
    assert spec["その他制限事項"] == "高度地区　　防火地域\xa0\xa0[ 詳細 ]"
    assert spec["用途地域"] == "\xa01種中高" and spec["引渡可能時期"] == "相談　"
    assert "総戸数" not in spec  # '　-　' is not a real value
    assert spec["リフォーム#2"] == "2026年1月　予定"
    assert rec["features"] == ["南向き", "小学校　徒歩10分以内", "角地", "2沿線以上利用可"]
    assert rec["deal_type"] == "仲介\xa0(先物)"
    assert texts["catch"] == "　【見学可】　駅近\xa0の住まい　"
    assert texts["pr"] == "　南向きの明るい住戸です。\n　　リフォーム済\xa0\xa0(2025年)\n駐車場あり　"
    assert texts["staff"] == "担当者　山田\n　お気軽に\xa0どうぞ"
    assert texts["event"] == "日程／公開中　(要予約)\n　時間／10:00～17:00\xa0"
    assert texts["notes"] == "担当者：山田、設備：公営水道　本下水、駐車場：有　(空有)"
    assert texts["seller"] == "　ご覧ください。" and texts["present"] == "QUOカード　500円分"
    assert rec["env"] == [["スーパー", "テスト　マート\xa0吉祥寺店：徒歩3分（210ｍ）"]]
    assert agent["name"] == "(株)テスト　不動産" and agent["tel"] == "0120-000-000"
    assert agent["hours"] == "10:00～18:00　水曜定休" and agent["license"] == "東京都知事（2）第123456号"
    assert agent["address"] == "〒180-0001\xa0東京都武蔵野市吉祥寺北町１-1　"
    assert rec["dates"] == {"info": "2026-11-01", "next": "2026-11-15"}
    assert rec["ll"] == {"lat": 35.712346, "lng": 139.587654, "kind": "map"}


def test_gallery_refs_captions_and_comments():
    _, rec, fields = page(SYNTHETIC)
    assert rec["img"] == {"b": "030/N010000", "l": [
        [1, "外観　写真", "南側　から撮影\xa0\n　2026年10月"],  # link text '詳しく見る' dropped
        [2, "リビング　LDK"],  # comment '-' is no comment
        [["090/N010000", 3], "間取り\xa0図"],  # another base
        4,  # no caption: bare n
    ]}  # the Dummy_Photo placeholder is not a photo; the repeat of photo 1 doesn't add an entry
    assert [X.img_src("77123456", rec["img"], i) for i in rec["img"]["l"]] == [
        "gazo/bukken/030/N010000/img/456/77123456/77123456_0001.jpg",
        "gazo/bukken/030/N010000/img/456/77123456/77123456_0002.jpg",
        "gazo/bukken/090/N010000/img/456/77123456/77123456_0003.jpg",
        "gazo/bukken/030/N010000/img/456/77123456/77123456_0004.jpg",
    ]
    assert fields["photos"] == 4


def test_a_path_outside_the_usual_layout_is_kept_whole():
    odd = "gazo/bukken/030/N010000/img/456/99999999/99999999_0001.jpg"  # another listing's photo
    img = X._gallery([("gazo/bukken/030/N010000/img/456/77123456/77123456_0002.png", "", ""), (odd, "x", "")],
                     "77123456")
    assert img == {"b": "030/N010000", "l": [[["030/N010000", 2, "png"], ""], [odd, "x"]]}
    assert X.img_src("77123456", img, img["l"][1]) == odd
    n001 = "gazo/bukken/100/N001000/img/78/12345678/12345678_000007.jpg"  # this kind: 2-digit folder, 6-digit n
    assert X.img_src("12345678", {"b": "100/N001000"}, 7) == n001


def test_derive_reads_the_one_line_form():
    _, _, d = page(SYNTHETIC)
    assert d["stations"] == [{"line": "ＪＲ　中央線", "name": "吉祥寺", "walk": 5},
                             {"line": "関東バス", "name": "東町　バス停", "bus_stop": True, "walk": 3},
                             {"line": "京王井の頭線", "name": "[井]　三鷹台", "walk": 12}]
    assert (d["mgmt_fee"], d["mgmt_form"], d["repair_fee"], d["other_monthly"]) == (12000, "委託 (通勤)", 5000, 870)
    assert (d["floor"], d["structure"], d["floors_above"], d["coverage_pct"], d["far_pct"]) == (3, "RC", 5, 60, 200)
    assert d["restrictions"] == "高度地区 防火地域" and d["zoning"] == "1種中高" and d["handover"] == "相談"
    assert d["road"] == {"dir": "北", "width_m": 4.0, "text": "無、北 4ｍ幅 (接道幅8ｍ)"}
    assert d["reform"] == {"date": "2025-03", "text": "2025年3月完了 (内装)"} and d["utilities"] == "公営水道 本下水"
    assert d["features"] == ["2沿線以上利用可", "南向き", "小学校 徒歩10分以内", "角地"]
    assert d["deal_type"] == "仲介 (先物)" and (d["lat"], d["lng"]) == (35.7123, 139.5877)


def test_derive_of_an_empty_record():
    assert derive({"tpl": "kr"}) == {}


def test_bus_stop_names_keep_their_spacing():
    d = page(by_name("land_20958625"))[2]
    assert d["stations"] == [{"line": "伊予鉄バス", "name": "東長戸　バス停", "bus_stop": True, "walk": 13}]
    assert page(by_name("used_house_21565257"))[2]["stations"][0]["line"] == "[21/37]イオン"


# ---------------------------------------------------------------- fields of three listings (Komae, 2026-10-05)

def test_condo_listing_page():
    d = page(by_name("detail_used_condo_20205670"))[2]
    assert d["mgmt_fee"] == 146000 and d["repair_fee"] == 26000
    assert (d["floor"], d["structure"], d["floors_above"]) == (3, "RC", 5)
    assert d["land_rights"] == "定期借地権" and "借地期間残存36年" in d["land_rights_note"]
    assert [s["name"] for s in d["stations"]] == ["喜多見", "狛江"]
    assert "南向き" in d["features"] and d["deal_type"] == "仲介"


def test_house_listing_page():
    d = page(by_name("detail_used_house_20779514"))[2]
    assert (d["structure"], d["floors_above"], d["coverage_pct"], d["far_pct"]) == ("木造", 2, 40, 80)
    assert d["road"]["dir"] == "東" and d["road"]["width_m"] == 5.0
    assert d["stations"][2] == {"line": "小田急バス", "name": "狛江第5小学校", "bus_stop": True, "walk": 3}
    assert d["reform"]["date"] == "2019-06"


def test_land_listing_page():
    d = page(by_name("detail_land_20862637"))[2]
    assert d["build_condition"] is True and (d["coverage_pct"], d["far_pct"]) == (60, 200)
    assert d["road"] == {"width_m": 10.0, "setback_m2": 48.24, "text": d["road"]["text"]}


def test_flat_is_the_one_line_form():
    """Pinned: derive reads every spec value and the notes through flat; changing it changes derive's output, so it
    needs a PARSE_VERSION bump (every stored record is re-derived then)."""
    assert flat("　a\xa0 b\n[ 詳細 ]　") == "a b"
    assert flat("1330万円[13号地] 1380万円 [ □ 支払シミュレーション ]") == "1330万円 1380万円"
    assert flat("[a[b] c") == "[a c" and flat("[" + "x" * 201 + "]") == "[" + "x" * 201 + "]"
    assert flat("-") == "-" and flat("　") == ""


# ---------------------------------------------------------------- pages that aren't listing pages, hostile input

@pytest.mark.parametrize("page_html, error", [
    ("<html><body>メンテナンス中</body></html>", "no spec rows"),
    ("<html><body><form><table><tr><th>認証</th><td>画像の文字を入力してください</td></tr></table>"
     "<img src='/captcha.png'></form></body></html>", "no title and no price"),
    ("", "no spec rows"),
])
def test_a_page_that_isnt_a_listing_page_is_an_error(page_html, error):
    """The pipeline's extract-error path keeps the stored record (a maintenance or captcha page never replaces it)."""
    with pytest.raises(ValueError, match=error):
        X.extract(page_html, "used_condo", "1")


def test_regexes_take_linear_time():
    import time
    soup, about = X.BeautifulSoup("<p></p>", "lxml"), X.BeautifulSoup("<td>" + "東京" * 30_000 + "</td>", "lxml").td
    t0 = time.perf_counter()
    flat("[" * 50_000)
    X._real("[" * 50_000)
    X._LICENCE.search("知" + "a" * 20_000)
    X._LICENCE.search("a" * 20_000 + "知事")
    X._MARKER.findall('{"lat":' * 20_000)
    X._agent(soup, "kr", {"会社概要": about})  # a 60k-char about text: capped, then the licence regex
    assert time.perf_counter() - t0 < 0.1


def test_a_huge_text_is_capped():
    td = X.BeautifulSoup("<td>" + "あ" * 50_000 + "</td>", "lxml").td
    assert len(X._text(td)) == X.MAX_TEXT and len(X._lines(td)) == X.MAX_TEXT


def test_unknown_labels_and_sections_are_reported_capped():
    page = html(by_name("detail_used_condo_20205670"))
    rows = "".join(f"<tr><th>新しい項目{i}</th><td>値</td></tr>" for i in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    page = page.replace("</table>", rows + "<tr><th>2026年11月15日</th><td>x</td></tr>"
                        "<tr><th>" + "長" * 31 + "</th><td>x</td></tr></table>", 1)
    page = page.replace("</body>", "<h2>新しい見出し</h2></body>")
    unknown = X.extract(page, "used_condo", "20205670")["unknown"]
    assert unknown["labels"][:2] == ["新しい項目A", "新しい項目B"] and len(unknown["labels"]) == X.MAX_UNKNOWN
    assert unknown["sections"] == ["新しい見出し"]
    assert X._unknown(["2026年11月15日", "長" * 31, "ok"]) == [X.OTHER, "ok"]


def test_only_plain_paths_are_kept_whole():
    weird = 'gazo/bukken/030/x" onerror="alert(1)'
    img = X._gallery([("gazo/bukken/030/N010000/img/456/77123456/77123456_0001.jpg", "", ""), (weird, "", "")],
                     "77123456")
    assert img == {"b": "030/N010000", "l": [1]}
    assert X._src("https://img01.suumo.com/front/GAZO/BUKKEN/SYSTEM/img/Dummy_Photo.jpg") is None
    assert X._gallery([(weird, "", "")], "1") is None


# ---------------------------------------------------------------- coordinates

@pytest.mark.parametrize("script, ll", [
    ("initIdo : '35.7', initKeido : '139.5'", {"lat": 35.7, "lng": 139.5, "kind": "map"}),
    ("initIdo : '139.5', initKeido : '35.7'", {"lat": 35.7, "lng": 139.5, "kind": "map"}),  # reversed: swapped
    ("initIdo : '0', initKeido : '0'", None),  # outside Japan
    ('[{"lat":35.1,"lng":139.1,"type":"modelroom"},{"lat":35.2,"lng":139.2,"type":"local"}]',
     {"lat": 35.2, "lng": 139.2, "kind": "local"}),
    ('[{"lat":35.1,"lng":139.1,"type":"modelroom"}]', None),  # a model room isn't the site
    ('[{"lng":139.3,"lat":35.3,"type":"localmodelroom"}]', {"lat": 35.3, "lng": 139.3, "kind": "localmodelroom"}),
    ('[{"lat":35,"lng":139,"type":"local"}]', {"lat": 35.0, "lng": 139.0, "kind": "local"}),  # integers
    ("", None),
])
def test_coordinates(script, ll):
    assert X._latlng(f"<script>{script}</script>") == ll


def test_an_ms_page_with_only_a_model_room_marker_or_none_has_no_point():
    page = html(by_name("new_condo_67736890"))
    assert X.extract(page, "new_condo", "67736890")["ll"]["kind"].startswith("local")
    for marked in (re.sub(r'"type":"local\w*"', '"type":"modelroom"', page), re.sub(r'"type":"\w+"', "", page)):
        assert "ll" not in X.extract(marked, "new_condo", "67736890")


def test_template_drift_shows_as_a_coverage_drop():
    """An ms page whose overview table is renamed is read as kr: the kr counts of spec rows and photos drop."""
    page = html(by_name("new_condo_67736890"))
    drifted = X.extract(page.replace("overview_table", "overview_tbl"), "new_condo", "67736890")
    assert drifted["tpl"] == "kr"
    p = object.__new__(pipeline.Pipeline)
    p.report = {"pages": pipeline._page_stats()}
    for rec in (page_record := X.extract(page, "new_condo", "67736890"), drifted):
        p._count(rec)
    t = p.report["pages"]["templates"]
    assert t["ms"]["n"] == 1 and t["ms"]["spec5"] == 1 and page_record["tpl"] == "ms"
    assert t["kr"]["n"] == 1 and t["kr"]["spec10"] == 0
