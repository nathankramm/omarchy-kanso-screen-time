"use strict"

// Structural tests for the bar, the hover card and the engine runner, by
// source shape (QML cannot run under node), like tests/service.test.js.
// They pin the card's house rules: plain text only (D13), no scrolling (D11),
// nothing spawned outside Service (D14), and shell members that exist.

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")

const ROOT = path.join(__dirname, "..")
const read = (rel) => fs.readFileSync(path.join(ROOT, rel), "utf8")
const QML = fs
  .readdirSync(path.join(ROOT, "qml"))
  .filter((f) => f.endsWith(".qml"))
  .map((f) => ({ name: "qml/" + f, src: read("qml/" + f) }))

// The text of every `Type {` block in a QML source, braces matched.
function blocks(src, type) {
  const out = []
  const re = new RegExp("(^|[^\\w.])" + type + "\\s*\\{", "g")
  let m
  while ((m = re.exec(src))) {
    let depth = 0
    let i = src.indexOf("{", m.index)
    const start = i
    for (; i < src.length; i++) {
      if (src[i] === "{") depth++
      else if (src[i] === "}" && --depth === 0) break
    }
    out.push(src.slice(start, i + 1))
  }
  return out
}

// Names a QML file declares: properties, functions, signals.
function declared(src) {
  const re =
    /^\s*(?:readonly\s+|required\s+|default\s+)*(?:property\s+\S+\s+(\w+)|function\s+(\w+)|signal\s+(\w+))/gm
  const out = new Set()
  let m
  while ((m = re.exec(src))) out.add(m[1] || m[2] || m[3])
  return out
}

test("D13: every Text is plain text", () => {
  let count = 0
  for (const f of QML) {
    for (const b of blocks(f.src, "Text")) {
      count++
      assert.match(
        b,
        /textFormat: Text\.PlainText/,
        f.name + ": " + b.slice(0, 80),
      )
    }
  }
  assert.ok(count >= 3, "found " + count + " Text blocks")
})

test("D11: nothing on the bar or the card scrolls", () => {
  for (const f of QML) {
    assert.doesNotMatch(
      f.src,
      /\b(Flickable|ScrollView|ListView|GridView|ScrollBar|PathView)\b/,
      f.name,
    )
  }
})

test("D14/V1: a hover spawns nothing; the bar runs the report (D33) and one page run per arrow", () => {
  const card = read("qml/HoverCard.qml")
  assert.doesNotMatch(card, /\bProcess\b|\.running\s*=|execDetached|bar\.run\(/)
  const bar = read("qml/BarWidget.qml")
  assert.doesNotMatch(bar, /bar\.run\(/)
  // one Process: the page run, started in runPage and nowhere else
  assert.equal((bar.match(/^\s*Process \{/gm) || []).length, 1)
  assert.equal((bar.match(/\.running = true/g) || []).length, 1)
  assert.match(
    bar,
    /pageProc\.command = \["timeout", "20", "\/usr\/bin\/python3", root\.service\.enginePath, "card", "--page", Pager\.engineKind\(request\.page\) \+ ":" \+ request\.key\];\n\s+pageProc\.running = true;/,
  )
  assert.match(bar, /onExited: root\.pageFinished\(pageOut\.text\)/)
  // runPage is reached from an arrow's request and from a finished run's queue only
  assert.deepEqual(bar.match(/root\.runPage\b[^;]*/g), [
    "root.runPage(r.spawn)",
    "root.runPage, r.spawn)",
  ])
  assert.deepEqual(bar.match(/root\.requestPage\([^)]*\)/g), [
    "root.requestPage(page, key)",
  ])
  assert.match(
    bar,
    /onPageRequested: \(page, key\) => root\.requestPage\(page, key\)/,
  )
  // and only an arrow emits pageRequested
  assert.deepEqual(card.match(/hc\.pageRequested\([^)]*\)/g), [
    "hc.pageRequested(header.pageName, header.view.next)",
    "hc.pageRequested(header.pageName, header.view.prev)",
  ])
  // the hover machine, its timers and open/close never touch a run
  const hoverParts = [
    bar.match(/onHoverWantedChanged: \{[\s\S]*?\n {4}\}/)[0],
    bar.match(/function openHoverCard\(\) \{[\s\S]*?\n {4}\}/)[0],
    bar.match(/function closeHoverCard\(\) \{[\s\S]*?\n {4}\}/)[0],
    blockWithId(bar, "Timer", "openTimer"),
    blockWithId(bar, "Timer", "closeTimer"),
  ]
  for (const part of hoverParts)
    assert.doesNotMatch(part, /Page|pageProc|runReport|execDetached/)
  // exactly one detached spawn, inside runReport, reached only by a right-click and copy/save
  assert.equal((bar.match(/execDetached/g) || []).length, 1)
  assert.match(
    bar,
    /function runReport\(verb\) \{\n\s+Quickshell\.execDetached\(/,
  )
  const callers = bar.match(/root\.runReport\("[a-z]+"\)/g) || []
  assert.deepEqual(callers.sort(), [
    'root.runReport("copy")',
    'root.runReport("export")',
    'root.runReport("menu")',
  ])
  const service = read("qml/Service.qml")
  assert.doesNotMatch(service, /--page|pageProc|Pager/)
  const proc = service.match(/id: engineProc[\s\S]*?\n    \}/)
  assert(proc, "engineProc exists")
  assert(
    proc[0].includes(
      'command: ["timeout", "20", root.python, root.enginePath, "card"]',
    ),
  )
  assert(proc[0].includes("onExited: root.applyEngineOutput(engineOut.text)"))
  const timer = service.match(/id: engineTimer[\s\S]*?\n    \}/)
  assert(timer, "engineTimer exists")
  assert(timer[0].includes("interval: 60000"))
  assert(timer[0].includes("triggeredOnStart: true"))
  assert(timer[0].includes("repeat: true"))
  // the engine is started from the timer and nowhere else
  assert.equal((service.match(/engineProc\.running = true/g) || []).length, 1)
  assert.match(
    service,
    /var result = Model\.acceptCard\(root\.card, raw\);\n\s+root\.card = result\.card;/,
  )
})

test("the bar reads the card from Service and owns the hover machine", () => {
  const bar = read("qml/BarWidget.qml")
  assert.match(
    bar,
    /readonly property var card: service \? service\.card : null/,
  )
  assert.match(bar, /triggerMode: "hover"/)
  assert.match(bar, /id: openTimer\n\s+interval: 250/)
  assert.match(bar, /id: closeTimer\n\s+interval: 300/)
  // nathan.monarch's 2026-10-02 fix: a null activePopout is a hand-off
  assert.match(
    bar,
    /if \(hoverPopup\.open && active && active !== hoverPopup\.coordinatorKey\)/,
  )
})

test("shell members used by the QML exist in the shell's own files", () => {
  // lint/qs/Commons/* are verbatim copies of the installed shell's files;
  // qmllint cannot check these members (.qmllint.ini disables
  // MissingProperty as shell-typing noise), so this does.
  for (const single of ["Style", "Color", "Border"]) {
    const names = declared(read("lint/qs/Commons/" + single + ".qml"))
    for (const f of QML) {
      const used = new Set(
        [...f.src.matchAll(new RegExp("\\b" + single + "\\.(\\w+)", "g"))].map(
          (m) => m[1],
        ),
      )
      for (const u of used)
        assert.ok(
          names.has(u),
          f.name + ": " + single + "." + u + " is not declared",
        )
    }
  }
  const popup = declared(read("lint/qs/Ui/PopupCard.qml"))
  for (const f of QML) {
    const id = (f.src.match(/PopupCard \{\s*id: (\w+)/) || [])[1]
    if (!id) continue
    for (const m of f.src.matchAll(new RegExp("\\b" + id + "\\.(\\w+)", "g")))
      assert.ok(
        popup.has(m[1]),
        f.name + ": " + id + "." + m[1] + " is not a PopupCard member",
      )
  }
})

test("D11: the card has a fixed size, whatever the rows", () => {
  const card = read("qml/HoverCard.qml")
  assert.match(card, /readonly property int maxRows: 7 /)
  assert.match(card, /height: hc\.maxRows \* hc\.rowHeight/)
  assert.match(
    card,
    /implicitHeight: page === "glance" \? glance_\.implicitHeight : today_\.implicitHeight/,
  )
  // rows are a Repeater over the engine's rows inside the fixed slot column
  assert.match(card, /model: hc\.dayPage \? hc\.dayPage\.rows : \[\]/)
  assert.match(card, /model: periodPage\.view \? periodPage\.view\.rows : \[\]/)
  assert.doesNotMatch(card, /chips/)
})

test("D16 pages: hover = glance, click = detail, the switch, close = glance", () => {
  const bar = read("qml/BarWidget.qml")
  assert.match(
    bar,
    /if \(b === Qt\.LeftButton\) \{\n\s+hoverCard\.page = "today";\n\s+root\.openHoverCard\(\);/,
  )
  // closing returns to the glance and every tab to its current page (V1)
  assert.match(
    bar,
    /if \(!open\) \{[\s\S]*?hoverCard\.page = "glance";\n\s+root\.pager = Pager\.reset\(root\.pager\);/,
  )
  const card = read("qml/HoverCard.qml")
  assert.match(card, /property string page: "glance"/)
  assert.match(card, /id: glance_\n\s+visible: hc\.page === "glance"/)
  assert.match(card, /id: today_\n\s+visible: hc\.page === "today"/)
  assert.match(card, /id: week_\n\s+visible: hc\.page === "week"/)
  assert.match(card, /id: month_\n\s+visible: hc\.page === "month"/)
  assert.match(card, /id: year_\n\s+visible: hc\.page === "year"/)
  assert.match(card, /text: "Details ›"\n\s+onActivated: hc\.page = "today"/)
  assert.doesNotMatch(card, /"Year ›"|"‹ Today"|yearIndex|weekIndex/)
})

test("5.4: status reports the engine, keys and names.json, and no idle state", () => {
  const bar = read("qml/BarWidget.qml")
  assert.match(bar, /function status\(\): string \{/)
  assert.match(bar, /return JSON\.stringify\(out\);/)
  const service = read("qml/Service.qml")
  const status = service.match(/function status\(\) \{[\s\S]*?\n    \}/)
  assert(status, "Service.status exists")
  for (const field of [
    "engine_ok",
    "engine_error",
    "last_run_age_s",
    "card_state",
    "stored_keys",
    "ignore_list",
    "engine",
  ])
    assert.match(status[0], new RegExp('"' + field + '":'), field)
  assert.match(status[0], /Model\.storedKeyCount\(root\.days\)/)
  assert.doesNotMatch(status[0], /idle/i)
})

test("D42/D48: the Day's chart is the day by hour (in part: over a caption), or one muted line", () => {
  const card = read("qml/HoverCard.qml")
  const plot = blockWithId(card, "Item", "plot")
  assert.match(
    plot,
    /^\{\s*id: plot\n\s+width: parent\.width\n\s+height: Style\.space\(96\)/,
  )
  // a whole day: the 24 bars in the plot every chart uses (current = accent, future = slot)
  assert.match(
    plot,
    /PeriodPlot \{\n\s+visible: hc\.dayHours !== null && \(hc\.dayHours\.state === "ok" \|\| plot\.partial\)\n/,
  )
  // D48: a day recorded in part draws its bars too, a caption's height shorter,
  // over the engine's muted "hours from <h>": the box stays 96 tall
  assert.match(
    plot,
    /readonly property bool partial: hc\.dayHours !== null && hc\.dayHours\.state === "partial"/,
  )
  assert.match(
    plot,
    /height: plot\.partial \? plot\.height - captionMetrics\.height - Style\.space\(2\) : plot\.height/,
  )
  const caption = blocks(plot, "Text")[0]
  assert.match(caption, /visible: plot\.partial\n/)
  assert.match(caption, /anchors\.bottom: parent\.bottom/)
  assert.match(caption, /text: plot\.partial \? hc\.dayHours\.text : ""/)
  assert.match(caption, /color: hc\.muted/)
  assert.match(caption, /textFormat: Text\.PlainText/)
  // otherwise the engine's "hourly from <date>", muted, in the same space
  const pending = blocks(plot, "Text")[1]
  assert.match(
    pending,
    /visible: hc\.dayHours !== null && hc\.dayHours\.state === "pending"/,
  )
  assert.match(pending, /text: hc\.dayHours \? hc\.dayHours\.text : ""/)
  assert.match(pending, /color: hc\.muted/)
  // no 30-day chart, no dashed average on the Day
  assert.doesNotMatch(plot, /DashedMean|MeanLabel|points|dayChart/)
  assert.match(
    card,
    /readonly property var dayHours: dayPage \? dayPage\.hours : null/,
  )
  // the shared plot marks the engine's "current" in the accent
  assert.match(
    componentBlock(card, "PeriodPlot"),
    /isCurrent: periodBar\.modelData\.state === "today" \|\| periodBar\.modelData\.state === "current"/,
  )
})

// The block of `type` whose body declares `id: <id>`.
function blockWithId(src, type, id) {
  const b = blocks(src, type).filter((x) =>
    new RegExp("^\\{\\s*id: " + id + "\\b").test(x),
  )
  assert.equal(b.length, 1, type + " " + id)
  return b[0]
}

test("D16 glance: no chart, no usual line, no Other", () => {
  const glance = blockWithId(read("qml/HoverCard.qml"), "Column", "glance_")
  assert.doesNotMatch(glance, /Canvas|\bplot\b|\bbars\b|hc\.chart|hc\.usual/)
  assert.match(glance, /Hero \{\}/)
  assert.match(glance, /SegmentBar \{\}/)
  assert.match(glance, /model: hc\.topRows/)
  assert.match(
    read("qml/HoverCard.qml"),
    /readonly property var topRows: rows\.filter\(r => !r\.other\)\.slice\(0, glanceRows\)/,
  )
})

test("D16 glance: at most three rows", () => {
  const card = read("qml/HoverCard.qml")
  assert.match(card, /readonly property int glanceRows: 3 /)
  assert.doesNotMatch(
    blockWithId(card, "Column", "glance_"),
    /model: hc\.rows\b/,
  )
})

test("D16 fix: the glance is today only, no week or all-time text", () => {
  const glance = blockWithId(read("qml/HoverCard.qml"), "Column", "glance_")
  assert.doesNotMatch(glance, /hc\.footer|all_time|since_text|week/)
  assert.match(glance, /text: "Details ›"/)
  assert.match(glance, /anchors\.right: parent\.right/)
})

test("D16 glance: its height never changes with the data", () => {
  const glance = blockWithId(read("qml/HoverCard.qml"), "Column", "glance_")
  assert.match(glance, /height: hc\.glanceRows \* hc\.rowHeight/)
  assert.doesNotMatch(glance, /\.length\s*\*|topRows\.length|rows\.length/)
})

test("V1 header: on every detail page the pill and ‹ › sit in fixed slots, so nothing moves", () => {
  const header = componentBlock(read("qml/HoverCard.qml"), "PageHeader")
  // the label is the pill's text; the arrows never anchor to it
  assert.doesNotMatch(
    blockWithId(header, "Text", "pillLabel"),
    /prevSlot|nextSlot|visible|anchors\.left|\bx:/,
  )
  assert.match(
    blockWithId(header, "Item", "nextSlot"),
    /anchors\.right: parent\.right\n\s+width: Style\.space\(20\)/,
  )
  assert.match(
    blockWithId(header, "Item", "pillSlot"),
    /anchors\.right: nextSlot\.left\n\s+width: Style\.space\(160\)/,
  )
  assert.match(
    blockWithId(header, "Item", "prevSlot"),
    /anchors\.right: pillSlot\.left\n\s+width: Style\.space\(20\)/,
  )
  for (const slot of ["prevSlot", "nextSlot", "pillSlot"]) {
    assert.doesNotMatch(
      blockWithId(header, "Item", slot),
      /^\{\s*id: \w+\s*\n\s*visible:/,
      slot,
    )
  }
  // an arrow shows only when the engine names a neighbour, and asks for exactly it
  assert.match(
    header,
    /visible: header\.view !== null && header\.view\.next !== null\n\s+onActivated: hc\.pageRequested\(header\.pageName, header\.view\.next\)/,
  )
  assert.match(
    header,
    /visible: header\.view !== null && header\.view\.prev !== null\n\s+onActivated: hc\.pageRequested\(header\.pageName, header\.view\.prev\)/,
  )
})

test("V1 Week, Month and Year: one page layout at the Day's fixed size, nothing scrolls", () => {
  const card = read("qml/HoverCard.qml")
  const page = componentBlock(card, "PeriodPage")
  const plot = componentBlock(card, "PeriodPlot")
  for (const b of [page, plot])
    assert.doesNotMatch(
      b,
      /Flickable|ScrollView|ListView|ScrollBar|contentHeight|contentY/,
    )
  assert.match(
    plot,
    /^\{\s*id: plotFrame\n(\s+(readonly )?property .*\n|\s+\/\/.*\n)+\s+width: parent \? parent\.width : hc\.cardWidth\n\s+height: Style\.space\(96\)/,
  )
  assert.match(
    page,
    /height: hc\.maxRows \* hc\.rowHeight\n\n\s+Repeater \{\n\s+model: periodPage\.view \? periodPage\.view\.rows : \[\]/,
  )
  assert.match(
    page,
    /SegmentBar \{\n\s+segments: periodPage\.view \? periodPage\.view\.segments : \[\]/,
  )
  assert.match(page, /line: periodPage\.view \? periodPage\.view\.line : null/)
  assert.doesNotMatch(page, /\.length\s*\*|\*\s*[\w.]*\.length/)
  // today or this month is the engine's "today" / "current"; empty slots are its future / before
  assert.match(
    plot,
    /isCurrent: periodBar\.modelData\.state === "today" \|\| periodBar\.modelData\.state === "current"/,
  )
  assert.match(
    plot,
    /color: periodBar\.isCurrent \? Color\.accent : hc\.barColor/,
  )
  assert.match(
    plot,
    /isSlot: periodBar\.modelData\.state === "future" \|\| periodBar\.modelData\.state === "before"/,
  )
  // the Day's section order and heights
  const order = [
    "PageHeader",
    "PageLine",
    "PeriodPlot",
    "SegmentBar",
    "AppRow",
    "PageFooter",
  ]
  const at = order.map((k) => page.indexOf(k))
  assert.ok(
    at.every((v, i) => v > 0 && (i === 0 || v > at[i - 1])),
    JSON.stringify(at),
  )
  const day = blockWithId(card, "Column", "today_")
  const dayOrder = [
    "PageHeader",
    "PageLine",
    "id: plot",
    "SegmentBar",
    "AppRow",
    "PageFooter",
  ]
  const dayAt = dayOrder.map((k) => day.indexOf(k))
  assert.ok(
    dayAt.every((v, i) => v > 0 && (i === 0 || v > dayAt[i - 1])),
    JSON.stringify(dayAt),
  )
  // the three pages are that layout, each on its own tab's page
  for (const [id, page, gap] of [
    ["week_", "week", 10],
    ["month_", "month", 2],
    ["year_", "year", 4],
  ]) {
    const b = blockWithId(card, "PeriodPage", id)
    assert.match(b, new RegExp("view: hc\\." + page + "Page\\n"), id)
    assert.match(b, new RegExp('pageName: "' + page + '"'), id)
    assert.match(b, new RegExp("gap: Style\\.space\\(" + gap + "\\)"), id)
  }
})

// The body of `component <name>: Type { ... }`.
function componentBlock(src, name) {
  const at = src.indexOf("component " + name + ":")
  assert.ok(at >= 0, "component " + name)
  const start = src.indexOf("{", at)
  let depth = 0
  let i = start
  for (; i < src.length; i++) {
    if (src[i] === "{") depth++
    else if (src[i] === "}" && --depth === 0) break
  }
  return src.slice(start, i + 1)
}

test("V1: a page never changes the card's height", () => {
  const card = read("qml/HoverCard.qml")
  // every detail page is sized by the Day's column; the glance is the short one
  assert.match(
    card,
    /implicitHeight: page === "glance" \? glance_\.implicitHeight : today_\.implicitHeight/,
  )
  // the Day's and every period page's sections: fixed heights, never from data
  assert.match(
    blockWithId(card, "Item", "plot"),
    /^\{\s*id: plot\n\s+width: parent\.width\n\s+height: Style\.space\(96\)/,
  )
  assert.match(
    componentBlock(card, "PageHeader"),
    /height: Math\.ceil\(displayMetrics\.height\)/,
  )
  assert.match(
    componentBlock(card, "PageLine"),
    /height: Math\.ceil\(bodyMetrics\.height\)/,
  )
  assert.match(
    componentBlock(card, "PageFooter"),
    /height: Math\.ceil\(captionMetrics\.height\)/,
  )
  for (const b of [
    blockWithId(card, "Column", "today_"),
    componentBlock(card, "PeriodPage"),
  ]) {
    assert.equal(
      (b.match(/height: hc\.maxRows \* hc\.rowHeight/g) || []).length,
      1,
    )
    assert.match(b, /spacing: Style\.space\(6\)/)
    assert.doesNotMatch(b, /implicitHeight|childrenRect|\.length\s*\*/)
  }
  // no height anywhere comes from how many rows, segments or bars there are
  assert.doesNotMatch(card, /^\s*(implicitH|h)eight:.*\.length/m)
})

test("V1 switch: Day · Week · Month · Year on every detail page, the current one bold and underlined", () => {
  const card = read("qml/HoverCard.qml")
  const links = blocks(componentBlock(card, "PageSwitch"), "Link")
  const want = [
    ["Day", "today"],
    ["Week", "week"],
    ["Month", "month"],
    ["Year", "year"],
  ]
  assert.equal(links.length, want.length)
  links.forEach((l, i) => {
    const [label, page] = want[i]
    assert.match(l, new RegExp('text: "' + label + '"'))
    assert.match(l, new RegExp('current: hc\\.page === "' + page + '"'))
    assert.match(l, new RegExp('onActivated: hc\\.page = "' + page + '"'))
  })
  const link = componentBlock(card, "Link")
  // the current page: text colour, bold, an accent underline (audit: accent text
  // is under 4.5:1 on light themes)
  assert.match(
    link,
    /color: link\.current \? hc\.foreground : linkArea\.containsMouse \? Color\.accent : hc\.muted/,
  )
  assert.match(link, /font\.bold: link\.current/)
  assert.match(
    link,
    /Rectangle \{\n\s+visible: link\.current\n[\s\S]*?anchors\.top: parent\.bottom[\s\S]*?height: 2[\s\S]*?color: Color\.accent/,
  )
  assert.match(componentBlock(card, "PageFooter"), /PageSwitch \{/)
  assert.match(blockWithId(card, "Column", "today_"), /PageFooter \{\}/)
  assert.match(componentBlock(card, "PeriodPage"), /PageFooter \{\}/)
})

test("D19 Day footer: all-time since the first day only, no week total", () => {
  const card = read("qml/HoverCard.qml")
  const footer = componentBlock(card, "PageFooter")
  assert.match(footer, /AllTimeLine \{/)
  assert.doesNotMatch(
    blockWithId(card, "Column", "today_") + footer,
    /week_text|week_ms|hc\.footer\.text/,
  )
  const line = componentBlock(card, "AllTimeLine")
  assert.match(line, /hc\.footer\.all_time_text/)
  assert.match(line, /hc\.footer\.since_text/)
  assert.doesNotMatch(line, /week/)
})

test("V1 tabs: each paints the page asked for, else the card's current one", () => {
  const card = read("qml/HoverCard.qml")
  assert.match(card, /property var shown: null/)
  assert.match(card, /signal pageRequested\(string page, string key\)/)
  for (const [prop, name, field] of [
    ["dayPage", "today", "day"],
    ["weekPage", "week", "week"],
    ["monthPage", "month", "month"],
    ["yearPage", "year", "year"],
  ])
    assert.match(
      card,
      new RegExp(
        "readonly property var " +
          prop +
          ': hc\\.pageFor\\("' +
          name +
          '", "' +
          field +
          '"\\)',
      ),
    )
  const pageFor = card.match(
    /function pageFor\(name, field\) \{[\s\S]*?\n    \}/,
  )[0]
  assert.match(pageFor, /if \(!hc\.live\)\n\s+return null;/)
  assert.match(
    pageFor,
    /if \(hc\.shown && hc\.shown\[name\]\)\n\s+return hc\.shown\[name\];/,
  )
  assert.match(pageFor, /return hc\.doc\[field\] \|\| null;/)
  const bar = read("qml/BarWidget.qml")
  assert.match(bar, /shown: root\.pager\.shown/)
  assert.match(bar, /failedPage: root\.pager\.failed/)
  // the current page's key is the engine card's own: asking for it runs nothing
  assert.match(
    bar,
    /var current = root\.card \? root\.card\[Pager\.engineKind\(page\)\] : null;\n\s+var r = Pager\.request\(root\.pager, page, key, current \? current\.key : ""\);/,
  )
})

test("V1 failure: a failed page run is one quiet line in the footer, never a broken card", () => {
  const footer = componentBlock(read("qml/HoverCard.qml"), "PageFooter")
  assert.match(
    footer,
    /AllTimeLine \{\n\s+visible: hc\.failedPage !== hc\.page/,
  )
  const line = blocks(footer, "Text")[0]
  assert.match(line, /visible: hc\.live && hc\.failedPage === hc\.page/)
  assert.match(line, /text: "Couldn't load that page"/)
  assert.match(line, /color: hc\.muted/)
})

test("audit: muted text is the readable mix, never Qt.darker", () => {
  const card = read("qml/HoverCard.qml")
  assert.match(card, /import "\.\.\/js\/Contrast\.js" as Contrast/)
  assert.match(card, /property color surface: Color\.popups\.background/)
  assert.match(
    card,
    /readonly property color muted: hc\.readable\(foreground, 4\.5, 0\.4\)/,
  )
  assert.match(card, /Contrast\.readableMix\(fg, hc\.surface, minRatio, from\)/)
  assert.doesNotMatch(card, /Qt\.darker/)
})

test("audit: chart bars are the 3:1 bar colour, on an untinted frame", () => {
  const card = read("qml/HoverCard.qml")
  assert.match(
    card,
    /readonly property color barColor: hc\.readable\(foreground, 3\.0, 0\.2\)/,
  )
  assert.doesNotMatch(card, /isZero \? 0\.18 : 0\.28/)
  assert.doesNotMatch(card, /faint\(0\.05\)/)
  // every chart, the Day's hours included, is the one untinted plot
  assert.doesNotMatch(blockWithId(card, "Item", "plot"), /color: (?!hc\.muted)/)
  assert.match(
    componentBlock(card, "PeriodPlot"),
    /height: Style\.space\(96\)\n\s+radius: Style\.cornerRadius\n\s+color: "transparent"/,
  )
  assert.equal((card.match(/\? Color\.accent : hc\.barColor/g) || []).length, 1)
})

test("audit C6: no date pill on the glance; the Day page keeps it", () => {
  const card = read("qml/HoverCard.qml")
  assert.doesNotMatch(componentBlock(card, "Hero"), /pill/i)
  assert.match(
    blockWithId(card, "Column", "today_"),
    /PageHeader \{\n\s+view: hc\.dayPage\n\s+pageName: "today"\n\s+pillText: hc\.dayPage \? hc\.dayPage\.pill : ""/,
  )
})

test("design pass + D43: the bars start after the scale's gutter, nothing else is inset", () => {
  const card = read("qml/HoverCard.qml")
  assert.doesNotMatch(
    blockWithId(card, "Item", "plot"),
    /(leftMargin|rightMargin|\bx): Style\.space\(\d+\)/,
  )
  const plot = componentBlock(card, "PeriodPlot")
  assert.match(plot, /readonly property int gutter: Style\.space\(30\)/)
  assert.match(plot, /anchors\.leftMargin: plotFrame\.gutter/)
  assert.doesNotMatch(plot, /(rightMargin|leftMargin): Style\.space\(\d+\)/)
})

test("D43: the engine's ticks, a faint solid line and a muted label in the gutter, at their y", () => {
  const plot = componentBlock(read("qml/HoverCard.qml"), "PeriodPlot")
  assert.match(
    plot,
    /model: plotFrame\.view && plotFrame\.view\.ticks \? plotFrame\.view\.ticks : \[\]/,
  )
  // the line's height is the engine's y times the bars' height, nothing computed
  assert.match(
    plot,
    /y: slots\.y \+ \(1 - tick\.modelData\.y\) \* slots\.height/,
  )
  const tick = blockWithId(plot, "Item", "tick")
  const [line] = blocks(tick, "Rectangle")
  assert.match(line, /height: 1\n\s+color: hc\.faint\(0\.14\)/) // solid, faint: not the dashed average
  const [label] = blocks(tick, "Text")
  assert.match(label, /text: tick\.modelData\.text/)
  assert.match(label, /color: hc\.muted/)
  assert.match(label, /width: plotFrame\.gutter - Style\.space\(4\)/)
  // the dashed average stays its own component
  assert.match(plot, /DashedMean \{/)
})

test("audit P1: the glance's rows show times only; Day keeps the vs text", () => {
  const row = componentBlock(read("qml/HoverCard.qml"), "AppRow")
  const vs = blockWithId(row, "Text", "rowVs")
  assert.match(vs, /visible: hc\.page !== "glance"/)
  assert.match(vs, /text: row\.modelData\.vs_text/)
})

test("D33: right-click runs the report menu and never opens the card", () => {
  const bar = read("qml/BarWidget.qml")
  const pressed = bar.match(/onPressed: function \(b\) \{[\s\S]*?\n {8}\}/)[0]
  const right = pressed.slice(pressed.indexOf("Qt.RightButton"))
  assert.match(right, /root\.runReport\("menu"\)/)
  assert.doesNotMatch(right, /openHoverCard|toggle|hoverCard\.page/)
  // left-click still opens the Day page
  assert.match(
    pressed,
    /b === Qt\.LeftButton\) \{\n\s+hoverCard\.page = "today";\n\s+root\.openHoverCard\(\);/,
  )
  assert.match(bar, /Qt\.resolvedUrl\("\.\.\/bin\/screen-time-report"\)/)
  assert.match(
    bar,
    /Quickshell\.execDetached\(\["bash", "-lc", 'exec "\$@"', "bash", root\.reportScript, verb\]\)/,
  )
})

test("D33: IPC verbs copy and save run the report script", () => {
  const bar = read("qml/BarWidget.qml")
  assert.match(bar, /function copy\(\): void \{\n\s+root\.runReport\("copy"\);/)
  assert.match(
    bar,
    /function save\(\): void \{\n\s+root\.runReport\("export"\);/,
  )
})

test("D16 status reports the page", () => {
  assert.match(read("qml/BarWidget.qml"), /out\.page = hoverCard\.page;/)
})

test("V1: the period plot reads its own bars, qualified (the Day chart's `bars` id shadows a bare name)", () => {
  const plot = componentBlock(read("qml/HoverCard.qml"), "PeriodPlot")
  // a bare `bars` here resolved to the Day page's chart Item: every slot 0 px wide
  assert.doesNotMatch(plot, /(^|[^.\w])bars\b/m)
  assert.match(plot, /model: slots\.periodBars/)
  assert.match(
    plot,
    /slotWidth: slots\.periodBars\.length > 0 \? \(slots\.width - plotFrame\.gap \* \(slots\.periodBars\.length - 1\)\) \/ slots\.periodBars\.length : 0/,
  )
})

test("D41 line: only the comparison; bold, or muted and regular while warming up", () => {
  const line = componentBlock(read("qml/HoverCard.qml"), "PageLine")
  assert.match(
    line,
    /readonly property bool warming: pageLine\.line !== null && pageLine\.line\.state === "baseline"/,
  )
  assert.match(line, /text: pageLine\.line \? pageLine\.line\.text : ""/)
  assert.match(line, /color: pageLine\.warming \? hc\.muted : hc\.foreground/)
  assert.match(line, /font\.bold: !pageLine\.warming/)
  assert.match(line, /elide: Text\.ElideRight/)
  // D41: no head; "avg" on the card only ever means the all-time average
  assert.doesNotMatch(read("qml/HoverCard.qml"), /\bhead\b/)
  const card = read("qml/HoverCard.qml")
  assert.match(
    card,
    /PageLine \{\n\s+line: hc\.dayPage \? hc\.dayPage\.line : null/,
  )
  assert.match(
    card,
    /PageLine \{\n\s+line: periodPage\.view \? periodPage\.view\.line : null/,
  )
})

test("D38 charts: no scale label; the dashed all-time average and its label on the period charts", () => {
  const card = read("qml/HoverCard.qml")
  assert.doesNotMatch(card, /y_max_text/)
  assert.equal(blocks(card, "Canvas").length, 1, "one dashed-line component")
  const plot = componentBlock(card, "PeriodPlot")
  assert.match(
    plot,
    /DashedMean \{\n\s+anchors\.fill: slots\n\s+meanY: plotFrame\.view && typeof plotFrame\.view\.mean_y === "number" \? plotFrame\.view\.mean_y : null/,
  )
  assert.match(
    plot,
    /MeanLabel \{\n\s+anchors\.right: parent\.right\n\s+text: plotFrame\.view \? plotFrame\.view\.mean_text : ""/,
  )
  // D42: the Day's hourly chart draws none (its engine view has no mean)
  assert.doesNotMatch(blockWithId(card, "Item", "plot"), /DashedMean|MeanLabel/)
})
