// #3: history.json's own version, unknown fields kept, never writing a file
// this version can't keep whole, and the (empty) migration chain.
const test = require("node:test")
const assert = require("node:assert")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")
const Model = require("../js/Model.js")

const ctx = vm.createContext({})
vm.runInContext(
  fs.readFileSync(path.join(__dirname, "..", "js", "State.js"), "utf8"),
  ctx,
)

test("the history schema is 1; absent or 0 is agx 1.6.2 / Kanso 0.9's format", () => {
  assert.equal(Model.HISTORY_SCHEMA, 1)
  const old = {
    days: { "2026-10-06": { total: 5, apps: { a: 5 } } },
    months: {},
    years: {},
  }
  assert.equal(Model.migrateHistory(old, 0), old) // empty chain: the same object
  assert.equal(Model.migrateHistory(old, undefined), old)
  assert.equal(Model.migrateHistory(old, 1), old)
})

test("a file this version can write: none, its own keys, older schemas", () => {
  assert.equal(Model.historyWriteBlock(null), "")
  assert.equal(Model.historyWriteBlock({ days: {}, months: {}, years: {} }), "")
  assert.equal(
    Model.historyWriteBlock({ schema: 1, days: {}, ext: { x: 1 } }),
    "",
  )
  assert.equal(Model.historyWriteBlock({ schema: 0, days: {} }), "")
})

test("never writes a newer file, or one with top-level keys it can't keep", () => {
  assert.equal(
    Model.historyWriteBlock({ schema: 2, days: {} }),
    "history.json is from a newer Kanso (schema 2)",
  )
  assert.equal(
    Model.historyWriteBlock({ days: {}, goals: {}, notes: [] }),
    "history.json has fields this Kanso can't keep (goals, notes)",
  )
})

test("parseHistoryText: the top-level object, else null", () => {
  assert.deepEqual(Model.parseHistoryText('{"schema": 1, "days": {}}'), {
    schema: 1,
    days: {},
  })
  for (const bad of ["", "{oops", "[1, 2]", "null", undefined]) {
    assert.equal(Model.parseHistoryText(bad), null, String(bad))
  }
})

test("a day's unknown fields survive copyDay, a sanitize rebuild and every accrual", () => {
  const hours = new Array(24).fill(0)
  hours[9] = 60000
  const day = {
    total: 60000,
    apps: { a: 60000 },
    hours: hours,
    note: "kept",
    tags: ["x"],
  }
  const copy = Model.copyDay(day)
  assert.equal(copy.note, "kept")
  assert.deepEqual(copy.tags, ["x"])
  // a rebuilt day (a bad app value) keeps them too
  const bad = Object.assign({}, day, { apps: { a: 60000, b: "nope" } })
  const clean = Model.sanitizeDay(bad)
  assert.equal(clean.changed, true)
  assert.equal(clean.day.note, "kept")
  assert.equal(clean.day.apps.b, undefined)
  // accrual (State.addSpan): a fresh object, the input untouched, fields kept
  const t = new Date(2026, 9, 7, 10, 0).getTime()
  const grown = ctx.addSpan(copy, "a", t, t + 60000, Model)
  assert.notEqual(grown, copy)
  assert.equal(grown.note, "kept")
  assert.equal(grown.total, 120000)
  assert.equal(copy.total, 60000)
  // never smuggle a prototype key through
  const proto = JSON.parse(
    '{"total": 1, "apps": {}, "__proto__": {"polluted": true}}',
  )
  assert.equal(Model.copyDay(proto).polluted, undefined)
})
