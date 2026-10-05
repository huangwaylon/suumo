"""Buttons and dropdowns: the only way to use the bot.

`MainMenu` is persistent (fixed `suumo:*` custom_ids, no timeout), so it keeps working after a restart.
Everything it opens is a `Session`: one person's ephemeral screen, edited in place on every tap. Session
components have custom_ids `s:<session>:<name>`; a tap on a session the bot no longer knows (restart, or idle
past SESSION_TIMEOUT) is answered by `SuumoBot.on_interaction` with 「古くなりました」.

Callbacks change state synchronously and redraw in the same response (one API call, well inside Discord's 3 s);
saving to #state happens afterwards.
"""
from __future__ import annotations

import logging
import math
import secrets
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import discord
from discord import ui

from ..catalog import Hit, Query, sort_hits
from . import text as T
from .store import MAX_FAVORITES, MAX_SEARCHES

if TYPE_CHECKING:
    from .app import SuumoBot

log = logging.getLogger("suumo.bot.ui")
JST = ZoneInfo("Asia/Tokyo")

SESSION_TIMEOUT = 6 * 3600   # idle time after which a screen is forgotten (each tap restarts the clock)
SESSIONS_PER_USER = 3        # older screens of the same person are forgotten first
PAGE_SIZE = 5                # listings per results page (one embed each, with photo)
SELECTS_PER_PAGE = 4         # area screen: dropdowns per page (the 5th row holds the buttons)
BUILDING_TYPES = ("used_condo", "new_condo", "used_house", "new_house")
GREY, BLUE, GREEN, RED = (discord.ButtonStyle.secondary, discord.ButtonStyle.primary,
                          discord.ButtonStyle.success, discord.ButtonStyle.danger)


def today():
    return datetime.now(JST).date().isoformat()


class MainMenu(ui.View):
    """The menu message at the bottom of the channel."""

    def __init__(self, bot: SuumoBot):
        super().__init__(timeout=None)
        self.bot = bot

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.channel_id == self.bot.channel_id

    @ui.button(label="物件をさがす", emoji="🔎", style=BLUE, custom_id="suumo:search", row=0)
    async def search(self, interaction, _button):
        await Session.open(self.bot, interaction, "panel")

    @ui.button(label="新着", emoji="🆕", style=GREY, custom_id="suumo:new", row=0)
    async def new(self, interaction, _button):
        await Session.open(self.bot, interaction, "new")

    @ui.button(label="値下げ", emoji="💴", style=GREY, custom_id="suumo:drops", row=0)
    async def drops(self, interaction, _button):
        await Session.open(self.bot, interaction, "drops")

    @ui.button(label="お気に入り", emoji="⭐", style=GREY, custom_id="suumo:favorites", row=1)
    async def favorites(self, interaction, _button):
        await Session.open(self.bot, interaction, "favorites")

    @ui.button(label="保存した条件", emoji="🔔", style=GREY, custom_id="suumo:saved", row=1)
    async def saved(self, interaction, _button):
        await Session.open(self.bot, interaction, "saved")

    @ui.button(label="使い方", emoji="❓", style=GREY, custom_id="suumo:help", row=1)
    async def help(self, interaction, _button):
        await interaction.response.send_message(T.HELP, ephemeral=True)


class Session(ui.View):
    """One person's screen: the search panel, its sub-screens, results, a listing, favorites, saved searches."""

    def __init__(self, bot: SuumoBot, interaction: discord.Interaction):
        super().__init__(timeout=SESSION_TIMEOUT)
        self.bot = bot
        self.sid = secrets.token_hex(4)
        self.uid = interaction.user.id
        self.user = bot.store.user(self.uid, interaction.user.display_name)
        self.q: Query = self.user.last_query
        self.screen = "panel"
        self.mode = "search"         # what the results list shows: search | favorites
        self.title = "🔎 検索結果"
        self.hits: list[Hit] = []
        self.page = 0                # results page
        self.pos = 0                 # index in hits of the listing on the detail screen
        self.line = None             # station screen: chosen line
        self.line_page = 0
        self.station_page = 0
        self.area_page = 0
        self.origin = None           # "saved" when results came from a saved search (offers a way back)
        self.picked = None           # saved screen: chosen saved search id
        self.notice = None           # one-time line shown at the top of the next redraw
        bot.register(self)

    @classmethod
    async def open(cls, bot: SuumoBot, interaction, mode):
        if not bot.ready_catalog():
            await interaction.response.send_message(T.LOADING, ephemeral=True)
            return
        s = cls(bot, interaction)
        if mode == "new":
            s.q, s.title = Query(new_only=True), "🆕 新着（この1週間）"
            s.open_results()
        elif mode == "drops":
            s.q, s.title = Query(drops_only=True), "💴 値下げ（この30日）"
            s.open_results()
        elif mode == "favorites":
            s.open_favorites()
        elif mode == "saved":
            s.screen = "saved"
        content, embeds = s.render()
        await interaction.response.send_message(content, embeds=embeds, view=s, ephemeral=True)
        await bot.after_change()

    @property
    def snap(self):
        return self.bot.catalog.snap

    # ---------- plumbing ----------

    def stop(self):
        self.bot.unregister(self)
        super().stop()

    async def on_timeout(self):
        self.bot.unregister(self)

    async def on_error(self, interaction, error, item):
        log.exception("session %s: %r failed", self.sid, item, exc_info=error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(T.ERROR, ephemeral=True)
            else:
                await interaction.response.send_message(T.ERROR, ephemeral=True)
        except discord.HTTPException:
            pass

    def cid(self, name):
        return f"s:{self.sid}:{name}"

    def add_select(self, name, placeholder, options, on_pick, row, multi=False):
        """options: [(label, value, description, chosen)]. on_pick(values) changes state; then a redraw."""
        options = options[:25]
        if not options:
            return
        select = ui.Select(custom_id=self.cid(name), placeholder=placeholder, row=row,
                           min_values=0 if multi else 1, max_values=len(options) if multi else 1,
                           options=[discord.SelectOption(label=T.clip(label, 100), value=value, default=chosen,
                                                         description=T.clip(desc, 100) if desc else None)
                                    for label, value, desc, chosen in options])

        async def callback(interaction):
            await self.apply(interaction, on_pick, interaction.data.get("values", []))

        select.callback = callback
        self.add_item(select)

    def add_button(self, name, label, emoji, on_press, row, style=GREY, disabled=False):
        button = ui.Button(custom_id=self.cid(name), label=T.clip(label, 80), emoji=emoji, style=style, row=row,
                           disabled=disabled)

        async def callback(interaction):
            await self.apply(interaction, on_press)

        button.callback = callback
        self.add_item(button)

    async def apply(self, interaction, change, *args):
        """Change state, redraw in the same response, then save if anything persistent changed."""
        try:
            change(*args)
        except Exception:
            log.exception("session %s: change failed", self.sid)
            self.notice = T.ERROR
        content, embeds = self.render()
        await interaction.response.edit_message(content=content, embeds=embeds, view=self)
        await self.bot.after_change()

    def render(self):
        self.clear_items()
        content, embeds = getattr(self, f"draw_{self.screen}")()
        self.notice = None
        return content, embeds

    def go(self, screen):
        def change(*_):
            self.screen = screen
        return change

    def count_with(self, **kw):
        return self.snap.count(self.q.set(**kw))

    def add_results_button(self, row, count=None):
        count = self.snap.count(self.q) if count is None else count
        self.add_button("show", f"{count:,}件を見る", "🔎", self.show_search, row, GREEN, disabled=count == 0)

    def show_search(self):
        if self.origin is None:
            self.title = "🔎 検索結果"
        self.open_results()

    def add_back(self, row):
        self.add_button("back", "戻る", "↩️", self.go("panel"), row)

    def shows_buildings(self):
        return not self.q.types or any(t in BUILDING_TYPES for t in self.q.types)

    def shows_land(self):
        return not self.q.types or any(t in ("land", "used_house", "new_house") for t in self.q.types)

    # ---------- search panel ----------

    def draw_panel(self):
        snap, q = self.snap, self.q
        count = snap.count(q)
        types = snap.facet_types(q)
        if len(snap.b_type) > 1:
            self.add_select("types", T.PH_TYPES, [
                (f"{T.TYPE_EMOJI[t]} {T.TYPE_JA[t]}", t, f"{n:,}件", t in q.types) for t, n in types
            ], lambda v: self.set(types=v), 0, multi=True)
        self.add_select("price_max", T.PH_PRICE_MAX, [(T.ANY, "none", None, q.price_max is None)] + [
            (f"〜{T.oku(v)}", str(v * 1_000_000), f"{self.count_with(price_max=v * 1_000_000):,}件",
             q.price_max == v * 1_000_000) for v in T.PRICE_MAX
        ], lambda v: self.set(price_max=_int(v)), 1)
        if self.shows_buildings():
            self.add_select("rooms", T.PH_ROOMS, [
                (label, str(b), f"{self.count_with(rooms=(b,)):,}件", b in q.rooms) for b, label in T.ROOMS.items()
            ], lambda v: self.set(rooms=[int(x) for x in v]), 2, multi=True)
        if len(snap.b_area) > 1:
            names = "・".join(T.area_name(snap, a) for a in q.areas)
            self.add_button("areas", T.button_value("エリア", names), "📍", self.go("areas"), 3,
                            BLUE if q.areas else GREY)
        stations = "・".join(q.stations) + (f" {q.walk_max}分" if q.walk_max is not None else "")
        self.add_button("stations", T.button_value("駅", stations.strip()), "🚉", self.go("stations"), 3,
                        BLUE if q.stations or q.walk_max is not None else GREY)
        size_set = any(v is not None for v in (q.price_min, q.size_min, q.land_min, q.age_max)) or q.post_1981
        self.add_button("size", "広さ・築年数など", "📐", self.go("size"), 3, BLUE if size_set else GREY)
        extra = len(q.features) + q.freehold_only + q.no_condition + q.new_only + q.drops_only
        self.add_button("extra", f"こだわり（{extra}）" if extra else "こだわり", "✨", self.go("extra"), 3,
                        BLUE if extra else GREY)
        self.add_results_button(4, count)
        self.add_button("save", "保存して通知", "💾", self.save, 4)
        self.add_button("reset", "リセット", "🧹", self.reset, 4, disabled=q.is_empty())
        return T.panel(q, snap, count, self.notice), []

    def set(self, **kw):
        self.q = self.q.set(**kw)
        self.remember()

    def reset(self):
        self.q = Query(sort=self.q.sort)
        self.remember()

    def remember(self):
        """Panel edits in a normal search are kept for next time (saved with the next save, not on every tap);
        the 🆕 / 💴 shortcuts and saved searches don't overwrite them."""
        if self.title == "🔎 検索結果" and self.origin is None:
            self.user.last_query = self.q

    def save(self):
        try:
            self.bot.store.save_search(self.user, self.q, today(), self.newest_run())
            self.notice = T.SAVED_OK
        except ValueError as e:
            self.notice = {"empty": T.SAVE_EMPTY, "dup": T.SAVE_DUP,
                           "full": T.SAVE_FULL.format(n=MAX_SEARCHES)}[str(e)]

    def newest_run(self):
        return self.snap.runs[-1] if self.snap.runs else ""

    # ---------- sub-screens ----------

    def draw_areas(self):
        snap, q = self.snap, self.q
        groups = T.area_groups(snap.facet_areas(q), snap)
        pages = max(1, math.ceil(len(groups) / SELECTS_PER_PAGE))
        self.area_page = min(self.area_page, pages - 1)
        for k, (label, areas) in enumerate(groups[self.area_page * SELECTS_PER_PAGE:][:SELECTS_PER_PAGE]):
            codes = {a for a, _ in areas}
            self.add_select(f"area{k}", f"📍 {label}", [
                (T.area_name(snap, a), a, f"{n:,}件", a in q.areas) for a, n in areas
            ], lambda v, codes=codes: self.set(areas=(set(self.q.areas) - codes) | set(v)), k, multi=True)
        self.add_back(4)
        self.add_results_button(4)
        self.add_button("all_areas", "すべてのエリア", "🧹", lambda: self.set(areas=()), 4, disabled=not q.areas)
        if pages > 1:
            self.add_button("area_prev", "前へ", "◀️", lambda: self._area_page(-1), 4, disabled=self.area_page == 0)
            self.add_button("area_next", "次へ", "▶️", lambda: self._area_page(1), 4,
                            disabled=self.area_page >= pages - 1)
        return T.areas_screen(q, snap, self.notice), []

    def _area_page(self, step):
        self.area_page += step

    def draw_stations(self):
        snap, q = self.snap, self.q
        lines = snap.facet_lines(q)
        if self.line is None and q.stations:  # open on the line of a chosen station
            self.line = next((ln for ln, _ in lines if set(q.stations) & snap.line_stations.get(ln, set())), None)
        per = 25
        pages = max(1, math.ceil(len(lines) / per))
        self.line_page = min(self.line_page, pages - 1)
        shown = lines[self.line_page * per:][:per]
        self.add_select("line", T.PH_LINE, [
            (ln, ln, f"{n:,}件", ln == self.line) for ln, n in shown
        ], lambda v: self._pick_line(v[0]), 0)
        station_pages = 1
        if self.line:
            on_line = snap.facet_stations(q, self.line)
            station_pages = max(1, math.ceil(len(on_line) / per))
            self.station_page = min(self.station_page, station_pages - 1)
            options = on_line[self.station_page * per:][:per]
            names = {n for n, _ in options}
            self.add_select("station", f"🚉 {self.line}の駅（いくつでも）", [
                (f"{n}駅", n, f"{c:,}件", n in q.stations) for n, c in options
            ], lambda v, names=names: self.set(stations=(set(self.q.stations) - names) | set(v)), 1, multi=True)
        self.add_select("walk", T.PH_WALK, [(T.ANY, "none", None, q.walk_max is None)] + [
            (f"徒歩{w}分以内", str(w), f"{self.count_with(walk_max=w):,}件", q.walk_max == w) for w in T.WALKS
        ], lambda v: self.set(walk_max=_int(v)), 2)
        if pages > 1:
            self.add_button("line_prev", "前の路線", "◀️", lambda: self._line_page(-1), 3,
                            disabled=self.line_page == 0)
            self.add_button("line_next", "次の路線", "▶️", lambda: self._line_page(1), 3,
                            disabled=self.line_page >= pages - 1)
        if station_pages > 1:
            self.add_button("station_prev", "前の駅", "⏪", lambda: self._station_page(-1), 3,
                            disabled=self.station_page == 0)
            self.add_button("station_next", "次の駅", "⏩", lambda: self._station_page(1), 3,
                            disabled=self.station_page >= station_pages - 1)
        self.add_back(4)
        self.add_results_button(4)
        self.add_button("clear_stations", "駅をクリア", "🧹", lambda: self.set(stations=(), walk_max=None), 4,
                        disabled=not (q.stations or q.walk_max is not None))
        return T.stations_screen(q, snap, self.line, self.notice), []

    def _pick_line(self, line):
        self.line = line
        self.station_page = 0

    def _line_page(self, step):
        self.line_page += step
        self.line = None

    def _station_page(self, step):
        self.station_page += step

    def draw_size(self):
        q = self.q
        self.add_select("price_min", T.PH_PRICE_MIN, [(T.ANY, "none", None, q.price_min is None)] + [
            (f"{T.oku(v)}〜", str(v * 1_000_000), f"{self.count_with(price_min=v * 1_000_000):,}件",
             q.price_min == v * 1_000_000) for v in T.PRICE_MIN
        ], lambda v: self.set(price_min=_int(v)), 0)
        if self.shows_buildings():
            self.add_select("size_min", T.PH_SIZE, [(T.ANY, "none", None, q.size_min is None)] + [
                (f"{v}㎡以上", str(v), f"{self.count_with(size_min=v):,}件", q.size_min == v) for v in T.SIZES
            ], lambda v: self.set(size_min=_int(v)), 1)
        if self.shows_land():
            self.add_select("land_min", T.PH_LAND, [(T.ANY, "none", None, q.land_min is None)] + [
                (f"{v}㎡以上（{v / T.TSUBO:.0f}坪）", str(v), f"{self.count_with(land_min=v):,}件", q.land_min == v)
                for v in T.LANDS
            ], lambda v: self.set(land_min=_int(v)), 2)
        if self.shows_buildings():
            options = [(T.ANY, "none", None, q.age_max is None and not q.post_1981)]
            options += [("新築・築1年未満" if v == 0 else f"築{v}年以内", str(v),
                         f"{self.count_with(age_max=v, post_1981=False):,}件", q.age_max == v) for v in T.AGES]
            options.append(("新耐震基準（1981年6月以降）", "quake",
                            f"{self.count_with(age_max=None, post_1981=True):,}件", q.post_1981))
            self.add_select("age", T.PH_AGE, options, self._pick_age, 3)
        self.add_back(4)
        self.add_results_button(4)
        return T.size_screen(q, self.snap, self.notice), []

    def _pick_age(self, values):
        v = values[0]
        if v == "quake":
            self.set(age_max=None, post_1981=True)
        else:
            self.set(age_max=_int(values), post_1981=False)

    def draw_extra(self):
        snap, q = self.snap, self.q
        tags = snap.facet_features(q)
        if tags:
            self.add_select("features", T.PH_FEATURES, [
                (f, f, f"{n:,}件", f in q.features) for f, n in tags
            ], lambda v: self.set(features=v), 0, multi=True)
        toggles = [("freehold_only", "所有権のみ", "📜"), ("new_only", "新着のみ", "🆕"),
                   ("drops_only", "値下げのみ", "💴")]
        if self.shows_land() and (not q.types or "land" in q.types or "new_house" in q.types):
            toggles.insert(1, ("no_condition", "建築条件なし", "🏷️"))
        for name, label, emoji in toggles:
            on = getattr(q, name)
            self.add_button(name, ("✅ " if on else "") + label, emoji,
                            lambda name=name: self.set(**{name: not getattr(self.q, name)}), 1, GREEN if on else GREY)
        self.add_back(4)
        self.add_results_button(4)
        extra_set = q.features or q.freehold_only or q.no_condition or q.new_only or q.drops_only
        self.add_button("clear_extra", "クリア", "🧹",
                        lambda: self.set(features=(), freehold_only=False, no_condition=False, new_only=False,
                                         drops_only=False), 4, disabled=not extra_set)
        return T.extra_screen(q, snap, bool(tags), self.notice), []

    # ---------- results and detail ----------

    def open_results(self):
        self.mode = "search"
        self.hits = self.snap.search(self.q)
        self.page = 0
        self.screen = "results"
        if self.title == "🔎 検索結果" and self.origin is None:
            self.user.last_query = self.q
            self.bot.store.dirty = True

    def open_favorites(self):
        self.mode = "favorites"
        self.screen = "results"
        self.page = 0
        snap = self.snap
        items = [snap.get(k) for k in sorted(self.user.favorites, key=lambda k: self.user.favorites[k]["added"],
                                                  reverse=True)]
        hits = [Hit(i, [o for o in snap.groups.get(i.dup, []) if o is not i] if i.dup and not i.removed else [])
                for i in items if i is not None]
        self.hits = hits if self.q.sort == "new" else sort_hits(hits, self.q, snap)

    def draw_results(self):
        snap, hits = self.snap, self.hits
        pages = max(1, math.ceil(len(hits) / PAGE_SIZE))
        self.page = min(self.page, pages - 1)
        start = self.page * PAGE_SIZE
        shown = hits[start:start + PAGE_SIZE]
        favs = self.user.favorites
        embeds = [T.result_embed(h, start + n + 1, snap, favs.get(h.key)) for n, h in enumerate(shown)]
        if self.mode == "favorites":
            if not hits:
                self.add_button("to_panel", "物件をさがす", "🔎", self.go("panel"), 0, BLUE)
                return T.FAV_EMPTY, []
            content = T.favorites_header(len(hits), sum(1 for h in hits if h.item.removed))
        else:
            content = T.results_header(self.title, len(hits), self.q, self.page, pages, self.notice)
            if not hits:
                content += "\n" + "\n".join(T.hints(self.q, snap, 0))
        if self.notice and self.mode == "favorites":
            content = f"{self.notice}\n\n{content}"
        if shown:
            self.add_select("detail", T.PH_DETAIL, [
                (*T.detail_option(h, start + n + 1), str(start + n), False) for n, h in enumerate(shown)
            ], self._open_detail, 0)
            self.add_select("sort", T.PH_SORT, [
                (label, key, None, key == self.q.sort) for key, label in T.SORTS.items()
            ], self._sort, 1)
        if pages > 1:
            self.add_button("prev", "前へ", "◀️", lambda: self._page(-1), 2, disabled=self.page == 0)
            self.add_button("next", "次へ", "▶️", lambda: self._page(1), 2, disabled=self.page >= pages - 1)
        if self.mode == "favorites":
            self.add_button("to_panel", "物件をさがす", "🔎", self.go("panel"), 2)
        else:
            self.add_button("to_panel", "条件を変える", "↩️", self.go("panel"), 2)
        if self.origin == "saved":
            self.add_button("to_saved", "保存した条件", "🔔", self.go("saved"), 2)
        return content, embeds

    def _page(self, step):
        self.page += step

    def _sort(self, values):
        self.set(sort=values[0])
        if self.mode == "favorites":
            self.open_favorites()
        else:
            self.hits = sort_hits(self.hits, self.q, self.snap)
            self.page = 0

    def _open_detail(self, values):
        self.pos = int(values[0])
        self.screen = "detail"

    def draw_detail(self):
        if not self.hits:
            self.screen = "results"
            return self.draw_results()
        self.pos = max(0, min(self.pos, len(self.hits) - 1))
        hit = self.hits[self.pos]
        fav = self.user.favorites.get(hit.key)
        self.add_button("star", "お気に入りから外す" if fav else "お気に入り", "⭐", self._star, 0,
                        GREY if fav else BLUE)
        self.add_button("prev_item", "前の物件", "◀️", lambda: self._move(-1), 0, disabled=self.pos == 0)
        self.add_button("next_item", "次の物件", "▶️", lambda: self._move(1), 0,
                        disabled=self.pos >= len(self.hits) - 1)
        self.add_button("to_list", "一覧に戻る", "↩️", self._back_to_list, 0)
        head = f"-# {self.pos + 1}/{len(self.hits)}件目"
        content = f"{self.notice}\n{head}" if self.notice else head
        return content, [T.detail_embed(hit, self.snap, fav)]

    def _move(self, step):
        self.pos += step

    def _back_to_list(self):
        self.page = self.pos // PAGE_SIZE
        self.screen = "results"

    def _star(self):
        hit = self.hits[self.pos]
        try:
            added = self.bot.store.toggle_favorite(self.user, hit.key, hit.item.price_lo, today(), self.newest_run())
            self.notice = (T.FAV_ADDED if self.user.notify_favorites else T.FAV_ADDED_QUIET) if added \
                else T.FAV_REMOVED
        except ValueError:
            self.notice = T.FAV_FULL.format(n=MAX_FAVORITES)

    # ---------- saved searches ----------

    def draw_saved(self):
        u, snap = self.user, self.snap
        if u.searches:
            self.add_select("saved", T.PH_SAVED, [
                (*T.saved_option(s, snap, n), s.id, s.id == self.picked) for n, s in enumerate(u.searches, 1)
            ], self._pick_saved, 0)
            self.add_button("run_saved", "この条件でさがす", "🔎", self._run_saved, 1, BLUE, disabled=not self.picked)
            self.add_button("delete_saved", "削除", "🗑️", self._delete_saved, 1, RED, disabled=not self.picked)
        self.add_button("new_search", "新しくさがす", "➕", self._new_search, 1, GREY if u.searches else BLUE)
        self.add_button("fav_notify", f"お気に入りの通知：{'オン' if u.notify_favorites else 'オフ'}", "⭐",
                        self._toggle_fav_notify, 2, GREEN if u.notify_favorites else GREY)
        content = T.saved_list(u, snap, self.picked) if u.searches else T.SAVED_EMPTY + (
            "\n\n" + T.DM_FAILED if u.dm_failed else "")
        return (f"{self.notice}\n\n{content}" if self.notice else content), []

    def _pick_saved(self, values):
        self.picked = values[0]

    def _saved(self):
        return next((s for s in self.user.searches if s.id == self.picked), None)

    def _run_saved(self):
        s = self._saved()
        if s:
            self.q, self.title, self.origin = s.query, "🔔 保存した条件", "saved"
            self.open_results()

    def _new_search(self):
        """To the search panel with the person's own last conditions (not a saved search's)."""
        self.q, self.title, self.origin = self.user.last_query, "🔎 検索結果", None
        self.screen = "panel"

    def _delete_saved(self):
        if self._saved():
            self.bot.store.delete_search(self.user, self.picked)
            self.picked = None
            self.notice = T.DELETED

    def _toggle_fav_notify(self):
        self.bot.store.set_notify_favorites(self.user, not self.user.notify_favorites, self.newest_run())


def _int(values):
    return None if values[0] == "none" else int(values[0])

