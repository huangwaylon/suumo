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
    noCondition: false, newOnly: false, dropsOnly: false, sort: "new",
  });

  // NFKC (full-width -> half-width), lower case, hiragana -> katakana: "ぱーく", "ﾊﾟｰｸ" and "パーク" all match.
  const normalize = (s) => (s || "").normalize("NFKC").toLowerCase()
    .replace(/[ぁ-ゖ]/g, (c) => String.fromCharCode(c.charCodeAt(0) + 0x60));

  function load(index) {
    const c = index.columns, n = c.id.length, items = new Array(n);
    let id = 0;
    for (let i = 0; i < n; i++) {
      id += c.id[i];
      const type = index.types[c.type[i]], area = index.areas[c.area[i]], town = index.towns[c.town[i]];
      const st = c.stations[i], stations = [];
      for (let j = 0; j < st.length; j += 2) stations.push([index.stations[st[j]], st[j + 1]]);
      const it = {
        id: String(id), idn: id, type, key: type + ":" + id, area: area[0], areaName: area[1],
        price: c.price[i], priceHi: c.priceMax[i] ?? c.price[i], plan: c.plan[i], size: c.size[i],
        land: c.land[i], age: c.age[i], built: c.built[i], builtInt: c.built[i] ? +c.built[i].replace("-", "") : 0,
        stations, features: new Set(c.features[i].map((f) => index.features[f])), flags: c.flags[i],
        others: c.others[i], newDate: c.newDate[i], firstSeen: c.firstSeen[i], unit: c.unit[i],
        town: town ? town[0].slice(index.prefs[area[2]].length) : null, lat: town ? town[1] : null,
        lng: town ? town[2] : null, name: c.name[i] || "", layout: c.layout[i] || "", image: c.image[i],
      };
      it.haystack = normalize([it.name, it.town, it.areaName, it.layout, ...stations.map(([s]) => s + "駅")].join(" "));
      items[i] = it;
    }
    index.columns = null;  // the items hold everything now; free the raw columns
    return { index, items, flags: index.flags, byKey: new Map(items.map((it) => [it.key, it])) };
  }

  function walkTo(it, chosen) {
    let best = null;
    for (const [s, w] of it.stations) {
      if (w != null && (!chosen.length || chosen.includes(s)) && (best == null || w < best)) best = w;
    }
    return best;
  }

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
    freehold: (it, v, q, F) => !v || !(it.flags & F.leasehold),
    noCondition: (it, v, q, F) => !v || !(it.flags & F.conditional),
    newOnly: (it, v, q, F) => !v || !!(it.flags & F.new),
    dropsOnly: (it, v, q, F) => !v || !!(it.flags & F.dropped),
    text: (it, v) => !v || normalize(v).split(/\s+/).every((w) => !w || it.haystack.includes(w)),
  };
  // Conditions on the building: they don't apply to land when 土地 is chosen; otherwise land fails them.
  const BUILDING = {
    plan: (it, v) => v == null || (it.plan != null && it.plan >= v),
    sizeMin: (it, v) => v == null || (it.size != null && it.size >= v),
    ageMax: (it, v) => v == null || (it.age != null && it.age <= v),
    post1981: (it, v, q, F) => !v || !!(it.flags & F.post1981),
  };
  const GROUP = { walk: "stations" };  // walk depends on the chosen stations: count them together

  // The conditions an item fails (at most 2 are collected: that's all faceting needs).
  function failing(db, it, q) {
    const out = [];
    const add = (d) => { if (!out.includes(d)) out.push(d); return out.length > 1; };
    for (const d in CHECKS) if (!CHECKS[d](it, q[d], q, db.flags) && add(GROUP[d] || d)) return out;
    const exempt = it.type === "land" && q.types.includes("land");
    for (const d in BUILDING) {
      // land isn't a building: choosing 土地 is what would let it through
      if (!exempt && !BUILDING[d](it, q[d], q, db.flags) && add(it.type === "land" ? "types" : d)) return out;
    }
    return out;
  }

  const matches = (db, it, q) => failing(db, it, q).length === 0;

  const newest = (d) => (d ? [0, -d] : [1, 0]);  // newest first, missing last
  const orLast = (v) => (v == null ? Infinity : v);
  function sortKey(it, q) {
    switch (q.sort) {
      case "price_asc": return [orLast(it.price), it.idn];
      case "price_desc": return [-(it.priceHi || 0), it.idn];
      case "size_desc": return [-(it.size || it.land || 0), it.idn];
      case "walk_asc": return [orLast(walkTo(it, q.stations)), it.idn];
      case "age_asc": return [orLast(it.age), ...newest(it.builtInt), it.idn];
      case "unit_asc": return [orLast(it.unit), it.idn];
      default: return [...newest(it.newDate), ...newest(it.firstSeen), -it.idn];
    }
  }
  function compare(a, b) {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
    return 0;
  }

  function search(db, q) {
    const hits = db.items.filter((it) => matches(db, it, q));
    const keys = new Map(hits.map((it) => [it, sortKey(it, q)]));
    return hits.sort((a, b) => compare(keys.get(a), keys.get(b)));
  }

  const count = (db, q) => db.items.reduce((n, it) => n + matches(db, it, q), 0);

  // Counts for every choice of every condition, in one pass: an item that fails no condition counts for
  // all choices it satisfies; one that fails exactly one condition counts for that condition's choices.
  // choices: {condition: [values]} for thresholds (priceMax, plan, walk...); sets are counted by value.
  function facets(db, q, choices) {
    const out = { total: 0, types: {}, areas: {}, stations: {}, features: {} };
    for (const d in choices) out[d] = {};
    for (const flag of ["freehold", "noCondition", "newOnly", "dropsOnly", "post1981"]) out[flag] = 0;
    const tally = (o, k) => { o[k] = (o[k] || 0) + 1; };
    for (const it of db.items) {
      const f = failing(db, it, q);
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
        const check = CHECKS[d] || BUILDING[d];
        for (const v of choices[d]) if (check(it, v, q, db.flags)) tally(out[d], v);
      }
      for (const flag in { freehold: 1, noCondition: 1, newOnly: 1, dropsOnly: 1 }) {
        if (on(flag) && CHECKS[flag](it, true, q, db.flags)) out[flag]++;
      }
      if (on("post1981") && BUILDING.post1981(it, true, q, db.flags)) out.post1981++;
    }
    return out;
  }

  return { emptyQuery, normalize, load, matches, search, count, facets };
});
