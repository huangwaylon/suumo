// URL <-> state. The URL: conditions (KEYS), the map's place (m=lat,lng,zoom, replaced on every move; the map moves
// only on the refit triggers), an open listing (id=), the favorites' comparison (cmp=1), the prefectures (p=).

import { ls } from "./util.js";
import { t } from "./format.js";
import { isListing, st } from "./state.js";

const KEYS = { q: "text", t: "types", a: "areas", st: "stations", f: "features", w: "walk", pmin: "priceMin",
  pmax: "priceMax", plan: "plan", s: "sizeMin", lm: "landMin", g: "ageMax", quake: "post1981", fh: "freehold",
  nc: "noCondition", new: "newOnly", drop: "dropsOnly", since: "since", sort: "sort", so: "south", top: "top",
  up: "upper", nf: "nofee", rd: "ready", tw: "tower", pk: "parking", fee: "feesMax", nr: "near", nw: "nearWalk" };

// Thresholds can be any sensible number (a link, a typed search), not only the offered choices.
const RANGE = { priceMin: [0, 1e11], priceMax: [0, 1e11], plan: [3, 30], sizeMin: [1, 2000], landMin: [1, 10000],
  ageMax: [0, 100], walk: [1, 120], feesMax: [0, 200000], nearWalk: [1, 60] };
const within = (f, v) => v !== "" && Number.isFinite(+v) && RANGE[f] && +v >= RANGE[f][0] && +v <= RANGE[f][1];

// URL parameters -> a query (unknown values dropped)
export function queryFrom(p) {
  const out = rawQuery(p);
  out.types = out.types.filter((type) => st.db.index.types.includes(type));
  out.areas = out.areas.filter((a) => st.areaNames.has(a));
  out.stations = out.stations.filter((s) => st.db.index.stations.includes(s));
  const kinds = nearKinds();
  out.near = out.near.filter((k) => kinds.includes(k));  // (an index without them, or a mistyped link)
  return out;
}
// ... of any prefecture (saved searches); an older link's conditions in today's terms (Filter.upgrade)
export function rawQuery(p) {
  let out = Filter.emptyQuery();
  const base = Filter.emptyQuery();
  for (const [k, f] of Object.entries(KEYS)) {
    if (!p.has(k)) continue;
    const v = p.get(k), d = base[f];
    if (Array.isArray(d)) out[f] = v ? v.split(",") : [];
    else if (typeof d === "boolean") out[f] = v === "1";
    else if (f === "since") out[f] = /^\d{10}$/.test(v) ? +v : null;
    else if (d === null) out[f] = within(f, v) ? +v : null;
    else out[f] = v;
  }
  if (!Object.hasOwn(t("sorts"), out.sort)) out.sort = "new";
  out = Filter.upgrade?.(out) ?? out;
  return out;
}
// a query -> its URL parameters (the conditions only)
export function paramsFor(query) {
  const p = new URLSearchParams(), base = Filter.emptyQuery();
  for (const [k, f] of Object.entries(KEYS)) {
    const v = query[f];
    if (JSON.stringify(v) === JSON.stringify(base[f])) continue;
    p.set(k, Array.isArray(v) ? v.join(",") : typeof v === "boolean" ? "1" : v);
  }
  return p;
}

export function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  st.q = queryFrom(p);
  st.mode = p.get("fav") === "1" ? "favorites" : "search";
  return p;
}

// The page's URL: the conditions; with tab, the open listing (or the favorites' 比較); with at, where the map is.
export function hashFor(extra = {}, { tab: withTab = true, at = true } = {}) {
  const p = paramsFor(st.q);
  if (st.chosen.join() !== st.manifest.prefs[0][0]) p.set("p", prefParam(st.chosen));
  if (st.mode === "favorites") p.set("fav", "1");
  if (withTab && isListing()) p.set("id", st.tab);
  if (withTab && st.tab === "compare" && st.mode === "favorites") p.set("cmp", "1");  // (the open tabs: not in a link)
  if (at && st.mapAt && st.mode === "search") p.set("m", st.mapAt);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return "#" + p.toString();
}
export const saveLast = () => st.mode === "search" && ls.set("suumo.last", hashFor({}, { tab: false }));  // reopened later
// 周辺環境: the kinds the indexes know (school, super...) and the time, with the count each choice would give.
export const nearKinds = () => [...new Set(st.db.parts.flatMap((p) => p.index.near || []))];
export const prefParam = (list) => (list.length > 1 && list.length === st.manifest.prefs.length ? "all" : list.join(","));  // p=
