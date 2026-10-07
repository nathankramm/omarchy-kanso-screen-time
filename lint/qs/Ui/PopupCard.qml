import QtQuick
import qs.Commons

// Lint-only stand-in for omarchy-shell's Ui/PopupCard.qml (checked against the
// installed 4.0.4 file). The real one is a PopupWindow anchored to the bar,
// with a Hyprland focus grab in click mode; that needs PopupWindow, Edges,
// QsWindow and HyprlandFocusGrab stubs, so this hand-written stub covers
// exactly the members this repo uses, with the real names and types.
Item {
    id: root

    required property Item anchorItem
    required property QtObject bar
    property var owner: null
    property int margin: Style.gapsOut
    property int padding: Style.spacing.popupPadding
    property int contentWidth: Style.space(280)
    property int contentHeight: Style.space(200)
    property var borderSpec: ({})
    property bool open: false
    property bool centerOnBar: false
    // "click" (focus grab) or "hover" (passive; the owner drives `open`).
    property string triggerMode: "click"

    readonly property var coordinatorKey: owner || root
    readonly property bool containsMouse: false
    readonly property real verticalContentInset: 0

    function fittedContentWidth(width, cap) {
        return width;
    }

    function fittedContentHeight(implicitHeight, cap) {
        return implicitHeight;
    }

    function close() {
        root.open = false;
    }

    default property alias contentItem: contentHolder.children

    Item {
        id: contentHolder
    }
}
