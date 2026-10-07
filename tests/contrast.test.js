"use strict"

// The card's derived colours, checked against five real Omarchy themes: the
// colours QML resolved for each (popup background, text), as the render
// harness logged them on 2026-10-06.
const { test } = require("node:test")
const assert = require("node:assert/strict")
const C = require("../js/Contrast.js")

const hex = (h) => ({
  r: parseInt(h.slice(1, 3), 16) / 255,
  g: parseInt(h.slice(3, 5), 16) / 255,
  b: parseInt(h.slice(5, 7), 16) / 255,
})
const THEMES = {
  "tokyo-night": { background: "#1a1b26", text: "#a9b1d6", accent: "#7aa2f7" },
  gruvbox: { background: "#282828", text: "#d4be98", accent: "#7daea3" },
  nord: { background: "#2e3440", text: "#d8dee9", accent: "#81a1c1" },
  "catppuccin-latte": {
    background: "#eff1f5",
    text: "#4c4f69",
    accent: "#1e66f5",
  },
  "rose-pine": { background: "#faf4ed", text: "#575279", accent: "#56949f" },
}

test("contrast: the WCAG reference values", () => {
  assert.equal(C.ratio(hex("#ffffff"), hex("#000000")).toFixed(2), "21.00")
  assert.equal(C.ratio(hex("#777777"), hex("#ffffff")).toFixed(2), "4.48")
})

test("muted text: readable (4.5:1) and quieter than the text, in every theme", () => {
  for (const [name, t] of Object.entries(THEMES)) {
    const bg = hex(t.background)
    const text = hex(t.text)
    const muted = C.readableMix(text, bg, 4.5, 0.4)
    const r = C.ratio(muted, bg)
    assert.ok(r >= 4.5, `${name}: muted ${r.toFixed(2)} < 4.5`)
    assert.ok(r < C.ratio(text, bg), `${name}: muted is not quieter than text`)
  }
})

test("readableMix never goes past the colour it dims", () => {
  // white text on white has no readable mix: it returns the text itself
  const w = hex("#ffffff")
  assert.deepEqual(C.readableMix(w, w, 4.5, 0.4), w)
})

test("chart bars: 3:1 against the surface (WCAG 1.4.11), and today's accent too, in every theme", () => {
  for (const [name, t] of Object.entries(THEMES)) {
    const bg = hex(t.background)
    const bar = C.readableMix(hex(t.text), bg, 3.0, 0.2)
    assert.ok(
      C.ratio(bar, bg) >= 3,
      `${name}: bar ${C.ratio(bar, bg).toFixed(2)} < 3`,
    )
    assert.ok(
      C.ratio(hex(t.accent), bg) >= 3,
      `${name}: accent ${C.ratio(hex(t.accent), bg).toFixed(2)} < 3`,
    )
  }
})
