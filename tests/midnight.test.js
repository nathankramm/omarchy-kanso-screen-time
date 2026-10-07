"use strict"

// D18: midnight rollover under QML conditions.
//
// In QML, State.js's module-level `Model` is always null (its `var Model`
// shadows the importer's namespace), so every State function must take the
// model from the service state (`stateModel`). The 2026-10-06 00:00 journal
// showed six `State.js[224] ... 'newDay' of null` TypeErrors, 5 s apart:
// advanceRollover handed rolloverIfNeeded the fresh object closeActiveBucket
// returned, which has no stateModel. Each throw aborted the heartbeat before
// `lastTick = now`, so the seventh tick saw a gap over suspendGapMs, took the
// suspend branch and dropped the open bucket; only then (no bucket, so
// closeActiveBucket returned the service itself) did the rollover succeed.
//
// This file loads State.js the way QML does (a vm context with no `module`,
// so `Model` is null) and drives a copy of Service.qml's heartbeat with a
// fake clock. No live files are read or written.

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")
const Model = require("../js/Model.js")

// SCREEN_TIME_STATE_JS lets a planted copy run against these tests.
const STATE_JS =
  process.env.SCREEN_TIME_STATE_JS ||
  path.join(__dirname, "..", "js", "State.js")

function loadStateLikeQml() {
  const ctx = vm.createContext({})
  vm.runInContext(fs.readFileSync(STATE_JS, "utf8"), ctx, {
    filename: STATE_JS,
  })
  return ctx
}

const GAP_MS = 30 * 1000 // Service.qml suspendGapMs
const TICK_MS = 5000 // Service.qml heartbeatTimer.interval

function at(h, m, s, day) {
  return new Date(2025, 5, day || 10, h, m, s).getTime()
}

// Service.qml's tracking core, transcribed: applyState, persist (without
// retention), commitElapsed, rolloverIfNeeded, switchActive (one tracked app,
// always the same) and the heartbeat. A throw inside the heartbeat aborts the
// rest of the handler, as a QML signal handler does, and is recorded.
function makeService(S, app, openAt) {
  const svc = {
    stateModel: Model,
    todayKey: Model.dayKey(new Date(openAt)),
    today: Model.newDay(),
    days: {},
    activeApp: app,
    activeStart: openAt,
    lastTick: openAt,
    errors: [],
    rollovers: 0,
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
    ]) {
      if (p[k] !== undefined) svc[k] = p[k]
    }
  }
  function persist() {
    const merged = Object.assign({}, svc.days)
    merged[svc.todayKey] = svc.today
    svc.days = merged
  }
  function rolloverIfNeeded(now) {
    const key = Model.dayKey(new Date(now))
    const patch = S.advanceRollover(svc, now, key, GAP_MS, svc.lastTick)
    if (!patch) return
    svc.rollovers++
    applyState(patch)
    persist()
  }
  function commitElapsed(now) {
    if (!svc.activeApp || !svc.activeStart) return
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
  }
  function switchActive(now) {
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
    persist()
    svc.activeApp = app
    svc.activeStart = now
  }
  svc.heartbeat = function (now) {
    try {
      if (S.isSuspendGap(now, svc.lastTick, GAP_MS)) {
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
        rolloverIfNeeded(now)
        persist()
        switchActive(now)
      } else {
        rolloverIfNeeded(now)
        commitElapsed(now)
        persist()
      }
      svc.lastTick = now
    } catch (e) {
      svc.errors.push(
        new Date(now).toTimeString().slice(0, 8) + " " + e.message,
      )
    }
  }
  return svc
}

// Objects made inside the vm context have its own Object prototype, which
// deepStrictEqual would compare; compare their plain JSON values instead.
function plain(x) {
  return JSON.parse(JSON.stringify(x))
}

// D42: 24 hour slots, `ms` at each given hour
function hours(at) {
  const h = new Array(24).fill(0)
  for (const [i, ms] of Object.entries(at)) h[i] = ms
  return h
}

function runAcrossMidnight(S, openAt, lastAt) {
  const svc = makeService(S, "editor", openAt)
  for (let t = openAt + TICK_MS; t <= lastAt; t += TICK_MS) svc.heartbeat(t)
  return svc
}

test("D18 harness: under QML conditions State.js's own Model is null", () => {
  const S = loadStateLikeQml()
  assert.equal(S.Model, null)
  assert.equal(typeof S.advanceRollover, "function")
})

test("D18 midnight: every second counted once, on its own side of 00:00:00", () => {
  const S = loadStateLikeQml()
  // Open at 23:59:00; ticks at 23:59:05 ... 23:59:55, 00:00:00, 00:00:05 ... 00:01:00.
  const svc = runAcrossMidnight(S, at(23, 59, 0), at(0, 1, 0, 11))
  assert.deepEqual(svc.errors, [], "no exception on any tick")
  assert.equal(svc.rollovers, 1, "the rollover happens exactly once")
  assert.equal(svc.todayKey, "2025-06-11")
  // 23:59:00 -> 00:00:00 is the old day's; 00:00:00 -> 00:01:00 the new day's;
  // D42: in hour 23 and hour 0
  assert.deepEqual(plain(svc.days["2025-06-10"]), {
    total: 60000,
    apps: { editor: 60000 },
    hours: hours({ 23: 60000 }),
  })
  assert.deepEqual(plain(svc.today), {
    total: 60000,
    apps: { editor: 60000 },
    hours: hours({ 0: 60000 }),
  })
  assert.deepEqual(plain(svc.days["2025-06-11"]), plain(svc.today))
})

test("D18 midnight: a tick that is not on the 5 s grid splits at 00:00:00 exactly", () => {
  const S = loadStateLikeQml()
  // The live phase: ticks at ...:58, :03, :08 (00:00:03 was the first error).
  const svc = runAcrossMidnight(S, at(23, 58, 58), at(0, 0, 58, 11))
  assert.deepEqual(svc.errors, [])
  assert.equal(svc.rollovers, 1)
  assert.equal(svc.days["2025-06-10"].total, 62000) // 23:58:58 -> 00:00:00
  assert.equal(svc.today.total, 58000) // 00:00:00 -> 00:00:58
})

test("D18 suspend across midnight: no suspended second counts on either day", () => {
  const S = loadStateLikeQml()
  // In focus from 23:57:00, ticking to 23:58:00; asleep until the 00:20:00
  // tick (a gap far over suspendGapMs); then ticking again to 00:21:00.
  const svc = makeService(S, "editor", at(23, 57, 0))
  for (let t = at(23, 57, 5); t <= at(23, 58, 0); t += TICK_MS) svc.heartbeat(t)
  for (let t = at(0, 20, 0, 11); t <= at(0, 21, 0, 11); t += TICK_MS)
    svc.heartbeat(t)
  assert.deepEqual(svc.errors, [], "no exception on any tick")
  assert.equal(svc.rollovers, 1, "the rollover happens exactly once")
  assert.equal(svc.todayKey, "2025-06-11")
  // The old day keeps exactly its pre-suspend minute, 23:57:00 -> 23:58:00.
  assert.deepEqual(plain(svc.days["2025-06-10"]), {
    total: 60000,
    apps: { editor: 60000 },
    hours: hours({ 23: 60000 }),
  })
  // The new day counts from the resume tick only, 00:20:00 -> 00:21:00.
  assert.deepEqual(plain(svc.today), {
    total: 60000,
    apps: { editor: 60000 },
    hours: hours({ 0: 60000 }),
  })
  assert.deepEqual(plain(svc.days["2025-06-11"]), plain(svc.today))
})
