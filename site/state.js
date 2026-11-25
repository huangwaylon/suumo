// The page's shared state: what several modules read and change, as properties of one object (an imported binding
// can't be reassigned), and the collections they share. Listings are handles (numbers, see filter.js), lists of
// them Int32Arrays: Filter.item(h) is the object shown, Filter.get.<field>(h) one field of it.

import { stored } from "./util.js";
import { item } from "./format.js";

export const st = {
  db: null,                // the chosen prefectures' listings, searched as one
  manifest: null,          // data/index.json: the prefectures there are, the saved list, when updated
  chosen: [],              // the prefectures searched (URL p=, default: the first, Tokyo)
  q: Filter.emptyQuery(),  // the conditions
  mode: "search",          // search | favorites (the shared saved list)
  hits: new Int32Array(0), // the results listed (in the map's view)
  shown: 0,                // ... how many of them are drawn
  mapHits: null,           // the results, on the map (hits: those in its view)
  byDay: false,            // the list has a heading per day (what's new, newest first)
  facets: null,            // the filter panel's counts (Filter.facets) ...
  facetsFor: null,         // ... and the conditions they're for
  listStale: false,        // the list is drawn when it's in sight again (the phone filter sheet, another tab)
  sayTimer: null,          // the count is said aloud shortly after the conditions change
  listSig: null,           // what the drawn cards show (render skips redrawing the same)
  areaNames: new Map(),    // area code -> its name
  lastVisit: null,         // YYYYMMDDHH of the data seen on the previous visit (for "since your last visit")
  dataHour: null,          // YYYYMMDDHH of the data shown now
  // The saved list is shared and kept on GitHub (saved.json, see suumo/saved.py): the heart opens a pre-filled
  // issue, a workflow applies it and rebuilds the site. Until then the request shows here as pending.
  pending: stored("suumo.pending", {}),  // key -> [save: true | false, time requested, prefecture]
  // the map's place (the list is what's in its view)
  view: null,              // [south, west, north, east]: what the map shows (null: no map; everything is listed)
  waiting: true,           // the first list waits for the map's view (at most MAP_WAIT ms)
  mapAt: null,             // the view in the URL: "lat,lng,zoom" of its centre
  fitNext: null,           // where the map goes next: "results", {m: "lat,lng,zoom"}, {b: [s, w, n, e]}, {id: key}
  lastPlaces: [],          // the areas and stations drawn (naming another one fits the map to the results)
  // the panel's tabs
  tab: "list",             // "list", a listing key or "compare"
  openedFrom: null,        // the card whose listing is open (focus returns to it)
  sheetBefore: null,       // the sheet's state before a listing took all of it
  cardW: 360,              // the width the cards' photos are asked for (listResults)
  rank: null,              // listing -> its place in the results (a kept marker layer can be in another order)
  prefsUrl: null,          // the URL the picker has chosen (kept when it closes, also by the back button)
};
st.mapHits = st.hits;

export const loaded = new Map();     // prefecture slug -> its loaded index (Filter.load)
// A loaded listing by its key ("used_condo:123"; favorites can be in prefectures not chosen): its handle, or -1
export const byKey = (key) => { for (const d of loaded.values()) { const h = Filter.find(d, key); if (h >= 0) return h; } return -1; };
export const listing = (key) => item(byKey(key));  // ... as an object, or null
export const favs = new Set();      // the saved list as it shows here: the site's list with the pending requests applied
// the panel's tabs: the list, the listings opened from it, 比較
export const MAX_TABS = 12;
export const tabs = [];          // listing keys, in the order opened
export const listY = {};         // the list's scroll position per mode (search, favorites)
export const isListing = (k = st.tab) => k !== "list" && k !== "compare";
