"use strict"

// Structural tests for the glyph-only bar widget. QML can't run under node,
// so these assert the wiring by source shape, like tests/service.test.js.

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")

const bar = fs.readFileSync(
  path.join(__dirname, "..", "qml", "BarWidget.qml"),
  "utf8",
)

test("ipc surface keeps every verb, and no destructive one", () => {
  for (const fn of ["open", "close", "show", "hide", "toggle", "status"]) {
    assert.match(bar, new RegExp("function " + fn + "\\("), fn + " exists")
  }
  assert.doesNotMatch(bar, /resetToday|resetAll/)
})

test("bar shows the glyph only: no number, no settings", () => {
  assert.match(bar, /readonly property string glyph: "󰔟"/)
  assert.doesNotMatch(bar, /root\.setting\(|settingBool|setSetting|Model\./)
  assert.doesNotMatch(bar, /barLabel|appList|Panel\.qml/)
})

test("#1: an older running Service (keepLoaded across an update) asks for a restart", () => {
  const fs = require("node:fs")
  const path = require("node:path")
  const root = path.join(__dirname, "..")
  const widget = fs.readFileSync(path.join(root, "qml/BarWidget.qml"), "utf8")
  const svc = fs.readFileSync(path.join(root, "qml/Service.qml"), "utf8")
  const card = fs.readFileSync(path.join(root, "qml/HoverCard.qml"), "utf8")
  // the two halves carry the same code version (bump both together)
  const expects = widget.match(
    /readonly property int expectedServiceVersion: (\d+)/,
  )
  const has = svc.match(/readonly property int serviceVersion: (\d+)/)
  assert(expects && has, "both versions declared")
  assert.equal(expects[1], has[1])
  // 0.9.0's Service has no serviceVersion: undefined !== expected reads as older
  assert.match(
    widget,
    /serviceStale: root\.service !== null && root\.service\.serviceVersion !== root\.expectedServiceVersion/,
  )
  assert.match(
    widget,
    /root\.serviceStale \? "Restart the shell to finish updating"/,
  )
  assert.match(
    widget,
    /root\.serviceStale \? "Run omarchy restart shell to finish updating"/,
  )
  assert.match(widget, /notice: root\.noticeShort/)
  assert.match(widget, /noticeDetail: root\.noticeLong/)
  // the glance's footer and every page's footer paint it, muted, elided
  assert.match(
    card,
    /visible: hc\.notice !== ""[\s\S]{0,200}text: hc\.notice\n[\s\S]{0,60}color: hc\.muted/,
  )
  assert.match(
    card,
    /visible: hc\.noticeDetail !== ""[\s\S]{0,200}text: hc\.noticeDetail\n[\s\S]{0,60}color: hc\.muted/,
  )
  assert.match(
    card,
    /AllTimeLine \{\n\s+visible: hc\.failedPage !== hc\.page && hc\.noticeDetail === ""/,
  )
})

test("#3: a Service that won't write history.json says so on the card", () => {
  const fs = require("node:fs")
  const path = require("node:path")
  const root = path.join(__dirname, "..")
  const widget = fs.readFileSync(path.join(root, "qml/BarWidget.qml"), "utf8")
  const svc = fs.readFileSync(path.join(root, "qml/Service.qml"), "utf8")
  assert.match(
    widget,
    /historyBlocked: root\.service !== null && root\.service\.historyReadOnly === true/,
  )
  assert.match(
    svc,
    /property int schema: 0\n\s+property var days: \(\{\}\)\n\s+property var months: \(\{\}\)\n\s+property var years: \(\{\}\)\n\s+property var ext: \(\{\}\)/,
  )
  assert.match(
    svc,
    /var block = Model\.historyWriteBlock\(Model\.parseHistoryText\(historyFile\.text\(\)\)\);/,
  )
  // every write path is guarded
  assert.equal((svc.match(/historyFile\.writeAdapter\(\)/g) || []).length, 2)
  assert.equal(
    (
      svc.match(
        /if \(!root\.historyReadOnly\)\n\s+historyFile\.writeAdapter\(\);/g,
      ) || []
    ).length,
    2,
  )
  assert.match(
    svc,
    /function persist\(\) \{\n\s+if \(root\.startupPhase \|\| root\.backupPending \|\| root\.historyReadOnly\)/,
  )
  assert.match(svc, /historyAdapter\.schema = Model\.HISTORY_SCHEMA;/)
  assert.match(svc, /Model\.migrateHistory\(\{/)
})
