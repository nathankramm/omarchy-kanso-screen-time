// Pure state machine: explicit inputs, fresh outputs, no side effects.
// Service.qml owns timers, disk and processes; this owns transitions.

// Model: Node requires it; in QML this file gets none (null, D18), so callers
// pass it in (state.stateModel, or the `model` argument).
var Model =
  typeof module !== "undefined" && module && module.exports
    ? require("./Model.js")
    : typeof Model !== "undefined"
      ? Model
      : null

function modelFor(state) {
  return state && state.stateModel ? state.stateModel : Model
}

function isSuspendGap(now, lastTick, suspendGapMs) {
  return lastTick > 0 && now - lastTick > suspendGapMs
}

// D42: the hours a day's time fell in, by local wall-clock hour (0 = 12-1 AM).
// A day's `hours` is 24 ms totals; time is split exactly at each hour boundary
// by date arithmetic, as midnight is. On a DST day the index is the wall-clock
// hour: the repeated fall-back hour adds into its one index, the skipped
// spring-forward hour stays 0. A day recorded whole has sum(hours) == total.
var HOUR_MS = 3600000

function nextHourMs(ms) {
  var d = new Date(ms)
  var next = new Date(
    d.getFullYear(),
    d.getMonth(),
    d.getDate(),
    d.getHours() + 1,
  ).getTime()
  // a wall clock that does not advance (a repeated DST hour) still moves on
  return next > ms ? next : ms + HOUR_MS
}

// A copy of a day's hours, or 24 zeros when it has none yet (a day recorded
// before hours were: its hours will not add up to its total, so it is partial).
// D46: through Model.hoursArray, the one hours check, which takes the
// runtime's array-likes; `model` comes from the caller (in QML this file's own
// Model is null).
function hoursOf(day, model) {
  var m = model || Model
  var h = m ? m.hoursArray(day && day.hours) : null
  if (h) return h
  var out = []
  for (var i = 0; i < 24; i++) out.push(0)
  return out
}

// Add [start, end) of `app` to `day`: total, the app and the hours it spans.
// A fresh day object; the input is never mutated.
function addSpan(day, app, start, end, model) {
  var dur = end - start
  if (!app || !isFinite(dur) || dur <= 0) return day
  var apps = Object.assign({}, day.apps)
  apps[app] = (apps[app] || 0) + dur
  var hours = hoursOf(day, model)
  var cursor = start
  var guard = 0
  while (cursor < end && guard < 400) {
    var stop = Math.min(end, nextHourMs(cursor))
    hours[new Date(cursor).getHours()] += stop - cursor
    cursor = stop
    guard++
  }
  // #3: a day's other fields ride along (copied, never mutated)
  return Object.assign({}, day, {
    total: day.total + dur,
    apps: apps,
    hours: hours,
  })
}

// Unmirrored delta of live day over history mirror, floored at zero.
function dayMinus(full, base, model) {
  var f = full && typeof full === "object" ? full : { total: 0, apps: {} }
  var b = base && typeof base === "object" ? base : { total: 0, apps: {} }
  var fa = f.apps && typeof f.apps === "object" ? f.apps : {}
  var ba = b.apps && typeof b.apps === "object" ? b.apps : {}
  var apps = {}
  var total = 0
  for (var app in fa) {
    if (!Object.prototype.hasOwnProperty.call(fa, app)) continue
    var rest = (Number(fa[app]) || 0) - (Number(ba[app]) || 0)
    if (rest > 0) {
      apps[app] = rest
      total += rest
    }
  }
  // D42: the hours too, index by index, floored at zero
  var fh = hoursOf(f, model)
  var bh = hoursOf(b, model)
  var hours = []
  for (var i = 0; i < 24; i++) hours.push(Math.max(0, fh[i] - bh[i]))
  return { total: total, apps: apps, hours: hours }
}

// Local midnight starting the day after ms (date arithmetic, so DST
// days land on the true wall-clock boundary).
function nextMidnightMs(ms) {
  var d = new Date(ms)
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1).getTime()
}

// Split [activeStart, now) across calendar days: { days, todayDur,
// resumeAt }. Every touched history day gets a fresh object; the caller
// owns today/todayDur and the reopened bucket at resumeAt. A multi-day
// suspend lands day by day instead of piling onto the start day.
function splitAcrossDays(state, model, app, activeStart, now, todayKey) {
  var d = Object.assign({}, state.days)
  var cursor = activeStart
  var guard = 0
  while (guard < 370 && model.dayKey(new Date(cursor)) !== todayKey) {
    var chunk = Math.max(0, Math.min(now, nextMidnightMs(cursor)) - cursor)
    if (chunk <= 0) break
    var key = model.dayKey(new Date(cursor))
    d[key] = addSpan(
      d[key] || model.newDay(),
      app,
      cursor,
      cursor + chunk,
      model,
    )
    cursor += chunk
    guard++
  }
  return { days: d, todayDur: Math.max(0, now - cursor), resumeAt: cursor }
}

// Fold the open bucket's time [activeStart, now) into the days, each calendar
// day its own share. closeActiveBucket then closes the bucket;
// commitElapsed keeps it open from where the fold stopped (a crash loses at
// most one interval). A suspend gap drops the time; so does a backward clock
// jump (the start lies in the future, so no duration can be trusted), which
// also re-anchors the baseline instead of billing nothing until the wall
// clock catches up.
function foldBucket(
  state,
  activeApp,
  activeStart,
  now,
  todayKey,
  suspendGapMs,
  lastTick,
  keepOpen,
) {
  if (!activeApp || !activeStart) return state
  function patch(today, days, reopenAt, tick) {
    return {
      today: today,
      days: days,
      todayKey: state.todayKey,
      activeApp: keepOpen ? activeApp : "",
      activeStart: keepOpen ? reopenAt : 0,
      lastTick: tick,
    }
  }
  if (isSuspendGap(now, lastTick, suspendGapMs))
    return patch(state.today, state.days, now, keepOpen ? state.lastTick : now)
  if (now < activeStart) return patch(state.today, state.days, now, now)
  if (Math.max(0, now - activeStart) <= 0) return state

  var model = modelFor(state)
  if (model.dayKey(new Date(activeStart)) === todayKey) {
    var today = addSpan(state.today, activeApp, activeStart, now, model)
    return patch(today, state.days, now, state.lastTick)
  }
  // Midnights crossed: history days take their shares. Closing credits today
  // its share; an open bucket keeps it in flight, reopened at its start.
  var split = splitAcrossDays(
    state,
    model,
    activeApp,
    activeStart,
    now,
    todayKey,
  )
  var closedToday =
    !keepOpen && split.todayDur > 0
      ? addSpan(state.today, activeApp, split.resumeAt, now, model)
      : state.today
  return patch(closedToday, split.days, split.resumeAt, state.lastTick)
}

function closeActiveBucket(
  state,
  activeApp,
  activeStart,
  now,
  todayKey,
  suspendGapMs,
  lastTick,
) {
  return foldBucket(
    state,
    activeApp,
    activeStart,
    now,
    todayKey,
    suspendGapMs,
    lastTick,
    false,
  )
}

function commitElapsed(
  state,
  activeApp,
  activeStart,
  now,
  todayKey,
  suspendGapMs,
  lastTick,
) {
  return foldBucket(
    state,
    activeApp,
    activeStart,
    now,
    todayKey,
    suspendGapMs,
    lastTick,
    true,
  )
}

// Carry the live bucket into a new calendar day; null when unneeded.
// `model` comes from the caller's service state (D18): advanceRollover
// hands over the fresh object closeActiveBucket returned, which carries
// no stateModel, and in QML the module-level Model is always null.
function rolloverIfNeeded(state, newKey, model) {
  if (newKey === state.todayKey) return null
  model = model || modelFor(state)
  var prev = state.days[newKey]
  var newToday =
    prev && typeof prev === "object" ? model.copyDay(prev) : model.newDay()
  return {
    todayKey: newKey,
    today: newToday,
    activeApp: state.activeApp,
    activeStart: 0,
  }
}

// Midnight in one transition (close+carry+reopen); null when unneeded.
// A backward day jump (clock stepped back) carries nothing: the live
// bucket drops and the day stays put instead of billing evening time
// onto yesterday morning.
function advanceRollover(state, now, newKey, suspendGapMs, lastTick) {
  if (newKey === state.todayKey) return null
  if (newKey < state.todayKey) {
    return {
      today: state.today,
      days: state.days,
      todayKey: state.todayKey,
      activeApp: "",
      activeStart: 0,
      lastTick: now,
    }
  }
  var app = state.activeApp
  // Close against the NEW day so the split attributes each portion exactly.
  var closed = closeActiveBucket(
    state,
    state.activeApp,
    state.activeStart,
    now,
    newKey,
    suspendGapMs,
    lastTick,
  )
  var patch = rolloverIfNeeded(closed, newKey, modelFor(state))
  patch.activeApp = app
  patch.activeStart = app ? now : 0
  // Carry the split-off yesterday portion along or lose it.
  var d = Object.assign({}, closed.days)
  // Flush unmirrored data first, computed pre-growth to avoid double count.
  var delta = dayMinus(
    state.today,
    state.days ? state.days[state.todayKey] : null,
    modelFor(state),
  )
  if (delta.total > 0) {
    var old =
      d[state.todayKey] && typeof d[state.todayKey] === "object"
        ? d[state.todayKey]
        : { total: 0, apps: {} }
    var apps = Object.assign({}, old.apps)
    var total = old.total || 0
    for (var dk in delta.apps) {
      if (!Object.prototype.hasOwnProperty.call(delta.apps, dk)) continue
      apps[dk] = (apps[dk] || 0) + delta.apps[dk]
      total += delta.apps[dk]
    }
    var hours = hoursOf(old, modelFor(state))
    for (var hi = 0; hi < 24; hi++) hours[hi] += delta.hours[hi]
    d[state.todayKey] = { total: total, apps: apps, hours: hours }
  }
  patch.days = d
  // Fold post-midnight growth into the carried day.
  var grown =
    (closed.today ? closed.today.total : 0) -
    (state.today ? state.today.total : 0)
  if (grown > 0 && app)
    patch.today = addSpan(patch.today, app, now - grown, now, modelFor(state))
  // closeActiveBucket decides lastTick (wake time on a gap, untouched
  // otherwise); the rollover carry must not lose that decision.
  if (closed.lastTick !== undefined) patch.lastTick = closed.lastTick
  return patch
}

// Apply a terminal resolve; null when stale or unchanged.
function applyResolvedApp(
  state,
  name,
  resolveForApp,
  todayKey,
  suspendGapMs,
  lastTick,
) {
  if (!state.resolveInFlight) return null
  // Same-terminal switches keep rawApp; only the generation token proves freshness.
  if (state.resolveSpawnGen !== state.resolveGeneration) return null
  if (state.rawApp !== resolveForApp) return null
  if (!name) name = state.rawApp
  var model = modelFor(state)
  name = model.canonicalApp(name)
  if (name === state.activeApp) return null
  var now = Date.now()
  var closed = closeActiveBucket(
    state,
    state.activeApp,
    state.activeStart,
    now,
    todayKey,
    suspendGapMs,
    lastTick,
  )
  return {
    resolveInFlight: false,
    activeApp: name,
    activeStart: name ? now : 0,
    today: closed.today,
    days: closed.days,
    lastTick: closed.lastTick,
  }
}

if (typeof module !== "undefined" && module && module.exports) {
  module.exports = {
    isSuspendGap: isSuspendGap,
    addSpan: addSpan,
    nextHourMs: nextHourMs,
    hoursOf: hoursOf,
    dayMinus: dayMinus,
    closeActiveBucket: closeActiveBucket,
    commitElapsed: commitElapsed,
    rolloverIfNeeded: rolloverIfNeeded,
    advanceRollover: advanceRollover,
    applyResolvedApp: applyResolvedApp,
  }
}
