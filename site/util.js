// Small helpers every module uses: DOM lookups and escaping, never-throwing localStorage, LRU caches, the media
// queries the layout follows, frames, the toast, swipes, and the click-action registry (ACTIONS: a button's action
// by its data attribute, caught once on the document by main.js; each module registers its own).

export const $ = (id) => document.getElementById(id);
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
// SUUMO's free text, escaped: its m<sup>2</sup> comes as "m 2" (59.9m 2 → 59.9m²)
export const prose = (s) => esc(String(s ?? "").replace(/(\d)\s?[mｍ]\s[2２](?![\d.,])/g, "$1m²"));
export const icon = (name) => `<svg class="i"><use href="#i-${name}"/></svg>`;
// Caches are Maps in least recently used order: touch gives key's value (made when missing) and moves it last;
// recent does so and keeps at most max entries.
export function touch(map, key, make) {
  let v;
  if (map.has(key)) { v = map.get(key); map.delete(key); } else v = make();
  map.set(key, v);
  return v;
}
export function recent(map, key, max, make) {
  const v = touch(map, key, make);
  while (map.size > max) map.delete(map.keys().next().value);
  return v;
}

// A button's action by its data attribute (data-tab-close="all" -> ACTIONS.tabClose("all", button)), caught once on
// the document (runAction), so buttons drawn anywhere (cards, chips, popups, the listing) need no listener of their
// own; each part of the page registers its own. (The filter panel, the picker and settings have their own listeners.)
export const ACTIONS = {};
export function runAction(b) {
  for (const k in ACTIONS) if (b.dataset[k] != null) { ACTIONS[k](b.dataset[k], b); return true; }
  return false;
}

// localStorage, never throwing: blocked storage (its getter throws SecurityError), a full quota, a corrupt value
export const ls = {
  get: (key) => { try { return localStorage.getItem(key); } catch { return null; } },
  set: (key, v) => { try { localStorage.setItem(key, v); } catch { /* blocked or full: this browser forgets */ } },
};
export const stored = (key, fallback) => { try { return JSON.parse(ls.get(key)) ?? fallback; } catch { return fallback; } };
export const keep = (key, v) => ls.set(key, JSON.stringify(v));
export const DARK = matchMedia("(prefers-color-scheme: dark)");
export const COARSE = matchMedia("(pointer: coarse)");
export const WIDE = matchMedia("(min-width: 1280px)");  // filters, results and map side by side (style.css too)
// map and panel side by side (style.css too): wide screens, and phones on their side (too short for a sheet)
export const SPLIT = matchMedia("(min-width: 1024px), (orientation: landscape) and (max-height: 500px) and (min-width: 640px)");

// A sideways swipe on el (a primary pointer moved over 40 px; only one that starts where from(e) says): step(1) for
// the next (a swipe to the left), step(-1) for the previous. A pinch or a scroll (pointercancel) is no swipe.
export function onSwipe(el, step, from = () => true) {
  let x0 = null;
  el.addEventListener("pointerdown", (e) => { x0 = e.isPrimary && from(e) ? e.clientX : null; }, { passive: true });
  el.addEventListener("pointercancel", () => { x0 = null; });
  el.addEventListener("pointerup", (e) => {
    const dx = x0 == null ? 0 : e.clientX - x0;
    x0 = null;
    if (Math.abs(dx) > 40) step(dx < 0 ? 1 : -1);
  });
}

let toastTimer = null;
// action: [label, onclick] for a button in it (shown longer)
export function toast(text, action) {
  $("toast").textContent = text;
  if (action) {
    const b = Object.assign(document.createElement("button"), { className: "toast-btn", textContent: action[0] });
    b.addEventListener("click", () => { $("toast").classList.remove("on"); action[1](); });
    $("toast").append(b);
  }
  $("toast").classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.remove("on"), action ? 12000 : 2200);
}

// The next frame (or a moment, when the page isn't drawn: a hidden tab has no frames).
// (A hidden tab: at once, as its timers are slowed to one a second.)
export const frame = () => document.hidden ? Promise.resolve() : new Promise((ok) => {
  const late = setTimeout(ok, 100);
  requestAnimationFrame(() => setTimeout(() => { clearTimeout(late); ok(); }));
});
