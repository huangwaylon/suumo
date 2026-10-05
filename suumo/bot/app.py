"""The search bot process: `python -m suumo bot` (launchd `local.suumo-bot.plist`, kept alive).

Runs beside the daily crawl and shares only files: it reads data/ (never state.db) and reloads when the export
rewrites it. Keeps the menu as the newest message in the channel, answers buttons, and after each crawl DMs
people whose saved searches or favorites have news.
"""
import asyncio
import contextlib
import logging
from pathlib import Path

import discord

from ..catalog import Catalog, read_events
from . import alerts
from . import text as T
from .store import DiscordStateBackend, Store
from .ui import SESSIONS_PER_USER, MainMenu

WATCH_SECONDS = 60     # how often data/ is checked for a new export
MENU_DELAY = 5         # seconds after a post before the menu is moved below it (a feed post is several messages)
MENU_SCAN = 50         # recent channel messages searched for old menus

log = logging.getLogger("suumo.bot")


class SuumoBot(discord.Client):
    def __init__(self, data_dir, channel_id: int, state_channel_id: int, backup_path, today_fn, proxy=None):
        super().__init__(intents=discord.Intents.default(), proxy=proxy)
        self.data_dir = Path(data_dir)
        self.catalog = Catalog(data_dir, today_fn)
        self.channel_id = channel_id
        self.store = Store()
        self.backend = DiscordStateBackend(self, state_channel_id, backup_path)
        self.sessions = {}               # sid -> Session, oldest first
        self.menu_view: MainMenu | None = None
        self.loaded = False
        self._menu_task = None
        self._save_lock = asyncio.Lock()
        self._tasks = set()

    # ---------- lifecycle ----------

    async def setup_hook(self):
        await asyncio.to_thread(self.catalog.refresh)
        log.info("catalog: %d listings, %d runs", len(self.catalog.snap.items), len(self.catalog.snap.runs))
        self.menu_view = MainMenu(self)
        self.add_view(self.menu_view)

    async def on_ready(self):
        if self.loaded:  # reconnects fire on_ready again
            return
        data = await self.backend.load()
        self.store = Store.from_json(data) if data else Store()
        self.store.drop_missing_favorites(self.catalog.snap)
        self.loaded = True
        log.info("ready as %s; %d users", self.user, len(self.store.users))
        await self.tidy_channel()
        self.spawn(self.watch_forever())

    def ready_catalog(self):
        return self.loaded and self.catalog.sig is not None

    def spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def channel(self):
        return self.get_channel(self.channel_id) or await self.fetch_channel(self.channel_id)

    # ---------- sessions ----------

    def register(self, session):
        self.sessions[session.sid] = session
        mine = [s for s in self.sessions.values() if s.uid == session.uid]
        for old in mine[:-SESSIONS_PER_USER]:
            old.stop()

    def unregister(self, session):
        self.sessions.pop(session.sid, None)

    async def on_interaction(self, interaction: discord.Interaction):
        """A tap on a screen this process doesn't know (opened before a restart, or long idle)."""
        if interaction.type is not discord.InteractionType.component:
            return
        cid = (interaction.data or {}).get("custom_id", "")
        if cid.startswith("s:") and cid.split(":")[1] not in self.sessions:
            with contextlib.suppress(discord.HTTPException):
                await interaction.response.edit_message(content=T.EXPIRED, embeds=[], view=None)

    # ---------- state ----------

    async def after_change(self):
        """Save in the background once something persistent changed (never inside an interaction's 3 s)."""
        if self.store.dirty and not self._save_lock.locked():
            self.spawn(self.persist())

    async def persist(self):
        async with self._save_lock:
            while self.store.dirty:
                self.store.dirty = False
                try:
                    await self.backend.save(self.store.to_json(), T.state_summary(self.store))
                except Exception:
                    log.exception("saving state failed; retrying on the next change")
                    self.store.dirty = True
                    return

    # ---------- the menu stays the newest message ----------

    def is_menu(self, m: discord.Message):
        return m.author.id == self.user.id and any(
            (getattr(c, "custom_id", None) or "").startswith("suumo:")
            for row in m.components for c in getattr(row, "children", [])
        )

    async def tidy_channel(self):
        """Delete our old menus; post a new one unless the newest message already is the menu."""
        ch = await self.channel()
        newest, menus = None, []
        async for m in ch.history(limit=MENU_SCAN):
            newest = newest or m
            if self.is_menu(m):
                menus.append(m)
        keep = newest if newest is not None and self.is_menu(newest) else None
        for m in menus:
            if m is not keep:
                try:
                    await m.delete()
                except discord.HTTPException as e:
                    log.warning("could not delete old menu %s: %r", m.id, e)
        if keep is None:
            await ch.send(T.MENU, view=self.menu_view, silent=True)

    async def on_message(self, m: discord.Message):
        if m.channel.id != self.channel_id or (self.user and self.is_menu(m)):
            return
        if self._menu_task and not self._menu_task.done():
            self._menu_task.cancel()
        self._menu_task = self.spawn(self._move_menu_later())

    async def _move_menu_later(self):
        await asyncio.sleep(MENU_DELAY)
        try:
            await self.tidy_channel()
        except Exception:
            log.exception("moving the menu failed")

    # ---------- data and alerts ----------

    async def watch_forever(self):
        while not self.is_closed():
            try:
                await self.watch_once()
            except Exception:
                log.exception("watch failed")
            await asyncio.sleep(WATCH_SECONDS)

    async def watch_once(self):
        if self.catalog.changed():
            await asyncio.to_thread(self.catalog.refresh)
            log.info("catalog reloaded: %d listings", len(self.catalog.snap.items))
            self.store.drop_missing_favorites(self.catalog.snap)
        await self.send_alerts()
        await self.after_change()

    async def send_alerts(self):
        snap = self.catalog.snap
        if not snap.runs:
            return
        events = {}
        for uid, u in list(self.store.users.items()):
            if not u.wants_alerts():
                continue
            if u.last_run is None:            # alerts start from now
                u.last_run = snap.runs[-1]
                self.store.dirty = True
                continue
            runs = alerts.pending_runs(u, snap)
            if not runs:
                continue
            for r in runs:
                if r not in events:
                    events[r] = read_events(self.data_dir / "events" / f"{r}.json") or []
            sections = alerts.collect(u, snap, [(r, events[r]) for r in runs],
                                      lambda q: T.search_heading(q, snap), T.FAV_HEADING)
            if sections and not await self.deliver(uid, u, sections):
                continue                       # Discord trouble: this person's runs are retried next time
            u.last_run = runs[-1]
            self.store.dirty = True
            await self.persist()               # record progress before the next person: nobody hears twice

    async def deliver(self, uid, u, sections):
        """DM; if DMs are refused, mention the person in the channel instead. False on other failures."""
        try:
            user = self.get_user(int(uid)) or await self.fetch_user(int(uid))
            await user.send(T.alert_message(sections))
            if u.dm_failed:
                u.dm_failed = False
                self.store.dirty = True
            return True
        except discord.Forbidden:
            log.info("DM refused by %s; posting in the channel", uid)
            u.dm_failed = True
            self.store.dirty = True
        except discord.HTTPException:
            log.exception("DM to %s failed", uid)
            return False
        try:
            ch = await self.channel()
            await ch.send(T.alert_message(sections, mention=uid),
                          allowed_mentions=discord.AllowedMentions(users=[discord.Object(int(uid))]))
            return True
        except discord.HTTPException:
            log.exception("channel alert for %s failed", uid)
            return False
