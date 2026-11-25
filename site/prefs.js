// Prefectures: the button before the count opens a picker (a modal sheet; a popover on desktop). Their indexes
// (data/p/<pref>.json) are loaded here, one a frame; past maxPrefs (phones 10) the oldest are released. The service
// worker says when a newer index is there; it can be loaded in place.

import { $, ACTIONS, COARSE, WIDE, esc, frame, icon, toast } from "./util.js";
import { num, place, t } from "./format.js";
import { loaded, st } from "./state.js";
import { prefParam, readHash } from "./url.js";
import { dataChanged, drawn, modeChanged, render, resultsKey } from "./results.js";
import { NL } from "./searchbox.js";
import { placeMap } from "./map.js";
import { addTabs, compareFavs, showTab } from "./panel.js";
import { closeOverlay, openOverlay } from "./overlays.js";

const REGIONS = {  // the usual 8 regions, by the prefecture names the manifest uses
  hokkaido: ["北海道"], tohoku: ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
  kanto: ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
  chubu: ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県"],
  kinki: ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
  chugoku: ["鳥取県", "島根県", "岡山県", "広島県", "山口県"], shikoku: ["徳島県", "香川県", "愛媛県", "高知県"],
  kyushu: ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"],
};
// Every chosen prefecture is held in memory (all of Japan: ~11 MB to download, ~120 MB loaded), so phones
// and small-memory devices take up to 10; others may choose all of Japan.
const BIG = matchMedia("(min-width: 700px)").matches && (navigator.deviceMemory ?? 8) >= 4;
const maxPrefs = () => (BIG ? st.manifest.prefs.length : 10);
const allPrefs = () => st.manifest.prefs.map(([slug]) => slug);
// the prefectures' names, and whether they're all of them (said as 全国): countsScope, loadingPrefs
export const prefsLabel = (list) => [list.map(prefName).join(t("sep")), list.length > 1 && list.length === st.manifest.prefs.length];
export const prefName = (slug) => place(st.manifest.prefs.find(([s]) => s === slug)?.[1] ?? slug);

// The button ("東京都 ▾", "東京都 ほか1 ▾") and, with several prefectures, one removable chip each.
export function renderPrefBar() {
  const off = st.mode !== "search" || st.manifest.prefs.length < 2;
  $("pref").hidden = off;
  const everywhere = st.chosen.length > 1 && st.chosen.length === st.manifest.prefs.length;
  $("pref").innerHTML = `<span>${esc(everywhere ? t("prefAll") : t("prefButton", prefName(st.chosen[0]), st.chosen.length - 1))}</span>${icon("down")}`;
  $("pref-chips").innerHTML = off || st.chosen.length < 2 || everywhere ? "" : st.chosen.map((slug) =>
    `<span class="chip on"><button class="chip-label" data-pref-open="1">${esc(prefName(slug))}</button><button class="chip-x" data-pref-remove="${slug}" aria-label="${esc(t("remove", prefName(slug)))}">${icon("x")}</button></span>`).join("");
}

function renderPrefs() {
  const typed = Filter.normalize($("prefs-q").value.trim());
  const match = ([slug, name]) => !typed || [name, I18N.en.places[name], slug].some((n) => n && Filter.normalize(n).includes(typed));
  const groups = Object.keys(REGIONS).map((r) => [r, st.manifest.prefs.filter(([, name]) => REGIONS[r].includes(name))]);
  const other = st.manifest.prefs.filter(([, name]) => !Object.values(REGIONS).some((names) => names.includes(name)));
  if (other.length) groups.push(["other", other]);
  const want = prefsFromHash(), full = want.length >= maxPrefs();  // (the URL's: some may still be loading)
  $("prefs-list").innerHTML = groups.map(([r, prefs]) => {
    const rows = prefs.filter(match).map(([slug, name, n]) => {
      const on = want.includes(slug), only = on && want.length === 1;
      return `<li class="pref-row${on ? " on" : ""}"><button class="pref-go" data-pref-go="${slug}"${on ? ' aria-current="true"' : ""}>${esc(place(name))}<span class="n">${num(n)}</span></button>
        <button class="pref-add" data-pref-add="${slug}" aria-pressed="${on}"${only || (!on && full) ? " disabled" : ""} aria-label="${esc(t(on ? "remove" : "prefAddTo", place(name)))}">${esc(t(on ? "prefAdded" : "prefAdd"))}</button></li>`;
    }).join("");
    return rows && `<section class="pref-group"><h3>${esc(t("regions")[r])}</h3><ul>${rows}</ul></section>`;
  }).join("") || `<p class="meta">${esc(t("prefNone"))}</p>`;
  if (BIG && !typed) {  // all of Japan, with what it costs to load (~30 bytes per home, compressed)
    const n = st.manifest.prefs.reduce((sum, [, , count]) => sum + count, 0), all = want.length === st.manifest.prefs.length;
    $("prefs-list").insertAdjacentHTML("afterbegin", `<button class="btn pref-all" data-pref-all="1" aria-pressed="${all}"${all ? " disabled" : ""}>${
      esc(t("prefAllPick", num(n), Math.max(1, Math.round(n * 30 / 1e6))))}</button>`);
  }
  $("prefs-note").textContent = full && !BIG ? t("prefMax", maxPrefs()) : "";
}

// Back closes the picker and keeps what it chose (as the filter sheet does). A popover on desktop, else modal.
const PREFS = {
  entry: "prefs", el: () => $("prefs"), state: () => ({ prefs: true }), modal: () => !WIDE.matches,
  show(open) { $("prefs").hidden = !open; $("pref").setAttribute("aria-expanded", open); },
  back() {  // keeping what it chose
    history.replaceState(history.state, "", st.prefsUrl);
    closeOverlay(PREFS, true);
    route();
  },
};
function openPrefs(open) {
  if (!open) { closeOverlay(PREFS); return; }
  if (!openOverlay(PREFS)) return;
  st.prefsUrl = location.href;
  $("prefs-q").value = "";
  renderPrefs();
  const box = $("prefs");
  if (WIDE.matches) {  // a popover under the button
    const r = $("pref").getBoundingClientRect(), panel = box.firstElementChild;
    panel.style.top = `${r.bottom + 6}px`;
    panel.style.left = `${Math.max(8, Math.min(r.left, innerWidth - panel.offsetWidth - 8))}px`;
  } else box.firstElementChild.style.top = box.firstElementChild.style.left = "";
  $("prefs-list").querySelector('[aria-current="true"]')?.scrollIntoView({ block: "center" });
  (WIDE.matches || !COARSE.matches ? $("prefs-q") : $("prefs-close")).focus();
}

function setPrefs(next, now = true) {  // the URL says which; route loads and shows them
  const p = new URLSearchParams(location.hash.slice(1));
  if (next.join() === st.manifest.prefs[0][0]) p.delete("p");
  else p.set("p", prefParam(next));
  history.replaceState(history.state, "", "#" + p.toString());
  st.prefsUrl = location.href;
  if (now) route();
}

// the prefectures' chips
Object.assign(ACTIONS, {
  prefOpen: () => openPrefs(true),
  prefRemove: (slug) => setPrefs(prefsFromHash().filter((s) => s !== slug)),
});

export function wirePrefs() {
  $("pref").addEventListener("click", () => openPrefs(true));
  $("prefs-close").addEventListener("click", () => openPrefs(false));
  $("prefs").addEventListener("click", onPrefsClick);
  $("prefs-q").addEventListener("input", renderPrefs);
  $("prefs-q").addEventListener("keydown", (e) => { if (e.key === "Enter") $("prefs-list").querySelector(".pref-go")?.click(); });
}

function onPrefsClick(e) {
  if (e.target === $("prefs")) return openPrefs(false);  // outside the panel
  const go = e.target.closest("[data-pref-go]"), add = e.target.closest("[data-pref-add]");
  if (go) { setPrefs([go.dataset.prefGo], !history.state?.prefs); openPrefs(false); }  // (closing routes)
  else if (e.target.closest("[data-pref-all]")) { setPrefs(allPrefs(), !history.state?.prefs); openPrefs(false); }
  else if (add) {
    const s = add.dataset.prefAdd, want = prefsFromHash();
    setPrefs(want.includes(s) ? want.filter((x) => x !== s) : [...want, s].slice(0, maxPrefs()));
    renderPrefs();
    $("prefs-list").querySelector(`[data-pref-add="${s}"]`)?.focus();
  }
}

// The prefectures the URL asks for (p=tokyo,kanagawa), else the first one.
export function prefsFromHash() {
  return prefsFromParam(new URLSearchParams(location.hash.slice(1)).get("p") || "");
}
// a saved search made in other prefectures than those shown
export const savedAway = (s) => !!s.p && prefsFromParam(s.p).join() !== st.chosen.join();
export function prefsFromParam(raw) {
  const p = raw === "all" ? allPrefs() : raw.split(",");
  const known = p.filter((s) => st.manifest.prefs.some(([slug]) => slug === s)).slice(0, maxPrefs());
  return known.length ? known : [st.manifest.prefs[0][0]];
}

const fetching = new Map();  // slug -> its index being fetched and loaded (asked again meanwhile: the same one)
let turn = Promise.resolve();
export async function loadPrefs(slugs) {
  const todo = slugs.filter((s) => !loaded.has(s));
  let done = 0;
  await Promise.all(todo.map((slug) => {
    if (!fetching.has(slug)) fetching.set(slug, (async () => {
      const res = await fetch(`data/p/${slug}.json`);
      if (!res.ok) throw new Error(`data/p/${slug}.json: ${res.status}`);
      const index = await res.json();
      await (turn = turn.then(frame));  // one prefecture a frame (all of Japan: the page keeps drawing meanwhile)
      loaded.set(slug, Filter.load(index, { places: I18N.en.places }));  // ("shibuya" finds 渋谷区)
    })().finally(() => fetching.delete(slug)));
    return fetching.get(slug).then(() => {
      if (todo.length > 1) $("count").textContent = t("loadingSome", ++done, todo.length);  // all of Japan: 47
    });
  })).finally(() => { $("loading").hidden = true; });
  dataChanged();
  offerNewer();
  await frame();  // the count drawn before the first search
}
// While prefectures' indexes load (Tokyo's: seconds on a phone): said in the list's head and above the list, and
// announced (role=status); hidden once they're loaded (loadPrefs)
export function showLoading(slugs) {
  const text = t("loadingPrefs", ...prefsLabel(slugs)), note = $("loading");
  $("count").textContent = text;
  note.hidden = false;
  note.textContent = text;
}

// The service worker (sw.js) shows a prefecture's index from this browser's cache, then fetches it again; when the
// new copy differs it says so here, and the newer data can be loaded in place.
export function watchUpdates() {
  if (!("serviceWorker" in navigator)) return;
  navigator.serviceWorker.register("sw.js").catch(() => {});
  navigator.serviceWorker.addEventListener("message", (e) => {
    const slug = e.data?.type === "updated" && /\/data\/p\/([^/]+)\.json$/.exec(new URL(e.data.url).pathname)?.[1];
    if (slug) { newer.add(slug); offerNewer(); }
  });
}

const newer = new Set();  // prefectures with a newer index than the one shown (it can come before ours is loaded)
function offerNewer() {
  const ready = [...newer].filter((s) => loaded.has(s));
  if (!ready.length) return;
  for (const s of ready) newer.delete(s);
  toast(t("dataUpdated"), [t("refresh"), () => reloadPrefs(ready).catch(() => {})]);
}

// Newer data for these prefectures, loaded in place (all at once: db is rebuilt once, when every one is there).
async function reloadPrefs(slugs) {
  if (!Filter.canLoad(slugs.length)) return location.reload();  // (a page refreshed in place very many times)
  const old = new Map(slugs.filter((s) => loaded.has(s)).map((s) => [s, loaded.get(s)]));
  for (const s of slugs) loaded.delete(s);
  try {
    await loadPrefs(slugs);
  } catch (err) {  // (offline again: keep showing what was there, all of it)
    for (const [s, part] of old) {
      if (loaded.has(s)) Filter.release(loaded.get(s));
      loaded.set(s, part);
    }
    throw err;
  }
  choosePrefs(st.chosen);
  for (const part of old.values()) Filter.release(part);  // (still drawn from them: nothing until redrawn)
  st.listSig = null;  // the same listings may have changed
  render();
}

export function choosePrefs(slugs) {
  st.chosen = slugs;
  // the loaded indexes are kept most recently chosen last; past maxPrefs (phones: 10), the oldest are released
  for (const s of slugs) { const part = loaded.get(s); loaded.delete(s); loaded.set(s, part); }
  for (const [s, part] of loaded) {
    if (loaded.size <= maxPrefs()) break;
    if (!slugs.includes(s)) { Filter.release(part); loaded.delete(s); }
  }
  st.db = Filter.combine(slugs.map((s) => loaded.get(s)));
  const d = st.db;  // the search box's dictionaries built while idle (else the first word typed waits ~80 ms on a phone)
  (window.requestIdleCallback || setTimeout)(() => { if (st.db === d) Filter.parse("", d, NL); });
  st.areaNames = new Map(st.db.index.areas.map(([code, name]) => [code, name]));
  dataChanged();
  st.fitNext = "results";
  $("filters-body").innerHTML = "";  // the area lists change
}

export function route() {
  const want = prefsFromHash(), missing = want.filter((s) => !loaded.has(s));
  if (missing.length) {  // load them, then come back
    showLoading(missing);
    loadPrefs(missing).then(route, () => {  // still the prefectures shown: the URL says so again
      toast(t("offline"));
      if (st.chosen.length) setPrefs(st.chosen, false);
      render();
    });
    return;
  }
  if (want.join() !== st.chosen.join()) choosePrefs(want);
  const before = st.mode, p = readHash(), key = p.get("id");
  modeChanged(before);
  if (resultsKey() !== drawn.search) render(); else placeMap();  // (the same results: the map goes where it says)
  if (key) { addTabs([key]); showTab(key); }  // a listing: its tab (opened when it isn't, as from a link)
  else if (p.get("cmp") === "1" && st.mode === "favorites") compareFavs(false);
  else if (st.tab !== "list") showTab("list");
}
