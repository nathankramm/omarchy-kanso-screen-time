#!/usr/bin/env python3
"""bin/screen-time-report, the bar's right-click report menu (D33).

Runs the real script with its own HOME (a made-up history) and stub commands
first on PATH: wl-copy, the notification senders and omarchy-menu-select record
what they were given. Never your own history, clipboard or Documents.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.environ.get(
    "SCREEN_TIME_REPORT_SCRIPT", os.path.join(ROOT, "bin", "screen-time-report")
)
ENGINE = os.path.join(ROOT, "python", "screen_time.py")

HISTORY = {
    "days": {
        "2025-06-11": {
            "total": 3_600_000,
            "apps": {"editor": 3_000_000, "bash": 600_000},
        }
    },
    "months": {},
    "years": {},
}

STUB = """#!/usr/bin/env bash
printf '%s\\n' "$(basename "$0")" "$@" >> "$STUB_LOG"
printf -- '---\\n' >> "$STUB_LOG"
case "$(basename "$0")" in
  wl-copy) cat > "$STUB_CLIPBOARD" ;;
  omarchy-menu-select)
    [[ -z ${STUB_CHOICE:-} ]] && exit 1
    printf '%s\\n' "$STUB_CHOICE" ;;
esac
"""


class Rig:
    def __init__(self, engine=ENGINE):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        data = os.path.join(self.home, ".config", "omarchy", "screen-time")
        os.makedirs(data)
        self.history = os.path.join(data, "history.json")
        with open(self.history, "w") as f:
            json.dump(HISTORY, f)
        stubs = os.path.join(t, "stubs")
        os.makedirs(stubs)
        for name in (
            "wl-copy",
            "omarchy-notification-send",
            "notify-send",
            "omarchy-menu-select",
        ):
            p = os.path.join(stubs, name)
            with open(p, "w") as f:
                f.write(STUB)
            os.chmod(p, 0o755)
        self.log = os.path.join(t, "stub.log")
        self.clipboard = os.path.join(t, "clipboard")
        self.env = dict(
            os.environ,
            HOME=self.home,
            PATH=stubs + os.pathsep + os.environ["PATH"],
            STUB_LOG=self.log,
            STUB_CLIPBOARD=self.clipboard,
            SCREEN_TIME_ENGINE=engine,
            TZ="America/Chicago",
        )

    def run(self, verb, choice=None):
        env = dict(self.env)
        if choice is not None:
            env["STUB_CHOICE"] = choice
        return subprocess.run(
            ["bash", SCRIPT, verb],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def calls(self):
        """[(command, [args])] in order."""
        if not os.path.exists(self.log):
            return []
        out = []
        with open(self.log) as f:
            text = f.read()
        for block in text.split("---\n"):
            lines = block.splitlines()
            if lines:
                out.append((lines[0], lines[1:]))
        return out

    def documents(self):
        d = os.path.join(self.home, "Documents")
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def engine_md(self):
        p = subprocess.run([sys.executable, "-I", ENGINE, "report", "--md"], env=self.env,
                           capture_output=True, text=True, check=True)  # fmt: skip
        return p.stdout

    def close(self):
        self.tmp.cleanup()


def without_generated(md):
    """The report minus its "Generated HH:MM" line, which can tick between two runs."""
    return [line for line in md.splitlines() if not line.startswith("Generated ")]


def today():
    return subprocess.run(["date", "+%F"], capture_output=True, text=True, check=False,
                          env=dict(os.environ, TZ="America/Chicago")).stdout.strip()  # fmt: skip


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


FAILING_ENGINE = (
    "import sys\nsys.stderr.write('history.json could not be read\\n')\nsys.exit(1)\n"
)


class ReportActionTests(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()

    def tearDown(self):
        self.rig.close()

    def test_export_saves_todays_report_atomically(self):
        before = sha(self.rig.history)
        r = self.rig.run("export")
        self.assertEqual(r.returncode, 0, r.stderr)
        name = f"screen-time-{today()}.md"
        self.assertEqual(self.rig.documents(), [name])  # no temp file left
        with open(os.path.join(self.rig.home, "Documents", name)) as f:
            saved = f.read()
        self.assertEqual(
            without_generated(saved), without_generated(self.rig.engine_md())
        )
        self.assertIn(("omarchy-notification-send",
                       ["--app-name", "Screen time", f"Report saved · {self.rig.home}/Documents/{name}"]),
                      self.rig.calls())  # fmt: skip
        self.assertEqual(sha(self.rig.history), before)

    def test_export_overwrites_todays_copy(self):
        os.makedirs(os.path.join(self.rig.home, "Documents"))
        old = os.path.join(self.rig.home, "Documents", f"screen-time-{today()}.md")
        with open(old, "w") as f:
            f.write("an older copy\n")
        self.assertEqual(self.rig.run("export").returncode, 0)
        with open(old) as f:
            self.assertNotIn("an older copy", f.read())

    def test_copy_puts_the_report_on_the_clipboard(self):
        r = self.rig.run("copy")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.rig.clipboard) as f:
            self.assertEqual(
                without_generated(f.read()), without_generated(self.rig.engine_md())
            )
        self.assertEqual(
            self.rig.calls()[-1],
            (
                "omarchy-notification-send",
                ["--app-name", "Screen time", "Report copied"],
            ),
        )
        self.assertEqual(self.rig.documents(), [])

    def test_the_menu_offers_copy_then_save(self):
        self.rig.run("menu", choice="")  # cancelled
        self.assertEqual(
            self.rig.calls(),
            [("omarchy-menu-select", ["Screen time", "Copy report", "Save report"])],
        )
        self.assertEqual(self.rig.documents(), [])
        self.assertFalse(os.path.exists(self.rig.clipboard))

    def test_the_menu_runs_the_chosen_item(self):
        self.rig.run("menu", choice="Save report")
        self.assertEqual(self.rig.documents(), [f"screen-time-{today()}.md"])
        self.assertFalse(os.path.exists(self.rig.clipboard))
        self.rig.run("menu", choice="Copy report")
        self.assertTrue(os.path.exists(self.rig.clipboard))


class ReportActionFailureTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        engine = os.path.join(self.dir.name, "failing_engine.py")
        with open(engine, "w") as f:
            f.write(FAILING_ENGINE)
        self.rig = Rig(engine=engine)

    def tearDown(self):
        self.rig.close()
        self.dir.cleanup()

    def test_a_failed_export_writes_nothing_and_says_why(self):
        r = self.rig.run("export")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.rig.documents(), [])  # no partial file, no temp file
        self.assertEqual(self.rig.calls(), [("omarchy-notification-send",
                         ["--app-name", "Screen time", "Screen time report failed", "history.json could not be read"])])  # fmt: skip

    def test_a_failed_copy_leaves_the_clipboard_alone(self):
        r = self.rig.run("copy")
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(self.rig.clipboard))
        self.assertEqual(
            [c for c, _ in self.rig.calls()], ["omarchy-notification-send"]
        )


if __name__ == "__main__":
    unittest.main()
