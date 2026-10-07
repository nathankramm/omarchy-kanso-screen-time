// The card's past pages (V1): which page each detail tab shows, and which
// `screen_time.py card --page kind:key` run may paint. Pure functions over a
// plain state; the bar widget owns the one Process and calls these. The card
// keeps the last good page while a run is out, and only the latest request
// paints: every request and every close bumps `seq`, and a run whose seq is
// not the latest is dropped when it ends.

var PAGE_SCHEMA = 11

// the card's page names; "today" is the Day page
var PAGES = ["today", "week", "month", "year"]

function engineKind(page) {
  return page === "today" ? "day" : page
}

function initial() {
  return {
    seq: 0,
    running: null, // {seq, page, key}: the run out now (at most one)
    queued: null, // the request waiting for it to end
    shown: { today: null, week: null, month: null, year: null },
    failed: "", // the page whose latest request failed
  }
}

function copy(state) {
  var shown = {}
  for (var i = 0; i < PAGES.length; i++)
    shown[PAGES[i]] = state.shown[PAGES[i]] || null
  return {
    seq: state.seq,
    running: state.running,
    queued: state.queued,
    shown: shown,
    failed: state.failed,
  }
}

// One run's stdout -> its page, or null. Only a JSON object on the last line,
// with this schema, state "ok" and exactly the kind and key asked for, is one.
function acceptPage(raw, request) {
  var text = String(raw === undefined || raw === null ? "" : raw).trim()
  var lines = text.split("\n")
  var doc = null
  try {
    doc = JSON.parse(lines[lines.length - 1])
  } catch (e) {
    doc = null
  }
  if (doc === null || typeof doc !== "object" || Array.isArray(doc)) return null
  if (doc.schema !== PAGE_SCHEMA || doc.state !== "ok") return null
  if (doc.kind !== engineKind(request.page) || doc.key !== request.key)
    return null
  if (doc.page === null || typeof doc.page !== "object") return null
  return doc.page
}

// An arrow on `page` asks for `key`. The current period's key (or none) is the
// engine's own card: no run. -> {state, spawn}: spawn is the request to start
// now, or null (none needed, or one is out and this one waits for it).
function request(state, page, key, currentKey) {
  var next = copy(state)
  next.seq = state.seq + 1
  next.failed = ""
  if (PAGES.indexOf(page) < 0) return { state: state, spawn: null }
  if (!key || key === currentKey) {
    next.shown[page] = null
    next.queued = null
    return { state: next, spawn: null }
  }
  var req = { seq: next.seq, page: page, key: String(key) }
  if (state.running) {
    next.queued = req
    return { state: next, spawn: null }
  }
  next.running = req
  return { state: next, spawn: req }
}

// The run out now has ended with `raw` on stdout. Only the latest request
// paints; a failed latest keeps the page shown and marks it failed. The
// queued request, if any, starts next.
function finished(state, raw) {
  var next = copy(state)
  var done = state.running
  next.running = null
  if (done && done.seq === state.seq) {
    var page = acceptPage(raw, done)
    if (page) next.shown[done.page] = page
    else next.failed = done.page
  }
  if (next.queued) {
    next.running = next.queued
    next.queued = null
    return { state: next, spawn: next.running }
  }
  return { state: next, spawn: null }
}

// The card closed: back to the current pages; a run still out paints nothing.
function reset(state) {
  var next = initial()
  next.seq = state.seq + 1
  next.running = state.running
  return next
}

if (typeof module !== "undefined" && module && module.exports) {
  module.exports = {
    PAGE_SCHEMA: PAGE_SCHEMA,
    PAGES: PAGES,
    engineKind: engineKind,
    initial: initial,
    acceptPage: acceptPage,
    request: request,
    finished: finished,
    reset: reset,
  }
}
