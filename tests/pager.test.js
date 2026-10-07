"use strict"

// js/Pager.js, the card's past pages (V1): only the latest request paints, the
// last good page stays until it does, one run at a time, closing forgets all.

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")

const Pager = require(process.env.SCREEN_TIME_PAGER || "../js/Pager.js")

// What `card --page kind:key` prints for a page (the engine's shape).
function out(page, key, extra) {
  return JSON.stringify(
    Object.assign(
      {
        schema: 11,
        state: "ok",
        message: "",
        as_of: "2026-10-06T12:00:00",
        kind: Pager.engineKind(page),
        key: key,
        page: { key: key, total_text: "1h" },
      },
      extra || {},
    ),
  )
}

function ask(state, page, key, current) {
  return Pager.request(
    state,
    page,
    key,
    current === undefined ? "NOW" : current,
  )
}

test("the current page needs no run: the card already holds it", () => {
  let s = Pager.initial()
  const r = ask(s, "week", "NOW")
  assert.equal(r.spawn, null)
  assert.equal(r.state.shown.week, null)
  assert.equal(ask(s, "week", null).spawn, null)
})

test("a past page is one run, and paints when it ends", () => {
  let r = ask(Pager.initial(), "month", "2026-09")
  assert.deepEqual(r.spawn, { seq: 1, page: "month", key: "2026-09" })
  assert.equal(r.state.shown.month, null) // the current one stays until it comes
  r = Pager.finished(r.state, "a warning\n" + out("month", "2026-09"))
  assert.deepEqual(r.state.shown.month, { key: "2026-09", total_text: "1h" })
  assert.equal(r.state.running, null)
  assert.equal(r.spawn, null)
})

test("a stale page never paints over a newer request", () => {
  let r = ask(Pager.initial(), "week", "2026-09-28")
  const first = r.spawn
  r = ask(r.state, "week", "2026-09-21") // a second click while the first runs
  assert.equal(r.spawn, null, "one run at a time: the second waits")
  r = Pager.finished(r.state, out("week", first.key))
  assert.equal(r.state.shown.week, null, "the first run's page is dropped")
  assert.deepEqual(r.spawn, { seq: 2, page: "week", key: "2026-09-21" })
  r = Pager.finished(r.state, out("week", "2026-09-21"))
  assert.equal(r.state.shown.week.key, "2026-09-21")
})

test("going back to the current page drops a run still out", () => {
  assert.equal(ask(Pager.initial(), "day", "2026-10-05").spawn, null) // the tab is "today"
  let r = ask(Pager.initial(), "today", "2026-10-05")
  assert.ok(r.spawn)
  r = ask(r.state, "today", "NOW")
  assert.equal(r.state.queued, null)
  r = Pager.finished(r.state, out("today", "2026-10-05"))
  assert.equal(r.state.shown.today, null)
  assert.equal(r.spawn, null)
})

test("a page asked for on another tab drops the first tab's run", () => {
  let r = ask(Pager.initial(), "week", "2026-09-28")
  r = ask(r.state, "year", "2025")
  r = Pager.finished(r.state, out("week", "2026-09-28"))
  assert.equal(r.state.shown.week, null)
  r = Pager.finished(r.state, out("year", "2025"))
  assert.equal(r.state.shown.year.key, "2025")
})

test("closing the card forgets every past page; a run still out paints nothing", () => {
  let r = ask(Pager.initial(), "year", "2025")
  r = Pager.finished(r.state, out("year", "2025"))
  r = ask(r.state, "week", "2026-09-28")
  let s = Pager.reset(r.state)
  assert.deepEqual(s.shown, {
    today: null,
    week: null,
    month: null,
    year: null,
  })
  r = Pager.finished(s, out("week", "2026-09-28"))
  assert.equal(r.state.shown.week, null)
  assert.equal(r.state.failed, "")
})

test("a failed run keeps the page shown and marks its tab failed", () => {
  let r = ask(Pager.initial(), "month", "2026-08")
  r = Pager.finished(r.state, out("month", "2026-08"))
  for (const raw of [
    "",
    "{oops",
    "[1]",
    out("month", "2026-07", { state: "error", message: "Not a page" }),
    out("month", "2026-07", { schema: 9 }),
    out("month", "2026-06"), // another key than the one asked for
    out("week", "2026-07"), // another kind
    out("month", "2026-07", { page: null }),
  ]) {
    let f = ask(r.state, "month", "2026-07")
    f = Pager.finished(f.state, raw)
    assert.equal(f.state.shown.month.key, "2026-08", raw)
    assert.equal(f.state.failed, "month", raw)
    // the next request clears it
    assert.equal(ask(f.state, "month", "2026-07").state.failed, "", raw)
  }
})

test("never two runs at once, and every request is answered", () => {
  // a seeded random walk of clicks and run ends: whenever a page paints it is
  // the latest request's, and a spawn only ever happens with nothing running
  let seed = 7
  const rand = (n) => {
    seed = (seed * 1103515245 + 12345) % 2147483648
    return seed % n
  }
  const keys = {
    today: ["d1", "d2"],
    week: ["w1", "w2"],
    month: ["m1"],
    year: ["y1", "y2"],
  }
  let s = Pager.initial()
  let running = null
  let latest = null
  for (let step = 0; step < 4000; step++) {
    if (running && rand(3) === 0) {
      const r = Pager.finished(s, out(running.page, running.key))
      for (const page of Pager.PAGES) {
        const before = s.shown[page]
        const after = r.state.shown[page]
        if (after && after !== before) {
          assert.equal(running.seq, latest.seq, "a stale run painted")
          assert.equal(after.key, latest.key)
        }
      }
      s = r.state
      running = r.spawn
    } else {
      const page = Pager.PAGES[rand(4)]
      const key = rand(5) === 0 ? "NOW" : keys[page][rand(keys[page].length)]
      const r = ask(s, page, key)
      s = r.state
      latest = { seq: s.seq, page: page, key: key }
      if (r.spawn) {
        assert.equal(running, null, "a second run while one is out")
        running = r.spawn
      }
    }
    assert.equal(s.running === null, running === null)
  }
})

test("every Pager function the QML calls exists", () => {
  const qml = fs
    .readdirSync(path.join(__dirname, "..", "qml"))
    .map((f) => fs.readFileSync(path.join(__dirname, "..", "qml", f), "utf8"))
    .join("\n")
  const used = new Set(
    (qml.match(/\bPager\.(\w+)/g) || [])
      .map((m) => m.slice(6))
      .filter((m) => m !== "js"), // "js/Pager.js" in a comment
  )
  assert.ok(used.size >= 5, [...used].join(" "))
  for (const name of used) assert.ok(name in Pager, name)
})

test("the page schema is the engine's", () => {
  const engine = fs.readFileSync(
    path.join(__dirname, "..", "python", "screen_time.py"),
    "utf8",
  )
  assert.match(engine, new RegExp("^SCHEMA = " + Pager.PAGE_SCHEMA + "$", "m"))
})
