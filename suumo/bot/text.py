"""Every user-facing string of the search bot, in Japanese, and the formatters that build them.

Layout follows the channel feed (notify.py): a bold heading, short lines, details in `-#` subtext.
Discord limits respected here: message 2000, embed title 256, description 4096, field 1024, total 6000,
select option label/description 100, button label 80.
"""
import math
import re

import discord

from ..catalog import NEW_TYPES, Hit, Item, Query
from ..notify import TYPE_JA, line, man

MAX_MESSAGE = 2000
TSUBO = 3.30578

# ---------- choices ----------

ROOMS = {1: "ワンルーム〜1LDK", 2: "2K〜2LDK", 3: "3K〜3LDK", 4: "4K以上"}
PRICE_MAX = [20, 30, 40, 50, 60, 70, 80, 90, 100, 120, 150, 200]   # 百万円
PRICE_MIN = [10, 20, 30, 40, 50, 60, 70, 80, 100]
SIZES = [40, 50, 60, 70, 80, 90, 100, 120]
LANDS = [50, 80, 100, 120, 150, 200]
AGES = [0, 5, 10, 15, 20, 25, 30, 40]
WALKS = [3, 5, 7, 10, 15, 20]
SORTS = {"new": "新着順", "price_asc": "価格が安い順", "price_desc": "価格が高い順", "size_desc": "広い順",
         "walk_asc": "駅から近い順", "age_asc": "築年数が新しい順", "unit_asc": "㎡単価が安い順"}
TYPE_EMOJI = {"used_condo": "🏢", "new_condo": "🏙️", "used_house": "🏠", "new_house": "🏡", "land": "🌳"}
TYPE_COLOR = {"used_condo": 0x4E79A7, "new_condo": 0x59A14F, "used_house": 0xF28E2B, "new_house": 0xE15759,
              "land": 0x9C755F}
PREF_CODES = {"08": "茨城県", "11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県"}
AREA_KINDS = {"1": "区部", "2": "市部"}  # third digit of a JIS municipality code; others are towns/villages
FIELD_JA = {"types": "種別", "areas": "エリア", "stations": "駅", "walk_max": "駅徒歩", "price_min": "予算の下限",
            "price_max": "予算の上限", "rooms": "間取り", "size_min": "広さ", "land_min": "土地面積",
            "age_max": "築年数", "post_1981": "新耐震", "features": "こだわり", "freehold_only": "所有権のみ",
            "no_condition": "建築条件なし", "new_only": "新着のみ", "drops_only": "値下げのみ"}

# ---------- fixed texts ----------

MENU = ("**🏠 SUUMO 物件さがし**\n"
        "-# ボタンを押すと、あなたにだけ見える画面が開きます。")
HELP = """**❓ 使い方**
- **🔎 物件をさがす**：種別・予算・間取りなどを選ぶと、その場で件数がわかります。「🔎 N件を見る」で一覧へ。
- **🆕 新着** / **💴 値下げ**：この1週間に出た物件 / この30日に値下げした物件をすぐに見られます。
- 一覧の「詳しく見る」で写真と詳しい情報、SUUMOのページへのリンクが出ます。
- **⭐ お気に入り**：詳しい画面の「⭐」で追加。価格の変更や掲載終了をDMでお知らせします。
- **💾 保存して通知**：さがす画面の条件を保存すると、新しく出た物件をDMでお知らせします（5件まで）。
- **🔔 保存した条件**：保存した条件の確認・削除、お気に入り通知のオン・オフ。
-# 物件情報は毎朝SUUMOから集めています。同じ物件を複数の会社が載せているときは1件にまとめています。"""
EXPIRED = "⌛ この画面は古くなりました。下のメニューからもう一度開いてください。"
ERROR = "⚠️ うまくいきませんでした。少し待ってからもう一度お試しください。"
LOADING = "⏳ 物件データを読み込み中です。少し待ってからもう一度お試しください。"
NOT_FOUND = "この物件は掲載が終わり、データが残っていません。"

PH_TYPES = "🏠 種別（いくつでも）"
PH_PRICE_MAX = "💴 予算の上限"
PH_PRICE_MIN = "💴 予算の下限"
PH_ROOMS = "🛏️ 間取り（いくつでも）"
PH_SIZE = "📐 広さ（専有・建物面積）"
PH_LAND = "🌳 土地面積"
PH_AGE = "🏗️ 築年数"
PH_LINE = "🚃 路線を選ぶ"
PH_STATION = "🚉 駅を選ぶ（いくつでも）"
PH_WALK = "🚶 駅から徒歩"
PH_FEATURES = "✨ こだわり（すべて満たすもの）"
PH_DETAIL = "🔍 詳しく見る物件を選ぶ"
PH_SORT = "↕️ 並び替え"
PH_SAVED = "🔔 保存した条件を選ぶ"
ANY = "指定なし"

SAVED_OK = "💾 保存しました。この条件に合う物件が新しく出たら、DMでお知らせします。"
SAVE_EMPTY = "条件を1つ以上選んでから保存してください。"
SAVE_DUP = "同じ条件はもう保存されています。"
SAVE_FULL = "保存できる条件は{n}件までです。「🔔 保存した条件」から削除してください。"
DELETED = "🗑️ 削除しました。"
FAV_ADDED = "⭐ お気に入りに追加しました。価格の変更や掲載終了をDMでお知らせします。"
FAV_ADDED_QUIET = "⭐ お気に入りに追加しました。"
FAV_REMOVED = "お気に入りから外しました。"
FAV_FULL = "お気に入りは{n}件までです。いらないものを外してください。"
FAV_EMPTY = "**⭐ お気に入り**\nまだありません。物件の詳しい画面で「⭐ お気に入り」を押すと追加できます。"
SAVED_EMPTY = ("**🔔 保存した条件**\nまだありません。「🔎 物件をさがす」で条件を選び、「💾 保存して通知」を押すと、"
               "新しい物件をDMでお知らせします。")
DM_FAILED = ("⚠️ DMを送れませんでした。サーバーのプライバシー設定で「ダイレクトメッセージ」を許可してください。"
             "それまではチャンネルでお知らせします。")


# ---------- small formatters ----------

def oku(yen_100m):
    """Option value in 百万円 -> '5,000万円' / '1億円' / '1億2,000万円'."""
    return man(yen_100m * 1_000_000)


def price(i: Item):
    r = i.rec
    if r.get("price") is None:
        return "価格未定"
    return man(r["price"]) + (f"〜{man(r['price_max'])}" if r.get("price_max") else "")


def m2(v, v_max=None, tsubo=False):
    if not v:
        return ""
    s = f"{v:g}㎡" + (f"〜{v_max:g}㎡" if v_max else "")
    return s + (f"（{v / TSUBO:.1f}坪）" if tsubo and not v_max else "")


def built(r):
    if not r.get("built"):
        return ""
    y, m = r["built"][:4], int(r["built"][5:7])
    return f"{y}年{m}月築"


def age_text(i: Item):
    if i.type in NEW_TYPES:
        return "新築"
    if i.age is None:
        return ""
    return "築1年未満" if i.age == 0 else f"築{i.age}年"


def walk_text(st):
    if st.get("bus"):
        return f"バス{st['bus']}分" + (f"・停歩{st['walk']}分" if st.get("walk") is not None else "")
    return f"徒歩{st['walk']}分" if st.get("walk") is not None else ""


def station_text(st):
    name = st["name"] + ("" if st.get("bus_stop") else "駅")
    return f"{st.get('line') or ''} {name} {walk_text(st)}".strip()


def short_station(r):
    st = (r.get("stations") or [None])[0]
    return f"{st['name']}駅 {walk_text(st)}".strip() if st else ""


def size_text(i: Item):
    r = i.rec
    if i.type in ("used_condo", "new_condo"):
        return m2(r.get("floor_m2"), r.get("floor_m2_max"))
    parts = []
    if r.get("land_m2"):
        parts.append("土地" + m2(r["land_m2"], r.get("land_m2_max"), tsubo=True))
    if r.get("building_m2"):
        parts.append("建物" + m2(r["building_m2"], r.get("building_m2_max")))
    return " ".join(parts)


def area_name(snap, code):
    return snap.areas.get(code, code)


def clip(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def big_image(url):
    """SUUMO's resize URL takes any size; the list default (192×144) looks blurry when shown large."""
    return re.sub(r"w=\d+&h=\d+", "w=600&h=450", url) if url else None


# ---------- conditions ----------

def _price_range(q):
    if q.price_min is None and q.price_max is None:
        return ""
    lo = man(q.price_min) if q.price_min is not None else ""
    hi = man(q.price_max) if q.price_max is not None else ""
    return f"{lo}〜{hi}"


def condition_lines(q: Query, snap):
    """The query, one line per group; empty list when nothing is set."""
    out = []
    if q.types:
        out.append("🏠 " + "・".join(TYPE_JA[t] for t in q.types))
    if q.areas:
        out.append("📍 " + "・".join(area_name(snap, a) for a in q.areas))
    if q.stations or q.walk_max is not None:
        st = "・".join(q.stations) if q.stations else "どの駅でも"
        out.append(f"🚉 {st}" + (f" 徒歩{q.walk_max}分以内" if q.walk_max is not None else ""))
    if q.price_min is not None or q.price_max is not None:
        out.append(f"💴 {_price_range(q)}")
    size = []
    if q.rooms:
        size.append("・".join(ROOMS[r] for r in q.rooms))
    if q.size_min is not None:
        size.append(f"広さ{q.size_min}㎡以上")
    if q.land_min is not None:
        size.append(f"土地{q.land_min}㎡以上")
    if size:
        out.append("📐 " + " ・ ".join(size))
    age = []
    if q.age_max is not None:
        age.append("新築・築1年未満" if q.age_max == 0 else f"築{q.age_max}年以内")
    if q.post_1981:
        age.append("新耐震（1981年6月以降）")
    if age:
        out.append("🏗️ " + " ・ ".join(age))
    extra = list(q.features) + [label for on, label in (
        (q.freehold_only, "所有権のみ"), (q.no_condition, "建築条件なし"), (q.new_only, "新着のみ"),
        (q.drops_only, "値下げのみ")) if on]
    if extra:
        out.append("✨ " + "・".join(extra))
    return out


def query_short(q: Query, snap, n=90):
    return clip(" / ".join(s[2:] for s in condition_lines(q, snap)) or "すべての物件", n)


def panel(q: Query, snap, count, notice=None):
    lines = [notice, ""] if notice else []
    lines.append("**🔎 物件をさがす**")
    cond = condition_lines(q, snap)
    lines += cond or ["-# 条件なし（すべての物件）。下のメニューで絞り込めます。"]
    lines.append(f"\n**該当 {count:,}件**")
    lines += hints(q, snap, count)
    return clip("\n".join(lines), MAX_MESSAGE)


def hints(q, snap, count):
    out = []
    if count == 0:
        loose = snap.loosen(q)
        if loose:
            field, n = loose[0]
            out.append(f"-# 「{FIELD_JA[field]}」の条件を外すと {n:,}件 あります。")
    missing = snap.undetailed(q)
    if missing:
        out.append(f"-# こだわり条件は詳しい情報を取得済みの物件だけが対象です（ほか{missing:,}件は確認中）。")
    return out


def button_value(prefix, values, n=30):
    """'📍 エリア' -> 'エリア: 狛江市・調布市' for a sub-screen button, so the panel shows what's set."""
    return clip(f"{prefix}: {values}", n) if values else prefix


def screen(title, body, q, snap, notice=None, count=None):
    """A sub-screen: optional notice, title, body lines, the live count and hints."""
    count = snap.count(q) if count is None else count
    lines = ([notice, ""] if notice else []) + [f"**{title}**"] + [b for b in body if b]
    lines.append(f"\n**該当 {count:,}件**")
    lines += hints(q, snap, count)
    return clip("\n".join(lines), MAX_MESSAGE)


def area_groups(areas, snap):
    """[(code, count)] sorted by code -> [(label, [(code, count)])], each group at most 25 (one dropdown)."""
    groups = {}
    for code, n in areas:
        groups.setdefault((code[:2], AREA_KINDS.get(code[2:3], "町村")), []).append((code, n))
    prefs = {p for p, _ in groups}
    out = []
    for (pref, kind), members in groups.items():
        label = f"{PREF_CODES.get(pref, pref)} {kind}" if len(prefs) > 1 else kind
        parts = math.ceil(len(members) / 25)
        size = math.ceil(len(members) / parts)
        for j in range(parts):
            out.append((label + (f"（{j + 1}/{parts}）" if parts > 1 else ""), members[j * size:(j + 1) * size]))
    return out


def areas_screen(q, snap, notice=None):
    chosen = "・".join(area_name(snap, a) for a in q.areas) or "すべてのエリア"
    return screen("📍 エリア", [f"選択中：{chosen}", "-# いくつでも選べます。"], q, snap, notice)


def stations_screen(q, snap, line_, notice=None):
    chosen = "・".join(f"{s}駅" for s in q.stations) or "指定なし"
    walk = f"徒歩{q.walk_max}分以内" if q.walk_max is not None else "指定なし"
    tip = "-# 路線を選ぶと駅が出ます。別の路線の駅も組み合わせられます。" if not line_ else (
        "-# 徒歩は、選んだ駅（なければ最寄り駅）までの時間です。バス便は含みません。")
    return screen("🚉 駅・徒歩", [f"駅：{chosen}", f"徒歩：{walk}", tip], q, snap, notice)


def size_screen(q, snap, notice=None):
    return screen("📐 広さ・築年数など", condition_lines(q, snap) or ["-# 条件なし"], q, snap, notice)


def extra_screen(q, snap, has_tags, notice=None):
    body = condition_lines(q, snap) or ["-# 条件なし"]
    if not has_tags:
        body.append("-# こだわり（ペット相談・角住戸など）は、物件の詳しい情報を集めると選べるようになります。")
    body.append("-# 「所有権のみ」は借地権・定期借地権の物件を除きます。")
    return screen("✨ こだわり", body, q, snap, notice)


# ---------- results ----------

def flags(hit: Hit, snap, fav=None):
    """fav: the person's favorite entry for this listing ({"price": ..., "added": ...}) or None."""
    i, r = hit.item, hit.item.rec
    out = []
    if fav is not None:
        was, now = fav.get("price"), i.price_lo
        if was and now and was != now:
            out.append(f"⭐ 追加時から{'⬇️' if now < was else '⬆️'}{man(abs(now - was))}")
        else:
            out.append("⭐")
    if i.removed:
        out.append(f"🔚 掲載終了 {r.get('removed_at', '')[5:].replace('-', '/')}")
    nd = snap.new_dates.get(i.key)
    if nd and nd >= snap.new_cutoff:
        out.append(f"🆕 {nd[5:].replace('-', '/')}")
    hist = snap.history.get(i.key)
    if hist and i.key in snap.dropped:
        d, old, new = hist[-1]
        out.append(f"⬇️ {man(old)}→{man(new)}")
    if i.leasehold:
        out.append(f"⚠️ {r['land_rights']}")
    if i.conditional:
        out.append("🏷️ 建築条件付・建物価格別" if r.get("price_excludes_building") else "🏷️ 建築条件付")
    if hit.others:
        out.append(f"👥 ほか{len(hit.others)}社も掲載")
    return out


def result_title(hit: Hit, n=None):
    i, r = hit.item, hit.item.rec
    bits = [price(i), r.get("layout") and clip(r["layout"], 24), size_text(i)]
    head = f"{n}. " if n is not None else ""
    return clip(head + " ・ ".join(b for b in bits if b), 256)


def result_embed(hit: Hit, n, snap, fav=None):
    i, r = hit.item, hit.item.rec
    name = r.get("name") or r.get("title") or ""
    where = f"{area_name(snap, i.area)}{r.get('town', '')}"
    info = [TYPE_JA[i.type], age_text(i), short_station(r)]
    lines = [f"**{clip(name, 60)}**" if name else None, f"📍 {where}", "・".join(b for b in info if b)]
    fees = _monthly(r)
    if fees:
        lines.append(f"💴 {fees}")
    fl = flags(hit, snap, fav)
    if fl:
        lines.append(" ".join(fl))
    e = discord.Embed(title=result_title(hit, n), url=r.get("url"), description="\n".join(x for x in lines if x),
                      color=TYPE_COLOR.get(i.type))
    if r.get("image"):
        e.set_thumbnail(url=r["image"])
    return e


def _monthly(r):
    fees = (r.get("mgmt_fee") or 0) + (r.get("repair_fee") or 0)
    return f"管理費・修繕積立金 月{fees / 10000:.1f}万円" if fees else ""


def results_header(title, total, q, page, pages, notice=None):
    head = [notice, ""] if notice else []
    head.append(f"**{title} {total:,}件**")
    if total:
        head.append(f"-# {SORTS[q.sort]} ・ {page + 1}/{pages}ページ")
    return "\n".join(head)


def detail_option(hit: Hit, n):
    i, r = hit.item, hit.item.rec
    label = clip(f"{n}. {price(i)} {r.get('layout') or TYPE_JA[i.type]}", 100)
    desc = clip(" ".join(b for b in (size_text(i), r.get("town", ""), short_station(r)) if b), 100)
    return label, desc


# ---------- detail ----------

def detail_embed(hit: Hit, snap, fav=None):
    i, r = hit.item, hit.item.rec
    e = discord.Embed(title=clip(f"{TYPE_EMOJI[i.type]} {r.get('name') or r.get('title') or TYPE_JA[i.type]}", 256),
                      url=r.get("url"), color=TYPE_COLOR.get(i.type))
    desc = []
    if r.get("title") and r.get("name"):
        desc.append(clip(r["title"], 300))
    fl = flags(hit, snap, fav)
    if fl:
        desc.append(" ".join(fl))
    desc.append(f"[SUUMOで見る]({r.get('url')})")
    e.description = "\n".join(desc)
    if r.get("image"):
        e.set_image(url=big_image(r["image"]))

    def add(name, value, inline=True):
        if value:
            e.add_field(name=name, value=clip(str(value), 1024), inline=inline)

    add("💴 価格", price(i) + ("（建物価格別）" if r.get("price_excludes_building") else ""))
    if i.unit_price:
        add("㎡単価", f"{i.unit_price / 10000:.1f}万円" + (f"（坪{i.unit_price * TSUBO / 10000:.0f}万円）"
                                                           if i.type in ("land", "used_house", "new_house") else ""))
    fees = [r.get("mgmt_fee") and f"管理費 月{r['mgmt_fee']:,}円",
            r.get("repair_fee") and f"修繕積立金 月{r['repair_fee']:,}円"]
    add("管理費・修繕", " / ".join(f for f in fees if f))
    add("🛏️ 間取り", r.get("layout"))
    if i.type in ("used_condo", "new_condo"):
        add("📐 専有面積", m2(r.get("floor_m2"), r.get("floor_m2_max")))
        add("バルコニー", m2(r.get("balcony_m2")))
    else:
        add("🌳 土地", m2(r.get("land_m2"), r.get("land_m2_max"), tsubo=True))
        add("📐 建物", m2(r.get("building_m2"), r.get("building_m2_max")))
    add("🏗️ 築年月", " ".join(b for b in (built(r), f"（{age_text(i)}）" if r.get("built") and age_text(i) else "")
                              if b) or (age_text(i) if i.type in NEW_TYPES else ""))
    floors = [f"{r['floor']}階" if r.get("floor") is not None else "",
              f"{r['floors_above']}階建" if r.get("floors_above") else ""]
    add("階", " / ".join(f for f in floors if f))
    add("構造", r.get("structure"))
    add("総戸数", f"{r['total_units']:,}戸" if r.get("total_units") and i.type in ("used_condo", "new_condo") else "")
    add("向き", r.get("direction"))
    add("🚉 交通", "\n".join(station_text(s) for s in r.get("stations") or []), inline=False)
    add("📍 所在地", r.get("address"), inline=False)
    if r.get("land_rights"):
        note = f"（{clip(r['land_rights_note'], 200)}）" if r.get("land_rights_note") else ""
        add("土地の権利", r["land_rights"] + note)
    add("用途地域", r.get("zoning"))
    if r.get("coverage_pct") or r.get("far_pct"):
        add("建ぺい率・容積率", f"{r.get('coverage_pct', '-')}% / {r.get('far_pct', '-')}%")
    road = r.get("road") or {}
    add("接道", " ".join(b for b in (road.get("dir"), road.get("width_m") and f"幅{road['width_m']:g}m") if b))
    if r.get("reform"):
        add("リフォーム", clip(r["reform"].get("text") or r["reform"].get("date") or "", 300), inline=False)
    add("✨ 特徴", "・".join(r.get("features") or []), inline=False)
    add("引渡し", r.get("handover"))
    add("取引態様", r.get("deal_type"))
    add("🏢 会社", r.get("agent"), inline=False)
    hist = snap.history.get(i.key)
    if hist:
        steps = [f"{d[5:].replace('-', '/')} {man(old)} → {man(new)}" for d, old, new in hist[-6:]]
        add("📉 価格の推移", "\n".join(steps), inline=False)
    if hit.others:
        shown = [f"[{clip(o.rec.get('agent') or '掲載', 40)}]({o.rec.get('url')}) {price(o)}" for o in hit.others[:8]]
        more = f"\nほか{len(hit.others) - 8}社" if len(hit.others) > 8 else ""
        add(f"👥 ほかの掲載（{len(hit.others)}社）", "\n".join(shown) + more, inline=False)
    nd = snap.new_dates.get(i.key) or r.get("first_seen")
    e.set_footer(text=f"{TYPE_JA[i.type]} ・ SUUMO {r['id']} ・ 掲載確認 {nd or '-'}")
    _fit(e)
    return e


def _fit(e: discord.Embed, limit=5800):
    """Drop trailing fields until the embed is under Discord's 6000-character total."""
    while len(e) > limit and e.fields:
        e.remove_field(len(e.fields) - 1)


# ---------- favorites / saved searches ----------

def favorites_header(total, removed):
    s = f"**⭐ お気に入り {total}件**"
    return s + (f"\n-# うち{removed}件は掲載終了" if removed else "")


def saved_list(user, snap, picked=None):
    lines = ["**🔔 保存した条件**", "-# 新しく出た物件をDMでお知らせします。"]
    for n, s in enumerate(user.searches, 1):
        mark = "▶ " if s.id == picked else ""
        lines.append(f"{mark}**{n}.** {query_short(s.query, snap, 200)}（いま{snap.count(s.query):,}件）")
    lines.append("")
    lines.append(f"⭐ お気に入りの価格変更・掲載終了・再���載の通知：**{'オン' if user.notify_favorites else 'オフ'}**")
    if user.dm_failed:
        lines.append(DM_FAILED)
    return clip("\n".join(lines), MAX_MESSAGE)


def saved_option(s, snap, n):
    return clip(f"{n}. {query_short(s.query, snap, 90)}", 100), clip(f"いま{snap.count(s.query):,}件", 100)


# ---------- alerts (DM) ----------

def alert_message(sections, mention=None):
    """sections: [(heading, [event, ...])] -> one message under the limit (extra lines summarized)."""
    head = f"<@{mention}> " if mention else ""
    lines = [f"{head}**🔔 お知らせ**"]
    budget = MAX_MESSAGE - 100
    for heading, events in sections:
        lines.append(f"\n**{heading}**")
        shown = 0
        for e in events:
            text = line(e)
            if sum(len(x) + 1 for x in lines) + len(text) > budget:
                break
            lines.append(text)
            shown += 1
        if shown < len(events):
            lines.append(f"-# ほか{len(events) - shown}件（「🔎 物件をさがす」で見られます）")
    return clip("\n".join(lines), MAX_MESSAGE)


def search_heading(q, snap):
    return f"💾 {query_short(q, snap, 120)}"


FAV_HEADING = "⭐ お気に入りの変化"


# ---------- state message ----------

def state_summary(store):
    users = store.users.values()
    return (f"🗂️ 検索ボットの設定データ（自動更新・消さないでください）\n"
            f"-# 利用者 {len(store.users)}人 ・ 保存した条件 {sum(len(u.searches) for u in users)}件 ・ "
            f"お気に入り {sum(len(u.favorites) for u in users)}件")


