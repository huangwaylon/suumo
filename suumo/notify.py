"""Post pending events to a Discord channel (REST, no gateway). User-facing text is Japanese.

Each message records which events it carries; events are marked posted only after their message is accepted,
so a failure part-way retries just the rest next run. Events older than STALE_HOURS are skipped (posted=-1)
to avoid a flood after Discord was unreachable for a while.
"""
import json
import time
from datetime import datetime, timedelta

import requests

API = "https://discord.com/api/v10"
LIMIT = 2000
PER_SECTION = 15
DETAIL_LIMIT = 20   # up to this many events: one line per listing; more (a wide scope): counts per area
STALE_HOURS = 48

TYPE_JA = {"used_condo": "中古マンション", "new_house": "新築一戸建て", "used_house": "中古一戸建て",
           "land": "土地", "new_condo": "新築マンション"}
PREF_JA = {"tokyo": "東京都", "chiba": "千葉県", "kanagawa": "神奈川県", "saitama": "埼玉県", "ibaraki": "茨城県"}
SECTIONS = [("new", "🆕 新着"), ("price_changed", "💴 価格変更"), ("relisted", "🔁 再掲載"), ("removed", "🔚 掲載終了")]


def man(yen):
    """87028000 -> '8,703万円', 131800000 -> '1億3,180万円'."""
    if yen is None:
        return "価格未定"
    m = round(yen / 10000)
    oku, rest = divmod(m, 10000)
    if oku and rest:
        return f"{oku}億{rest:,}万円"
    return f"{oku}億円" if oku else f"{m:,}万円"


def _price(p):
    return man(p.get("price")) + (f"〜{man(p['price_max'])}" if p.get("price_max") else "")


def _size(p):
    if p.get("floor_m2"):
        return f"{p['floor_m2']:g}㎡"
    return " ".join(f"{label}{p[k]:g}㎡" for k, label in (("land_m2", "土地"), ("building_m2", "建物")) if p.get(k))


def _station(p):
    st = (p.get("stations") or [None])[0]
    if not st:
        return ""
    how = f"バス{st['bus']}分" if st.get("bus") else (f"徒歩{st['walk']}分" if st.get("walk") is not None else "")
    return f"{st['name']}駅 {how}".strip()


def line(e):
    kind, p = e["kind"], e["payload"]
    head = f"**{_price(p)}**"
    if kind == "price_changed" and p.get("old_price") and p.get("price"):
        diff = p["price"] - p["old_price"]
        head = f"{man(p['old_price'])} → **{man(p['price'])}** {'⬇️' if diff < 0 else '⬆️'}{man(abs(diff))}"
    link = f" [詳細](<https://suumo.jp{p['path']}>)" if p.get("path") and kind != "removed" else ""
    bits = [TYPE_JA[e["type"]], p.get("layout"), _size(p), f"築{p['built'][:4]}年" if p.get("built") else None,
            _station(p)]
    return f"- {head} {p.get('area', '')}{p.get('town', '')}{link}\n  -# {'・'.join(b for b in bits if b)}"


def compose(events, now):
    """Events -> [(text, [seq, ...])], each text under Discord's limit, carrying the events it shows."""
    if not events:
        return []
    pieces = _detail_pieces(events, now) if len(events) <= DETAIL_LIMIT else _summary_pieces(events, now)
    return _pack(pieces)


def _where(events):
    return "、".join(PREF_JA.get(p, p) for p in sorted({e["pref"] for e in events}))


def _detail_pieces(events, now):
    pieces = [(f"**🏠 SUUMO 新着・更新**\n-# {now:%Y-%m-%d %H:%M} ・ {_where(events)}", [])]
    for kind, label in SECTIONS:
        items = [e for e in events if e["kind"] == kind]
        if not items:
            continue
        pieces.append((f"**{label} {len(items)}件**", []))
        pieces += [(line(e), [e.get("seq")]) for e in items[:PER_SECTION]]
        if len(items) > PER_SECTION:
            pieces.append((f"-# ほか{len(items) - PER_SECTION}件", [e.get("seq") for e in items[PER_SECTION:]]))
    return pieces


def _kind(e):
    """new / drop / rise / relisted / removed (a price change split by direction)."""
    if e["kind"] != "price_changed":
        return e["kind"]
    p = e["payload"]
    return "drop" if p.get("old_price") and p.get("price") and p["price"] < p["old_price"] else "rise"


COUNTS = [("new", "🆕 新着"), ("drop", "⬇️ 値下げ"), ("rise", "⬆️ 値上げ"), ("relisted", "🔁 再掲載"),
          ("removed", "🔚 掲載終了")]
SUMMARY_FOOTER = "-# 物件の一覧は下のメニューの「🆕 新着」「💴 値下げ」から。条件を保存すると新着をDMでお知らせします。"


def _counts(events, labels=True):
    n = {}
    for e in events:
        n[_kind(e)] = n.get(_kind(e), 0) + 1
    if labels:
        return " ・ ".join(f"{label} **{n[k]:,}件**" for k, label in COUNTS if n.get(k))
    return " ".join(f"{label.split()[0]}{n[k]:,}" for k, label in COUNTS if n.get(k))


def _summary_pieces(events, now):
    """Too many to list: totals, a line per type, a line per area. Listings are in the bot (🆕 / 💴)."""
    pieces = [(f"**🏠 SUUMO 今日の動き**\n-# {now:%Y-%m-%d %H:%M} ・ {_where(events)}\n{_counts(events)}", [])]
    by_type = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)
    pieces.append(("**種別**\n" + "\n".join(f"- {TYPE_JA[t]}　{_counts(by_type[t], labels=False)}"
                                         for t in TYPE_JA if t in by_type), []))
    by_area = {}
    for e in events:
        by_area.setdefault((e["pref"], e.get("area_code", "")), []).append(e)
    multi = len({p for p, _ in by_area}) > 1
    pieces.append(("**エリア別**", []))
    for (pref, _code), items in sorted(by_area.items()):
        name = items[0]["payload"].get("area") or _code
        prefix = PREF_JA.get(pref, pref) if multi else ""
        pieces.append((f"- **{prefix}{name}**　{_counts(items, labels=False)}", [e.get("seq") for e in items]))
    pieces.append((SUMMARY_FOOTER, []))
    return pieces


def _pack(pieces):
    """Join pieces into messages under Discord's limit; each message carries the seqs of its pieces."""
    messages, text, seqs = [], "", []
    for piece, piece_seqs in pieces:
        if text and len(text) + 1 + len(piece) > LIMIT:
            messages.append((text, seqs))
            text, seqs = "", []
        text = f"{text}\n{piece}" if text else piece
        seqs = seqs + piece_seqs
    messages.append((text, seqs))
    return messages


def send(token, channel_id, content):
    for _ in range(5):
        r = requests.post(f"{API}/channels/{channel_id}/messages",
                          headers={"Authorization": f"Bot {token}"},
                          json={"content": content, "flags": 4,  # 4 = suppress link previews
                                "allowed_mentions": {"parse": []}}, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return r.json()["id"]
    raise RuntimeError("Discord rate limit: gave up")


def notify(db, now: datetime, token=None, channel_id=None, log=print):
    """Post every pending event. Without token/channel, print a preview and leave events pending."""
    stale = (now - timedelta(hours=STALE_HOURS)).strftime("%Y%m%dT%H%M%S")
    db.x("UPDATE events SET posted=-1 WHERE posted=0 AND run_id < ?", stale)
    db.commit()
    rows = db.x("SELECT * FROM events WHERE posted=0 ORDER BY seq").fetchall()
    messages = compose([dict(r, payload=json.loads(r["payload"])) for r in rows], now)
    if not messages:
        log("discord: nothing to post")
        return []
    if not (token and channel_id):
        log(f"discord: not configured; preview of {len(messages)} message(s):")
        for text, _ in messages:
            log("-" * 40 + "\n" + text)
        return messages
    posted = 0
    for text, seqs in messages:
        try:
            send(token, channel_id, text)
        except Exception as e:
            log(f"discord: send failed, {len(messages) - posted} message(s) left for the next run: {e!r}")
            break
        if seqs:
            db.x(f"UPDATE events SET posted=1 WHERE seq IN ({','.join('?' * len(seqs))})", *seqs)
            db.commit()
        posted += 1
    log(f"discord: posted {posted}/{len(messages)} message(s)")
    return messages
