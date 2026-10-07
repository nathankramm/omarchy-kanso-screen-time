"use strict"

// D42: every day records its time by local wall-clock hour.
//
// State.js is loaded the way QML loads it (a vm context: its own Model is
// null, the service hands it one) and a transcription of Service.qml's
// tracking core (heartbeat, switch, persist, restart) runs on a fake clock in
// America/Chicago, so DST days are real. No live files are read or written.
// SCREEN_TIME_STATE_JS / SCREEN_TIME_MODEL_JS let planted copies run here.

process.env.TZ = "America/Chicago"

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")

const STATE_JS =
  process.env.SCREEN_TIME_STATE_JS ||
  path.join(__dirname, "..", "js", "State.js")
const Model = require(
  process.env.SCREEN_TIME_MODEL_JS ||
    path.join(__dirname, "..", "js", "Model.js"),
)

function loadStateLikeQml() {
  const ctx = vm.createContext({})
  vm.runInContext(fs.readFileSync(STATE_JS, "utf8"), ctx, {
    filename: STATE_JS,
  })
  return ctx
}

const GAP_MS = 30 * 1000 // Service.qml suspendGapMs
const TICK_MS = 5000 // the heartbeat
const MIN = 60000
const HOUR = 3600000

// a local wall-clock time on 2025-06-<day> (or any y, m)
function at(day, h, m, s, month, year) {
  return new Date(
    year || 2025,
    month === undefined ? 5 : month,
    day,
    h,
    m || 0,
    s || 0,
  ).getTime()
}

// plain values: arrays made inside the vm context have its own prototypes
function plain(x) {
  return JSON.parse(JSON.stringify(x))
}

function sum(a) {
  return a.reduce((x, y) => x + y, 0)
}

function hours(set) {
  const h = new Array(24).fill(0)
  for (const [i, ms] of Object.entries(set)) h[i] = ms
  return h
}

// Service.qml's tracking core, transcribed: applyState, the 5 s save
// (persist), the heartbeat (commitElapsed + rollover), a focus switch
// (closeActiveBucket, then the new app opens) and a restart (load + copyDay).
function makeService(S, app, openAt) {
  const svc = {
    stateModel: Model,
    todayKey: Model.dayKey(new Date(openAt)),
    today: Model.newDay(),
    days: {},
    activeApp: app,
    activeStart: openAt,
    lastTick: openAt,
  }
  function applyState(p) {
    if (!p) return
    for (const k of [
      "today",
      "days",
      "todayKey",
      "activeApp",
      "activeStart",
      "lastTick",
    ])
      if (p[k] !== undefined) svc[k] = p[k]
  }
  svc.persist = function () {
    const merged = Object.assign({}, svc.days)
    merged[svc.todayKey] = svc.today
    svc.days = merged
  }
  svc.heartbeat = function (now) {
    const key = Model.dayKey(new Date(now))
    const patch = S.advanceRollover(svc, now, key, GAP_MS, svc.lastTick)
    if (patch) {
      applyState(patch)
      svc.persist()
    }
    if (svc.activeApp && svc.activeStart)
      applyState(
        S.commitElapsed(
          svc,
          svc.activeApp,
          svc.activeStart,
          now,
          svc.todayKey,
          GAP_MS,
          svc.lastTick,
        ),
      )
    svc.lastTick = now
    svc.persist()
  }
  svc.switchTo = function (next, now) {
    applyState(
      S.closeActiveBucket(
        svc,
        svc.activeApp,
        svc.activeStart,
        now,
        svc.todayKey,
        GAP_MS,
        svc.lastTick,
      ),
    )
    svc.activeApp = next
    svc.activeStart = next ? now : 0
    svc.lastTick = now
    svc.persist()
  }
  // the shell restarts: history.json is written, read back and sanitized,
  // and today is the stored day again (Service.qml onHistoryLoaded)
  svc.restart = function (now, app) {
    const doc = JSON.parse(JSON.stringify({ days: svc.days }))
    const clean = Model.sanitizeHistory(doc.days, {}, {})
    svc.days = clean.days
    svc.todayKey = Model.dayKey(new Date(now))
    const prev = svc.days[svc.todayKey]
    svc.today = prev ? Model.copyDay(prev) : Model.newDay()
    svc.activeApp = app
    svc.activeStart = now
    svc.lastTick = now
  }
  return svc
}

function tickThrough(svc, from, to) {
  for (let t = from; t <= to; t += TICK_MS) svc.heartbeat(t)
}

function assertWhole(day, msg) {
  assert.ok(
    Array.isArray(day.hours) && day.hours.length === 24,
    msg + ": 24 hours",
  )
  assert.equal(sum(day.hours), day.total, msg + ": sum(hours) == total")
}

test("a bucket across an hour boundary splits at hh:00:00 exactly", () => {
  const S = loadStateLikeQml()
  const svc = makeService(S, "editor", at(10, 9, 59, 0))
  tickThrough(svc, at(10, 9, 59, 5), at(10, 10, 1, 0))
  assert.deepEqual(plain(svc.today.hours), hours({ 9: MIN, 10: MIN }))
  assert.equal(svc.today.total, 2 * MIN)
  // and one long close, no ticks: 9:50 -> 11:10
  const one = S.addSpan(
    Model.newDay(),
    "editor",
    at(10, 9, 50),
    at(10, 11, 10),
    Model,
  )
  assert.deepEqual(
    plain(one.hours),
    hours({ 9: 10 * MIN, 10: HOUR, 11: 10 * MIN }),
  )
})

test("a bucket across midnight: hour 23 on the old day, hour 0 on the new", () => {
  const S = loadStateLikeQml()
  const svc = makeService(S, "editor", at(10, 23, 58, 0))
  tickThrough(svc, at(10, 23, 58, 5), at(11, 0, 2, 0))
  assert.deepEqual(plain(svc.days["2025-06-10"].hours), hours({ 23: 2 * MIN }))
  assert.deepEqual(plain(svc.today.hours), hours({ 0: 2 * MIN }))
  assertWhole(svc.days["2025-06-10"], "the old day")
  assertWhole(svc.today, "the new day")
})

test("a suspend across an hour books nothing to the suspended time", () => {
  const S = loadStateLikeQml()
  const svc = makeService(S, "editor", at(10, 9, 58, 0))
  tickThrough(svc, at(10, 9, 58, 5), at(10, 9, 59, 0))
  tickThrough(svc, at(10, 10, 20, 0), at(10, 10, 21, 0)) // asleep 9:59 -> 10:20
  assert.deepEqual(plain(svc.today.hours), hours({ 9: MIN, 10: MIN }))
  assertWhole(svc.today, "today")
})

test("a suspend across midnight books nothing to either day's suspended time", () => {
  const S = loadStateLikeQml()
  const svc = makeService(S, "editor", at(10, 23, 57, 0))
  tickThrough(svc, at(10, 23, 57, 5), at(10, 23, 58, 0))
  tickThrough(svc, at(11, 0, 20, 0), at(11, 0, 21, 0))
  assert.deepEqual(plain(svc.days["2025-06-10"].hours), hours({ 23: MIN }))
  assert.deepEqual(plain(svc.today.hours), hours({ 0: MIN }))
  assertWhole(svc.days["2025-06-10"], "the old day")
  assertWhole(svc.today, "the new day")
})

test("app switches within an hour: each app's time in the hour it fell in", () => {
  const S = loadStateLikeQml()
  const svc = makeService(S, "editor", at(10, 14, 0, 0))
  tickThrough(svc, at(10, 14, 0, 5), at(10, 14, 20, 0))
  svc.switchTo("web", at(10, 14, 20, 2))
  tickThrough(svc, at(10, 14, 20, 5), at(10, 14, 50, 0))
  svc.switchTo("editor", at(10, 14, 50, 0))
  tickThrough(svc, at(10, 14, 50, 5), at(10, 15, 10, 0))
  assert.deepEqual(plain(svc.today.hours), hours({ 14: HOUR, 15: 10 * MIN }))
  assert.deepEqual(plain(svc.today.apps), {
    editor: 40 * MIN + 2000,
    web: 30 * MIN - 2000,
  })
  assertWhole(svc.today, "today")
})

test("a restart mid-hour keeps the day's hours and goes on adding to them", () => {
  const S = loadStateLikeQml()
  let svc = makeService(S, "editor", at(10, 9, 0, 0))
  tickThrough(svc, at(10, 9, 0, 5), at(10, 9, 30, 0))
  svc.restart(at(10, 9, 31, 0), "editor") // the shell restarts at 9:31
  tickThrough(svc, at(10, 9, 31, 5), at(10, 10, 15, 0))
  assert.deepEqual(plain(svc.today.hours), hours({ 9: 59 * MIN, 10: 15 * MIN }))
  assertWhole(svc.today, "today after a restart")
})

test("a day recorded before hours existed is partial, never invented", () => {
  const S = loadStateLikeQml()
  const svc = makeService(S, "editor", at(10, 9, 0, 0))
  svc.days = { "2025-06-10": { total: 2 * HOUR, apps: { editor: 2 * HOUR } } }
  svc.restart(at(10, 9, 0, 0), "editor")
  tickThrough(svc, at(10, 9, 0, 5), at(10, 9, 10, 0))
  assert.equal(svc.today.total, 2 * HOUR + 10 * MIN)
  assert.deepEqual(plain(svc.today.hours), hours({ 9: 10 * MIN }))
  assert.ok(
    sum(svc.today.hours) < svc.today.total,
    "partial: the earlier 2h has no hour",
  )
})

test("DST fall back (Nov 2): the repeated 1 AM adds into hour 1", () => {
  const S = loadStateLikeQml()
  // 00:30 CDT to 02:30 CST is three real hours: 30m in hour 0, both 1 AMs in
  // hour 1 (2h), 30m in hour 2
  const start = at(2, 0, 30, 0, 10)
  const end = start + 3 * HOUR
  assert.equal(new Date(end).getHours(), 2)
  const d = S.addSpan(Model.newDay(), "editor", start, end, Model)
  assert.deepEqual(
    plain(d.hours),
    hours({ 0: 30 * MIN, 1: 2 * HOUR, 2: 30 * MIN }),
  )
  assertWhole(d, "the fall-back day")
  // and ticking through it, as the tracker does
  const svc = makeService(S, "editor", start)
  tickThrough(svc, start + TICK_MS, end)
  assert.deepEqual(plain(svc.today.hours), plain(d.hours))
})

test("DST spring forward (Mar 9): the skipped 2 AM stays 0", () => {
  const S = loadStateLikeQml()
  // 01:30 CST to 03:30 CDT is one real hour: 30m in hour 1, 30m in hour 3
  const start = at(9, 1, 30, 0, 2)
  const end = start + HOUR
  assert.equal(new Date(end).getHours(), 3)
  const d = S.addSpan(Model.newDay(), "editor", start, end, Model)
  assert.deepEqual(plain(d.hours), hours({ 1: 30 * MIN, 3: 30 * MIN }))
  const svc = makeService(S, "editor", start)
  tickThrough(svc, start + TICK_MS, end)
  assert.deepEqual(plain(svc.today.hours), plain(d.hours))
  assertWhole(svc.today, "the spring-forward day")
})

test("sum(hours) == total throughout a week of switches, suspends, restarts and midnights", () => {
  const S = loadStateLikeQml()
  let seed = 42
  const rand = (n) => {
    seed = (seed * 1103515245 + 12345) % 2147483648
    return seed % n
  }
  const apps = ["editor", "web", "chat"]
  let t = at(9, 8, 0, 0)
  const svc = makeService(S, "editor", t)
  const end = at(16, 8, 0, 0)
  let checks = 0
  while (t < end) {
    const r = rand(100)
    if (r < 2)
      t += (5 + rand(120)) * MIN // asleep
    else if (r < 4) {
      t += TICK_MS
      svc.restart(t, apps[rand(3)])
    } else if (r < 12) {
      t += 1000 + rand(4000)
      svc.switchTo(apps[rand(3)], t)
    } else t += TICK_MS
    svc.heartbeat(t)
    for (const [k, day] of Object.entries(svc.days)) {
      assertWhole(day, k)
      checks++
    }
  }
  assert.ok(Object.keys(svc.days).length >= 8, "a week of days")
  assert.ok(checks > 1000)
})

test("a saved day's hours survive sanitize; malformed hours are dropped, the day kept", () => {
  const day = {
    total: 2 * HOUR,
    apps: { a: 2 * HOUR },
    hours: hours({ 9: HOUR, 10: HOUR }),
  }
  const clean = Model.sanitizeHistory({ "2025-06-10": day }, {}, {})
  assert.equal(clean.days["2025-06-10"], day, "a clean day keeps its identity")
  for (const bad of [
    [1, 2],
    "x",
    hours({ 3: -1 }),
    hours({ 3: NaN }),
    hours({ 4: "5" }),
  ]) {
    const c = Model.sanitizeHistory(
      { "2025-06-10": { total: HOUR, apps: { a: HOUR }, hours: bad } },
      {},
      {},
    )
    assert.deepEqual(
      c.days["2025-06-10"],
      { total: HOUR, apps: { a: HOUR } },
      JSON.stringify(bad),
    )
  }
  assert.deepEqual(Model.copyDay(day), day)
  assert.notEqual(
    Model.copyDay(day).hours,
    day.hours,
    "a copy, not the stored array",
  )
})

test("a day rebuilt by sanitize (a junk app value) keeps its well-formed hours", () => {
  const h = hours({ 9: HOUR })
  const clean = Model.sanitizeHistory(
    { "2025-06-10": { total: HOUR, apps: { a: HOUR, b: "junk" }, hours: h } },
    {},
    {},
  )
  assert.deepEqual(plain(clean.days["2025-06-10"]), {
    total: HOUR,
    apps: { a: HOUR },
    hours: h,
  })
})

test("at midnight, time not yet saved reaches the old day with its hours", () => {
  // the 5 s save had last run at 23:58; the tracker has counted on to 23:59:30
  // and midnight comes before the next save: the rollover flushes the
  // unsaved 90 s into the old day, hours included
  const S = loadStateLikeQml()
  const svc = {
    stateModel: Model,
    todayKey: "2025-06-10",
    today: {
      total: 3 * MIN + 30000,
      apps: { editor: 3 * MIN + 30000 },
      hours: hours({ 23: 3 * MIN + 30000 }),
    },
    days: {
      "2025-06-10": {
        total: 2 * MIN,
        apps: { editor: 2 * MIN },
        hours: hours({ 23: 2 * MIN }),
      },
    },
    activeApp: "editor",
    activeStart: at(10, 23, 59, 30),
    lastTick: at(10, 23, 59, 55),
  }
  const patch = S.advanceRollover(
    svc,
    at(11, 0, 0, 0),
    "2025-06-11",
    GAP_MS,
    svc.lastTick,
  )
  const old = plain(patch.days["2025-06-10"])
  // 3m 30s counted before 23:59:30, plus the last 30 s closed at midnight
  assert.equal(old.total, 4 * MIN)
  assert.deepEqual(old.hours, hours({ 23: 4 * MIN }))
  assertWhole(old, "the old day")
})

// D46: QML's JsonAdapter hands a loaded array over as a sequence (indexes and a
// length, not an Array); every hours path must take one.
function sequence(arr) {
  const s = { length: arr.length }
  arr.forEach((v, i) => {
    s[i] = v
  })
  return s
}

test("D46: an array-like day's hours are kept by sanitize, the restart copy and every accrual", () => {
  const S = loadStateLikeQml()
  const loaded = {
    total: HOUR + 10 * MIN,
    apps: { editor: HOUR + 10 * MIN },
    hours: sequence(hours({ 9: HOUR, 10: 10 * MIN })),
  }
  assert.equal(Array.isArray(loaded.hours), false)
  assert.deepEqual(
    Model.hoursArray(loaded.hours),
    hours({ 9: HOUR, 10: 10 * MIN }),
  )
  // sanitize: a clean day keeps its identity, no "malformed" rebuild
  const clean = Model.sanitizeHistory({ "2025-06-10": loaded }, {}, {})
  assert.equal(clean.days["2025-06-10"], loaded)
  // the restart copy: a plain array
  const today = Model.copyDay(loaded)
  assert.ok(Array.isArray(today.hours))
  assert.deepEqual(today.hours, hours({ 9: HOUR, 10: 10 * MIN }))
  // an accrual straight onto the loaded record (a midnight split into a loaded
  // past day goes this way) adds to its hours
  const d = S.addSpan(loaded, "editor", at(10, 10, 10), at(10, 10, 20), Model)
  assert.deepEqual(plain(d.hours), hours({ 9: HOUR, 10: 20 * MIN }))
  assertWhole(d, "the loaded day")
  // the rollover flush: unsaved time over a loaded mirror
  const delta = S.dayMinus(d, loaded, Model)
  assert.deepEqual(plain(delta.hours), hours({ 10: 10 * MIN }))
})

test("D46: the one hours check rejects what is not 24 finite numbers >= 0", () => {
  for (const bad of [
    null,
    undefined,
    "x",
    5,
    [1, 2],
    sequence([1, 2]),
    sequence(hours({ 3: -1 })),
    sequence(hours({ 3: NaN })),
    sequence(hours({ 3: "5" })),
    { length: "24" },
  ])
    assert.equal(
      Model.hoursArray(bad),
      null,
      String(bad && JSON.stringify(bad)),
    )
})
