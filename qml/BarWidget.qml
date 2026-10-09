pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import "../js/Pager.js" as Pager

// Bar button: the hourglass glyph only (no number, no settings, no tint).
// Hover opens the card (HoverCard.qml); the engine runs in Service on a 60 s
// timer (D14) and the card paints Service's last good result, so a hover never
// spawns a process. Only three things run one: a right-click or the copy/save
// IPC verbs (the report script, bin/screen-time-report, D33), and a ‹ › on a
// detail page (one `card --page kind:key` run, V1).
BarWidget {
    id: root
    moduleName: "io.github.nathankramm.kanso"

    readonly property var service: bar && bar.shell ? bar.shell.serviceFor("io.github.nathankramm.kanso") : null
    readonly property string glyph: "󰔟"
    readonly property var card: service ? service.card : null
    // #1: the Service code version this widget was built with (Service.qml
    // serviceVersion). An update reloads this widget but keeps the old Service
    // running (keepLoaded) until the shell restarts: tracking runs old code,
    // and an old Model rejects a newer engine's card and keeps showing stale
    // numbers. Say so instead of staying silent. 0.9.0's Service has no
    // serviceVersion (undefined), so it reads as older.
    readonly property int expectedServiceVersion: 2
    readonly property bool serviceStale: root.service !== null && root.service.serviceVersion !== root.expectedServiceVersion
    // #3: the Service won't write a history.json it can't keep whole.
    readonly property bool historyBlocked: root.service !== null && root.service.historyReadOnly === true
    // One muted line, in the glance's footer and the pages' footer.
    readonly property string noticeShort: root.serviceStale ? "Restart the shell to finish updating" : (root.historyBlocked ? "Not saving history: update Kanso" : "")
    readonly property string noticeLong: root.serviceStale ? "Run omarchy restart shell to finish updating" : (root.historyBlocked ? "Not saving history.json: update Kanso" : "")
    // Static UI text, shown only while there is no good card yet (D14).
    readonly property string fallbackText: {
        if (!root.service)
            return "Screen time is not running";
        if (root.service.engineError !== "")
            return "Screen time: " + root.service.engineError;
        return "Screen time is starting";
    }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    // ---- the hover card. A passive overlay with no focus grab, so this widget
    //      owns its life (nathan.monarch's machine, the clock and portfolio
    //      cards' timings):
    //        open  after 250 ms of the button hovered;
    //        stay  while the button or the card is hovered;
    //        close 300 ms after both have left, and at once when another
    //              owner takes the popout.
    readonly property bool hoverWanted: button.tooltipHovered === true || hoverPopup.containsMouse
    property string hoverState: "closed"       // closed | opening | open | closing | forced

    onHoverWantedChanged: {
        if (hoverWanted) {
            closeTimer.stop();
            if (!hoverPopup.open) {
                root.hoverState = "opening";
                openTimer.restart();
            } else {
                root.hoverState = "open";
            }
        } else {
            openTimer.stop();
            if (hoverPopup.open) {
                root.hoverState = "closing";
                closeTimer.restart();
            } else {
                root.hoverState = "closed";
            }
        }
    }

    function openHoverCard() {
        closeTimer.stop();
        hoverPopup.open = true;
        root.hoverState = "forced";
    }

    function closeHoverCard() {
        openTimer.stop();
        closeTimer.stop();
        hoverPopup.open = false;
        root.hoverState = "closed";
    }

    Timer {
        id: openTimer
        interval: 250
        onTriggered: {
            if (root.hoverWanted) {
                hoverPopup.open = true;
                root.hoverState = hoverPopup.open ? "open" : "closed";
            }
        }
    }

    Timer {
        id: closeTimer
        interval: 300
        onTriggered: {
            if (!root.hoverWanted)
                root.closeHoverCard();
        }
    }

    Connections {
        target: root.bar
        ignoreUnknownSignals: true
        function onActivePopoutChanged() {
            // Bar.requestPopout closes the old owner (whose release blanks
            // activePopout) before naming the new one: null here is that
            // hand-off passing through, not a takeover (nathan.monarch's
            // 2026-10-02 fix).
            var active = root.bar ? root.bar.activePopout : null;
            if (hoverPopup.open && active && active !== hoverPopup.coordinatorKey)
                root.closeHoverCard();
        }
    }

    // The card keeps the PopupCard's own coordinator key (no `owner`).
    PopupCard {
        id: hoverPopup
        anchorItem: button
        bar: root.bar
        triggerMode: "hover"
        // contentWidth/contentHeight are the WHOLE popup, padding and border
        // included, so the insets are added here: 460 on screen.
        contentWidth: hoverPopup.fittedContentWidth(hoverCard.implicitWidth + hoverPopup.padding * 2 + Border.left(hoverPopup.borderSpec) + Border.right(hoverPopup.borderSpec))
        contentHeight: hoverPopup.fittedContentHeight(hoverCard.implicitHeight)
        onOpenChanged: {
            if (!open) {
                // Closing the card returns it to the glance (D16) and every tab to
                // its current page; a page run still out paints nothing (V1).
                hoverCard.page = "glance";
                root.pager = Pager.reset(root.pager);
            }
            if (!open && root.hoverState !== "closed") {
                openTimer.stop();
                closeTimer.stop();
                root.hoverState = "closed";
            }
        }

        HoverCard {
            id: hoverCard
            doc: root.card
            fallbackText: root.card ? "" : root.fallbackText
            notice: root.noticeShort
            noticeDetail: root.noticeLong
            foreground: Color.popups.text
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
            shown: root.pager.shown
            failedPage: root.pager.failed
            onPageRequested: (page, key) => root.requestPage(page, key)
        }
    }

    // ---- past pages (V1). The card holds only the current day, week, month and
    //      year; an arrow asks for one other page: one engine run, here, never in
    //      Service and never on a hover. js/Pager.js decides what paints: the
    //      latest request only, the last good page kept until it does.
    property var pager: Pager.initial()

    function requestPage(page, key) {
        var current = root.card ? root.card[Pager.engineKind(page)] : null;
        var r = Pager.request(root.pager, page, key, current ? current.key : "");
        root.pager = r.state;
        root.runPage(r.spawn);
    }

    function runPage(request) {
        if (!request)
            return;
        if (!root.service || pageProc.running) {
            root.pageFinished("");
            return;
        }
        pageProc.command = ["timeout", "20", "/usr/bin/python3", root.service.enginePath, "card", "--page", Pager.engineKind(request.page) + ":" + request.key];
        pageProc.running = true;
    }

    function pageFinished(text) {
        var r = Pager.finished(root.pager, text);
        root.pager = r.state;
        // the queued request starts after this run's handler has returned
        if (r.spawn)
            Qt.callLater(root.runPage, r.spawn);
    }

    Process {
        id: pageProc
        stdout: StdioCollector {
            id: pageOut
        }
        // QProcess::ExitStatus never loads into lint; the handler takes no args.
        onExited: root.pageFinished(pageOut.text)
    }

    //   omarchy-shell io.github.nathankramm.kanso open|close|toggle   # the card (screenshot aid)
    //   omarchy-shell io.github.nathankramm.kanso status | jq
    // bin/screen-time-report: copy or save `screen-time report --md` (D33). Run
    // detached through a login shell, as the shell's own bar.run does, so the
    // Omarchy commands it calls are on PATH.
    readonly property string reportScript: {
        var u = Qt.resolvedUrl("../bin/screen-time-report").toString();
        return u.startsWith("file://") ? u.slice(7) : u;
    }
    function runReport(verb) {
        Quickshell.execDetached(["bash", "-lc", 'exec "$@"', "bash", root.reportScript, verb]);
    }

    IpcHandler {
        target: "io.github.nathankramm.kanso"
        function open(): void {
            root.openHoverCard();
        }
        function close(): void {
            root.closeHoverCard();
        }
        function show(): void {
            root.openHoverCard();
        }
        function hide(): void {
            root.closeHoverCard();
        }
        function toggle(): void {
            if (hoverPopup.open)
                root.closeHoverCard();
            else
                root.openHoverCard();
        }
        // One JSON line: the engine, the card, stored keys, names.json, and
        // this bar's hover state. There is no idle state.
        // The report (D33): `copy` to the clipboard, `save` to ~/Documents.
        // (`export` is a reserved word in QML's JavaScript, so it cannot name a verb.)
        function copy(): void {
            root.runReport("copy");
        }
        function save(): void {
            root.runReport("export");
        }
        function status(): string {
            var out = root.service ? root.service.status() : {
                "service": "not running"
            };
            out.hover_state = root.hoverState;
            out.hover_open = hoverPopup.open;
            out.page = hoverCard.page;
            // the past page each tab shows ("" = the current one), and page runs
            out.shown = {};
            for (var i = 0; i < Pager.PAGES.length; i++) {
                var shown = root.pager.shown[Pager.PAGES[i]];
                out.shown[Pager.PAGES[i]] = shown ? shown.key : "";
            }
            out.page_failed = root.pager.failed;
            out.widget_expects_service = root.expectedServiceVersion;
            out.notice = root.noticeShort;
            out.page_running = pageProc.running;
            return JSON.stringify(out);
        }
    }

    WidgetButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        // The label only reserves the glyph's advance; OpticalGlyph paints it.
        text: root.vertical ? "" : root.glyph
        labelVisible: false
        hasVisualContent: true
        fixedHeight: root.vertical ? Style.bar.iconSlot : -1
        horizontalMargin: 8.5
        // The card replaces the plain tooltip.
        tooltipText: ""
        // Hover shows the glance; a click opens the detail, the Today page (D16).
        // A right-click offers the report (D33) and never opens the card.
        onPressed: function (b) {
            if (b === Qt.LeftButton) {
                hoverCard.page = "today";
                root.openHoverCard();
            } else if (b === Qt.RightButton) {
                root.runReport("menu");
            }
        }

        OpticalGlyph {
            anchors.fill: parent
            text: root.glyph
            fontFamily: button.fontFamily
            fontSize: root.vertical ? Style.font.icon : button.fontSize
            color: button.foreground
        }
    }
}
