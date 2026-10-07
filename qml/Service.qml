import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import "../js/Model.js" as Model
import "../js/State.js" as State

// Functions and handlers cross-reference sibling ids; muted for the linter.
// qmllint disable unqualified

// Screen-time tracker: accrues focused time per app into per-day records.
// Persisted as { "<YYYY-MM-DD>": { total, apps } }; 60s commits bound
// crash loss. Transitions live in State.js; this file owns timers,
// disk I/O, processes and bindings.
Item {
    id: root

    // Injected by omarchy-shell.
    property var shell: null
    // Passed explicitly; QML JS modules don't share imports.
    readonly property var stateModel: Model

    readonly property string home: Quickshell.env("HOME")
    readonly property string dataDir: home + "/.config/omarchy/screen-time"
    readonly property string historyPath: dataDir + "/history.json"
    // D20: days past keepDays, apps and all, live in archive/<year>.json
    readonly property string archiveDir: dataDir + "/archive"
    readonly property string archiverPath: {
        var u = Qt.resolvedUrl("../python/archive_days.py").toString();
        return u.startsWith("file://") ? u.slice(7) : u;
    }
    // One archive run at a time; a failed run waits for the next day (or start).
    property bool archiveInFlight: false
    property string archiveFailedOn: ""
    // Shared process env (HOME for ~ expansion). Typed var so the
    // Map-vs-Hash literal inference stays in one audited place.
    readonly property var procEnv: ({
            "HOME": home
        })
    readonly property string resolverPath: {
        var u = Qt.resolvedUrl("../python/resolve_app.py").toString();
        return u.startsWith("file://") ? u.slice(7) : u;
    }

    // Terminals report the window class; resolve the pty foreground instead.
    // Wayland terminals report either a short binary name or a reverse-DNS
    // app id, depending on the desktop file they ship. Ghostty, Kitty and
    // WezTerm all use reverse-DNS under Hyprland, so matching short names
    // alone leaves them tracked as ordinary apps and skips the resolver that
    // attributes their time to the command being run.
    readonly property var terminalAppIds: ["foot", "alacritty", "kitty", "ghostty", "wezterm", "konsole", "gnome-terminal", "tilix", "xfce4-terminal", "termite", "st", "org.omarchy.terminal", "com.mitchellh.ghostty", "net.kovidgoyal.kitty", "org.wezfurlong.wezterm", "org.gnome.terminal", "org.gnome.console", "org.kde.konsole", "com.raggesilver.blackbox", "dev.warp.warp", "io.elementary.terminal"]

    // App-detail window in days, fixed: the value upstream agx used on this
    // machine (its weekly-graph length was never set), so switching never
    // prunes anything. Days
    // that age past it roll their totals into the perpetual per-day archive.
    readonly property int keepDays: 365

    // A tick later than this means the loop froze: suspend or clock jump.
    readonly property int suspendGapMs: 30 * 1000
    property double lastTick: 0

    // Live state: always REPLACED, never mutated, so bindings fire.
    property string todayKey: Model.dayKey(new Date())
    property var today: Model.newDay()
    // Disk mirror; root.today is the source of truth.
    property var days: ({})
    // Pre-archive monthly lumps; never overlaps the day archive.
    property var months: ({})
    // Per-day archive keeping retro facts past the raw window.
    property var years: ({})

    property string activeApp: ""
    property double activeStart: 0
    // Raw compositor appId; activeApp is the resolved name.
    property string rawApp: ""
    property string resolveForApp: ""
    property bool resolveInFlight: false
    // Generation tokens stop stale terminal resolves misattributing.
    property int resolveGeneration: 0
    property int resolveSpawnGen: 0
    property bool ready: false
    property bool startupPhase: true
    // Mirrors omarchy's first-party session services (omarchy.lock,
    // omarchy.idle) so tracking pauses during lock/screensaver without
    // touching the compositor or spawning helpers on the hot path.
    // startedAt timestamps only report pause durations in debug logs.
    property bool sessionLocked: false
    property bool screensaverActive: false
    property double lockStartedAt: 0
    property double screensaverStartedAt: 0
    // Debug timing logs for lock/screensaver intervals; off by default.
    property bool debugLogging: false
    property var lockService: null
    property var idleService: null
    // Shell injects the plugin API asynchronously; re-run lookups on change.
    onShellChanged: root.refreshShellServices()

    // ---- State transition helpers ------------------------------------------
    // Spread a State.js patch onto live props so bindings fire.
    function applyState(patch) {
        if (!patch)
            return;
        if (patch.today !== undefined)
            root.today = patch.today;
        if (patch.days !== undefined)
            root.days = patch.days;
        if (patch.todayKey !== undefined)
            root.todayKey = patch.todayKey;
        if (patch.activeApp !== undefined)
            root.activeApp = patch.activeApp;
        if (patch.activeStart !== undefined)
            root.activeStart = patch.activeStart;
        if (patch.lastTick !== undefined)
            root.lastTick = patch.lastTick;
        if (patch.resolveInFlight !== undefined)
            root.resolveInFlight = patch.resolveInFlight;
    }

    // ---- Tracking ----------------------------------------------------------

    function isTerminal(appId) {
        return appId && root.terminalAppIds.indexOf(appId.toLowerCase()) !== -1;
    }

    // steam_app_<id> resolves to the game title via local manifests.
    function isSteamApp(appId) {
        return appId && appId.toLowerCase().indexOf("steam_app_") === 0;
    }

    // Ignored apps are never counted (fed from names.json in a later
    // commit). Normalized on write so readers compare lowercase keys only.
    // Names are display-only: stored keys are never renamed.
    property var ignoredApps: []
    function setIgnoredApps(ignored) {
        root.ignoredApps = Model.parseIgnoredApps(ignored);
        // An app ignored mid-focus stops accruing now, not at the next
        // focus switch.
        if (root.ready && root.activeApp && Model.isIgnoredApp(root.activeApp, root.ignoredApps)) {
            var now = Date.now();
            applyState(State.closeActiveBucket(root, root.activeApp, root.activeStart, now, root.todayKey, root.suspendGapMs, root.lastTick));
            root.activeApp = "";
            root.activeStart = 0;
            root.persist();
        }
    }

    // names.json is the only source of the ignore list; renames and hides
    // there are display-only and belong to the engine. Watched live, because
    // editors and agents rewrite it at any time.
    readonly property string namesPath: dataDir + "/names.json"
    property var namesState: ({
            "ignore": [],
            "warned": false
        })
    // FileView only watches a file it has loaded once, so a names.json created
    // after startup would go unseen until a shell restart (measured on
    // Quickshell 0.3.1). Poll while it is missing; the watch takes over after.
    property bool namesMissing: false
    function onNamesEvent(kind, text) {
        var wasMissing = root.namesMissing;
        root.namesMissing = kind === "missing";
        if (kind === "missing" && wasMissing)
            return;
        var next = Model.applyNamesFile(root.namesState, kind, text);
        root.namesState = {
            "ignore": next.ignore,
            "warned": next.warned
        };
        if (next.warn)
            console.warn("kanso: " + next.warn);
        root.setIgnoredApps(next.ignore);
    }

    FileView {
        id: namesFile
        path: root.namesPath
        watchChanges: true
        printErrors: false
        onLoaded: root.onNamesEvent("loaded", text())
        onLoadFailed: function (error) {
            root.onNamesEvent(error === FileViewError.FileNotFound ? "missing" : "failed", "");
        }
        onFileChanged: reload()
    }

    Timer {
        id: namesRetryTimer
        interval: 5000
        repeat: true
        running: root.namesMissing
        onTriggered: namesFile.reload()
    }

    // ---- The engine (D14) ---------------------------------------------------
    // python/screen_time.py computes everything the card shows. It runs here,
    // once for the whole shell (every bar's card paints `card`, so two docked
    // bars never mean two runs), at startup and every 60 s. Nothing spawns on
    // hover. A run that prints no readable card keeps the last good one.
    readonly property string enginePath: {
        var u = Qt.resolvedUrl("../python/screen_time.py").toString();
        return u.startsWith("file://") ? u.slice(7) : u;
    }
    property var card: null
    property bool engineOk: false
    property string engineError: ""
    property double lastRunMs: 0
    property int engineRuns: 0
    function applyEngineOutput(raw) {
        var result = Model.acceptCard(root.card, raw);
        root.card = result.card;
        root.engineOk = result.ok;
        root.engineError = result.error;
        root.engineRuns++;
        root.lastRunMs = Date.now();
    }

    // What the status IPC reports (5.4): states, ages and counts, no times
    // per app. names.ignore_list is this service's names.json reader (the
    // ignore list); names.engine is how the engine read it (renames, hides).
    function status() {
        return {
            "ready": root.ready,
            "engine_ok": root.engineOk,
            "engine_error": root.engineError,
            "engine_runs": root.engineRuns,
            "last_run_age_s": root.lastRunMs > 0 ? Math.round((Date.now() - root.lastRunMs) / 1000) : null,
            "card_state": root.card ? root.card.state : null,
            "card_schema": root.card ? root.card.schema : null,
            "stored_keys": Model.storedKeyCount(root.days),
            "ignored_apps": root.ignoredApps.length,
            "names": {
                "ignore_list": root.namesMissing ? "missing" : (root.namesState.warned ? "invalid" : "ok"),
                "engine": root.card && root.card.names ? root.card.names.state : null
            }
        };
    }

    // `timeout` bounds a hung run; the next tick starts a fresh one.
    Process {
        id: engineProc
        command: ["timeout", "20", "python3", root.enginePath, "card"]
        stdout: StdioCollector {
            id: engineOut
            waitForEnd: true
        }
        onExited: root.applyEngineOutput(engineOut.text)
    }

    Timer {
        id: engineTimer
        interval: 60000
        repeat: true
        running: true
        triggeredOnStart: true
        onTriggered: {
            if (!engineProc.running)
                engineProc.running = true;
        }
    }

    // Screensaver/portal windows open no bucket.
    function shouldTrack(appId) {
        if (!appId)
            return false;
        var id = String(appId).toLowerCase();
        if (id === "org.omarchy.screensaver")
            return false;
        if (id.indexOf("xdg-desktop-portal") === 0)
            return false;
        if (Model.isIgnoredApp(appId, root.ignoredApps))
            return false;
        return true;
    }

    function switchActive() {
        // Pre-ready focus events open unguarded buckets (and defeat the
        // lastTick baseline); the load handlers call back once ready.
        if (!root.ready)
            return;
        var now = Date.now();
        applyState(State.closeActiveBucket(root, root.activeApp, root.activeStart, now, root.todayKey, root.suspendGapMs, root.lastTick));
        root.persist();
        var tl = ToplevelManager.activeToplevel;
        var app = tl && tl.appId ? tl.appId : "";
        root.rawApp = app;
        root.resolveInFlight = false;
        // Paused (locked or screensaver up): keep the bucket closed.
        // Toplevel events still fire under lock; reopening here would
        // accrue straight through the pause.
        if (root.sessionLocked || root.screensaverActive) {
            root.activeApp = "";
            root.activeStart = 0;
            return;
        }
        if (app && !root.shouldTrack(app)) {
            root.activeApp = "";
            root.activeStart = 0;
            return;
        }
        if (app && (root.isTerminal(app) || root.isSteamApp(app))) {
            root.activeApp = "";
            root.activeStart = 0;
            root.beginResolve();
        } else {
            root.activeApp = Model.canonicalApp(app);
            root.activeStart = app ? now : 0;
        }
    }

    // Re-resolve the focused terminal; a new request invalidates the running one.
    function beginResolve() {
        root.resolveForApp = root.rawApp;
        root.resolveInFlight = true;
        root.resolveGeneration++;
        if (!resolverProc.running) {
            root.resolveSpawnGen = root.resolveGeneration;
            resolverProc.running = true;
        }
    }

    // Refreshes terminals whose foreground changed mid-focus.
    function applyResolvedApp(name) {
        // Paused: drop the result so an in-flight resolver landing mid-lock
        // cannot reopen a bucket. Unlock re-resolves via switchActive().
        if (root.sessionLocked || root.screensaverActive) {
            root.resolveInFlight = false;
            root.resolveForApp = "";
            return;
        }
        var patch = State.applyResolvedApp(root, name, root.resolveForApp, root.todayKey, root.suspendGapMs, root.lastTick);
        // Always clear, even on no-op, so refresh isn't watchdog-gated.
        root.resolveInFlight = false;
        applyState(patch);
        if (patch)
            root.persist();
    }

    // Fold the in-flight bucket in; a crash loses at most one interval.
    function commitElapsed(now) {
        if (!root.ready || !root.activeApp || !root.activeStart)
            return;
        applyState(State.commitElapsed(root, root.activeApp, root.activeStart, now, root.todayKey, root.suspendGapMs, root.lastTick));
    }

    function rolloverIfNeeded() {
        var key = Model.dayKey(new Date());
        var now = Date.now();
        // One transition owns midnight (close+carry+reopen); no ordering slip.
        var patch = State.advanceRollover(root, now, key, root.suspendGapMs, root.lastTick);
        if (!patch)
            return;
        applyState(patch);
        root.persist();
    }

    // ---- Persistence -------------------------------------------------------

    // Fold today into a fresh mirror object so the adapter notifier fires.
    // Writes wait while the corrupt-file backup runs.
    property bool backupPending: false
    function persist() {
        if (root.startupPhase || root.backupPending)
            return;
        var merged = Object.assign({}, root.days);
        merged[root.todayKey] = root.today;
        // D20: days past keepDays go to archive/<year>.json with their apps, and
        // leave history.json only once the archive confirms them (onArchived).
        // A save starts the archiver only when days have rolled out.
        var roll = Model.rolloutDays(merged, root.todayKey, root.keepDays);
        if (Model.shouldArchive(roll.out, root.archiveInFlight, root.archiveFailedOn, root.todayKey))
            root.startArchive(roll.out);
        root.days = merged;
        historyAdapter.days = merged;
    }

    function startArchive(out) {
        root.archiveInFlight = true;
        archiveProc.request = JSON.stringify({
            "dir": root.archiveDir,
            "days": out
        });
        archiveProc.expected = Object.keys(out).length;
        archiveProc.running = true;
    }

    // The archive confirmed `text`'s keys: drop exactly those from history.json.
    // Anything short of every rolled-out day marks today failed: no retry storm.
    function onArchived(text, expected) {
        root.archiveInFlight = false;
        var keys = Model.parseArchived(text);
        if (keys.length < expected) {
            root.archiveFailedOn = root.todayKey;
            console.warn("kanso: archive confirmed " + keys.length + " of " + expected + " days; they stay in history.json until the next try");
        }
        if (keys.length === 0)
            return;
        root.days = Model.confirmArchived(root.days, keys);
        historyAdapter.days = root.days;
    }

    // Failure streak; any scheduled save resets it.
    property int saveFailCount: 0

    function scheduleSave() {
        if (root.startupPhase || root.backupPending)
            return;
        // Start, never restart: continuous focus flapping must not defer
        // the write indefinitely past the crash window.
        if (!saveTimer.running)
            saveTimer.start();
    }

    function onHistoryLoaded() {
        // Non-object sections are discarded with a single warning.
        var clean = Model.sanitizeHistory(historyAdapter.days, historyAdapter.months, historyAdapter.years);
        if (clean.days !== historyAdapter.days || clean.months !== historyAdapter.months || clean.years !== historyAdapter.years)
            console.warn("kanso: history.json has malformed sections; ignoring them");
        var d = clean.days;
        var m = clean.months;
        // D20: nothing is pruned at load; the first save hands days past the
        // window to the archiver. `years` is the legacy totals-only archive:
        // read as is, never grown.
        root.months = m;
        root.days = d;
        root.years = clean.years;
        if (!root.ready) {
            root.todayKey = Model.dayKey(new Date());
            var prev = d[root.todayKey];
            // D42: Model.copyDay keeps the day's hours (a restart mid-hour loses none)
            root.today = prev && typeof prev === "object" ? Model.copyDay(prev) : Model.newDay();
            root.ready = true;
            root.startupPhase = false;
            root.lastTick = Date.now();
            root.switchActive();
        } else {
            // Retry keeps the live bucket; refresh the mirror only.
            var nd = Object.assign({}, root.days);
            nd[root.todayKey] = root.today;
            root.days = nd;
        }
    }

    function onHistoryLoadFailed() {
        // Corrupt files are preserved aside; tracking starts empty immediately.
        console.warn("kanso: history load failed, starting empty");
        if (!root.backupAttempted) {
            root.backupAttempted = true;
            root.backupPending = true;
            backupProc.running = true;
        }
        if (!root.ready) {
            root.days = {};
            root.todayKey = Model.dayKey(new Date());
            root.ready = true;
            root.startupPhase = false;
            root.lastTick = Date.now();
            root.switchActive();
        }
    }

    FileView {
        id: historyFile
        path: root.historyPath
        printErrors: true
        atomicWrites: true
        onAdapterUpdated: {
            // Fresh data resets the failure streak.
            root.saveFailCount = 0;
            root.scheduleSave();
        }
        onLoaded: root.onHistoryLoaded()
        onLoadFailed: root.onHistoryLoadFailed()
        onSaveFailed: function (error) {
            // Retry with capped backoff; suspend after 6 straight failures.
            root.saveFailCount++;
            if (root.saveFailCount > 6) {
                console.warn("kanso: history save failed (" + FileViewError.toString(error) + "), suspending retries until next change");
                return;
            }
            var delay = Math.min(1500 * Math.pow(2, root.saveFailCount - 1), 60000);
            console.warn("kanso: history save failed (" + FileViewError.toString(error) + "), retrying in " + delay + "ms");
            saveRetryTimer.interval = delay;
            saveRetryTimer.restart();
        }

        // FileViewAdapter is C++-only in Quickshell: complete at runtime,
        // incomplete to the linter. Muted via the ini (UnresolvedType/
        // TypeError) instead of a scoped directive, because that directive
        // name is unknown to qmllint 6.4.
        JsonAdapter {
            id: historyAdapter
            property var days: ({})
            property var months: ({})
            property var years: ({})
        }
    }

    // D20: hand rolled-out days to python/archive_days.py, one JSON line on stdin.
    Process {
        id: archiveProc
        property string request: ""
        property int expected: 0
        environment: root.procEnv
        command: ["timeout", "60", "python3", root.archiverPath]
        stdinEnabled: true
        onStarted: {
            write(request + "\n");
            request = "";
        }
        stdout: StdioCollector {
            id: archiveOut
        }
        onExited: root.onArchived(archiveOut.text, archiveProc.expected)
    }

    // QProcess::ExitStatus never loads into lint; handlers take no args.
    // Muted via the ini (BadSignalHandler/Parameters), see .qmllint.ini.
    Process {
        id: ensureDirProc
        environment: root.procEnv
        command: ["bash", "-c", "mkdir -p \"$HOME/.config/omarchy/screen-time\"; f=\"$HOME/.config/omarchy/screen-time/history.json\"; [[ -f \"$f\" ]] || printf '{}\\n' > \"$f\""]
        onExited: historyFile.reload()
    }

    // Polls for missed focus events; real switches are event-driven.
    Timer {
        id: reconcileTimer
        interval: 2000
        repeat: true
        running: root.ready
        onTriggered: {
            var tl = ToplevelManager.activeToplevel;
            var app = tl && tl.appId ? tl.appId : "";
            if (app !== root.rawApp)
                root.switchActive();
        }
    }

    // Move aside non-empty files that fail to parse. The validity check
    // uses python3 when present, but the move itself never depends on
    // it: without python an unreadable file is still preserved aside
    // instead of being overwritten on the next save.
    property bool backupAttempted: false
    Process {
        id: backupProc
        environment: root.procEnv
        command: ["bash", "-c", "f=\"$HOME/.config/omarchy/screen-time/history.json\"; if [[ -s \"$f\" ]]; then if command -v python3 >/dev/null 2>&1 && python3 -c 'import json,sys; json.load(open(sys.argv[1]))' \"$f\" 2>/dev/null; then :; else mv -f \"$f\" \"$f.corrupt-$(date +%s)\"; fi; fi"]
        onExited: {
            // Unblock writes; queued state persists on the next tick.
            root.backupPending = false;
            root.persist();
        }
    }

    // Foreground can change without compositor notice; re-resolve live.
    Timer {
        id: terminalRefreshTimer
        interval: 5000
        repeat: true
        running: root.ready && root.isTerminal(root.rawApp) && !root.resolveInFlight
        onTriggered: root.beginResolve()
    }

    // Kill hung resolvers so refresh can start a fresh process.
    // No generation bump needed: every switchActive clears
    // resolveInFlight, and beginResolve re-syncs the tokens, so a late
    // exit only ever matches a live run of the same terminal.
    Timer {
        id: resolveWatchdog
        interval: 10000
        repeat: false
        running: root.resolveInFlight
        onTriggered: {
            root.resolveInFlight = false;
            if (resolverProc.running)
                resolverProc.running = false;
        }
    }

    // Empty stdout falls back to rawApp; stderr is logged so breakage is visible.
    // sh wrapper: missing python3 still exits 0 instead of stalling to watchdog.
    Process {
        id: resolverProc
        command: ["sh", "-c", "command -v python3 >/dev/null 2>&1 && exec python3 \"$1\" || exit 0", "sh", root.resolverPath]
        stdout: StdioCollector {
            id: resolverOut
            waitForEnd: true
        }
        stderr: StdioCollector {
            id: resolverErr
            waitForEnd: true
        }
        onExited: {
            var err = resolverErr.text.trim();
            if (err)
                console.warn("kanso: resolver stderr:", err);
            root.applyResolvedApp(resolverOut.text.trim());
        }
    }

    // ---- Session pause (lock / screensaver) ----------------------------------
    // Lock state comes from omarchy.lock at the source instead of polling
    // loginctl, so lock/unlock needs no extra process spawning. The shell's
    // service registry fills asynchronously; lookups retry until both
    // services resolve, transitions are event-driven afterward.
    function refreshShellServices() {
        if (!root.shell)
            return;
        root.lockService = root.shell.serviceFor("omarchy.lock");
        root.idleService = root.shell.serviceFor("omarchy.idle");
        if (root.lockService) {
            root.setSessionLocked(root.lockService.locked);
            // Event-driven source wins once reachable; stop the fallback
            // watcher so it can't fight it or leak a bash loop.
            sessionStateWatcher.running = false;
        }
        if (root.idleService)
            root.setScreensaverActive(root.idleService.screensaverStartedThisCycle || root.idleService.screensaverWindowCount > 0);
        if (root.lockService && root.idleService) {
            serviceLookupTimer.stop();
            root.serviceLookupWarned = false;
        }
    }

    function setSessionLocked(locked) {
        locked = locked === true;
        if (locked === root.sessionLocked)
            return;
        root.sessionLocked = locked;
        if (locked) {
            root.lockStartedAt = Date.now();
            root.cancelResume();
            // Kill in-flight terminal resolves; their results would reopen
            // a bucket through the pause (see applyResolvedApp).
            root.resolveInFlight = false;
            root.resolveForApp = "";
            if (root.debugLogging)
                console.warn("kanso: lock started");
            var now = Date.now();
            applyState(State.closeActiveBucket(root, root.activeApp, root.activeStart, now, root.todayKey, root.suspendGapMs, root.lastTick));
            root.persist();
        } else {
            var endedAt = Date.now();
            var duration = root.lockStartedAt ? endedAt - root.lockStartedAt : 0;
            if (root.debugLogging)
                console.warn("kanso: lock ended, duration=" + duration + "ms");
            root.lockStartedAt = 0;
            // Deferred: with 10s state sources an "unpaused" reading can be
            // stale (screensaver flag clearing just before lock engages).
            // applyResume re-validates both flags before reopening.
            root.scheduleResume();
        }
    }

    function setScreensaverActive(active) {
        active = active === true;
        if (active === root.screensaverActive)
            return;
        root.screensaverActive = active;
        if (active) {
            root.screensaverStartedAt = Date.now();
            root.cancelResume();
            if (root.debugLogging)
                console.warn("kanso: screensaver started");
            var now = Date.now();
            applyState(State.closeActiveBucket(root, root.activeApp, root.activeStart, now, root.todayKey, root.suspendGapMs, root.lastTick));
            root.persist();
        } else {
            var endedAt = Date.now();
            var duration = root.screensaverStartedAt ? endedAt - root.screensaverStartedAt : 0;
            if (root.debugLogging)
                console.warn("kanso: screensaver ended, duration=" + duration + "ms");
            root.screensaverStartedAt = 0;
            if (!root.sessionLocked)
                root.scheduleResume();
        }
    }

    // Unpauses schedule a resume instead of reopening immediately, so a
    // stale reading from a ~10s state source can't briefly reopen a bucket.
    property bool resumePending: false
    Timer {
        id: resumeTimer
        interval: 2000
        repeat: false
        onTriggered: root.applyResume()
    }
    function scheduleResume() {
        root.resumePending = true;
        resumeTimer.restart();
    }
    function cancelResume() {
        root.resumePending = false;
        resumeTimer.stop();
    }
    function applyResume() {
        if (root.sessionLocked || root.screensaverActive) {
            root.resumePending = false;
            return;
        }
        if (!root.resumePending)
            return;
        root.resumePending = false;
        // Rebase to the resume instant: the pause may have ended up to two
        // seconds ago, and reopening must not bill from the stale lastTick.
        var now = Date.now();
        root.lastTick = now;
        root.switchActive();
    }

    property bool serviceLookupWarned: false

    Timer {
        id: serviceLookupTimer
        interval: 250
        repeat: true
        running: root.ready && (!root.lockService || !root.idleService)
        property int attempts: 0
        onTriggered: {
            root.refreshShellServices();
            attempts++;
            if (attempts >= 40 && !root.serviceLookupWarned) {
                // Sandboxed serviceFor may never resolve these (scoped to a
                // plugin's own service). Warn once instead of failing silent.
                root.serviceLookupWarned = true;
                console.warn("kanso: omarchy.lock/omarchy.idle services unavailable after 10s; " + "falling back to a persistent lock watcher (~10s pause accuracy)");
            }
        }
    }

    // Fallback when sandboxed lookups can't reach lock/idle services: one
    // persistent watcher checking lock every 10s, printing only on change.
    // Stops automatically if event-driven lookups ever succeed.
    Process {
        id: sessionStateWatcher
        environment: root.procEnv
        command: ["bash", "-c", "ppid=$PPID; prev=''; while :; do " + "kill -0 $ppid 2>/dev/null || exit 0; " + "cur=$(omarchy-shell lock isLocked 2>/dev/null); " + "if [ -n \"$cur\" ] && [ \"$cur\" != \"$prev\" ]; then printf '%s\\n' \"$cur\"; prev=\"$cur\"; fi; " + "sleep 10; done"]
        stdout: SplitParser {
            onRead: function (line) {
                root.setSessionLocked(String(line).trim() === "true");
            }
        }
    }

    // Restarts the watcher if it exits while lock service stays unreachable.
    Timer {
        id: watcherSupervisorTimer
        interval: 5000
        repeat: true
        running: root.ready && !root.lockService
        onTriggered: {
            if (!root.lockService && !sessionStateWatcher.running)
                sessionStateWatcher.running = true;
        }
    }

    // Screensaver fallback is an in-process toplevel scan (no spawning),
    // running only while the idle service is unreachable.
    Timer {
        id: screensaverScanTimer
        interval: 10000
        repeat: true
        running: root.ready && !root.idleService
        onTriggered: root.setScreensaverActive(root.screensaverWindowVisible())
    }

    // omarchy launches the screensaver as "org.omarchy.screensaver", so a
    // toplevel scan is a reliable sandbox-visible proxy for "screensaver up".
    function screensaverWindowVisible() {
        var toplevels = ToplevelManager.toplevels;
        if (!toplevels || !toplevels.length)
            return false;
        for (var i = 0; i < toplevels.length; i++) {
            var t = toplevels[i];
            if (t && t.appId && String(t.appId).toLowerCase() === "org.omarchy.screensaver")
                return true;
        }
        return false;
    }

    // Event-driven sources, used whenever the sandboxed lookups succeed.
    Connections {
        target: root.lockService
        function onLockedChanged() {
            root.setSessionLocked(root.lockService.locked);
        }
    }

    Connections {
        target: root.idleService
        function onScreensaverStartedThisCycleChanged() {
            root.setScreensaverActive(root.idleService.screensaverStartedThisCycle);
        }
        function onScreensaverWindowCountChanged() {
            root.setScreensaverActive(root.idleService.screensaverWindowCount > 0);
        }
    }

    // Fresh baseline resolves suspends down to ~30s.
    Timer {
        id: heartbeatTimer
        interval: 5000
        repeat: true
        running: root.ready
        onTriggered: {
            var now = Date.now();
            if (State.isSuspendGap(now, root.lastTick, root.suspendGapMs)) {
                applyState(State.closeActiveBucket(root, root.activeApp, root.activeStart, now, root.todayKey, root.suspendGapMs, root.lastTick));
                // Roll past midnight before reopening, or wake seconds land on yesterday.
                root.rolloverIfNeeded();
                root.persist();
                root.switchActive();
            } else {
                root.rolloverIfNeeded();
                root.commitElapsed(now);
                root.persist();
            }
            root.lastTick = now;
        }
    }

    Timer {
        id: commitTimer
        interval: 60000
        repeat: true
        running: root.ready
        onTriggered: {
            var now = Date.now();
            root.rolloverIfNeeded();
            root.commitElapsed(now);
            root.persist();
            root.lastTick = now;
        }
    }

    Timer {
        id: saveTimer
        interval: 1500
        repeat: false
        onTriggered: historyFile.writeAdapter()
    }

    // Save-retry driver (backoff computed in onSaveFailed).
    Timer {
        id: saveRetryTimer
        repeat: false
        onTriggered: historyFile.writeAdapter()
    }

    Connections {
        target: ToplevelManager
        function onActiveToplevelChanged() {
            root.switchActive();
        }
    }

    Component.onCompleted: {
        ensureDirProc.running = true;
    }
}
