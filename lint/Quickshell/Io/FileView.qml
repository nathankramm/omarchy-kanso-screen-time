import QtQuick

// Lint-only stand-in for Quickshell.Io.FileView.
QtObject {
    id: root
    property string path: ""
    property bool printErrors: false
    property bool atomicWrites: false
    property bool watchChanges: false
    default property FileViewAdapter adapter

    signal adapterUpdated
    signal loaded
    signal loadFailed(var error)
    signal saveFailed(var error)
    signal fileChanged

    function reload() {
    }

    function text(): string {
        return "";
    }

    function writeAdapter() {
    }
}
