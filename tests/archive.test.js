"use strict"

// D20: every day's per-app detail kept forever, outside the history.json the
// tracker rewrites every few seconds.
//
// A transcription of qml/Service.qml's save path (persist, startArchive,
// onArchived, the 5 s save) drives the REAL python/archive_days.py and the
// REAL engine against a temp data dir, with a fake clock. State.js is loaded
// the way QML loads it (its own Model null) for the midnight cases. No live
// files are read or written.

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const os = require("node:os")
const path = require("node:path")
const vm = require("node:vm")
const { spawnSync } = require("node:child_process")
const Model = require("../js/Model.js")

const ROOT = path.join(__dirname, "..")
const ARCHIVER = path.join(ROOT, "python", "archive_days.py")
const ENGINE = path.join(ROOT, "python", "screen_time.py")
const KEEP_DAYS = 365
const MIN = 60000

function dayAdd(key, n) {
  const d = new Date(key + "T12:00:00")
  d.setDate(d.getDate() + n)
  return Model.dayKey(d)
}

function tracked(key, i) {
  // a made-up day: two apps, sizes varying with the day
  const a = (10 + (i % 50)) * MIN
  const b = (i % 7) * MIN
  const apps = b ? { editor: a, web: b } : { editor: a }
  return { total: a + b, apps: apps }
}

// Service.qml's save path, transcribed. `archiver(request)` returns stdout.
function makeService(dir, opts) {
  const svc = {
    dir: dir,
    days: {},
    months: {},
    years: {},
    today: Model.newDay(),
    todayKey: "",
    archiveInFlight: false,
    archiveFailedOn: "",
    archiverRuns: 0,
    saves: 0,
    pending: null, // a started archive run whose exit has not been delivered
  }
  svc.persist = function () {
    const merged = Object.assign({}, svc.days)
    merged[svc.todayKey] = svc.today
    const roll = Model.rolloutDays(merged, svc.todayKey, KEEP_DAYS)
    if (
      Model.shouldArchive(
        roll.out,
        svc.archiveInFlight,
        svc.archiveFailedOn,
        svc.todayKey,
      )
    )
      svc.startArchive(roll.out)
    svc.days = merged
    // the archiver's exit is a later event-loop callback (Process.onExited): it
    // arrives after persist() returns, never inside it
    if (svc.pending && !(opts && opts.deferExit)) svc.deliverExit()
  }
  svc.startArchive = function (out) {
    svc.archiveInFlight = true
    svc.archiverRuns++
    const request = JSON.stringify({
      dir: path.join(dir, "archive"),
      days: out,
    })
    const expected = Object.keys(out).length
    const p = spawnSync(
      "python3",
      ["-I", (opts && opts.archiver) || ARCHIVER],
      { input: request + "\n", encoding: "utf8" },
    )
    svc.pending = { text: p.stdout, expected: expected }
  }
  svc.deliverExit = function () {
    const run = svc.pending
    svc.pending = null
    svc.onArchived(run.text, run.expected)
  }
  svc.onArchived = function (text, expected) {
    svc.archiveInFlight = false
    const keys = Model.parseArchived(text)
    if (keys.length < expected) svc.archiveFailedOn = svc.todayKey
    if (keys.length === 0) return
    svc.days = Model.confirmArchived(svc.days, keys)
  }
  svc.save = function () {
    // historyFile.writeAdapter(): days, months, years as the adapter holds them
    svc.saves++
    fs.writeFileSync(
      path.join(dir, "history.json"),
      JSON.stringify({ days: svc.days, months: svc.months, years: svc.years }),
    )
  }
  svc.load = function () {
    // onHistoryLoaded (D20: no prune at load)
    const doc = JSON.parse(
      fs.readFileSync(path.join(dir, "history.json"), "utf8"),
    )
    svc.days = doc.days || {}
    svc.months = doc.months || {}
    svc.years = doc.years || {}
    const prev = svc.days[svc.todayKey]
    svc.today = prev ? Model.copyDay(prev) : Model.newDay()
  }
  return svc
}

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "kanso-d20-"))
}

function engineJson(dir, args, asOf) {
  const p = spawnSync(
    "python3",
    [
      "-I",
      ENGINE,
      ...args,
      "--history",
      path.join(dir, "history.json"),
      "--names",
      path.join(dir, "names.json"),
      "--as-of",
      asOf,
    ],
    {
      encoding: "utf8",
      env: Object.assign({}, process.env, { TZ: "America/Chicago", HOME: dir }),
    },
  )
  assert.equal(p.status, 0, p.stderr)
  return JSON.parse(p.stdout)
}

function archiveDays(dir) {
  const out = {}
  const d = path.join(dir, "archive")
  if (!fs.existsSync(d)) return out
  for (const f of fs.readdirSync(d).filter((n) => /^\d{4}\.json$/.test(n))) {
    Object.assign(
      out,
      JSON.parse(fs.readFileSync(path.join(d, f), "utf8")).days,
    )
  }
  return out
}

function sumTotals(days) {
  return Object.values(days).reduce((s, r) => s + r.total, 0)
}

// 400 tracked days ending (and including) 2026-01-15, all in history.json
function seedHistory(dir, extra) {
  const days = {}
  for (let i = 0; i < 400; i++) {
    const key = dayAdd("2026-01-15", i - 399)
    days[key] = tracked(key, i)
  }
  fs.writeFileSync(
    path.join(dir, "history.json"),
    JSON.stringify(
      Object.assign({ days: days, months: {}, years: {} }, extra || {}),
    ),
  )
  return days
}

test("D20: 400 days roll out across a year boundary, apps kept, totals unchanged", () => {
  const dir = tmpDir()
  const seeded = seedHistory(dir)
  const before = engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00")
    .all_time.total_ms
  const svc = makeService(dir)
  svc.todayKey = "2026-01-15"
  svc.load()
  svc.persist()
  svc.save()
  // 400 - 365 = 35 days, Dec 12 2024 .. Jan 15 2025: both sides of a year boundary
  const archived = archiveDays(dir)
  assert.equal(Object.keys(archived).length, 35)
  assert.deepEqual(fs.readdirSync(path.join(dir, "archive")).sort(), [
    "2024.json",
    "2025.json",
  ])
  assert.equal(Object.keys(svc.days).length, 365)
  for (const [k, rec] of Object.entries(archived))
    assert.deepEqual(rec, seeded[k], k)
  assert.equal(sumTotals(svc.days) + sumTotals(archived), sumTotals(seeded))
  // a restart reads archive + history: the engine's totals are identical
  const after = engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00")
  assert.equal(after.all_time.total_ms, before)
  // report shows per-app detail for an archived day
  const old = engineJson(
    dir,
    ["report", "--day", "2024-12-20", "--json"],
    "2026-01-15T12:00:00",
  )
  assert.deepEqual(
    old.rows.map((r) => r.name).sort(),
    Object.keys(seeded["2024-12-20"].apps).sort(),
  )
})

test("D20: the 5-second save never writes the archive; it runs once when days roll out", () => {
  const dir = tmpDir()
  seedHistory(dir)
  const svc = makeService(dir)
  svc.todayKey = "2026-01-15"
  svc.load()
  // three days, an hour of saves every 5 s each (720 a day); one new day rolls out each midnight
  for (let day = 0; day < 3; day++) {
    svc.todayKey = dayAdd("2026-01-15", day)
    if (day > 0) svc.today = Model.newDay()
    for (let s = 0; s < 720; s++) {
      svc.today = {
        total: svc.today.total + 5000,
        apps: { editor: (svc.today.apps.editor || 0) + 5000 },
      }
      svc.persist()
    }
    svc.save()
  }
  assert.equal(svc.archiverRuns, 3) // one per day with something to roll out, not per save (2,160 saves)
  assert.equal(Object.keys(svc.days).length, 365)
})

test("D20: a crash after the archive write, before the history save: no loss, no double count", () => {
  const dir = tmpDir()
  const seeded = seedHistory(dir)
  const before = engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00")
    .all_time.total_ms
  const svc = makeService(dir, { deferExit: true })
  svc.todayKey = "2026-01-15"
  svc.load()
  svc.persist() // the archiver ran and wrote archive/*.json ...
  // ... and the shell died before its exit was handled or history.json saved
  assert.equal(Object.keys(archiveDays(dir)).length, 35)
  const crashed = JSON.parse(
    fs.readFileSync(path.join(dir, "history.json"), "utf8"),
  )
  assert.equal(Object.keys(crashed.days).length, 400) // history still holds every day
  assert.equal(
    engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00").all_time
      .total_ms,
    before,
  )
  // restart: the same days roll out again; the archive is rewritten, not grown
  const firstArchive = fs.readFileSync(
    path.join(dir, "archive", "2025.json"),
    "utf8",
  )
  const again = makeService(dir)
  again.todayKey = "2026-01-15"
  again.load()
  again.persist()
  again.save()
  assert.equal(
    fs.readFileSync(path.join(dir, "archive", "2025.json"), "utf8"),
    firstArchive,
  )
  assert.equal(
    engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00").all_time
      .total_ms,
    before,
  )
  assert.equal(
    sumTotals(again.days) + sumTotals(archiveDays(dir)),
    sumTotals(seeded),
  )
})

test("D20: a crash between two year files: only the written year leaves history", () => {
  const dir = tmpDir()
  const seeded = seedHistory(dir)
  // an archiver that writes 2024.json, then dies before 2025.json
  const half = path.join(dir, "half_archiver.py")
  fs.writeFileSync(
    half,
    [
      "import json, sys",
      `sys.path.insert(0, ${JSON.stringify(path.dirname(ARCHIVER))})`,
      "import archive_days as a",
      "req = json.loads(sys.stdin.readline())",
      "days = {k: v for k, v in req['days'].items() if k.startswith('2024')}",
      "written, ok = a.archive(req['dir'], days)",
      "print(json.dumps({'archived': written}))",
      "sys.exit(9)",
    ].join("\n"),
  )
  const svc = makeService(dir, { archiver: half })
  svc.todayKey = "2026-01-15"
  svc.load()
  svc.persist()
  svc.save()
  const archived = archiveDays(dir)
  assert.ok(Object.keys(archived).every((k) => k.startsWith("2024")))
  for (const k of Object.keys(archived))
    assert.ok(!(k in svc.days), k + " left history")
  assert.ok("2025-01-15" in svc.days, "2025's days wait in history")
  assert.equal(svc.archiveFailedOn, "2026-01-15") // no retry storm today
  assert.equal(sumTotals(svc.days) + sumTotals(archived), sumTotals(seeded)) // nothing lost
  // the next day the full archiver takes the rest
  const next = makeService(dir)
  next.todayKey = "2026-01-16"
  next.load()
  next.persist()
  next.save()
  assert.ok(Object.keys(archiveDays(dir)).some((k) => k.startsWith("2025")))
})

test("D20: migration from today's format keeps legacy totals and archives the rest with apps", () => {
  const dir = tmpDir()
  const legacy = { 2023: { "2023-05-01": 2 * 60 * MIN } }
  seedHistory(dir, { years: legacy, months: { "2022-11": 5 * 60 * MIN } })
  const before = engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00")
    .all_time.total_ms
  const svc = makeService(dir)
  svc.todayKey = "2026-01-15"
  svc.load()
  svc.persist()
  svc.save()
  const saved = JSON.parse(
    fs.readFileSync(path.join(dir, "history.json"), "utf8"),
  )
  assert.deepEqual(saved.years, legacy) // the totals-only archive is kept, never grown
  assert.deepEqual(saved.months, { "2022-11": 5 * 60 * MIN })
  assert.equal(Object.keys(archiveDays(dir)).length, 35)
  assert.equal(
    engineJson(dir, ["report", "--json"], "2026-01-15T12:00:00").all_time
      .total_ms,
    before,
  )
})

// ---- midnight and suspend across midnight, with the D20 save path -------------

function loadStateLikeQml() {
  const ctx = vm.createContext({})
  vm.runInContext(
    fs.readFileSync(path.join(ROOT, "js", "State.js"), "utf8"),
    ctx,
  )
  return ctx
}

function trackedService(dir, openAt) {
  // Service's heartbeat (5 s) over the D20 persist
  const S = loadStateLikeQml()
  const svc = makeService(dir)
  Object.assign(svc, {
    stateModel: Model,
    activeApp: "editor",
    activeStart: openAt,
    lastTick: openAt,
    errors: [],
    rollovers: 0,
  })
  svc.todayKey = Model.dayKey(new Date(openAt))
  const apply = (p) => {
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
  svc.heartbeat = function (now) {
    try {
      if (S.isSuspendGap(now, svc.lastTick, 30000)) {
        apply(
          S.closeActiveBucket(
            svc,
            svc.activeApp,
            svc.activeStart,
            now,
            svc.todayKey,
            30000,
            svc.lastTick,
          ),
        )
        const p = S.advanceRollover(
          svc,
          now,
          Model.dayKey(new Date(now)),
          30000,
          svc.lastTick,
        )
        if (p) {
          svc.rollovers++
          apply(p)
          svc.persist()
        }
        svc.persist()
        svc.activeApp = "editor"
        svc.activeStart = now
      } else {
        const p = S.advanceRollover(
          svc,
          now,
          Model.dayKey(new Date(now)),
          30000,
          svc.lastTick,
        )
        if (p) {
          svc.rollovers++
          apply(p)
          svc.persist()
        }
        if (svc.activeApp && svc.activeStart)
          apply(
            S.commitElapsed(
              svc,
              svc.activeApp,
              svc.activeStart,
              now,
              svc.todayKey,
              30000,
              svc.lastTick,
            ),
          )
        svc.persist()
      }
      svc.lastTick = now
    } catch (e) {
      svc.errors.push(e.message)
    }
  }
  return svc
}

const at = (y, mo, d, h, m, s) => new Date(y, mo - 1, d, h, m, s).getTime()

test("D20: midnight with a day rolling out stays exact, and the archive runs once", () => {
  const dir = tmpDir()
  const days = {}
  for (let i = 0; i < 365; i++) {
    const key = dayAdd("2026-01-14", i - 364)
    days[key] = tracked(key, i)
  }
  fs.writeFileSync(
    path.join(dir, "history.json"),
    JSON.stringify({ days: days, months: {}, years: {} }),
  )
  const svc = trackedService(dir, at(2026, 1, 14, 23, 59, 0))
  svc.load()
  svc.today = Object.assign({}, days["2026-01-14"])
  const before14 = svc.today.total
  for (
    let t = at(2026, 1, 14, 23, 59, 5);
    t <= at(2026, 1, 15, 0, 1, 0);
    t += 5000
  )
    svc.heartbeat(t)
  svc.save()
  assert.deepEqual(svc.errors, [])
  assert.equal(svc.rollovers, 1)
  assert.equal(svc.archiverRuns, 1) // 2025-01-15 rolled out at midnight
  assert.ok("2025-01-15" in archiveDays(dir))
  assert.equal(svc.days["2026-01-14"].total, before14 + 60000) // 23:59:00 -> 00:00:00
  assert.equal(svc.today.total, 60000) // 00:00:00 -> 00:01:00
})

test("D20: suspend across midnight counts no suspended second, with the archive", () => {
  const dir = tmpDir()
  const days = {}
  for (let i = 0; i < 365; i++) {
    const key = dayAdd("2026-01-14", i - 364)
    days[key] = tracked(key, i)
  }
  fs.writeFileSync(
    path.join(dir, "history.json"),
    JSON.stringify({ days: days, months: {}, years: {} }),
  )
  const svc = trackedService(dir, at(2026, 1, 14, 23, 57, 0))
  svc.load()
  svc.today = Object.assign({}, days["2026-01-14"])
  const before14 = svc.today.total
  for (
    let t = at(2026, 1, 14, 23, 57, 5);
    t <= at(2026, 1, 14, 23, 58, 0);
    t += 5000
  )
    svc.heartbeat(t)
  for (
    let t = at(2026, 1, 15, 0, 20, 0);
    t <= at(2026, 1, 15, 0, 21, 0);
    t += 5000
  )
    svc.heartbeat(t)
  svc.save()
  assert.deepEqual(svc.errors, [])
  assert.equal(svc.rollovers, 1)
  assert.equal(svc.days["2026-01-14"].total, before14 + 60000)
  assert.equal(svc.today.total, 60000)
  assert.ok("2025-01-15" in archiveDays(dir))
  assert.equal(Object.keys(svc.days).length, 365)
})
