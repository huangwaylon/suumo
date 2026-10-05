"""What the bot remembers per person: last conditions, saved searches, favorites, alert progress.

Persisted as `state.json` on a bot message in #state (DISCORD_STATE_CHANNEL_ID), with a local backup written on
every save and used when the message is gone (the gym bot's pattern). The message text is a short summary.
"""
import io
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from datetime import date

import discord

from ..catalog import Query

VERSION = 1
MAX_SEARCHES = 5
MAX_FAVORITES = 100
FORGET_AFTER_DAYS = 7      # a favorite missing from data/ this long has been purged (not a mid-export reload)
STATE_FILENAME = "state.json"
HISTORY_SCAN_LIMIT = 50

log = logging.getLogger("suumo.bot.state")


@dataclass
class Saved:
    id: str
    query: Query
    created: str


@dataclass
class User:
    name: str = ""
    last_query: Query = field(default_factory=Query)
    searches: list = field(default_factory=list)     # [Saved]
    favorites: dict = field(default_factory=dict)    # key -> {"price": yen|None, "added": "YYYY-MM-DD"}
    notify_favorites: bool = True
    last_run: str | None = None                      # newest run whose events this person has been told about
    dm_failed: bool = False

    def wants_alerts(self):
        return bool(self.searches or (self.notify_favorites and self.favorites))

    def to_dict(self):
        return {"name": self.name, "last_query": self.last_query.to_dict(),
                "searches": [{"id": s.id, "query": s.query.to_dict(), "created": s.created} for s in self.searches],
                "favorites": self.favorites, "notify_favorites": self.notify_favorites, "last_run": self.last_run,
                "dm_failed": self.dm_failed}

    @classmethod
    def from_dict(cls, d):
        return cls(name=d.get("name", ""), last_query=Query.from_dict(d.get("last_query")),
                   searches=[Saved(s["id"], Query.from_dict(s.get("query")), s.get("created", ""))
                             for s in d.get("searches", [])],
                   favorites=dict(d.get("favorites", {})), notify_favorites=d.get("notify_favorites", True),
                   last_run=d.get("last_run"), dm_failed=d.get("dm_failed", False))


class Store:
    def __init__(self):
        self.users: dict[str, User] = {}
        self.dirty = False

    def user(self, uid, name=None):
        u = self.users.get(str(uid))
        if u is None:
            u = self.users[str(uid)] = User()
            self.dirty = True
        if name and u.name != name:
            u.name = name
            self.dirty = True
        return u

    # --- actions (raise ValueError with a user-facing message key) ---

    def _alerts_from_now(self, u: User, was_on, newest_run):
        """Alerts (re)starting start from now: not from the history in git, nor from runs while they were off."""
        if not was_on and u.wants_alerts():
            u.last_run = newest_run

    def save_search(self, u: User, q: Query, today, newest_run):
        q = q.set(sort="new")
        if q.is_empty():
            raise ValueError("empty")
        if any(s.query == q for s in u.searches):
            raise ValueError("dup")
        if len(u.searches) >= MAX_SEARCHES:
            raise ValueError("full")
        was_on = u.wants_alerts()
        u.searches.append(Saved(secrets.token_hex(3), q, today))
        self._alerts_from_now(u, was_on, newest_run)
        self.dirty = True

    def delete_search(self, u: User, sid):
        u.searches = [s for s in u.searches if s.id != sid]
        self.dirty = True

    def toggle_favorite(self, u: User, key, price, today, newest_run):
        """Returns True when added."""
        if key in u.favorites:
            del u.favorites[key]
            self.dirty = True
            return False
        if len(u.favorites) >= MAX_FAVORITES:
            raise ValueError("full")
        was_on = u.wants_alerts()
        u.favorites[key] = {"price": price, "added": today}
        self._alerts_from_now(u, was_on, newest_run)
        self.dirty = True
        return True

    def set_notify_favorites(self, u: User, on, newest_run):
        was_on = u.wants_alerts()
        u.notify_favorites = on
        self._alerts_from_now(u, was_on, newest_run)
        self.dirty = True

    def drop_missing_favorites(self, snap, today):
        """Forget favorites missing from data/ for FORGET_AFTER_DAYS (purged 30 days after removal). A single
        reload can miss a listing that the export is just moving into removed.jsonl, so never drop at once."""
        for u in self.users.values():
            for k, fav in list(u.favorites.items()):
                if snap.get(k) is not None:
                    if fav.pop("missing", None):
                        self.dirty = True
                elif "missing" not in fav:
                    fav["missing"] = today.isoformat()
                    self.dirty = True
                elif (today - date.fromisoformat(fav["missing"])).days >= FORGET_AFTER_DAYS:
                    del u.favorites[k]
                    self.dirty = True

    # --- JSON ---

    def to_json(self):
        return {"version": VERSION, "users": {uid: u.to_dict() for uid, u in sorted(self.users.items())}}

    @classmethod
    def from_json(cls, d):
        if d.get("version") != VERSION:
            raise SystemExit(f"bot state version {d.get('version')!r} is not {VERSION}; refusing to overwrite it")
        s = cls()
        s.users = {uid: User.from_dict(u) for uid, u in d.get("users", {}).items()}
        return s


class DiscordStateBackend:
    """state.json as an attachment on the bot's newest message with one in the state channel."""

    def __init__(self, client: discord.Client, channel_id: int, backup_path):
        self.client = client
        self.channel_id = channel_id
        self.backup_path = str(backup_path)
        self.message: discord.Message | None = None

    async def _channel(self):
        return self.client.get_channel(self.channel_id) or await self.client.fetch_channel(self.channel_id)

    async def _find_message(self):
        channel = await self._channel()
        async for m in channel.history(limit=HISTORY_SCAN_LIMIT):
            if m.author.id == self.client.user.id and _attachment(m):
                return m
        return None

    async def load(self):
        """The saved state, or the local backup, or None when neither exists."""
        self.message = await self._find_message()
        if self.message:
            return json.loads(await _attachment(self.message).read())
        if os.path.exists(self.backup_path):
            log.warning("no state message in the state channel; restoring from %s", self.backup_path)
            with open(self.backup_path, encoding="utf-8") as f:
                return json.load(f)
        return None

    async def save(self, data, summary):
        payload = json.dumps(data, ensure_ascii=False, indent=1).encode()
        tmp = self.backup_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, self.backup_path)
        if self.message:
            try:
                self.message = await self.message.edit(content=summary, attachments=[_file(payload)])
                return
            except discord.NotFound:
                log.warning("state message was deleted; posting a new one")
        self.message = await (await self._channel()).send(summary, file=_file(payload), silent=True)


def _attachment(m):
    return next((a for a in m.attachments if a.filename == STATE_FILENAME), None)


def _file(payload):
    return discord.File(io.BytesIO(payload), filename=STATE_FILENAME)
