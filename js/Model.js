// Pure JS helpers for the screen-time tracker: day keys, canonical app
// names, the ignore list, history sanitizing and retention. No Qt imports
// here so the functions stay testable in isolation.

function pad2(n) {
  n = Math.floor(n)
  return n < 10 ? "0" + n : String(n)
}

// Fold browser binaries and worker comms into one canonical app key.
// QML mirrors js/browser_aliases.json as a literal (no sync file reads);
// a test asserts the mirror matches, so update both together.
var BROWSER_ALIASES = (function () {
  if (typeof module !== "undefined" && module && module.exports)
    return require("./browser_aliases.json")
  return qmlBrowserAliases()
})()

// QML mirror of js/browser_aliases.json. Keep in sync with that file;
// tests/model.test.js fails if this lags a change.
function qmlBrowserAliases() {
  return {
    "zen-bin": "zen",
    zen_browser: "zen",
    zen: "zen",
    firefox: "firefox",
    librewolf: "librewolf",
    waterfox: "waterfox",
    "tor-browser": "tor-browser",
    "mullvad-browser": "mullvad-browser",
    "google-chrome": "google-chrome",
    chrome: "google-chrome",
    chromium: "chromium",
    brave: "brave",
    "brave-browser": "brave",
    vivaldi: "vivaldi",
    "microsoft-edge": "microsoft-edge",
    edge: "microsoft-edge",
  }
}

var CHROMIUM_WEB_APP_RE =
  /^((?:chrome|chromium|brave|msedge|vivaldi)-([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?))(__.*-(?:Default|Profile_[0-9]+))?$/i

// Map any app name to its canonical tracking key. Unknown names pass
// through unchanged so non-browser apps keep their own identity.
function canonicalApp(name) {
  if (!name) return ""
  var key = String(name)
  if (
    BROWSER_ALIASES &&
    Object.prototype.hasOwnProperty.call(BROWSER_ALIASES, key)
  )
    return BROWSER_ALIASES[key]
  var webApp = key.match(CHROMIUM_WEB_APP_RE)
  if (webApp && webApp[3]) return webApp[1]
  return key
}

// Display label: Chromium windows fold to hostname, reverse-DNS IDs to the
// last segment, binaries pass through. Steam classes arrive pre-resolved
// by python/resolve_app.py. Never touches the filesystem.
function displayName(app) {
  if (!app) return ""
  var s = String(app)

  var webApp = s.match(CHROMIUM_WEB_APP_RE)
  if (webApp) return webApp[2].toLowerCase()

  if (!/^(?:[a-z][a-z0-9-]*\.){2,}[a-z0-9_-]+$/i.test(s)) return s.toLowerCase()
  var last = s.split(".").pop()
  if (!last) return s.toLowerCase()
  return last.charAt(0).toLowerCase() + last.slice(1).toLowerCase()
}

// Ignore list: an array or a comma string, lowercased and deduped.
function parseIgnoredApps(value) {
  var raw = []
  if (Array.isArray(value)) raw = value
  else if (typeof value === "string") raw = value.split(",")
  var list = []
  for (var i = 0; i < raw.length; i++) {
    var app = String(raw[i] || "")
      .trim()
      .toLowerCase()
    if (app && list.indexOf(app) === -1) list.push(app)
  }
  return list
}

// True when name matches the ignore list as raw, canonical or display
// name, so "zen-bin" is caught by an entry for "zen" and vice versa.
function isIgnoredApp(name, ignoredList) {
  if (!name || !ignoredList || ignoredList.length === 0) return false
  var candidates = [
    String(name).trim().toLowerCase(),
    String(canonicalApp(name)).toLowerCase(),
    String(displayName(name)).toLowerCase(),
  ]
  for (var i = 0; i < candidates.length; i++) {
    if (candidates[i] && ignoredList.indexOf(candidates[i]) !== -1) return true
  }
  return false
}

// The engine's card schema this plugin paints (python/screen_time.py SCHEMA).
var CARD_SCHEMA = 11

// One engine run's stdout -> the card to show. Only a JSON object on the last
// line, with this schema, a state and a today object, is a card; anything
// else keeps the last good card (D14), so a bad run never blanks the card.
function acceptCard(prev, raw) {
  var text = String(raw === undefined || raw === null ? "" : raw).trim()
  var lines = text.split("\n")
  var doc = null
  try {
    doc = JSON.parse(lines[lines.length - 1])
  } catch (e) {
    doc = null
  }
  var ok =
    isPlainObject(doc) &&
    doc.schema === CARD_SCHEMA &&
    typeof doc.state === "string" &&
    isPlainObject(doc.today)
  if (ok) return { card: doc, ok: true, error: "" }
  var error =
    text === ""
      ? "the engine printed nothing"
      : "the engine printed no card it could read"
  return { card: prev || null, ok: false, error: error }
}

// Distinct app keys stored across days ({ key: { apps } }), for the status IPC.
function storedKeyCount(days) {
  var seen = {}
  var n = 0
  for (var d in days || {}) {
    var apps = days[d] && isPlainObject(days[d].apps) ? days[d].apps : {}
    for (var k in apps) {
      if (!Object.prototype.hasOwnProperty.call(seen, k)) {
        seen[k] = true
        n++
      }
    }
  }
  return n
}

// Next ignore state from one names.json event. kind is "loaded" (text is
// the file), "missing" (no file: no ignores) or "failed" (unreadable).
// A file that cannot be read or parsed keeps the last good list, so an
// editor's half-written save never starts counting an ignored app, and it
// warns once per failure streak, not on every reload.
function applyNamesFile(prev, kind, text) {
  var last = prev && Array.isArray(prev.ignore) ? prev.ignore : []
  var warned = !!(prev && prev.warned)
  if (kind === "missing") return { ignore: [], warned: false, warn: "" }
  var problem = ""
  var doc = null
  if (kind === "loaded") {
    try {
      doc = JSON.parse(String(text))
    } catch (e) {
      problem = "is not valid JSON"
    }
    if (!problem && !isPlainObject(doc)) problem = "is not a JSON object"
    if (!problem && doc.ignore !== undefined && !Array.isArray(doc.ignore))
      problem = '"ignore" is not a list'
  } else {
    problem = "could not be read"
  }
  if (!problem) {
    var list = doc.ignore === undefined ? [] : parseIgnoredApps(doc.ignore)
    return { ignore: list, warned: false, warn: "" }
  }
  var warn = warned
    ? ""
    : "names.json " + problem + "; keeping the last good ignore list"
  return { ignore: last, warned: true, warn: warn }
}

// Parse a day key into a local Date, or null when malformed or rolled
// over ("2026-02-30" is not a date). Every key reader below funnels
// through here instead of trusting the Date constructor's rollover.
function keyToDate(key) {
  var p = String(key || "").split("-")
  if (p.length !== 3) return null
  var y = Number(p[0])
  var m = Number(p[1])
  var d = Number(p[2])
  if (!isFinite(y) || !isFinite(m) || !isFinite(d)) return null
  var dt = new Date(y, m - 1, d)
  if (dt.getFullYear() !== y || dt.getMonth() !== m - 1 || dt.getDate() !== d)
    return null
  return dt
}

// True for real padded calendar days ("2026-08-19"). Rolled-over
// overflow ("2026-02-30", "2026-13-01") and garbage fail, so unpadded
// keys never mis-compare against padded ones downstream.
function isDayKey(key) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(key || ""))) return false
  var p = String(key).split("-")
  var y = Number(p[0])
  var m = Number(p[1])
  var d = Number(p[2])
  if (m < 1 || m > 12 || d < 1 || d > 31) return false
  var dt = new Date(y, m - 1, d)
  return dt.getFullYear() === y && dt.getMonth() === m - 1 && dt.getDate() === d
}

// True for real calendar months ("2026-08").
function isMonthKey(key) {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(String(key || ""))
}

// Malformed history sections fall back to empty; arrays are rejected.
function isPlainObject(v) {
  return !!v && typeof v === "object" && !Array.isArray(v)
}

function sanitizeHistory(days, months, years) {
  var cleanDays = isPlainObject(days) ? days : {}
  var cleanMonths = isPlainObject(months) ? months : {}
  var rebuilt = false
  var out = {}
  for (var k in cleanDays) {
    if (!Object.prototype.hasOwnProperty.call(cleanDays, k)) continue
    // Malformed keys ("junk", "__proto__", unpadded dates) never survive
    // a load; downstream lexicographic compares assume real day keys.
    if (!isDayKey(k)) {
      rebuilt = true
      continue
    }
    var fixed = sanitizeDay(cleanDays[k])
    out[k] = fixed.day
    if (fixed.changed) rebuilt = true
  }
  var mout = {}
  for (var mk in cleanMonths) {
    if (!Object.prototype.hasOwnProperty.call(cleanMonths, mk)) continue
    if (!isMonthKey(mk)) {
      rebuilt = true
      continue
    }
    mout[mk] = cleanMonths[mk]
  }
  return {
    days: rebuilt ? out : cleanDays,
    months: rebuilt ? mout : cleanMonths,
    years: sanitizeYears(years),
  }
}

// Returns { day, changed }; unchanged days keep object identity.
function sanitizeDay(d) {
  if (!isPlainObject(d)) return { day: newDay(), changed: true }
  var total = Number(d.total) || 0
  if (!isFinite(total) || total < 0) total = 0
  var apps = isPlainObject(d.apps) ? d.apps : {}
  var cleanApps = {}
  var appsChanged = apps !== d.apps
  for (var app in apps) {
    if (!Object.prototype.hasOwnProperty.call(apps, app)) continue
    if (app === "__proto__") {
      appsChanged = true
      continue
    }
    var ms = Number(apps[app])
    if (isFinite(ms) && ms >= 0) cleanApps[app] = ms
    else appsChanged = true
  }
  // D42: hours are optional; malformed ones are dropped (the day stays valid)
  var hoursOk = d.hours === undefined || isHours(d.hours)
  if (total === d.total && !appsChanged && hoursOk)
    return { day: d, changed: false }
  var out = { total: total, apps: cleanApps }
  var hours = hoursArray(d.hours)
  if (hours) out.hours = hours
  return { day: out, changed: true }
}

// The year archive maps "YYYY" to { "YYYY-MM-DD": ms }. Returns the input
// untouched when nothing is discarded so callers can detect malformed data
// by identity; rebuilds a clean object when entries are dropped.
function sanitizeYears(years) {
  if (!isPlainObject(years)) return {}
  var rebuilt = false
  var out = {}
  for (var yk in years) {
    if (!Object.prototype.hasOwnProperty.call(years, yk)) continue
    if (!/^\d{4}$/.test(String(yk)) || !isPlainObject(years[yk])) {
      rebuilt = true
      continue
    }
    var day = {}
    var dayChanged = false
    for (var dk in years[yk]) {
      if (!Object.prototype.hasOwnProperty.call(years[yk], dk)) continue
      // Day keys must be real dates of their own year; anything else
      // (including "__proto__") is discarded, never assigned.
      if (!isDayKey(dk) || String(dk).slice(0, 4) !== String(yk)) {
        dayChanged = true
        continue
      }
      var v = Number(years[yk][dk])
      if (isFinite(v) && v >= 0) day[dk] = v
      else dayChanged = true
    }
    if (dayChanged) rebuilt = true
    out[yk] = day
  }
  if (!rebuilt) return years
  return out
}

// Local-time calendar key, e.g. "2026-08-13". Anything without a real
// calendar date yields "" rather than throwing.
function dayKey(date) {
  if (!date || typeof date.getTime !== "function" || isNaN(date.getTime()))
    return ""
  return (
    date.getFullYear() +
    "-" +
    pad2(date.getMonth() + 1) +
    "-" +
    pad2(date.getDate())
  )
}

// D42: a new day records its hours from the start, so it can be whole.
function newDay() {
  return {
    total: 0,
    apps: {},
    hours: [
      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    ],
  }
}

// A stored day as the live today: its total, apps and (when it has them) hours.
function copyDay(prev) {
  var out = { total: prev.total || 0, apps: Object.assign({}, prev.apps || {}) }
  var hours = hoursArray(prev.hours)
  if (hours) out.hours = hours
  return out
}

// D46: THE hours check. A day's hours as a plain JS array copy, or null. It
// takes a real Array or any array-like of length 24 (QML's JsonAdapter hands a
// loaded array over as a sequence, which Array.isArray rejects: the 2026-10-07
// restart dropped every day's hours that way) of finite numbers >= 0.
function hoursArray(h) {
  if (h === null || h === undefined || typeof h !== "object") return null
  if (Number(h.length) !== 24) return null
  var out = []
  for (var i = 0; i < 24; i++) {
    var v = h[i]
    if (typeof v !== "number" || !isFinite(v) || v < 0) return null
    out.push(v)
  }
  return out
}

// D42: 24 finite, non-negative ms totals (D46: any array-like hoursArray takes).
function isHours(h) {
  return hoursArray(h) !== null
}

function prevKey(key) {
  var d = keyToDate(key)
  if (!d) return ""
  d.setDate(d.getDate() - 1)
  return dayKey(d)
}

// Prune past keepDays (ISO keys compare lexicographically); unchanged
// input returns by identity. Absurd windows (Infinity) prune nothing
// instead of hanging the cutoff loop.
function pruneDays(days, todayKey, keepDays) {
  if (!days || !(keepDays >= 1) || !isFinite(keepDays)) return days
  var cutoff = todayKey
  for (var i = 1; i < keepDays; i++) cutoff = prevKey(cutoff)
  var out = {}
  var changed = false
  for (var k in days) {
    if (k >= cutoff) out[k] = days[k]
    else changed = true
  }
  return changed ? out : days
}

// 12 monthly totals merging raw days, month lumps and archive.
function numMs(v) {
  var n = Number(v)
  return isFinite(n) && n > 0 ? n : 0
}

// Rolls days dropped by the retention window into the perpetual per-day
// archive. Only the total survives — the app breakdown never leaves the
// 365-day detail window.
function rollupArchive(years, prunedDays) {
  if (!prunedDays) return years || {}
  var out = Object.assign({}, years || {})
  for (var dk in prunedDays) {
    if (!Object.prototype.hasOwnProperty.call(prunedDays, dk)) continue
    // Only real day keys roll up; anything else (including "__proto__")
    // would corrupt the archive object.
    if (!isDayKey(dk)) continue
    var d = prunedDays[dk]
    // String totals (from corrupt history) must archive as numbers.
    var total = numMs(d && d.total)
    if (total <= 0) continue
    var parts = String(dk).split("-")
    if (parts.length !== 3) continue
    var yearObj = out[parts[0]] ? Object.assign({}, out[parts[0]]) : {}
    yearObj[dk] = total
    out[parts[0]] = yearObj
  }
  return out
}

// Prune old days into the archive; unchanged inputs return by identity.
// The archive grows forever, so every recorded year keeps its day totals.
function applyRetention(days, years, todayKey, keepDays) {
  var kept = pruneDays(days, todayKey, keepDays)
  if (kept === days) return { days: days, years: years, pruned: false }
  var pruned = {}
  for (var k in days) {
    if (
      Object.prototype.hasOwnProperty.call(days, k) &&
      !Object.prototype.hasOwnProperty.call(kept, k)
    )
      pruned[k] = days[k]
  }
  return {
    days: kept,
    years: rollupArchive(years, pruned),
    pruned: true,
  }
}

// ---- D20: per-app detail kept forever ---------------------------------------
// history.json (rewritten every few seconds) holds the keepDays window only.
// Older days, apps and all, go to archive/<year>.json through
// python/archive_days.py, and leave history.json only once the archive has
// confirmed them: a crash at any point leaves a day in history.json, in the
// archive, or in both (readers count it once), never in neither.

// {keep, out}: `out` is every valid day older than the window, with its apps.
function rolloutDays(days, todayKey, keepDays) {
  var keep = pruneDays(days, todayKey, keepDays)
  var out = {}
  if (keep !== days) {
    for (var k in days) {
      if (
        Object.prototype.hasOwnProperty.call(days, k) &&
        !Object.prototype.hasOwnProperty.call(keep, k) &&
        isDayKey(k)
      )
        out[k] = days[k]
    }
  }
  return { keep: keep, out: out }
}

// Whether this save should start the archiver: only when days have rolled
// out, no archive run is in flight, and none failed earlier today (a failure
// waits for the next day or the next start, never a retry every save).
function shouldArchive(out, inFlight, failedOn, todayKey) {
  if (inFlight || failedOn === todayKey) return false
  for (var k in out) {
    if (Object.prototype.hasOwnProperty.call(out, k)) return true
  }
  return false
}

// The day keys archive_days.py confirmed (its last stdout line), or [].
function parseArchived(text) {
  var lines = String(text || "")
    .trim()
    .split("\n")
  try {
    var doc = JSON.parse(lines[lines.length - 1])
    var keys = doc && doc.archived
    return Array.isArray(keys) ? keys.filter(isDayKey) : []
  } catch (e) {
    return []
  }
}

// days without the confirmed keys (a fresh object; untouched when none match).
function confirmArchived(days, keys) {
  var out = {}
  var changed = false
  var gone = {}
  for (var i = 0; i < keys.length; i++) gone[keys[i]] = true
  for (var k in days) {
    if (!Object.prototype.hasOwnProperty.call(days, k)) continue
    if (gone[k]) changed = true
    else out[k] = days[k]
  }
  return changed ? out : days
}

// Node-style exports only so `node --test` can drive these pure functions;
// QML's JS engine never defines `module`, so this guard is inert there.
if (typeof module !== "undefined" && module && module.exports) {
  module.exports = {
    pad2: pad2,
    qmlBrowserAliases: qmlBrowserAliases,
    canonicalApp: canonicalApp,
    displayName: displayName,
    parseIgnoredApps: parseIgnoredApps,
    isIgnoredApp: isIgnoredApp,
    applyNamesFile: applyNamesFile,
    acceptCard: acceptCard,
    storedKeyCount: storedKeyCount,
    CARD_SCHEMA: CARD_SCHEMA,
    isDayKey: isDayKey,
    isMonthKey: isMonthKey,
    keyToDate: keyToDate,
    sanitizeHistory: sanitizeHistory,
    sanitizeDay: sanitizeDay,
    numMs: numMs,
    dayKey: dayKey,
    newDay: newDay,
    copyDay: copyDay,
    isHours: isHours,
    hoursArray: hoursArray,
    prevKey: prevKey,
    pruneDays: pruneDays,
    rolloutDays: rolloutDays,
    shouldArchive: shouldArchive,
    parseArchived: parseArchived,
    confirmArchived: confirmArchived,
    applyRetention: applyRetention,
    sanitizeYears: sanitizeYears,
    rollupArchive: rollupArchive,
  }
}
