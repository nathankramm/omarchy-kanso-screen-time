pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../js/Contrast.js" as Contrast

// The screen-time hover card, in nathan.monarch's grammar at 460 px. Five pages
// (D16: hover = glance, click = detail): the GLANCE (hero · segmented bar · the
// top three rows · Details ›); and four detail pages behind a Day · Week · Month ·
// Year switch: the DAY (D11: hero · the line vs the all-time average · the 30-day chart · one
// segmented bar · up to six rows and Other · the footer); the WEEK (D19: hero ·
// the week line · seven day bars · segmented bar · rows · footer); the MONTH (V1:
// the Week's layout, a bar per day) and the YEAR (D19b: twelve month bars).
// ‹ › on every detail page ask the owner for the neighbour's page (V1: the bar
// runs `card --page`, never a hover); until it comes the page shown stays.
// Fixed size, nothing scrolls: every section has a fixed height, the rows
// area always holds MAX_ROWS slots, and every detail page is the Day's size.
//
// THE PAINTER RULE (the Iron Rule): every string here is a whole field from
// the engine's card (python/screen_time.py, CARD CONTRACT). This file does no
// arithmetic on time and builds no text; its only sums are pixels (a 0..1
// value times a size). Every Text is PlainText (D13).
Item {
    id: hc

    property var doc: null                 // the engine's card, or null
    property string fallbackText: ""       // one line, only while there is no card
    property color foreground: Color.foreground
    property color surface: Color.popups.background   // what the card is painted on
    property string fontFamily: Style.font.family
    property int cardWidth: Style.space(428)   // + the popup's insets = 460 on screen
    property string page: "glance"           // "glance" | "today" | "week" | "month" | "year"; the owner resets it on close
    // A past page per detail tab, from the owner's `card --page` runs (js/Pager.js
    // `shown`); a tab without one shows the engine card's current page.
    property var shown: null
    property string failedPage: ""           // the tab whose latest page run failed: one quiet line
    signal pageRequested(string page, string key)

    readonly property int maxRows: 7       // Today: six named rows and Other
    readonly property int glanceRows: 3    // the glance: the top three named rows
    // Secondary text: the dimmest mix of the text toward the surface that still
    // reads at 4.5:1 (WCAG 1.4.3), so it is quieter than the text on light and
    // dark themes alike (a plain darker shade was louder on light ones; audit C2).
    readonly property color muted: hc.readable(foreground, 4.5, 0.4)
    // Unlabelled chart bars (and zero ticks) need 3:1 against the surface
    // (WCAG 1.4.11); the faint 28 % / 18 % greys measured 1.5-2.0:1.
    readonly property color barColor: hc.readable(foreground, 3.0, 0.2)
    readonly property bool live: doc !== null && doc.state === "ok"
    readonly property var today: live && doc.today ? doc.today : null
    readonly property var rows: live && doc.rows ? doc.rows : []
    // the engine's rows are largest first with Other last: the glance takes the first three named
    readonly property var topRows: rows.filter(r => !r.other).slice(0, glanceRows)
    readonly property var segments: live && doc.segments ? doc.segments : []
    readonly property var footer: live && doc.footer ? doc.footer : null
    readonly property int rowHeight: Math.ceil(bodyMetrics.height) + Style.space(7)
    // each detail tab's page: the past one asked for, else the card's current one
    readonly property var dayPage: hc.pageFor("today", "day")
    readonly property var dayHours: dayPage ? dayPage.hours : null
    readonly property var weekPage: hc.pageFor("week", "week")
    readonly property var monthPage: hc.pageFor("month", "month")
    readonly property var yearPage: hc.pageFor("year", "year")

    function pageFor(name, field) {
        if (!hc.live)
            return null;
        if (hc.shown && hc.shown[name])
            return hc.shown[name];
        return hc.doc[field] || null;
    }

    function faint(alpha) {
        return Qt.rgba(foreground.r, foreground.g, foreground.b, alpha);
    }
    // a colour, not data: the Iron Rule's arithmetic ban is about time
    function readable(fg, minRatio, from) {
        var c = Contrast.readableMix(fg, hc.surface, minRatio, from);
        return Qt.rgba(c.r, c.g, c.b, 1);
    }

    width: parent ? parent.width : cardWidth
    implicitWidth: cardWidth
    // Each page has a fixed height; the glance is the short one.
    implicitHeight: page === "glance" ? glance_.implicitHeight : today_.implicitHeight

    // A small text link: a hover-mode popup takes no focus, but it takes clicks
    // (nathan.monarch's open link).
    component Link: Text {
        id: link
        signal activated
        property bool current: false   // the page switch's highlight (D19)
        textFormat: Text.PlainText
        // the current page reads in the text colour (accent text fell to 3.14:1 on
        // light themes); its accent underline is a graphic, which needs only 3:1
        color: link.current ? hc.foreground : linkArea.containsMouse ? Color.accent : hc.muted
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
        font.bold: link.current

        Rectangle {
            visible: link.current
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.bottom
            anchors.topMargin: 1
            height: 2
            radius: 1
            color: Color.accent
        }

        MouseArea {
            id: linkArea
            anchors.fill: parent
            anchors.margins: -Style.space(4)
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: link.activated()
        }
    }

    // The glance's hero: today's total · "today" (no date pill: the bar's clock shows
    // the date, audit C6); while there is no card, the fallback line takes its place.
    component Hero: Item {
        width: parent ? parent.width : hc.cardWidth
        height: Math.ceil(displayMetrics.height)

        Text {
            visible: !hc.live
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            textFormat: Text.PlainText
            text: hc.doc && !hc.live ? (hc.doc.message || "") : hc.fallbackText
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
        }

        Text {
            id: heroTotal
            textFormat: Text.PlainText
            text: hc.today ? hc.today.total_text : ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.display
            font.bold: true
            font.features: ({
                    "tnum": 1
                })
        }

        Text {
            anchors.left: heroTotal.right
            anchors.leftMargin: Style.space(8)
            anchors.baseline: heroTotal.baseline
            textFormat: Text.PlainText
            text: hc.today ? hc.today.label : ""
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }
    }

    // One segmented bar: every row, the engine's weights as widths. Shared
    // (today's segments unless a page passes its own).
    component SegmentBar: Row {
        id: segmentBar
        property var segments: hc.segments
        width: parent ? parent.width : hc.cardWidth
        height: 6
        spacing: 2

        Repeater {
            model: segmentBar.segments

            Rectangle {
                id: segment
                required property var modelData
                height: 6
                radius: 1
                width: Math.max(1, (segmentBar.width - 2 * (segmentBar.segments.length - 1)) * (Number(segment.modelData.weight) || 0))
                color: segment.modelData.swatch
            }
        }
    }

    // An app row: swatch · name · vs its average · today. Shared by the glance and Today.
    component AppRow: Item {
        id: row
        required property var modelData
        width: parent ? parent.width : hc.cardWidth
        height: hc.rowHeight

        Rectangle {
            x: 0
            y: Style.space(3)
            width: 6
            height: hc.rowHeight - Style.space(8)
            radius: 1
            color: row.modelData.swatch
        }

        Text {
            id: rowName
            x: Style.space(12)
            y: Style.space(2)
            width: rowVs.x - x - Style.space(8)
            textFormat: Text.PlainText
            text: row.modelData.name || ""
            color: row.modelData.other ? hc.muted : hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
        }

        Text {
            id: rowVs
            // the glance is the least on screen: times only (audit P1); Day keeps the comparison
            visible: hc.page !== "glance"
            x: rowValue.x - Style.space(14) - implicitWidth
            anchors.baseline: rowName.baseline
            textFormat: Text.PlainText
            text: row.modelData.vs_text || ""
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
            font.features: ({
                    "tnum": 1
                })
        }

        Text {
            id: rowValue
            x: parent.width - Style.space(64)
            width: Style.space(64)
            horizontalAlignment: Text.AlignRight
            anchors.baseline: rowName.baseline
            textFormat: Text.PlainText
            text: row.modelData.value_text || ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.body
            font.features: ({
                    "tnum": 1
                })
        }
    }

    // The detail pages' footer, left: all-time · since the first day. Shared by
    // Day, Week and Year (D19: the week total lives on the Week page).
    component AllTimeLine: Row {
        spacing: Style.space(6)

        Text {
            textFormat: Text.PlainText
            text: hc.footer ? "all-time" : ""
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }
        Text {
            textFormat: Text.PlainText
            text: hc.footer ? hc.footer.all_time_text : ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
            font.features: ({
                    "tnum": 1
                })
        }
        Text {
            textFormat: Text.PlainText
            text: hc.footer ? hc.footer.since_text : ""
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }
    }

    // The detail pages' footer, right: Day · Week · Month · Year, the current page
    // bold and underlined (D19). "Day" is the page called "today".
    component PageSwitch: Row {
        visible: hc.live
        spacing: Style.space(6)

        Link {
            text: "Day"
            current: hc.page === "today"
            onActivated: hc.page = "today"
        }
        Text {
            textFormat: Text.PlainText
            text: "·"
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }
        Link {
            text: "Week"
            current: hc.page === "week"
            onActivated: hc.page = "week"
        }
        Text {
            textFormat: Text.PlainText
            text: "·"
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }
        Link {
            text: "Month"
            current: hc.page === "month"
            onActivated: hc.page = "month"
        }
        Text {
            textFormat: Text.PlainText
            text: "·"
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }
        Link {
            text: "Year"
            current: hc.page === "year"
            onActivated: hc.page = "year"
        }
    }

    // The detail pages' footer: all-time on the left (or, when this tab's latest
    // page run failed, one quiet line in its place) · the switch on the right.
    component PageFooter: Item {
        width: parent ? parent.width : hc.cardWidth
        height: Math.ceil(captionMetrics.height)

        AllTimeLine {
            visible: hc.failedPage !== hc.page
        }

        Text {
            visible: hc.live && hc.failedPage === hc.page
            textFormat: Text.PlainText
            text: "Couldn't load that page"
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }

        PageSwitch {
            anchors.right: parent.right
        }
    }

    // A detail page's header: its total · its label; the pill (the day, the week's
    // range, the month, the year) sits between ‹ and ›, each in a fixed slot, so
    // nothing moves when an arrow hides. An arrow asks the owner for the engine's
    // prev/next page (V1); while there is no card, the fallback line shows instead.
    component PageHeader: Item {
        id: header
        property var view: null
        property string pageName: ""
        property string pillText: ""
        width: parent ? parent.width : hc.cardWidth
        height: Math.ceil(displayMetrics.height)

        Text {
            visible: !hc.live
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width
            textFormat: Text.PlainText
            text: hc.doc && !hc.live ? (hc.doc.message || "") : hc.fallbackText
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.body
            elide: Text.ElideRight
        }

        Text {
            id: headerTotal
            textFormat: Text.PlainText
            text: header.view ? header.view.total_text : ""
            color: hc.foreground
            font.family: hc.fontFamily
            font.pixelSize: Style.font.display
            font.bold: true
            font.features: ({
                    "tnum": 1
                })
        }

        Text {
            anchors.left: headerTotal.right
            anchors.leftMargin: Style.space(8)
            anchors.baseline: headerTotal.baseline
            textFormat: Text.PlainText
            text: header.view ? header.view.label : ""
            color: hc.muted
            font.family: hc.fontFamily
            font.pixelSize: Style.font.caption
        }

        Item {
            id: nextSlot
            anchors.right: parent.right
            width: Style.space(20)
            height: parent.height

            Link {
                anchors.centerIn: parent
                text: "›"
                font.pixelSize: Style.font.body
                visible: header.view !== null && header.view.next !== null
                onActivated: hc.pageRequested(header.pageName, header.view.next)
            }
        }

        Item {
            id: pillSlot
            anchors.right: nextSlot.left
            width: Style.space(160)
            height: parent.height

            Rectangle {
                visible: header.view !== null
                anchors.centerIn: parent
                width: pillLabel.implicitWidth + Style.space(12)
                height: pillLabel.implicitHeight + Style.space(4)
                radius: height / 2
                color: Style.normalFillFor(hc.foreground, Color.accent, Color.urgent)
                border.width: 1
                border.color: Style.normalBorderFor(hc.foreground, Color.accent, Color.urgent)

                Text {
                    id: pillLabel
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: header.pillText
                    color: hc.muted
                    font.family: hc.fontFamily
                    font.pixelSize: Style.font.caption
                    font.features: ({
                            "tnum": 1
                        })
                }
            }
        }

        Item {
            id: prevSlot
            anchors.right: pillSlot.left
            width: Style.space(20)
            height: parent.height

            Link {
                anchors.centerIn: parent
                text: "‹"
                font.pixelSize: Style.font.body
                visible: header.view !== null && header.view.prev !== null
                onActivated: hc.pageRequested(header.pageName, header.view.prev)
            }
        }
    }

    // A page's line under the hero, in the engine's words (D5: neutral colours):
    // the comparison with the all-time average, bold, or muted and regular while
    // there is not yet enough history for one ("average starts …", D38). D41: the
    // line is only the comparison; "avg" always means the all-time average.
    component PageLine: Text {
        id: pageLine
        property var line: null
        readonly property bool warming: pageLine.line !== null && pageLine.line.state === "baseline"
        width: parent ? parent.width : hc.cardWidth
        height: Math.ceil(bodyMetrics.height)
        textFormat: Text.PlainText
        text: pageLine.line ? pageLine.line.text : ""
        color: pageLine.warming ? hc.muted : hc.foreground
        font.family: hc.fontFamily
        font.pixelSize: Style.font.body
        font.bold: !pageLine.warming
        font.features: ({
                "tnum": 1
            })
        elide: Text.ElideRight
    }

    // A chart's dashed line at the all-time average (D38), the engine's y times
    // pixels; nothing while there is no average yet.
    component DashedMean: Canvas {
        id: dashed
        property var meanY: null
        onMeanYChanged: requestPaint()
        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()

        function rgba(c, a) {
            return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + a + ")";
        }
        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            if (dashed.meanY === null)
                return;
            ctx.setLineDash([4, 3]);
            ctx.strokeStyle = rgba(hc.muted, 0.95);
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.moveTo(0, (1 - dashed.meanY) * height);
            ctx.lineTo(width, (1 - dashed.meanY) * height);
            ctx.stroke();
        }
    }

    // Its label, the chart's top right: "avg 2h 37m · 12 days".
    component MeanLabel: Text {
        y: Style.space(2)
        textFormat: Text.PlainText
        color: hc.muted
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
        font.features: ({
                "tnum": 1
            })
    }

    // The Week's, Month's and Year's bars, in the 30-day chart's frame and D15
    // style: the engine's x and y times pixels; today (or this month) in the
    // accent; future slots and slots before the first tracked day are empty
    // outlines, never a bar. The frame is untinted: a tint lowered every bar's
    // contrast (audit: the today bar fell to 2.92:1 on rose-pine).
    component PeriodPlot: Rectangle {
        id: plotFrame
        property var view: null
        property int gap: Style.space(4)    // between slots
        // D43: the narrow left gutter the scale's labels sit in; the slots narrow for it
        readonly property int gutter: Style.space(30)
        width: parent ? parent.width : hc.cardWidth
        height: Style.space(96)
        radius: Style.cornerRadius
        color: "transparent"

        // ---- the time scale (D43): the engine's ticks, each a faint solid line at
        //      its y (times the slots' height) and its label in the gutter, muted.
        //      Decorative: the dashed average and its label stay distinct.
        Repeater {
            model: plotFrame.view && plotFrame.view.ticks ? plotFrame.view.ticks : []

            Item {
                id: tick
                required property var modelData
                x: 0
                y: slots.y + (1 - tick.modelData.y) * slots.height
                width: plotFrame.width
                height: 1

                Rectangle {
                    x: plotFrame.gutter
                    width: parent.width - plotFrame.gutter
                    height: 1
                    color: hc.faint(0.14)
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    x: 0
                    width: plotFrame.gutter - Style.space(4)
                    horizontalAlignment: Text.AlignRight
                    textFormat: Text.PlainText
                    text: tick.modelData.text
                    color: hc.muted
                    font.family: hc.fontFamily
                    font.pixelSize: Style.font.caption
                    font.features: ({
                            "tnum": 1
                        })
                }
            }
        }

        Item {
            id: slots
            anchors.fill: parent
            anchors.topMargin: captionMetrics.height + Style.space(4)
            anchors.bottomMargin: captionMetrics.height + Style.space(4)
            anchors.leftMargin: plotFrame.gutter
            // qualified, as AGENTS.md asks of delegate and outer reads
            readonly property var periodBars: plotFrame.view ? plotFrame.view.bars : []
            // n slots at x = i / (n - 1): a slot is 1/n of the width less its gap
            readonly property real slotWidth: slots.periodBars.length > 0 ? (slots.width - plotFrame.gap * (slots.periodBars.length - 1)) / slots.periodBars.length : 0

            Repeater {
                model: slots.periodBars

                Item {
                    id: periodBar
                    required property var modelData
                    readonly property bool isSlot: periodBar.modelData.state === "future" || periodBar.modelData.state === "before"
                    readonly property bool isCurrent: periodBar.modelData.state === "today" || periodBar.modelData.state === "current"
                    readonly property bool isZero: !(Number(periodBar.modelData.ms) > 0)
                    x: periodBar.modelData.x * (slots.width - slots.slotWidth)
                    width: slots.slotWidth
                    height: slots.height

                    Rectangle {
                        visible: periodBar.isSlot
                        anchors.fill: parent
                        radius: 1
                        color: "transparent"
                        border.width: 1
                        border.color: hc.faint(0.12)
                    }

                    Rectangle {
                        visible: !periodBar.isSlot
                        width: parent.width
                        height: periodBar.isZero ? 1 : Math.max(1, periodBar.modelData.y * parent.height)
                        y: parent.height - height
                        radius: periodBar.isZero ? 0 : 1
                        color: periodBar.isCurrent ? Color.accent : hc.barColor
                    }

                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.top: parent.bottom
                        anchors.topMargin: Style.space(3)
                        textFormat: Text.PlainText
                        text: periodBar.modelData.label || ""
                        color: periodBar.isCurrent ? hc.foreground : hc.muted
                        font.family: hc.fontFamily
                        font.pixelSize: Style.font.caption
                    }
                }
            }
        }

        DashedMean {
            anchors.fill: slots
            meanY: plotFrame.view && typeof plotFrame.view.mean_y === "number" ? plotFrame.view.mean_y : null
        }

        MeanLabel {
            anchors.right: parent.right
            text: plotFrame.view ? plotFrame.view.mean_text : ""
        }
    }

    // The Week, Month and Year pages: the Day's sections at the Day's heights, so
    // every detail page is the same fixed size. Header · line · bars · segmented
    // bar · that period's rows (always MAX_ROWS slots) · footer.
    component PeriodPage: Column {
        id: periodPage
        property var view: null
        property string pageName: ""
        property string pillText: ""
        property int gap: Style.space(4)
        width: parent ? parent.width : hc.cardWidth
        spacing: Style.space(6)

        PageHeader {
            view: periodPage.view
            pageName: periodPage.pageName
            pillText: periodPage.pillText
        }

        PageLine {
            line: periodPage.view ? periodPage.view.line : null
        }

        PeriodPlot {
            view: periodPage.view
            gap: periodPage.gap
        }

        SegmentBar {
            segments: periodPage.view ? periodPage.view.segments : []
        }

        Column {
            width: parent.width
            height: hc.maxRows * hc.rowHeight

            Repeater {
                model: periodPage.view ? periodPage.view.rows : []

                AppRow {}
            }
        }

        Rectangle {
            width: parent.width
            height: 1
            color: hc.live ? hc.faint(0.18) : "transparent"
        }

        PageFooter {}
    }

    FontMetrics {
        id: displayMetrics
        font.family: hc.fontFamily
        font.pixelSize: Style.font.display
        font.bold: true
    }
    FontMetrics {
        id: bodyMetrics
        font.family: hc.fontFamily
        font.pixelSize: Style.font.body
    }
    FontMetrics {
        id: captionMetrics
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
    }

    // ---- the GLANCE (D16): what hover shows, today only. Hero · segmented bar · the
    //      top three named rows (no Other: the bar shows the rest) · Details ›. Fixed size:
    //      the rows area always holds glanceRows slots, whatever the data.
    Column {
        id: glance_
        visible: hc.page === "glance"
        width: parent.width
        spacing: Style.space(6)

        Hero {}

        SegmentBar {}

        Column {
            width: parent.width
            height: hc.glanceRows * hc.rowHeight

            Repeater {
                model: hc.topRows

                AppRow {}
            }
        }

        Rectangle {
            width: parent.width
            height: 1
            color: hc.live ? hc.faint(0.18) : "transparent"
        }

        // today only: the footer holds just the way to the detail
        Item {
            width: parent.width
            height: Math.ceil(captionMetrics.height)

            Link {
                id: detailsLink
                anchors.right: parent.right
                visible: hc.live
                text: "Details ›"
                onActivated: hc.page = "today"
            }
        }
    }

    // ---- the DAY page (click / Details ›): any day, today first. Header · the
    //      line vs the average · the 30-day chart ending that day · segmented bar · rows · footer
    Column {
        id: today_
        visible: hc.page === "today"
        width: parent.width
        spacing: Style.space(6)

        PageHeader {
            view: hc.dayPage
            pageName: "today"
            pillText: hc.dayPage ? hc.dayPage.pill : ""
        }

        // ---- the day against the all-time average day (D38)
        PageLine {
            line: hc.dayPage ? hc.dayPage.line : null
        }

        // ---- the day by hour (D42): 24 bars, 12 AM to 12 AM, in the shared bar plot
        //      (this hour in the accent, later hours empty slots); a day recorded
        //      in part draws the hours it has over the engine's muted caption,
        //      "hours from 12 PM" (D48), in the same space; a day with no usable
        //      hours shows the engine's one muted line instead
        Item {
            id: plot
            width: parent.width
            height: Style.space(96)
            readonly property bool partial: hc.dayHours !== null && hc.dayHours.state === "partial"

            PeriodPlot {
                visible: hc.dayHours !== null && (hc.dayHours.state === "ok" || plot.partial)
                height: plot.partial ? plot.height - captionMetrics.height - Style.space(2) : plot.height
                view: hc.dayHours
                gap: Style.space(2)
            }

            Text {
                visible: plot.partial
                anchors.bottom: parent.bottom
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                textFormat: Text.PlainText
                text: plot.partial ? hc.dayHours.text : ""
                color: hc.muted
                font.family: hc.fontFamily
                font.pixelSize: Style.font.caption
            }

            Text {
                visible: hc.dayHours !== null && hc.dayHours.state === "pending"
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                textFormat: Text.PlainText
                text: hc.dayHours ? hc.dayHours.text : ""
                color: hc.muted
                font.family: hc.fontFamily
                font.pixelSize: Style.font.caption
            }
        }

        SegmentBar {
            segments: hc.dayPage ? hc.dayPage.segments : []
        }

        // ---- rows: always MAX_ROWS slots tall
        Column {
            width: parent.width
            height: hc.maxRows * hc.rowHeight

            Repeater {
                model: hc.dayPage ? hc.dayPage.rows : []

                AppRow {}
            }
        }

        Rectangle {
            width: parent.width
            height: 1
            color: hc.live ? hc.faint(0.18) : "transparent"
        }

        PageFooter {}
    }

    // ---- the WEEK page (D19): seven day bars, Mon-Sun
    PeriodPage {
        id: week_
        visible: hc.page === "week"
        width: parent.width
        view: hc.weekPage
        pageName: "week"
        pillText: hc.weekPage ? hc.weekPage.range_text : ""
        gap: Style.space(10)
    }

    // ---- the MONTH page (V1): a bar per day of the month
    PeriodPage {
        id: month_
        visible: hc.page === "month"
        width: parent.width
        view: hc.monthPage
        pageName: "month"
        pillText: hc.monthPage ? hc.monthPage.range_text : ""
        gap: Style.space(2)
    }

    // ---- the YEAR page (D19b): twelve month bars
    PeriodPage {
        id: year_
        visible: hc.page === "year"
        width: parent.width
        view: hc.yearPage
        pageName: "year"
        pillText: hc.yearPage ? hc.yearPage.key : ""
        gap: Style.space(4)
    }
}
