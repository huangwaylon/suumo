// Overlays: the photo viewer, the filter sheet (phones), the prefecture picker, settings. Each is a history entry
// (back closes the top one), closed by Escape too; Tab stays inside it; while it's modal the page behind is inert;
// closed, the focus goes back where it was (or where its `closed` says).

import { $, SPLIT } from "./util.js";
import { FILTERS } from "./filters-panel.js";

// Each is a history entry (back closes the top one), closed by Escape too; Tab stays inside it; while it's modal
// the page behind is inert; closed, the focus goes back where it was (or where its `closed` says). An overlay:
// {entry: its history state's key, el(), state(current state): its entry's, modal(), show(open), and optionally
// back() (what back does, else it closes), closed(focus) (after closing), keys(e) (true: the key is handled)}.
const overlays = [];  // the open ones, the top last
export const topOverlay = () => overlays.at(-1) ?? null;
export const isOpen = (o) => overlays.includes(o);
export function openOverlay(o) {  // false when it's open already
  if (isOpen(o)) return false;
  o.focus = document.activeElement;
  overlays.push(o);
  history.pushState(o.state(history.state), "", location.href);
  o.show(true);
  pageInert();
  return true;
}
// Closing goes back in history while its entry is the current one (popstate closes it: popped), else at once.
export function closeOverlay(o, popped = false) {
  if (!isOpen(o)) return;
  if (!popped && history.state?.[o.entry]) { history.back(); return; }
  dropOverlay(o);
  if (o.closed) o.closed(o.focus); else o.focus?.focus();
}
export function dropOverlay(o) {  // (its history entry and the focus left as they are)
  overlays.splice(overlays.indexOf(o), 1);
  o.show(false);
  pageInert();
}
// What a modal overlay leaves out of reach: the filter sheet the header, the panel and the map (it's in the
// layout); the others the header and the layout. A full bottom sheet hides the map.
export function pageInert() {
  const sheet = isOpen(FILTERS), dialog = overlays.some((o) => o !== FILTERS && o.modal());
  document.querySelector(".bar").inert = sheet || dialog;
  document.querySelector(".layout").inert = dialog;
  $("panel").inert = sheet;
  $("map").inert = sheet || (!SPLIT.matches && $("panel").dataset.sheet === "full");
}

// Keep Tab inside an open dialog.
export function trapFocus(e, root) {
  const f = [...root.querySelectorAll("a[href], button, select, input")].filter((el) => !el.disabled && el.offsetParent);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}
