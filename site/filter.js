// Search rules for the site: load the index, filter, fold duplicates, sort, count choices.
// Mirrors suumo/catalog.py (Snapshot.matches, fold, sort_hits); tests/test_site.py runs both on the same
// queries and requires identical results. Change both together.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Filter = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const LEASEHOLD = 1, CONDITIONAL = 2, POST_1981 = 8, NEW = 16, DROPPED = 32;
  const SORTS = ["new", "price_asc", "price_desc", "size_desc", "walk_asc", "age_asc", "unit_asc"];

  function emptyQuery() {
    return {
      text: "", types: [], areas: [], stations: [], walk: null, priceMin: null, priceMax: null, rooms: [],
      sizeMin: null, landMin: null, ageMax: null, post1981: false, features: [], freehold: false,
      noCondition: false, newOnly: false, dropsOnly: false, sort: "new",
    };
  }

  function normalize(s) {
    return (s || "").normalize("NFKC").toLowerCase();
  }

  function load(index) {
    const c = {};
    index.columns.forEach((name, i) => { c[name] = i; });
    const items = index.rows.map((r, idx) => {
      const type = index.types[r[c.type]];
      const town = r[c.town] == null ? null : index.towns[r[c.town]];
      const stations = r[c.stations].map(([s, w]) => [index.stations[s], w]);
      const item = {
        idx, id: r[c.id], idn: Number(r[c.id]), type, key: type + ":" + r[c.id],
        area: index.areas[r[c.area]][0], areaName: index.areas[r[c.area]][1],
        price: r[c.price], priceHi: r[c.price_max] != null ? r[c.price_max] : r[c.price],
        rooms: r[c.rooms], size: r[c.size], land: r[c.land], age: r[c.age], built: r[c.built],
        builtInt: r[c.built] ? Number(r[c.built].replace("-", "")) : 0,
        stations, features: new Set(r[c.features].map((f) => index.features[f])), flags: r[c.flags],
        dup: r[c.dup], newDate: r[c.new_date], firstSeen: r[c.first_seen], unit: r[c.unit],
        town: town ? town[0].replace(/^東京都/, "") : null, lat: town ? town[1] : null, lng: town ? town[2] : null,
        name: r[c.name] || "", layout: r[c.layout] || "", image: r[c.image],
      };
      item.haystack = normalize([item.name, item.town, item.areaName, item.layout,
        ...stations.map(([n]) => n + "駅")].join(" "));
      return item;
    });
    const groups = new Map();
    for (const it of items) {
      if (it.dup != null) {
        if (!groups.has(it.dup)) groups.set(it.dup, []);
        groups.get(it.dup).push(it);
      }
    }
    return { index, items, groups };
  }

  function walkTo(it, chosen) {
    let best = null;
    for (const [n, w] of it.stations) {
      if (w != null && (!chosen.length || chosen.includes(n)) && (best == null || w < best)) best = w;
    }
    return best;
  }

  function matches(it, q, skip) {
    skip = skip || "";
    if (q.types.length && skip !== "types" && !q.types.includes(it.type)) return false;
    if (q.areas.length && skip !== "areas" && !q.areas.includes(it.area)) return false;
    if (q.stations.length && skip !== "stations" && !it.stations.some(([n]) => q.stations.includes(n))) return false;
    if (q.walk != null) {
      const w = walkTo(it, skip === "stations" ? [] : q.stations);
      if (w == null || w > q.walk) return false;
    }
    if (q.priceMax != null && (it.price == null || it.price > q.priceMax)) return false;
    if (q.priceMin != null && (it.priceHi == null || it.priceHi < q.priceMin)) return false;
    // building conditions imply a building, except for land the user explicitly asked for
    const building = it.type !== "land" || !q.types.includes("land");
    if (building && q.rooms.length && skip !== "rooms" && !q.rooms.some((b) => it.rooms & (1 << (b - 1)))) return false;
    if (building && q.sizeMin != null && (it.size == null || it.size < q.sizeMin)) return false;
    if (q.landMin != null && (it.land == null || it.land < q.landMin)) return false;
    if (building && q.ageMax != null && (it.age == null || it.age > q.ageMax)) return false;
    if (building && q.post1981 && !(it.flags & POST_1981)) return false;
    if (q.features.length && skip !== "features" && !q.features.every((f) => it.features.has(f))) return false;
    if (q.freehold && it.flags & LEASEHOLD) return false;
    if (q.noCondition && it.flags & CONDITIONAL) return false;
    if (q.newOnly && !(it.flags & NEW)) return false;
    if (q.dropsOnly && !(it.flags & DROPPED)) return false;
    if (q.text) {
      for (const word of normalize(q.text).split(/\s+/)) {
        if (word && !it.haystack.includes(word)) return false;
      }
    }
    return true;
  }

  // One hit per property: the cheapest (then lowest id) matching listing represents its duplicate group.
  function fold(db, matched) {
    const best = new Map();
    const hits = [];
    for (const it of matched) {
      if (it.dup == null) { hits.push({ item: it, others: [] }); continue; }
      const cur = best.get(it.dup);
      if (!cur || cheaper(it, cur)) best.set(it.dup, it);
    }
    for (const [dup, it] of best) hits.push({ item: it, others: db.groups.get(dup).filter((o) => o !== it) });
    return hits;
  }

  function cheaper(a, b) {
    const pa = a.price == null ? [1, 0] : [0, a.price], pb = b.price == null ? [1, 0] : [0, b.price];
    return cmp([...pa, a.idn], [...pb, b.idn]) < 0;
  }

  function cmp(a, b) {
    for (let i = 0; i < a.length; i++) {
      if (a[i] < b[i]) return -1;
      if (a[i] > b[i]) return 1;
    }
    return 0;
  }

  const INF = Infinity;
  const newest = (d) => (d ? [0, -d] : [1, 0]);
  const orNone = (v) => (v == null ? INF : v);

  function sortKey(it, q) {
    switch (SORTS.includes(q.sort) ? q.sort : "new") {
      case "price_asc": return [orNone(it.price), it.idn];
      case "price_desc": return [-(it.priceHi || 0), it.idn];
      case "size_desc": return [-(it.size || it.land || 0), it.idn];
      case "walk_asc": return [orNone(walkTo(it, q.stations)), it.idn];
      case "age_asc": return [orNone(it.age), ...newest(it.builtInt), it.idn];
      case "unit_asc": return [orNone(it.unit), it.idn];
      default: return [...newest(it.newDate), ...newest(it.firstSeen), -it.idn];
    }
  }

  function search(db, q) {
    const hits = fold(db, db.items.filter((it) => matches(it, q)));
    const keys = new Map(hits.map((h) => [h, sortKey(h.item, q)]));
    return hits.sort((a, b) => cmp(keys.get(a), keys.get(b)));
  }

  // Number of properties (duplicates counted once) matching q.
  function count(db, q, skip, pool) {
    const seen = new Set();
    let n = 0;
    for (const it of pool || db.items) {
      if (!matches(it, q, skip)) continue;
      if (it.dup == null) n++;
      else if (!seen.has(it.dup)) { seen.add(it.dup); n++; }
    }
    return n;
  }

  // Counts per value of one dimension, given the rest of the query: {value: properties}.
  function facet(db, q, dim, valuesOf) {
    const sets = new Map();
    for (const it of db.items) {
      if (!matches(it, q, dim)) continue;
      const id = it.dup == null ? "i" + it.idx : "d" + it.dup;
      for (const v of valuesOf(it)) {
        if (!sets.has(v)) sets.set(v, new Set());
        sets.get(v).add(id);
      }
    }
    const out = {};
    for (const [v, s] of sets) out[v] = s.size;
    return out;
  }

  return { emptyQuery, load, matches, search, count, facet, walkTo, normalize, SORTS };
});
