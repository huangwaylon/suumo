// Search rules for the site: load the index, match, sort, count choices.
// Loaded by the page and, in tests, by Node (tests/test_site.py runs these rules on built indexes).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Filter = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const emptyQuery = () => ({
    text: "", types: [], areas: [], stations: [], walk: null, priceMin: null, priceMax: null, plan: null,
    sizeMin: null, landMin: null, ageMax: null, post1981: false, features: [], freehold: false,
    noCondition: false, newOnly: false, dropsOnly: false, since: null, sort: "new",
  });

  // NFKC (full-width -> half-width), lower case, hiragana -> katakana, no accents: "ぱーく", "ﾊﾟｰｸ" and "パーク"
  // all match, and so do "jiyugaoka" and "Jiyūgaoka".
  const normalize = (s) => (s || "").normalize("NFKC").toLowerCase()
    .replace(/[ぁ-ゖ]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0x60))
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "").normalize("NFC");
  // A station name as typed: "Futako-Tamagawa", "futakotamagawa" and "ふたこたまがわ" are the same.
  const stationKey = (s) => normalize(s).replace(/[\s\-・]/g, "");

  // A listing's tags: a bit string, 6 bits per character, bit k = index.features[k] (see site._mask).
  const B64 = Object.fromEntries([..."ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"].map((ch, v) => [ch, v]));
  function tags(mask, names) {
    const out = new Set();
    for (let k = 0; k < mask.length; k++) {
      const v = B64[mask[k]];
      for (let b = 0; b < 6; b++) if (v & (1 << b)) out.add(names[k * 6 + b]);
    }
    return out;
  }

  // A listing's photo: a number is the usual path, shortened by the builder (see site._image).
  const photo = (v, id) => (typeof v === "number" ? `030/N010000/${id.slice(-3)}/${String(v).padStart(4, "0")}.jpg` : v);

  function load(index) {
    const c = index.columns, n = c.id.length, items = new Array(n);
    let id = 0;
    for (let i = 0; i < n; i++) {
      id += c.id[i];
      const type = index.types[c.type[i]], area = index.areas[c.area[i]], town = index.towns[c.town[i]];
      const st = c.stations[i], stations = [];
      for (let j = 0; j < st.length; j++) {  // [name, walk minutes, bus minutes, car km] (nulls when not)
        const m = c.access[i][j];
        stations.push([index.stations[st[j]], m >= 0 && m < 1000 ? m : null, m < 0 ? -m : null, m >= 1000 ? (m - 1000) / 10 : null]);
      }
      const it = {
        id: String(id), idn: id, type, key: type + ":" + id, area: area[0], areaName: area[1],
        price: c.price[i], priceHi: c.priceMax[i] ?? c.price[i], plan: c.plan[i], size: c.size[i],
        land: c.land[i], age: c.age[i] == null ? null : Math.floor(c.age[i] / 12), ageMonths: c.age[i],
        stations, features: tags(c.features[i], index.features), flags: c.flags[i],
        others: c.others[i], newAt: c.newAt[i], newDate: c.newAt[i] ? Math.floor(c.newAt[i] / 100) : null,
        dropAt: c.drop[i]?.[0] ?? null, prevPrice: c.drop[i]?.[1] ?? null, firstSeen: c.firstSeen[i], unit: c.unit[i],
        town: town ? town[0].slice(area[2].length) : null, lat: town ? town[1] : null,
        lng: town ? town[2] : null, name: index.names[c.name[i]] ?? "", layout: index.layouts[c.layout[i]] ?? "", image: photo(c.image[i], String(id)),
      };
      items[i] = it;
    }
    index.columns = null;  // the items hold everything now; free the raw columns
    const gone = index.flags.gone || 0;  // ended listings kept because saved: in the saved list, never in search
    // station -> [kana, English] (null for bus stops: kanji only)
    const stationNames = new Map(index.stations.map((s, i) => [s, index.stationNames?.[i] || null]));
    return { index, items: items.filter((it) => !(it.flags & gone)), flags: index.flags, stationNames,
      byKey: new Map(items.map((it) => [it.key, it])) };
  }

  // Several prefectures' indexes (each loaded with `load`) searched as one.
  function combine(dbs) {
    const all = (f) => dbs.flatMap(f);
    const index = { types: [...new Set(all((d) => d.index.types))], areas: all((d) => d.index.areas),
      stations: [...new Set(all((d) => d.index.stations))], towns: all((d) => d.index.towns), flags: dbs[0].flags };
    return { index, items: all((d) => d.items), flags: index.flags, stationNames: new Map(all((d) => [...d.stationNames])),
      byKey: new Map(all((d) => [...d.byKey])) };
  }

  // The text a search word is looked for in, built the first time a text search runs: names, places and stations
  // in kanji, kana and English (it.alias: the English area name, set by the page).
  const haystack = (it, db) => (it.haystack ??= normalize([it.name, it.town, it.areaName, it.alias, it.layout,
    ...it.stations.flatMap(([s]) => [s + "駅", ...(db.stationNames.get(s) || [])])].filter(Boolean).join(" ")));

  function walkTo(it, chosen) {
    let best = null;
    for (const [s, w] of it.stations) {
      if (w != null && (!chosen.length || chosen.includes(s)) && (best == null || w < best)) best = w;
    }
    return best;
  }

  const STATION_WORDS = new Set(["station", "sta", "sta.", "駅"]);  // "Shibuya Station" finds what "Shibuya" does
  let typed = [null, []];  // the last text searched and its words: split once per search, not once per item
  const words = (v) => (typed[0] === v ? typed : (typed = [v, normalize(v).split(/\s+/).filter((w) => w && !STATION_WORDS.has(w))]))[1];

  // One check per condition: (item, value, query) -> passes. Empty values pass.
  const CHECKS = {
    types: (it, v) => !v.length || v.includes(it.type),
    areas: (it, v) => !v.length || v.includes(it.area),
    stations: (it, v) => !v.length || it.stations.some(([s]) => v.includes(s)),
    walk: (it, v, q) => v == null || (walkTo(it, q.stations) ?? Infinity) <= v,
    priceMax: (it, v) => v == null || (it.price != null && it.price <= v),
    priceMin: (it, v) => v == null || (it.priceHi != null && it.priceHi >= v),
    landMin: (it, v) => v == null || (it.land != null && it.land >= v),
    features: (it, v) => v.every((f) => it.features.has(f)),
    freehold: (it, v, q, db) => !v || !(it.flags & db.flags.leasehold),
    noCondition: (it, v, q, db) => !v || !(it.flags & db.flags.conditional),
    newOnly: (it, v, q, db) => !v || !!(it.flags & db.flags.new),
    dropsOnly: (it, v, q, db) => !v || !!(it.flags & db.flags.dropped),
    // new or cheaper since an hour (YYYYMMDDHH): "since your last visit"
    since: (it, v) => v == null || it.newAt > v || it.dropAt > v,
    text: (it, v, q, db) => !v || words(v).every((w) => haystack(it, db).includes(w)),
  };
  // Conditions on the building: they don't apply to land when 土地 is chosen; otherwise land fails them.
  const BUILDING = {
    plan: (it, v) => v == null || (it.plan != null && it.plan >= v),
    sizeMin: (it, v) => v == null || (it.size != null && it.size >= v),
    ageMax: (it, v) => v == null || (it.age != null && it.age <= v),
    post1981: (it, v, q, db) => !v || !!(it.flags & db.flags.post1981),
  };
  const GROUP = { walk: "stations" };  // walk depends on the chosen stations: count them together
  const FLAGS = ["freehold", "noCondition", "newOnly", "dropsOnly", "post1981"];
  const ALL = [...Object.entries(CHECKS).map(([d, check]) => [d, check, false]),
    ...Object.entries(BUILDING).map(([d, check]) => [d, check, true])];

  // The query's conditions that are set, as [name, check, on the building]: the others pass every item.
  const conditions = (q) => ALL.filter(([d]) => {
    const v = q[d];
    return v != null && v !== false && v !== "" && !(Array.isArray(v) && !v.length);
  });

  // The conditions an item fails (at most 2 are collected: that's all faceting needs).
  function failing(db, it, q, conds = conditions(q)) {
    const out = [];
    const exempt = it.type === "land" && q.types.includes("land");
    for (const [d, check, building] of conds) {
      if ((building && exempt) || check(it, q[d], q, db)) continue;
      // land isn't a building: choosing 土地 is what would let it through
      const dim = building && it.type === "land" ? "types" : GROUP[d] || d;
      if (!out.includes(dim) && out.push(dim) > 1) return out;
    }
    return out;
  }

  const matches = (db, it, q, conds = conditions(q)) => failing(db, it, q, conds).length === 0;

  const newest = (d) => (d ? -d : Infinity);  // newest first, missing last
  const orLast = (v) => (v == null ? Infinity : v);
  function sortKey(it, q) {
    switch (q.sort) {
      case "price_asc": return [orLast(it.price), it.idn];
      case "price_desc": return [-(it.priceHi || 0), it.idn];
      case "size_desc": return [-(it.size || it.land || 0), it.idn];
      case "walk_asc": return [orLast(walkTo(it, q.stations)), it.idn];
      case "age_asc": return [orLast(it.ageMonths), it.idn];
      case "unit_asc": return [orLast(it.unit), it.idn];
      case "drop_desc": return [it.prevPrice && it.price ? it.price / it.prevPrice : Infinity, it.idn];  // biggest cut first
      default: return [newest(it.newDate), newest(it.firstSeen), -it.idn];
    }
  }
  function compare(a, b) {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
  }

  // The matching items (of all, or of the given ones), sorted.
  function search(db, q, items = db.items) {
    const conds = conditions(q), hits = [];
    for (const it of items) if (matches(db, it, q, conds)) hits.push([sortKey(it, q), it]);
    return hits.sort((a, b) => compare(a[0], b[0])).map((h) => h[1]);
  }

  function count(db, q) {
    const conds = conditions(q);
    let n = 0;
    for (const it of db.items) if (matches(db, it, q, conds)) n++;
    return n;
  }

  // Counts for every choice of every condition, in one pass: an item that fails no condition counts for
  // all choices it satisfies; one that fails exactly one condition counts for that condition's choices.
  // choices: {condition: [values]} for thresholds (priceMax, plan, walk...); sets are counted by value.
  function facets(db, q, choices) {
    const out = { total: 0, types: {}, areas: {}, stations: {}, features: {} };
    const n = {};  // threshold choices counted in arrays (much faster than objects keyed by large numbers)
    for (const d in choices) n[d] = new Array(choices[d].length).fill(0);
    for (const flag of FLAGS) out[flag] = 0;
    const tally = (o, k) => { o[k] = (o[k] || 0) + 1; };
    const conds = conditions(q);
    for (const it of db.items) {
      const f = failing(db, it, q, conds);
      if (f.length > 1) continue;
      const only = f[0], free = only === undefined, on = (d) => free || only === d;
      if (free) out.total++;
      if (on("types")) tally(out.types, it.type);
      if (on("areas")) tally(out.areas, it.area);
      if (on("stations")) {
        for (const [s, w] of it.stations) if (q.walk == null || (w != null && w <= q.walk)) tally(out.stations, s);
      }
      if (on("features")) for (const t of it.features) if (q.features.every((x) => x === t || it.features.has(x))) tally(out.features, t);
      for (const d in choices) {
        if (!on(GROUP[d] || d)) continue;
        const check = CHECKS[d] || BUILDING[d], values = choices[d], counts = n[d];
        for (let i = 0; i < values.length; i++) if (check(it, values[i], q, db)) counts[i]++;
      }
      for (const flag of FLAGS) if (on(flag) && (CHECKS[flag] || BUILDING[flag])(it, true, q, db)) out[flag]++;
    }
    for (const d in choices) out[d] = Object.fromEntries(choices[d].map((v, i) => [v, n[d][i]]));
    return out;
  }

  // How well a station's kanji, kana or English name matches what was typed: 0 same, 1 starts with it,
  // 2 contains it, 3 no match.
  function stationMatch(db, s, typed) {
    const term = stationKey(typed);
    let best = 3;
    for (const n of [s, ...(db.stationNames.get(s) || [])]) {
      const k = n ? stationKey(n) : "";
      best = Math.min(best, k === term ? 0 : k.startsWith(term) ? 1 : k.includes(term) ? 2 : 3);
    }
    return best;
  }

  return { emptyQuery, normalize, stationKey, load, combine, matches, search, count, facets, stationMatch };
});
