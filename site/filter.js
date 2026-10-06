// Search rules for the site: load the index, filter, fold duplicates, sort, count choices.
// Loaded by the page and, in tests, by Node (tests/test_site.py drives these rules with real queries).
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.Filter = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function emptyQuery() {
    return {
      text: "", types: [], areas: [], stations: [], walk: null, priceMin: null, priceMax: null, rooms: [],
      sizeMin: null, landMin: null, ageMax: null, post1981: false, features: [], freehold: false,
      noCondition: false, newOnly: false, dropsOnly: false, sort: "new",
    };
  }

  const normalize = (s) => (s || "").normalize("NFKC").toLowerCase();

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
        price: r[c.price], priceHi: r[c.price_max] ?? r[c.price],
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
      if (it.dup == null) continue;
      if (!groups.has(it.dup)) groups.set(it.dup, []);
      groups.get(it.dup).push(it);
    }
    return { index, items, groups, flags: index.flags, byKey: new Map(items.map((it) => [it.key, it])) };
  }

  function walkTo(it, chosen) {
    let best = null;
    for (const [n, w] of it.stations) {
      if (w != null && (!chosen.length || chosen.includes(n)) && (best == null || w < best)) best = w;
    }
    return best;
  }

  // skip: a condition to ignore, for counting the choices of that condition
  function matches(db, it, q, skip) {
    const F = db.flags;
    if (q.types.length && skip !== "types" && !q.types.includes(it.type)) return false;
    if (q.areas.length && skip !== "areas" && !q.areas.includes(it.area)) return false;
    if (q.stations.length && skip !== "stations" && !it.stations.some(([n]) => q.stations.includes(n))) return false;
    if (q.walk != null) {
      const w = walkTo(it, skip === "stations" ? [] : q.stations);
      if (w == null || w > q.walk) return false;
    }
    if (q.priceMax != null && (it.price == null || it.price > q.priceMax)) return false;
    if (q.priceMin != null && (it.priceHi == null || it.priceHi < q.priceMin)) return false;
    // building conditions imply a building, except for land asked for (or being counted as a type choice)
    const building = it.type !== "land" || !(q.types.includes("land") || skip === "types");
    if (building && q.rooms.length && skip !== "rooms" && !q.rooms.some((b) => it.rooms & (1 << (b - 1)))) return false;
    if (building && q.sizeMin != null && (it.size == null || it.size < q.sizeMin)) return false;
    if (q.landMin != null && (it.land == null || it.land < q.landMin)) return false;
    if (building && q.ageMax != null && (it.age == null || it.age > q.ageMax)) return false;
    if (building && q.post1981 && !(it.flags & F.post1981)) return false;
    if (q.features.length && skip !== "features" && !q.features.every((f) => it.features.has(f))) return false;
    if (q.freehold && it.flags & F.leasehold) return false;
    if (q.noCondition && it.flags & F.conditional) return false;
    if (q.newOnly && !(it.flags & F.new)) return false;
    if (q.dropsOnly && !(it.flags & F.dropped)) return false;
    if (q.text) {
      for (const word of normalize(q.text).split(/\s+/)) {
        if (word && !it.haystack.includes(word)) return false;
      }
    }
    return true;
  }

  const others = (db, it) => (it.dup == null ? [] : db.groups.get(it.dup).filter((o) => o !== it));
  const hitFor = (db, key) => (db.byKey.has(key) ? { item: db.byKey.get(key), others: others(db, db.byKey.get(key)) } : null);
  const cheaper = (a, b) => (a.price ?? Infinity) - (b.price ?? Infinity) || a.idn - b.idn;

  // One hit per property: the cheapest (then lowest id) matching listing represents its duplicate group.
  function fold(db, matched) {
    const best = new Map(), hits = [];
    for (const it of matched) {
      if (it.dup == null) hits.push({ item: it, others: [] });
      else if (!best.has(it.dup) || cheaper(it, best.get(it.dup)) < 0) best.set(it.dup, it);
    }
    for (const it of best.values()) hits.push({ item: it, others: others(db, it) });
    return hits;
  }

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
    const hits = fold(db, db.items.filter((it) => matches(db, it, q)));
    const keys = new Map(hits.map((h) => [h, sortKey(h.item, q)]));
    return hits.sort((a, b) => compare(keys.get(a), keys.get(b)));
  }

  // Number of properties (duplicates counted once) matching q.
  function count(db, q) {
    const dups = new Set();
    let n = 0;
    for (const it of db.items) {
      if (!matches(db, it, q)) continue;
      if (it.dup == null) n++;
      else if (!dups.has(it.dup)) { dups.add(it.dup); n++; }
    }
    return n;
  }

  // Properties per value of one condition, given the rest of the query: {value: count}.
  function facet(db, q, dim, valuesOf) {
    const sets = new Map();
    for (const it of db.items) {
      if (!matches(db, it, q, dim)) continue;
      const id = it.dup == null ? "i" + it.idx : "d" + it.dup;
      for (const v of valuesOf(it)) {
        if (!sets.has(v)) sets.set(v, new Set());
        sets.get(v).add(id);
      }
    }
    return Object.fromEntries([...sets].map(([v, s]) => [v, s.size]));
  }

  return { emptyQuery, load, search, count, facet, hitFor };
});
