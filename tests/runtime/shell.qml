// D46: the tracker's history load and save, run by the REAL Quickshell runtime.
// The FileView and JsonAdapter are declared exactly as in qml/Service.qml, so the
// day records reach Model.js the way they do live (as the runtime's array-likes,
// not JSON.parse arrays). One run: load -> sanitize -> today's copy -> one accrual
// through State.commitElapsed -> save, as Service.qml's onHistoryLoaded, heartbeat
// and persist do. A second run on the saved file is the restart.
// Driven by tests/test_runtime_hours.py; ST_* environment variables set the run.
import QtQuick
import Quickshell
import Quickshell.Io
import "js/Model.js" as Model
import "js/State.js" as State

ShellRoot {
    id: root
    property string historyPath: Quickshell.env("ST_HISTORY")
    property string todayKey: Quickshell.env("ST_TODAY")
    property real spanStart: Number(Quickshell.env("ST_START"))
    property real spanEnd: Number(Quickshell.env("ST_END"))
    property var days: ({})
    property var today: null

    function onHistoryLoaded() {
        // Service.qml onHistoryLoaded, verbatim in effect. #3: never write a file
        // this version can't keep whole; older schemas step through migrateHistory.
        var block = Model.historyWriteBlock(Model.parseHistoryText(historyFile.text()));
        if (block !== "") {
            console.log("ST-READONLY " + block);
            quitTimer.start();
            return;
        }
        if (historyAdapter.schema < Model.HISTORY_SCHEMA) {
            var migrated = Model.migrateHistory({
                "days": historyAdapter.days,
                "months": historyAdapter.months,
                "years": historyAdapter.years,
                "ext": historyAdapter.ext
            }, historyAdapter.schema);
            if (migrated.days !== historyAdapter.days)
                historyAdapter.days = migrated.days;
        }
        var clean = Model.sanitizeHistory(historyAdapter.days, historyAdapter.months, historyAdapter.years);
        if (clean.days !== historyAdapter.days || clean.months !== historyAdapter.months || clean.years !== historyAdapter.years)
            console.warn("kanso: history.json has malformed sections; ignoring them");
        root.days = clean.days;
        var prev = root.days[root.todayKey];
        root.today = prev && typeof prev === "object" ? Model.copyDay(prev) : Model.newDay();
        var raw = historyAdapter.days[root.todayKey];
        console.log("ST-PROBE " + JSON.stringify({
            "isArray": raw && raw.hours !== undefined ? Array.isArray(raw.hours) : null,
            "kind": raw && raw.hours !== undefined ? Object.prototype.toString.call(raw.hours) : null,
            "copied": root.today.hours !== undefined
        }));
        // the heartbeat's accrual (State.js loaded as QML loads it: its Model is null)
        var svc = {
            "stateModel": Model,
            "today": root.today,
            "days": root.days,
            "todayKey": root.todayKey,
            "activeApp": "editor",
            "activeStart": root.spanStart,
            "lastTick": root.spanEnd - 1000
        };
        var p = State.commitElapsed(svc, "editor", root.spanStart, root.spanEnd, root.todayKey, 30000, svc.lastTick);
        root.today = p.today;
        // Service.qml persist: today into a fresh mirror, then the adapter write
        var merged = Object.assign({}, root.days);
        merged[root.todayKey] = root.today;
        if (historyAdapter.schema !== Model.HISTORY_SCHEMA)
            historyAdapter.schema = Model.HISTORY_SCHEMA;
        historyAdapter.days = merged;
        historyFile.writeAdapter();
        console.log("ST-WROTE");
        quitTimer.start();
    }

    // the write is atomic and done by the time this fires; the test reads the file
    Timer {
        id: quitTimer
        interval: 1500
        onTriggered: Qt.quit()
    }

    FileView {
        id: historyFile
        path: root.historyPath
        printErrors: true
        atomicWrites: true
        onLoaded: root.onHistoryLoaded()
        onLoadFailed: {
            console.log("ST-LOAD-FAILED");
            Qt.quit();
        }
        onSaveFailed: console.log("ST-SAVE-FAILED")

        JsonAdapter {
            id: historyAdapter
            property int schema: 0
            property var days: ({})
            property var months: ({})
            property var years: ({})
            property var ext: ({})
        }
    }
}
