// Settings: language and theme (applied to the page), USD prices; the settings overlay.

import { $, DARK, esc } from "./util.js";
import { S, day, saveSettings, settings, showUsd, t, useLanguage } from "./format.js";
import { st } from "./state.js";
import { render } from "./results.js";
import { sheetSemantics } from "./filters-panel.js";
import { credits } from "./map.js";
import { showRail } from "./rail.js";
import { rerenderTab } from "./panel.js";
import { closeOverlay, openOverlay } from "./overlays.js";

export function applyTheme() {
  const dark = settings.theme === "dark" || (settings.theme === "auto" && DARK.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue("--surface");
  showRail();  // the lines' halos follow the theme
}

export function applyLanguage() {
  useLanguage();
  document.documentElement.lang = S.lang;
  document.title = t("title");
  for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
  for (const el of document.querySelectorAll("[data-t-label]")) el.setAttribute("aria-label", t(el.dataset.tLabel));
  for (const el of document.querySelectorAll("[data-t-placeholder]")) el.placeholder = t(el.dataset.tPlaceholder);
  $("sort").innerHTML = Object.entries(t("sorts")).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("filters-body").innerHTML = "";  // rebuilt in the new language
  $("sort").value = st.q.sort;
  sheetSemantics();  // its labels depend on the rail's state
  credits();
}

export function wireSettings() {
  $("settings-open").addEventListener("click", () => openSettings(true));
  $("settings-close").addEventListener("click", () => openSettings(false));
  $("settings").addEventListener("click", (e) => {
    if (e.target === $("settings")) return openSettings(false);
    const b = e.target.closest("[data-setting]");
    if (!b) return;
    settings[b.dataset.setting] = b.dataset.value;
    saveSettings();
    if (b.dataset.setting === "theme") applyTheme();
    else if (b.dataset.setting === "usd") { render(); rerenderTab(); }
    else { applyLanguage(); render(); rerenderTab(); }
    renderSettings();
    $("settings-body").querySelector(`[data-setting="${b.dataset.setting}"][data-value="${b.dataset.value}"]`)?.focus();
  });
  DARK.addEventListener("change", () => { if (settings.theme === "auto") applyTheme(); });
}

function renderSettings() {
  const group = (name, options, now = settings[name]) => `<div class="seg" role="group" aria-labelledby="set-${name}">${Object.entries(options).map(([k, label]) =>
    `<button class="seg-btn" data-setting="${name}" data-value="${k}" aria-pressed="${now === k}">${esc(label)}</button>`).join("")}</div>`;
  $("settings-body").innerHTML = `<section class="sec"><h3 id="set-lang">${esc(t("language"))}</h3>${group("lang", { ja: "日本語", en: "English" })}</section>
    <section class="sec"><h3 id="set-theme">${esc(t("theme"))}</h3>${group("theme", t("themes"))}</section>
    ${st.manifest?.fx ? `<section class="sec"><h3 id="set-usd">${esc(t("usdSetting"))}</h3>${group("usd", t("onOff"), showUsd() ? "on" : "off")}</section>` : ""}
    ${st.manifest?.updated ? `<p class="meta">${esc(t("updated", day(st.manifest.updated.slice(0, 10))))}</p>` : ""}`;
}

const SETTINGS = { entry: "settings", el: () => $("settings"), state: () => ({ settings: true }), modal: () => true,
  show(open) { $("settings").hidden = !open; } };
function openSettings(open) {
  if (!open) { closeOverlay(SETTINGS); return; }
  if (!openOverlay(SETTINGS)) return;
  renderSettings();
  $("settings-close").focus();
}
