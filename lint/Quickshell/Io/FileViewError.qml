pragma Singleton
import QtQuick

// Lint-only stand-in for Quickshell.Io.FileViewError.
QtObject {
    // Same order as quickshell-io.qmltypes, so FileViewError.FileNotFound resolves.
    enum Enum {
        Success,
        Unknown,
        FileNotFound,
        PermissionDenied,
        NotAFile
    }

    function toString(error) {
        return String(error)
    }
}
