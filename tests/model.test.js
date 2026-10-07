"use strict"

const { test } = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../js/Model.js")

test("dayKey pads month and day", () => {
  assert.equal(Model.dayKey(new Date(2026, 7, 15)), "2026-08-15")
  assert.equal(Model.dayKey(new Date(2026, 0, 3)), "2026-01-03")
})

test("canonicalApp folds browser subprocess names", () => {
  assert.equal(Model.canonicalApp("zen-bin"), "zen")
  assert.equal(Model.canonicalApp("brave-browser"), "brave")
  assert.equal(Model.canonicalApp("foot"), "foot")
  assert.equal(Model.canonicalApp(""), "")
})

test("canonicalApp folds Chromium web apps across profiles", () => {
  const defaultProfile = Model.canonicalApp("chrome-chatgpt.com__-Default")
  const numberedProfile = Model.canonicalApp("chrome-chatgpt.com__-Profile_2")

  assert.equal(defaultProfile, "chrome-chatgpt.com")
  assert.equal(numberedProfile, defaultProfile)
  assert.equal(
    Model.canonicalApp("chrome-music.apple.com__lv_home-Default"),
    "chrome-music.apple.com",
  )
})

test("canonicalApp normalizes Chromium-family web app keys", () => {
  assert.equal(
    Model.canonicalApp("chromium-calendar.google.com__-Profile_1"),
    "chromium-calendar.google.com",
  )
  assert.equal(
    Model.canonicalApp("brave-calendar.google.com__-Default"),
    "brave-calendar.google.com",
  )
  assert.equal(
    Model.canonicalApp("msedge-calendar.google.com__-Default"),
    "msedge-calendar.google.com",
  )
  assert.equal(
    Model.canonicalApp("vivaldi-calendar.google.com__-Default"),
    "vivaldi-calendar.google.com",
  )
})

test("displayName shortens reverse-DNS ids and passes plain names", () => {
  assert.equal(Model.displayName("com.github.user.Codium"), "codium")
  assert.equal(Model.displayName("org.mozilla.firefox"), "firefox")
  assert.equal(Model.displayName("io.github.pkruow.Cli"), "cli")
  assert.equal(Model.displayName("opencode"), "opencode")
  assert.equal(Model.displayName("google-chrome"), "google-chrome")
  assert.equal(Model.displayName(""), "")
  assert.equal(Model.displayName(null), "")
})

test("displayName extracts hostnames from Chromium-family web app keys", () => {
  assert.equal(Model.displayName("chrome-chatgpt.com__-Default"), "chatgpt.com")
  assert.equal(
    Model.displayName("chrome-music.apple.com__lv_home-Default"),
    "music.apple.com",
  )
  assert.equal(
    Model.displayName("chrome-calendar.google.com__-Profile_1"),
    "calendar.google.com",
  )
  assert.equal(
    Model.displayName("chromium-chatgpt.com__-Default"),
    "chatgpt.com",
  )
  assert.equal(Model.displayName("brave-chatgpt.com__-Default"), "chatgpt.com")
  assert.equal(Model.displayName("msedge-chatgpt.com__-Default"), "chatgpt.com")
  assert.equal(
    Model.displayName("vivaldi-chatgpt.com__-Default"),
    "chatgpt.com",
  )
  assert.equal(Model.displayName("chrome-chatgpt.com"), "chatgpt.com")
})

test("displayName keeps dotted non-reverse-DNS names intact", () => {
  assert.equal(Model.displayName("Minecraft* 26.2"), "minecraft* 26.2")
  assert.equal(Model.displayName("editor-1.2"), "editor-1.2")
})

test("displayName passes unresolved Steam ids through untouched", () => {
  // Game titles are resolved by python/resolve_app.py before storage;
  // the display layer must never touch the filesystem for a label.
  // (require()-based resolution cannot run in QML's JS engine anyway.)
  assert.equal(Model.displayName("steam_app_730"), "steam_app_730")
  assert.equal(Model.resolveSteamAppName, undefined)
})

test("sanitizeHistory keeps valid sections and rejects malformed ones", () => {
  const days = { "2026-08-21": { total: 5, apps: { zen: 5 } } }
  const months = { "2026-07": 9823400 }

  const clean = Model.sanitizeHistory(days, months)
  assert.equal(clean.days, days)
  assert.equal(clean.months, months)

  // Arrays pass typeof "object" but are not valid history containers.
  assert.deepEqual(Model.sanitizeHistory([1, 2], days).days, {})
  assert.deepEqual(Model.sanitizeHistory(days, ["x"]).months, {})
  assert.deepEqual(Model.sanitizeHistory(null, undefined).days, {})
  assert.deepEqual(Model.sanitizeHistory("{}", 42).months, {})
})

test("prevKey handles month and year boundaries", () => {
  assert.equal(Model.prevKey("2026-08-15"), "2026-08-14")
  assert.equal(Model.prevKey("2026-03-01"), "2026-02-28")
  assert.equal(Model.prevKey("2026-01-01"), "2025-12-31")
})

test("empty or malformed keys never produce garbage day keys", () => {
  assert.equal(Model.prevKey(""), "")
  assert.equal(Model.prevKey("not-a-date"), "")
})

test("pruneDays keeps only the retention window", () => {
  const days = {
    "2026-07-15": { total: 1 },
    "2026-08-01": { total: 2 },
    "2026-08-10": { total: 3 },
    "2026-08-15": { total: 4 },
  }
  const out = Model.pruneDays(days, "2026-08-15", 7)
  assert.deepEqual(Object.keys(out), ["2026-08-10", "2026-08-15"])
})

test("pruneDays returns the same object when nothing is pruned", () => {
  const days = { "2026-08-15": { total: 3 } }
  assert.equal(Model.pruneDays(days, "2026-08-15", 31), days)
})

test("browser_aliases.json is the single source of truth for canonicalApp", () => {
  const aliases = require("../js/browser_aliases.json")
  assert.equal(typeof aliases, "object")
  assert.ok(Object.keys(aliases).length > 0)
  for (const [key, target] of Object.entries(aliases)) {
    assert.equal(
      Model.canonicalApp(key),
      target,
      `canonicalApp("${key}") should return "${target}" from browser_aliases.json`,
    )
  }
})

test("QML inline browser aliases match browser_aliases.json", () => {
  // QML cannot read the JSON file synchronously (Quickshell's XHR blocks
  // local files), so Model.js mirrors the data as a literal. Fail loudly
  // if the mirror drifts from the canonical file — a silent divergence
  // would fold browsers differently under QML vs Node.
  const fs = require("fs")
  const file = JSON.parse(
    fs.readFileSync(require.resolve("../js/browser_aliases.json"), "utf8"),
  )
  const qml = Model.qmlBrowserAliases()
  assert.deepEqual(qml, file)
  assert.ok(Object.keys(qml).length > 0)
})

// ---- Data safety: pruneDays -----------------------------------------------

test("pruneDays never removes todayKey", () => {
  const days = {}
  for (let i = 0; i < 40; i++) {
    const d = new Date(2026, 7, 15 - i)
    days[Model.dayKey(d)] = { total: i * 1000, apps: {} }
  }
  const out = Model.pruneDays(days, "2026-08-15", 7)
  assert.ok(out["2026-08-15"], "today must survive pruning")
})

test("pruneDays never removes days within the retention window", () => {
  const days = {
    "2026-08-09": { total: 100 },
    "2026-08-10": { total: 200 },
    "2026-08-11": { total: 300 },
    "2026-08-12": { total: 400 },
    "2026-08-13": { total: 500 },
    "2026-08-14": { total: 600 },
    "2026-08-15": { total: 700 },
  }
  const out = Model.pruneDays(days, "2026-08-15", 7)
  // All 7 days should survive
  assert.equal(Object.keys(out).length, 7)
})

test("pruneDays with keepDays of 1 keeps only today", () => {
  const days = {
    "2026-08-14": { total: 100 },
    "2026-08-15": { total: 200 },
  }
  const out = Model.pruneDays(days, "2026-08-15", 1)
  assert.deepEqual(Object.keys(out), ["2026-08-15"])
})

test("pruneDays with keepDays of 0 returns original (no-op)", () => {
  const days = { "2026-08-15": { total: 100 } }
  const out = Model.pruneDays(days, "2026-08-15", 0)
  assert.equal(out, days)
})

test("pruneDays with negative keepDays returns original (no-op)", () => {
  const days = { "2026-08-15": { total: 100 } }
  const out = Model.pruneDays(days, "2026-08-15", -5)
  assert.equal(out, days)
})

test("pruneDays with null days returns original", () => {
  assert.equal(Model.pruneDays(null, "2026-08-15", 7), null)
})

test("pruneDays across year boundary keeps correct window", () => {
  const days = {
    "2025-12-30": { total: 100 },
    "2025-12-31": { total: 200 },
    "2026-01-01": { total: 300 },
    "2026-01-02": { total: 400 },
  }
  const out = Model.pruneDays(days, "2026-01-02", 3)
  assert.ok(out["2026-01-02"])
  assert.ok(out["2026-01-01"])
  assert.ok(out["2025-12-31"])
  assert.equal(out["2025-12-30"], undefined)
})

// ---- Data safety: corrupt / missing input ----------------------------------

test("dayKey produces consistent keys across Date object reuse", () => {
  const d = new Date(2026, 0, 1)
  const k1 = Model.dayKey(d)
  const k2 = Model.dayKey(d)
  assert.equal(k1, k2)
  assert.equal(k1, "2026-01-01")
})

// ---- year archive ----------------------------------------------------------

const HOUR_MS = 3600000

test("sanitizeHistory validates the years archive", () => {
  assert.deepEqual(
    Model.sanitizeHistory({}, {}, { 2026: { "2026-01-02": HOUR_MS } }).years,
    { 2026: { "2026-01-02": HOUR_MS } },
  )
  assert.deepEqual(
    Model.sanitizeHistory(
      {},
      {},
      { 2026: { "2026-01-02": "x", "2026-01-03": 0 } },
    ).years,
    { 2026: { "2026-01-03": 0 } },
  )
  assert.deepEqual(Model.sanitizeHistory({}, {}, { 2026: "nope" }).years, {})
})

test("rollupArchive coerces string totals to numbers", () => {
  const out = Model.rollupArchive({}, { "2026-08-01": { total: "3600" } })
  assert.deepEqual(out, { 2026: { "2026-08-01": 3600 } })
})

test("rollupArchive keeps only per-day totals, never app maps", () => {
  const pruned = {
    "2026-08-01": { total: HOUR_MS, apps: { web: HOUR_MS } },
    "2026-08-02": { total: 0, apps: { done: 1 } },
    "2025-12-31": { total: 2 * HOUR_MS, apps: {} },
  }
  const base = { 2026: { "2026-08-03": 1000 } }
  const out = Model.rollupArchive(base, pruned)
  assert.deepEqual(out, {
    2026: { "2026-08-03": 1000, "2026-08-01": HOUR_MS },
    2025: { "2025-12-31": 2 * HOUR_MS },
  })
  assert.deepEqual(base, { 2026: { "2026-08-03": 1000 } })
})

test("applyRetention prunes days into the archive in one step", () => {
  const days = {
    "2026-01-01": { total: HOUR_MS, apps: {} },
    "2026-08-15": { total: 2 * HOUR_MS, apps: {} },
  }
  const r = Model.applyRetention(days, {}, "2026-08-15", 95)
  assert.equal(r.pruned, true)
  assert.deepEqual(Object.keys(r.days), ["2026-08-15"])
  assert.deepEqual(r.years, { 2026: { "2026-01-01": HOUR_MS } })
})

test("applyRetention grows the archive across many calendar years", () => {
  const days = {
    "2023-02-10": { total: HOUR_MS, apps: {} },
    "2024-06-20": { total: 2 * HOUR_MS, apps: {} },
    "2026-08-15": { total: HOUR_MS, apps: {} },
  }
  const r = Model.applyRetention(days, {}, "2026-08-15", 365)
  assert.deepEqual(r.years, {
    2023: { "2023-02-10": HOUR_MS },
    2024: { "2024-06-20": 2 * HOUR_MS },
  })
})

test("applyRetention returns inputs untouched when nothing is pruned", () => {
  const days = { "2026-08-15": { total: HOUR_MS, apps: {} } }
  const years = { 2026: { "2026-01-02": HOUR_MS } }
  const r = Model.applyRetention(days, years, "2026-08-15", 95)
  assert.equal(r.pruned, false)
  assert.equal(r.days, days)
  assert.equal(r.years, years)
})

test("pruneDays with missing keepDays returns days untouched", () => {
  const days = {
    "2026-01-01": { total: 100 },
    "2026-08-15": { total: 200 },
  }
  assert.equal(Model.pruneDays(days, "2026-08-15", undefined), days)
  assert.equal(Model.pruneDays(days, "2026-08-15", NaN), days)
})

test("sanitizeHistory cleans malformed day shapes", () => {
  const days = {
    "2026-08-15": { total: "not-a-number", apps: ["zen"] },
    "2026-08-16": { total: -5, apps: { zen: NaN, foot: 60000 } },
  }
  const clean = Model.sanitizeHistory(days, {}, {})
  assert.equal(clean.days["2026-08-15"].total, 0)
  assert.deepEqual(clean.days["2026-08-15"].apps, {})
  assert.equal(clean.days["2026-08-16"].total, 0)
  assert.deepEqual(clean.days["2026-08-16"].apps, { foot: 60000 })
})

test("parseIgnoredApps accepts arrays and comma strings, lowercased deduped", () => {
  assert.deepEqual(Model.parseIgnoredApps(["Zen", " zen ", "", "foot"]), [
    "zen",
    "foot",
  ])
  assert.deepEqual(Model.parseIgnoredApps("Zen, foot,, "), ["zen", "foot"])
  assert.deepEqual(Model.parseIgnoredApps(undefined), [])
  assert.deepEqual(Model.parseIgnoredApps(42), [])
})

test("isIgnoredApp matches raw, canonical and display names", () => {
  assert.equal(Model.isIgnoredApp("zen-bin", ["zen"]), true)
  assert.equal(Model.isIgnoredApp("com.github.user.Codium", ["codium"]), true)
  assert.equal(Model.isIgnoredApp("foot", ["foot"]), true)
  assert.equal(Model.isIgnoredApp("foot", ["kitty"]), false)
  assert.equal(Model.isIgnoredApp("foot", []), false)
  assert.equal(Model.isIgnoredApp("", ["foot"]), false)
})

// ---- names.json ignore list --------------------------------------------------

// ---- the engine's card (D14) -------------------------------------------------

const GOOD = JSON.stringify({ schema: 11, state: "ok", today: { total_ms: 1 } })

test("acceptCard takes a valid card from the last line", () => {
  const r = Model.acceptCard(null, "a warning\n" + GOOD + "\n")
  assert.equal(r.ok, true)
  assert.equal(r.error, "")
  assert.deepEqual(r.card, JSON.parse(GOOD))
})

test("acceptCard keeps the last good card on a bad run", () => {
  const prev = { schema: 11, state: "ok", today: {} }
  for (const raw of [
    "",
    "   \n",
    undefined,
    null,
    "{not json",
    "[1, 2]",
    '{"schema": 1, "state": "ok", "today": {}}',
    '{"schema": 2, "state": "ok", "today": {}}',
    '{"schema": 3, "state": "ok", "today": {}}',
    '{"schema": 4, "state": "ok", "today": {}}',
    '{"schema": 5, "state": "ok", "today": {}}',
    '{"schema": 6, "state": "ok", "today": {}}',
    '{"schema": 7, "state": "ok", "today": {}}',
    '{"schema": 8, "state": "ok", "today": {}}',
    '{"schema": 9, "state": "ok", "today": {}}',
    '{"schema": 10, "state": "ok", "today": {}}',
    '{"schema": 11, "today": {}}',
    '{"schema": 11, "state": "ok"}',
    GOOD + "\ntrailing junk",
  ]) {
    const r = Model.acceptCard(prev, raw)
    assert.equal(r.ok, false, String(raw))
    assert.equal(r.card, prev, String(raw))
    assert.ok(r.error.startsWith("the engine printed"), String(raw))
  }
  assert.equal(Model.acceptCard(null, "").error, "the engine printed nothing")
  assert.equal(Model.acceptCard(undefined, "{x").card, null)
})

test("acceptCard takes fallback cards too: they are documents", () => {
  const raw = JSON.stringify({ schema: 11, state: "missing", today: {} })
  assert.equal(Model.acceptCard(null, raw).ok, true)
})

test("the card schema matches the engine's", () => {
  const engine = require("node:fs").readFileSync(
    require("node:path").join(__dirname, "..", "python", "screen_time.py"),
    "utf8",
  )
  assert.match(engine, new RegExp("^SCHEMA = " + Model.CARD_SCHEMA + "$", "m"))
})

test("storedKeyCount counts distinct app keys across days", () => {
  const days = {
    "2025-06-10": { total: 3, apps: { a: 1, b: 2 } },
    "2025-06-11": { total: 4, apps: { b: 1, c: 3 } },
    "2025-06-12": { total: 0, apps: "nope" },
    "2025-06-13": null,
  }
  assert.equal(Model.storedKeyCount(days), 3)
  assert.equal(Model.storedKeyCount({}), 0)
  assert.equal(Model.storedKeyCount(undefined), 0)
})

const FRESH = { ignore: [], warned: false }

test("applyNamesFile reads a valid document's ignore list", () => {
  const r = Model.applyNamesFile(
    FRESH,
    "loaded",
    '{"rename": {"claude": "Claude Code"}, "hide": ["bash"], "ignore": ["Zen", "foot", "zen"]}',
  )
  assert.deepEqual(r, { ignore: ["zen", "foot"], warned: false, warn: "" })
})

test("applyNamesFile: no ignore key, or a missing file, means no ignores", () => {
  const prev = { ignore: ["foot"], warned: false }
  assert.deepEqual(
    Model.applyNamesFile(prev, "loaded", '{"hide": []}').ignore,
    [],
  )
  assert.deepEqual(Model.applyNamesFile(prev, "missing", ""), {
    ignore: [],
    warned: false,
    warn: "",
  })
})

test("applyNamesFile keeps the last good list when the file is bad", () => {
  const prev = { ignore: ["foot"], warned: false }
  for (const [kind, text] of [
    ["loaded", "{not json"],
    ["loaded", ""],
    ["loaded", '["foot"]'],
    ["loaded", "null"],
    ["loaded", '{"ignore": "foot"}'],
    ["failed", ""],
  ]) {
    const r = Model.applyNamesFile(prev, kind, text)
    assert.deepEqual(r.ignore, ["foot"], kind + " " + text)
    assert.equal(r.warned, true, kind + " " + text)
    assert.match(r.warn, /^names\.json .+; keeping the last good ignore list$/)
  }
})

test("applyNamesFile warns once per failure streak, again after recovery", () => {
  let s = { ignore: ["foot"], warned: false }
  const warns = []
  for (const [kind, text] of [
    ["loaded", "{bad"],
    ["loaded", "{bad"],
    ["failed", ""],
    ["loaded", '{"ignore": ["kitty"]}'],
    ["loaded", "{bad"],
  ]) {
    s = Model.applyNamesFile(s, kind, text)
    if (s.warn) warns.push(s.warn)
  }
  assert.equal(warns.length, 2)
  assert.deepEqual(s.ignore, ["kitty"])
})

test("applyNamesFile tolerates a missing or malformed previous state", () => {
  assert.deepEqual(Model.applyNamesFile(undefined, "loaded", "{bad").ignore, [])
  assert.deepEqual(Model.applyNamesFile({}, "failed", "").ignore, [])
})

test("pure helpers return safe defaults instead of throwing", () => {
  assert.equal(Model.dayKey(null), "")
  assert.equal(Model.dayKey(undefined), "")
  assert.equal(Model.dayKey(new Date(NaN)), "")
  assert.deepEqual(Model.pruneDays({ a: 1 }, "2026-08-19", Infinity), { a: 1 })
})

test("isDayKey accepts real padded days only", () => {
  assert.equal(Model.isDayKey("2026-08-19"), true)
  assert.equal(Model.isDayKey("2024-02-29"), true)
  assert.equal(Model.isDayKey("2026-02-29"), false)
  assert.equal(Model.isDayKey("2026-13-01"), false)
  assert.equal(Model.isDayKey("2026-8-5"), false)
  assert.equal(Model.isDayKey("garbage"), false)
  assert.equal(Model.isDayKey("__proto__"), false)
  assert.equal(Model.isDayKey(""), false)
  assert.equal(Model.isDayKey(null), false)
})

test("isMonthKey accepts real calendar months only", () => {
  assert.equal(Model.isMonthKey("2026-08"), true)
  assert.equal(Model.isMonthKey("2026-13"), false)
  assert.equal(Model.isMonthKey("2026-8"), false)
  assert.equal(Model.isMonthKey("__proto__"), false)
})

test("sanitizeHistory drops malformed keys, keeps identity when clean", () => {
  const days = { "2026-08-19": { total: 1000, apps: {} } }
  const months = { "2026-08": 1000 }
  const years = { 2026: { "2026-08-18": 1000 } }
  const clean = Model.sanitizeHistory(days, months, years)
  assert.equal(clean.days, days)
  assert.equal(clean.months, months)
  const dirty = Model.sanitizeHistory(
    { "2026-08-19": { total: 1000, apps: {} }, junk: { total: 1, apps: {} } },
    { "2026-08": 1000, nope: 5 },
    { 2026: { "2026-08-18": 1000, "2026-02-29": 5 } },
  )
  assert.deepEqual(Object.keys(dirty.days), ["2026-08-19"])
  assert.deepEqual(Object.keys(dirty.months), ["2026-08"])
  assert.deepEqual(Object.keys(dirty.years[2026]), ["2026-08-18"])
})

test("sanitizeYears drops wrong-year days and proto keys", () => {
  const out = Model.sanitizeYears({
    2026: { "2026-08-18": 1000, "2025-01-01": 5 },
    junk: { "2026-08-18": 1 },
  })
  assert.deepEqual(Object.keys(out), ["2026"])
  assert.deepEqual(Object.keys(out[2026]), ["2026-08-18"])
  const proto = JSON.parse('{"2026": {"__proto__": 5, "2026-08-18": 1}}')
  assert.deepEqual(Object.keys(Model.sanitizeYears(proto)[2026]), [
    "2026-08-18",
  ])
  assert.equal({}.polluted, undefined)
})

test("rollupArchive only rolls real day keys", () => {
  const out = Model.rollupArchive(
    {},
    {
      "2026-08-18": { total: 1000, apps: {} },
      junk: { total: 5, apps: {} },
    },
  )
  assert.deepEqual(Object.keys(out["2026"]), ["2026-08-18"])
  assert.equal({}.polluted, undefined)
})

test("overflow keys never parse as dates", () => {
  assert.equal(Model.prevKey("2026-13-01"), "")
})

test("leap days round-trip through every key reader", () => {
  assert.equal(Model.prevKey("2024-03-01"), "2024-02-29")
  assert.ok(Model.isDayKey("2024-02-29"))
})

test("every leap-year day round-trips through keys", () => {
  var d = new Date(2024, 0, 1)
  var count = 0
  while (d.getTime() < new Date(2025, 0, 1).getTime()) {
    const key = Model.dayKey(d)
    assert.ok(Model.isDayKey(key), key)
    count++
    d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)
  }
  assert.equal(count, 366)
})

test("v1.5.0 history loads without modification", () => {
  const days = {
    "2026-08-19": { total: 490875, apps: { zen: 313349, opencode: 148706 } },
    "2026-08-18": { total: 3600000, apps: { zen: 3600000 } },
  }
  const months = { "2026-07": 9823400 }
  const years = { 2026: { "2026-06-01": 1800000 } }
  const clean = Model.sanitizeHistory(days, months, years)
  assert.equal(clean.days, days)
  assert.equal(clean.months, months)
  assert.equal(clean.years, years)
})

test("upgrading retention preserves every millisecond", () => {
  const today = new Date()
  const days = {}
  let n = 0
  for (let back = 119; back >= 0; back--) {
    const d = new Date(
      today.getFullYear(),
      today.getMonth(),
      today.getDate() - back,
    )
    const key = Model.dayKey(d)
    const total = 3600000 + back * 1000
    days[key] = { total: total, apps: { zen: total } }
    n++
  }
  assert.equal(Object.keys(days).length, 120)
  const months = { "2026-07": 9823400 }
  const todayKey = Model.dayKey(today)
  const year = today.getFullYear()
  const years = {}
  years[year] = {}
  const before =
    Object.keys(days).reduce((t, k) => t + days[k].total, 0) + months["2026-07"]
  const ret = Model.applyRetention(days, years, todayKey, 95)
  assert.equal(ret.pruned, true)
  const afterDays = Object.keys(ret.days).reduce(
    (t, k) => t + ret.days[k].total,
    0,
  )
  const afterArchive = Object.keys(ret.years[year]).reduce(
    (t, k) => t + ret.years[year][k],
    0,
  )
  assert.equal(afterDays + afterArchive + months["2026-07"], before)
  // Nothing older than the window survives as day detail.
  const cutoff = Model.dayKey(
    new Date(today.getFullYear(), today.getMonth(), today.getDate() - 94),
  )
  for (const k of Object.keys(ret.days)) assert.ok(k >= cutoff, k)
})
