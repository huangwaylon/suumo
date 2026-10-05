"""Post unposted events to a Discord channel (REST, no gateway). User-facing text is Japanese.

Events are marked posted only after Discord accepts the message, so a failed send is retried next run.
Events older than STALE_HOURS are dropped unposted (avoids a flood after Discord was off for a while).
"""
import json
import time
from datetime import datetime, timedelta

import requests

API = "https://discord.com/api/v10"
LIMIT = 2000
PER_SECTION = 15
STALE_HOURS = 48

TYPE_JA = {"used_condo": "中古マンション", "new_house": "新築一戸建て", "used_house": "中古一戸建て",
           "land": "土地", "new_condo": "新築マンション"}
PREF_JA = {"tokyo": "東京都", "chiba": "千葉県", "kanagawa": "神奈川県", "saitama": "埼玉県", "ibaraki": "茨城県"}
SECTIONS = [("new", "🆕 新着"), ("price_changed", "💴 価格変更"), ("relisted", "🔁 再掲載"), ("removed", "🔚 掲載終了")]


def man(yen):
    """87028000 -> '8,702万円', 131800000 -> '1億3,180万円'."""
    if yen is None:
        return "価格未定"
    m = round(yen / 10000)
    oku, rest = divmod(m, 10000)
    if oku and rest:
        return f"{oku}億{rest:,}万円"
    return f"{oku}億円" if oku else f"{m:,}万円"


def _price(p):
    s = man(p.get("price"))
    return s + (f"〜{man(p['price_max'])}" if p.get("price_max") else "")


def _size(p):
    if p.get("floor_m2"):
        return f"{p['floor_m2']:g}㎡"
    parts = []
    if p.get("land_m2"):
        parts.append(f"土地{p['land_m2']:g}㎡")
    if p.get("building_m2"):
        parts.append(f"建物{p['building_m2']:g}㎡")
    return " ".join(parts)


def _station(p):
    st = (p.get("stations") or [None])[0]
    if not st:
        return ""
    how = f"バス{st['bus']}分" if st.get("bus") else (f"徒歩{st['walk']}分" if st.get("walk") is not None else "")
    return f"{st['name']}駅 {how}".strip()


def line(kind, e):
    p = e["payload"]
    where = f"{p.get('area', '')}{p.get('town', '')}"
    url = "https://suumo.jp" + p["path"] if p.get("path") else None
    head = f"**{_price(p)}**"
    if kind == "price_changed" and p.get("old_price") and p.get("price"):
        diff = p["price"] - p["old_price"]
        arrow = "⬇️" if diff < 0 else "⬆️"
        head = f"{man(p['old_price'])} → **{man(p['price'])}** {arrow}{man(abs(diff))}"
    bits = [TYPE_JA[e["type"]], p.get("layout"), _size(p), f"築{p['built'][:4]}年" if p.get("built") else None,
            _station(p)]
    detail = "・".join(b for b in bits if b)
    link = f" [詳細](<{url}>)" if url and kind != "removed" else ""
    return f"- {head} {where}{link}\n  -# {detail}"


def compose(events, now):
    """Events -> list of message strings (each under Discord's 2000-char limit)."""
    if not events:
        return []
    prefs = sorted({e["pref"] for e in events})
    head = f"**🏠 SUUMO 新着・更新**\n-# {now:%Y-%m-%d %H:%M} ・ {'、'.join(PREF_JA.get(p, p) for p in prefs)}"
    blocks = [head]
    for kind, label in SECTIONS:
        items = [e for e in events if e["kind"] == kind]
        if not items:
            continue
        lines = [line(kind, e) for e in items[:PER_SECTION]]
        if len(items) > PER_SECTION:
            lines.append(f"-# ほか{len(items) - PER_SECTION}件")
        blocks.append(f"**{label} {len(items)}件**\n" + "\n".join(lines))
    messages, cur = [], ""
    for block in blocks:
        for chunk in _split(block):
            if len(cur) + len(chunk) + 1 > LIMIT:
                messages.append(cur)
                cur = chunk
            else:
                cur = f"{cur}\n{chunk}" if cur else chunk
    if cur:
        messages.append(cur)
    return messages


def _split(block):
    """A block longer than the limit is split on line boundaries."""
    if len(block) <= LIMIT:
        return [block]
    out, cur = [], ""
    for ln in block.split("\n"):
        if len(cur) + len(ln) + 1 > LIMIT:
            out.append(cur)
            cur = ln
        else:
            cur = f"{cur}\n{ln}" if cur else ln
    return out + ([cur] if cur else [])


def send(token, channel_id, content):
    for attempt in range(5):
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
    """Post every unposted event. Without token/channel, print a preview and leave events unposted."""
    stale = (now - timedelta(hours=STALE_HOURS)).isoformat(timespec="seconds")
    db.x("UPDATE events SET posted=-1 WHERE posted=0 AND run_id IN (SELECT run_id FROM runs WHERE started < ?)", stale)
    rows = db.x("SELECT * FROM events WHERE posted=0 ORDER BY seq").fetchall()
    events = [dict(r, payload=json.loads(r["payload"])) for r in rows]
    messages = compose(events, now)
    if not messages:
        log("discord: nothing to post")
        db.commit()
        return []
    if not (token and channel_id):
        log(f"discord: not configured; preview of {len(messages)} message(s):")
        for m in messages:
            log("-" * 40 + "\n" + m)
        db.commit()
        return messages
    for m in messages:
        send(token, channel_id, m)
    db.x(f"UPDATE events SET posted=1 WHERE seq IN ({','.join('?' * len(rows))})", *[r["seq"] for r in rows])
    db.commit()
    log(f"discord: posted {len(messages)} message(s) for {len(rows)} event(s)")
    return messages
