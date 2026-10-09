"use strict"

const { test } = require("node:test")
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")

const service = fs.readFileSync(
  path.join(__dirname, "..", "qml", "Service.qml"),
  "utf8",
)

test("service tracks lock and screensaver state", () => {
  assert.match(service, /property bool sessionLocked: false/)
  assert.match(service, /property bool screensaverActive: false/)
  assert.match(service, /serviceFor\("omarchy\.lock"\)/)
  assert.match(service, /serviceFor\("omarchy\.idle"\)/)
  assert.match(service, /function setSessionLocked\(locked\)/)
  assert.match(service, /function setScreensaverActive\(active\)/)
})

test("lock and screensaver transitions close and persist the active bucket", () => {
  assert.match(
    service,
    /function setSessionLocked[\s\S]*?State\.closeActiveBucket\([\s\S]*?root\.persist\(\)/,
  )
  assert.match(
    service,
    /function setScreensaverActive[\s\S]*?State\.closeActiveBucket\([\s\S]*?root\.persist\(\)/,
  )
})

test("screen-time debugging is opt-in", () => {
  assert.match(service, /property bool debugLogging: false/)
  assert.match(service, /if \(root\.debugLogging\)\s+console\.warn/)
})

test("pause keeps buckets closed against reopen paths", () => {
  // Focus events and reconcile ticks during lock must not reopen a bucket.
  assert.match(
    service,
    /function switchActive[\s\S]*?root\.sessionLocked \|\| root\.screensaverActive[\s\S]*?root\.activeApp = ""[\s\S]*?root\.activeStart = 0/,
  )
  // A resolver landing during the pause must not reopen a bucket either.
  assert.match(
    service,
    /function applyResolvedApp[\s\S]*?root\.sessionLocked \|\| root\.screensaverActive[\s\S]*?root\.resolveForApp = ""/,
  )
  // Locking kills any in-flight resolve.
  assert.match(
    service,
    /if \(locked\) \{[\s\S]*?root\.resolveForApp = ""[\s\S]*?lock started/,
  )
})

test("falls back to a persistent lock watcher when services are unavailable", () => {
  // Scoped plugin shells may never resolve the lock/idle services; the
  // plugin must say so once and watch instead of accruing through locks.
  assert.match(service, /property bool serviceLookupWarned: false/)
  assert.match(service, /id: sessionStateWatcher/)
  assert.match(service, /omarchy-shell lock isLocked/)
  // Exits when its parent shell dies, so restarts don't accumulate watchers.
  assert.match(service, /kill -0 \$ppid 2>\/dev\/null \|\| exit 0/)
  // Single persistent watcher (change-only output), not a respawning poll.
  assert.match(service, /SplitParser/)
  assert.match(
    service,
    /setSessionLocked\(String\(line\)\.trim\(\) === "true"\)/,
  )
  assert.doesNotMatch(service, /id: lockPollProc/)
  assert.doesNotMatch(service, /StdioCollector \{\s*\n\s*id: lockPollOut/)
  // Watcher is supervised while the lock service is unreachable, and
  // stopped the moment the event-driven path becomes available.
  assert.match(service, /id: watcherSupervisorTimer/)
  assert.match(service, /running: root\.ready && !root\.lockService/)
  assert.match(
    service,
    /if \(root\.lockService\) \{\s*\n\s*root\.setSessionLocked[\s\S]*?sessionStateWatcher\.running = false/,
  )
  // The screensaver fallback is an in-process scan, no spawning.
  assert.match(service, /id: screensaverScanTimer/)
  assert.match(service, /running: root\.ready && !root\.idleService/)
  assert.match(service, /function screensaverWindowVisible/)
  // The watcher feeds the same pause state machine; no bucket handling in
  // the watcher process block.
  const watcher = service.match(
    /Process \{\s*\n\s*id: sessionStateWatcher[\s\S]*?\n    \}/,
  )
  assert(watcher, "sessionStateWatcher block exists")
  assert(!watcher[0].includes("closeActiveBucket"))
})

test("resume after pause is deferred and re-validated", () => {
  // Unlock/screensaver-dismissal schedules a resume instead of reopening
  // immediately, so a stale reading from a ~10s state source cannot
  // briefly reopen a bucket.
  assert.match(service, /function scheduleResume/)
  assert.match(service, /function cancelResume/)
  assert.match(
    service,
    /function applyResume[\s\S]*?if \(root\.sessionLocked \|\| root\.screensaverActive\) \{[\s\S]*?return/,
  )
  assert.match(service, /root\.scheduleResume\(\)/)
  // Locking or starting the screensaver cancels any pending resume.
  const lockStart = service.match(/if \(locked\) \{[\s\S]*?\n    \}/)
  assert(lockStart && lockStart[0].includes("root.cancelResume()"))
  const ssStart = service.match(/if \(active\) \{[\s\S]*?\n    \}/)
  assert(ssStart && ssStart[0].includes("root.cancelResume()"))
})

test("the service lookup stops after 40 tries and says so once (#8)", () => {
  // a third-party plugin's serviceFor is scoped to its own id: never resolves
  assert.match(service, /services not reachable from a plugin/)
  assert.match(
    service,
    /running: root\.ready && !root\.serviceLookupDone && \(!root\.lockService \|\| !root\.idleService\)/,
  )
  assert.match(
    service,
    /if \(attempts >= 40\) \{\n\s+root\.serviceLookupDone = true;/,
  )
})

test("any session lock counts: the shell's isLocked, then omarchy-hyprland-session-locked (#4)", () => {
  assert.match(
    service,
    /cur=\$\(omarchy-shell lock isLocked 2>\/dev\/null\); " \+ "if \[ \\"\$cur\\" = false \] && omarchy-hyprland-session-locked 2>\/dev\/null; then cur=true; fi; "/,
  )
})

test("ignored apps are filtered; names are stored canonical, never renamed", () => {
  assert.match(service, /property var ignoredApps: \[\]/)
  assert.match(service, /function setIgnoredApps\(ignored\)/)
  assert.match(service, /Model\.parseIgnoredApps\(ignored\)/)
  assert.match(service, /Model\.isIgnoredApp\(appId, root\.ignoredApps\)/)
  assert.match(service, /root\.activeApp = Model\.canonicalApp\(app\)/)
  assert.doesNotMatch(service, /appAliases|resolveAppName|refoldToday/)
})

test("debounced saves cannot starve under focus flapping", () => {
  const schedule = service.match(/function scheduleSave\(\) \{[\s\S]*?\n    \}/)
  assert(schedule, "scheduleSave block exists")
  assert(schedule[0].includes("if (!saveTimer.running)"))
  assert(!schedule[0].includes("saveTimer.restart()"))
})

test("pre-ready focus opens no bucket and load failure re-keys today", () => {
  const sw = service.match(/function switchActive\(\) \{[\s\S]*?\n    \}/)
  assert(sw, "switchActive block exists")
  assert(sw[0].includes("if (!root.ready)"))
  const failed = service.match(
    /function onHistoryLoadFailed\(\) \{[\s\S]*?\n    \}/,
  )
  assert(failed, "onHistoryLoadFailed block exists")
  assert(failed[0].includes("root.todayKey = Model.dayKey(new Date())"))
})

test("ignoring the focused app evicts its live bucket", () => {
  const prefs = service.match(
    /function setIgnoredApps\(ignored\) \{[\s\S]*?\n    \}/,
  )
  assert(prefs, "setIgnoredApps block exists")
  assert(
    prefs[0].includes("Model.isIgnoredApp(root.activeApp, root.ignoredApps)"),
  )
  assert(prefs[0].includes("State.closeActiveBucket"))
  assert(prefs[0].includes('root.activeApp = ""'))
})

test("corrupt history is set aside without depending on python", () => {
  const backup = service.match(/id: backupProc[\s\S]*?\n    \}/)
  assert(backup, "backupProc block exists")
  assert(backup[0].includes("[[ -s "))
  assert(backup[0].includes(".corrupt-$(date +%s)"))
  assert(!backup[0].includes("|| exit 0"), "no early exit without python")
})

test("the app-detail window is a fixed 365 days with no setter", () => {
  // 365 is what upstream used here (weekly-graph length unset). Since D20 the
  // days past it keep their apps in archive/<year>.json; the window only keeps
  // history.json (rewritten every few seconds) from growing with the years.
  assert.match(service, /readonly property int keepDays: 365\n/)
  assert.doesNotMatch(service, /setKeepDays|keepDays = /)
  // one use: the save's rollout check (D20 dropped the load-time prune)
  assert.equal((service.match(/root\.keepDays\)/g) || []).length, 1)
})

test("names.json is watched live and feeds the ignore list", () => {
  assert.match(
    service,
    /readonly property string namesPath: dataDir \+ "\/names\.json"/,
  )
  const view = service.match(/id: namesFile[\s\S]*?\n    \}/)
  assert(view, "namesFile FileView exists")
  assert(view[0].includes("path: root.namesPath"))
  assert(view[0].includes("watchChanges: true"))
  assert(view[0].includes("onFileChanged: reload()"))
  assert(view[0].includes('onLoaded: root.onNamesEvent("loaded", text())'))
  assert(view[0].includes("FileViewError.FileNotFound"))
  const handler = service.match(
    /function onNamesEvent\(kind, text\) \{[\s\S]*?\n    \}/,
  )
  assert(handler, "onNamesEvent exists")
  assert(
    handler[0].includes("Model.applyNamesFile(root.namesState, kind, text)"),
  )
  assert(handler[0].includes("root.setIgnoredApps(next.ignore)"))
  // names.json must never be written by the plugin.
  assert(!view[0].includes("writeAdapter") && !view[0].includes("setText"))
})

test("a names.json missing at startup is polled until it appears", () => {
  // FileView only watches a file it has loaded once (Quickshell 0.3.1).
  const timer = service.match(/id: namesRetryTimer[\s\S]*?\n    \}/)
  assert(timer, "namesRetryTimer exists")
  assert(timer[0].includes("running: root.namesMissing"))
  assert(timer[0].includes("onTriggered: namesFile.reload()"))
  assert.match(service, /root\.namesMissing = kind === "missing";/)
  // A repeated "missing" must not re-apply the empty list every poll.
  assert.match(service, /if \(kind === "missing" && wasMissing\)\n\s+return;/)
})

test("D42: the save writes today's record whole and a restart copies its hours", () => {
  const svc = fs.readFileSync(
    path.join(__dirname, "..", "qml", "Service.qml"),
    "utf8",
  )
  const persist = svc.match(/function persist\(\) \{[\s\S]*?\n {4}\}/)[0]
  // today's object as the tracker built it, hours and all, never rebuilt here
  assert.match(persist, /merged\[root\.todayKey\] = root\.today;/)
  assert.doesNotMatch(persist, /total:|apps:|hours/)
  assert.match(
    svc,
    /root\.today = prev && typeof prev === "object" \? Model\.copyDay\(prev\) : Model\.newDay\(\);/,
  )
})

test("the engine, archiver, resolver and report run the system Python (#10)", () => {
  assert.match(
    service,
    /readonly property string python: "\/usr\/bin\/python3"/,
  )
  const fs = require("node:fs")
  const path = require("node:path")
  const root = path.join(__dirname, "..")
  for (const f of [
    "qml/Service.qml",
    "qml/BarWidget.qml",
    "bin/screen-time-report",
  ]) {
    const src = fs
      .readFileSync(path.join(root, f), "utf8")
      .split("\n")
      .filter((line) => !/^\s*(\/\/|#)/.test(line))
      .join("\n")
    // a bare `python3` call would take the first one on PATH (a mise/pyenv shim)
    assert.doesNotMatch(src, /(\["|"|\s)python3["\s]/, f)
  }
  const report = fs.readFileSync(
    path.join(root, "bin/screen-time-report"),
    "utf8",
  )
  assert.match(report, /python=\$\{SCREEN_TIME_PYTHON:-\/usr\/bin\/python3\}/)
})
