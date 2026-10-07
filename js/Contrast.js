// WCAG contrast for painting (pure; Node- and QML-importable). The card's
// derived colours must stay readable on any Omarchy theme, light or dark:
// Qt.darker() turned "muted" text LOUDER than the text on light themes.
// Colours are {r, g, b} in 0..1 (a QML color has the same fields).

function channel(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

// WCAG 2.2 relative luminance and contrast ratio.
function luminance(c) {
  return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
}

function ratio(a, b) {
  var la = luminance(a)
  var lb = luminance(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

// f of a over (1 - f) of b, in sRGB, as alpha blending paints it.
function mix(a, b, f) {
  return {
    r: a.r * f + b.r * (1 - f),
    g: a.g * f + b.g * (1 - f),
    b: a.b * f + b.b * (1 - f),
  }
}

// The dimmest mix of fg toward bg, from `from` up, that still reads at
// minRatio against bg; fg itself when nothing dimmer does. Mixing toward the
// background only lowers contrast, so the result never outshouts fg.
function readableMix(fg, bg, minRatio, from) {
  for (var step = Math.round(from * 100); step < 100; step++) {
    var c = mix(fg, bg, step / 100)
    if (ratio(c, bg) >= minRatio) return c
  }
  return mix(fg, bg, 1)
}

if (typeof module !== "undefined" && module && module.exports) {
  module.exports = {
    luminance: luminance,
    ratio: ratio,
    mix: mix,
    readableMix: readableMix,
  }
}
