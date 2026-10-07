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
