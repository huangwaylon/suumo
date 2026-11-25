// Settings (language, theme, USD) and strings: t(key, ...args) from i18n.js in the chosen language; numbers,
// prices (yen, ≈ USD at the day's rate), sizes, dates; a listing's parts as text (station, age, photo URLs, links).

import { esc, keep, stored } from "./util.js";
import { st } from "./state.js";
import { hashFor } from "./url.js";

export const settings = { lang: "ja", theme: "auto", ...stored("suumo.settings", {}) };
export const saveSettings = () => keep("suumo.settings", settings);
if (!I18N[settings.lang]) settings.lang = "ja";
export let S = I18N[settings.lang];
export const useLanguage = () => { S = I18N[settings.lang]; };  // (settings.lang changed)
const lookup = (strings, key) => key.split(".").reduce((o, k) => o?.[k], strings);
export const t = (key, ...args) => { const v = lookup(S, key) ?? lookup(I18N.ja, key); return typeof v === "function" ? v(...args) : v; };
// fixed SUUMO values (land rights, zoning, utilities...) in English: whole, else part by part ("商業、１種住居")
export const value = (v) => {
  if (v == null || !S.yearMonth) return v;  // Japanese: as listed
  v = v.replace(/[（(]納戸[)）]/g, S.values["（納戸）"]);  // 6LDK+2S（納戸）
  const month = /^(\d{4})年(\d{1,2})月(上旬|中旬|下旬)?(予定)?$/.exec(v);
  if (month) return S.yearMonth(...month.slice(1));
  return S.values[v] ?? v.split(/([、／/])/).map((p) => S.values[p.trim()] ?? (p === "、" || p === "／" ? ", " : p)).join("");
};
export const place = (name) => S.places?.[name] ?? name;
export const stationName = (s) => (S.lang === "en" && st.db?.stationNames.get(s)?.[1]) || s;  // English when known
export const feature = (f) => S.features?.[f] ?? f.normalize("NFKC");
export const PLANS = [[3, "1K"], [5, "1LDK"], [7, "2DK"], [8, "2LDK"], [10, "3DK"], [11, "3LDK"], [14, "4LDK"]];
export const PAGE = 30, { FLAGS, TSUBO, get, item } = Filter;
const IMG = "https://img01.suumo.com/jj/resizeImage?src=";

const formats = {};
export const num = (n) => (formats[S.lang] ||= new Intl.NumberFormat(S.lang === "en" ? "en-US" : "ja-JP")).format(n);
export function money(yen, html = false) {
  if (yen == null) return t("undecided");
  if (S.lang === "en") return yen >= 1e9 ? `¥${+(yen / 1e9).toFixed(2)}B` : `¥${+(yen / 1e6).toFixed(1)}M`;
  const m = Math.round(yen / 1e4), oku = Math.floor(m / 1e4), rest = m % 1e4;
  const unit = (s) => (html ? `<small>${s}</small>` : s);
  const whole = (s) => (html ? `<span class="nw">${s}</span>` : s);  // (one price never breaks across lines)
  if (oku) return whole(`${oku}${unit("億")}${rest ? num(rest) + unit("万円") : unit("円")}`);
  return whole(num(m) + unit("万円"));
}
export const price = (it, html) => money(it.price, html) +
  (it.priceHi && it.priceHi !== it.price ? `${S.lang === "en" ? "–" : "〜"}${money(it.priceHi, html)}` : "");
// the price in dollars too, at the day's rate (manifest.fx, the ECB's); yen is the source of truth
function dollars(yen) {
  const d = yen / st.manifest?.fx?.jpy_per_usd;
  return !(d > 0) ? "" : d >= 1e6 ? `$${+(d / 1e6).toFixed(2)}M` : `$${Math.round(d / 1e3)}K`;
}
export const showUsd = () => (settings.usd ? settings.usd === "on" : S.lang === "en");  // default: in English only
export function usd(it) {
  if (!showUsd()) return "";
  const lo = dollars(it.price), hi = it.priceHi && it.priceHi !== it.price ? dollars(it.priceHi) : "";
  return lo && `<small class="usd" title="${esc(t("usdRate", st.manifest.fx.jpy_per_usd, day(st.manifest.fx.date)))}">${esc(t("usd", hi ? `${lo}–${hi}` : lo))}</small>`;
}
export const m2 = (v) => (v ? `${+v.toFixed(2)}㎡` : "");
export const tsubo = (v) => (v ? t("tsubo", (v / TSUBO).toFixed(1)) : "");
export const isCondo = (type) => type === "used_condo" || type === "new_condo";
export const day = (d) => { const s = String(d ?? "").replaceAll("-", ""); return s.length === 8 ? t("date", +s.slice(0, 4), +s.slice(4, 6), +s.slice(6)) : ""; };
export function age(it) {
  if (it.type === "new_condo" || it.type === "new_house") return t("newBuild");
  return it.age == null ? "" : it.age === 0 ? t("ageUnder1") : t("ageYears", it.age);
}
export function sizes(it) {  // on cards: whole ㎡, land and building on one line
  const r = (v) => `${Math.round(v)}㎡`;
  if (isCondo(it.type)) return [it.size && m2(it.size)];
  return [[it.land && `${t("land")} ${r(it.land)}`, it.size && `${t("building")} ${r(it.size)}`].filter(Boolean).join(" · ")];
}
export function station(it) {
  const closest = (chosen) => it.stations.reduce((best, [s, w]) =>
    (w != null && (!chosen || st.q.stations.includes(s)) && (!best || w < best[1]) ? [s, w] : best), null);
  const chosen = st.mode === "search" && st.q.stations.length;
  const best = closest(chosen);  // a chosen station when there are any: that's why it's listed
  if (best) return t("walk", stationName(best[0]), best[1]);
  const [s, , bus, car] = (chosen && it.stations.find(([n]) => st.q.stations.includes(n))) || it.stations[0] || [];
  if (!s) return it.stop ? t("stopWalkTo", it.stop[0], it.stop[1]) : "";
  return car ? t("car", stationName(s), car) : t("bus", stationName(s), bus);
}
// A link to a listing (a click opens its tab, see the click actions; with a modifier key, a new browser tab)
export const listingLink = (it, inner, cls = "") => `<a${cls ? ` class="${cls}"` : ""} href="${esc(hashFor({ id: it.key }))}" data-key="${esc(it.key)}">${inner}</a>`;
// A listing's photo at w×h (lazy unless eager; removed when it fails, unless keep)
export const photoTag = (it, w, h, { eager = false, keep = false } = {}) =>
  `<img src="${esc(image(it, w, h))}" alt=""${eager ? "" : ' loading="lazy"'} decoding="async"${keep ? "" : ' onerror="this.remove()"'}>`;
export const cardPhoto = (it) => image(it, st.cardW, st.cardW * 3 / 4);
function image(it, w, h) {
  if (it.image == null || it.image === "") return "";
  const p = Filter.photo(it.image, it.id);  // (an item's photo number: 0 is one)
  if (p.startsWith("http")) return p.replace(/w=\d+&h=\d+/, `w=${w}&h=${h}`);
  return `${IMG}${encodeURIComponent(p)}&w=${w}&h=${h}`;
}
export const typeName = (type) => t("types")[type];
export const safeUrl = (u) => (/^https:\/\//.test(u || "") ? u : "#");
export const areaName = (code) => place(st.areaNames.get(code) || code);
// a plan rank (rooms × 3 + K 0 / DK 1 / LDK 2, see catalog.plan_rank) as written: 11 -> "3LDK"
export const planLabel = (v) => PLANS.find(([p]) => p === v)?.[1] ?? `${Math.floor(v / 3)}${["K", "DK", "LDK"][v % 3]}`;
export const ageLabel = (v) => t("ages")[v] ?? t("ageWithin", v);
// 2026100619 (JST hour) -> "3時間前" within a day, else "10/6"
const atHour = (h) => Date.UTC(Math.floor(h / 1e6), Math.floor(h / 1e4) % 100 - 1, Math.floor(h / 100) % 100, h % 100 - 9);
export function ago(h) {
  if (!h) return "";
  const hours = Math.round((Date.now() - atHour(h)) / 3600e3);
  return hours < 24 ? t("hoursAgo", Math.max(hours, 0)) : `${Math.floor(h / 1e4) % 100}/${Math.floor(h / 100) % 100}`;
}

// 20261006 -> "10月6日（火）" / "Tue, Oct 6"
export function weekday(d) {
  const date = new Date(Math.floor(d / 1e4), Math.floor(d / 100) % 100 - 1, d % 100);
  return date.toLocaleDateString(S.lang === "en" ? "en-US" : "ja-JP", { month: "short", day: "numeric", weekday: "short" });
}

// the walk time never breaks across lines ("徒歩5分", "5 min walk")
export const nowrapWalk = (s) => esc(s).replace(/(徒歩\d+分|停歩\d+分|\d+ min walk)/, '<span class="nw">$1</span>');
export const fees = (it) => (isCondo(it.type) && it.fees != null ? t("feesCard", it.fees) : "");
// a condo's monthly costs: the index's fees, else from the record (management + repair fund (+ other monthly
// costs), unknown unless both are stated; catalog.Item's rule)
export const monthlyFees = (it, r) => it?.fees ?? (r.mgmt_fee && r.repair_fee ? r.mgmt_fee + r.repair_fee + (r.other_monthly || 0) : null);
