#!/usr/bin/env python3
"""D46: hours survive a restart, through the REAL Quickshell runtime.

tests/runtime/shell.qml declares the history FileView and JsonAdapter exactly as
qml/Service.qml does and runs its load (sanitize, today's copy), one accrual
through State.js and its save. Day records reach Model.js the way they do live:
the runtime hands arrays over as sequences, not as JSON.parse arrays, which is
what the Node tests could not see (the 2026-10-07 12:40 restart dropped every
hour). Each run is headless (offscreen, its own HOME and runtime dir, no D-Bus,
no display); a second run on the saved file is the restart.

SCREEN_TIME_JS_DIR lets a planted copy of js/ run here. Skipped only when the
quickshell binary is not installed.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JS_DIR = os.environ.get("SCREEN_TIME_JS_DIR", os.path.join(ROOT, "js"))
MIN = 60_000
HOUR = 3_600_000
TODAY = "2026-10-07"
DROP = ("WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "QT_QPA_PLATFORMTHEME",
        "QT_IM_MODULE", "GDK_BACKEND", "DBUS_SESSION_BUS_ADDRESS")  # fmt: skip


def local_ms(stamp):
    """Epoch ms of a local America/Chicago wall-clock time "2026-10-07 10:20"."""
    p = subprocess.run(["date", "-d", stamp, "+%s"], capture_output=True, text=True,
                       env=dict(os.environ, TZ="America/Chicago"), check=True)  # fmt: skip
    return int(p.stdout.strip()) * 1000


def hours(**at):
    h = [0] * 24
    for k, ms in at.items():
        h[int(k[1:])] = ms
    return h


class RuntimeHarness(unittest.TestCase):
    """One throwaway HOME, history file and harness config per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.cfg = os.path.join(t, "cfg")
        os.makedirs(self.cfg)
        shutil.copy(os.path.join(HERE, "runtime", "shell.qml"), self.cfg)
        shutil.copytree(JS_DIR, os.path.join(self.cfg, "js"))
        for d in ("home", "run"):
            os.makedirs(os.path.join(t, d), mode=0o700)
        self.history = os.path.join(t, "history.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, days):
        with open(self.history, "w") as f:
            json.dump({"days": days, "months": {}, "years": {}}, f, indent=4)

    def read(self):
        with open(self.history) as f:
            return json.load(f)["days"]

    def run_tracker(self, start, end, expect_write=True):
        """One shell lifetime: load, accrue [start, end) to the editor, save."""
        t = self.tmp.name
        env = {k: v for k, v in os.environ.items() if k not in DROP}
        env.update(TZ="America/Chicago", HOME=os.path.join(t, "home"), XDG_RUNTIME_DIR=os.path.join(t, "run"),
                   QT_QPA_PLATFORM="offscreen", DBUS_SESSION_BUS_ADDRESS="unix:path=" + os.path.join(t, "no-bus"),
                   ST_HISTORY=self.history, ST_TODAY=TODAY, ST_START=str(local_ms(start)), ST_END=str(local_ms(end)))  # fmt: skip
        p = subprocess.run(["quickshell", "--no-color", "-p", self.cfg], capture_output=True, text=True,
                           env=env, timeout=60, check=False)  # fmt: skip
        log = p.stdout + p.stderr
        if not expect_write:
            self.assertNotIn("ST-WROTE", log)
            return log, None
        self.assertIn("ST-WROTE", log, log[-2000:])
        probe = json.loads(log.split("ST-PROBE ", 1)[1].splitlines()[0])
        return log, probe


@unittest.skipUnless(shutil.which("quickshell"), "quickshell is not installed")
class RuntimeHoursTests(RuntimeHarness):
    def test_hours_survive_load_save_and_a_restart_exactly(self):
        yesterday = {
            "total": 3 * HOUR,
            "apps": {"editor": 3 * HOUR},
            "hours": hours(h9=2 * HOUR, h22=HOUR),
        }
        self.write({
            "2026-10-06": yesterday,
            TODAY: {"total": 40 * MIN, "apps": {"editor": 40 * MIN}, "hours": hours(h9=30 * MIN, h10=10 * MIN)},
        })  # fmt: skip
        # the first lifetime: 10 minutes in hour 10
        log, probe = self.run_tracker("2026-10-07 10:20", "2026-10-07 10:30")
        self.assertNotIn("malformed", log)
        self.assertTrue(probe["copied"], probe)  # the loaded hours reached today's copy
        day = self.read()[TODAY]
        self.assertEqual(day["hours"], hours(h9=30 * MIN, h10=20 * MIN))
        self.assertEqual((day["total"], sum(day["hours"])), (50 * MIN, 50 * MIN))
        # the restart: 20 minutes across the hour into hour 11
        log, probe = self.run_tracker("2026-10-07 10:50", "2026-10-07 11:10")
        self.assertNotIn("malformed", log)
        days = self.read()
        self.assertEqual(
            days[TODAY]["hours"], hours(h9=30 * MIN, h10=30 * MIN, h11=10 * MIN)
        )
        self.assertEqual(
            (days[TODAY]["total"], sum(days[TODAY]["hours"])), (70 * MIN, 70 * MIN)
        )
        self.assertEqual(days["2026-10-06"], yesterday)  # untouched, hours and all

    def test_a_partial_day_keeps_its_hours_and_stays_partial(self):
        # 1h counted before hours existed; 10m since
        self.write(
            {
                TODAY: {
                    "total": 70 * MIN,
                    "apps": {"editor": 70 * MIN},
                    "hours": hours(h9=10 * MIN),
                }
            }
        )
        log, _ = self.run_tracker("2026-10-07 10:00", "2026-10-07 10:05")
        self.assertNotIn("malformed", log)
        day = self.read()[TODAY]
        self.assertEqual(day["hours"], hours(h9=10 * MIN, h10=5 * MIN))
        self.assertEqual(day["total"], 75 * MIN)

    def test_a_day_without_hours_loads_clean_and_gains_them(self):
        self.write({TODAY: {"total": HOUR, "apps": {"editor": HOUR}}})
        log, probe = self.run_tracker("2026-10-07 10:00", "2026-10-07 10:05")
        self.assertNotIn("malformed", log)
        self.assertFalse(probe["copied"])  # nothing to copy: today starts its hours now
        self.assertEqual(self.read()[TODAY]["hours"], hours(h10=5 * MIN))


@unittest.skipUnless(shutil.which("quickshell"), "quickshell is not installed")
class RuntimeSchemaTests(RuntimeHarness):
    """#3, through the real runtime: history.json's version round-trips, a day's
    unknown fields and `ext` survive a save and a restart, and a file this
    version can't keep whole is never written."""

    def raw_write(self, doc):
        with open(self.history, "w") as f:
            json.dump(doc, f, indent=4)
        with open(self.history, "rb") as f:
            return f.read()

    def test_schema_written_unknown_day_fields_and_ext_kept_across_a_restart(self):
        self.raw_write({
            "days": {"2026-10-06": {"total": HOUR, "apps": {"editor": HOUR}, "note": "yesterday"},
                     TODAY: {"total": 10 * MIN, "apps": {"editor": 10 * MIN}, "hours": hours(h9=10 * MIN), "tags": ["work"]}},
            "months": {}, "years": {}, "ext": {"future": {"x": 1}},
        })  # fmt: skip
        for start, end in (
            ("2026-10-07 10:00", "2026-10-07 10:05"),
            ("2026-10-07 10:10", "2026-10-07 10:20"),
        ):
            log, _ = self.run_tracker(start, end)
            self.assertNotIn("malformed", log)
            with open(self.history) as f:
                doc = json.load(f)
            self.assertEqual(doc["schema"], 1)  # written on the first save
            self.assertEqual(doc["ext"], {"future": {"x": 1}})
            self.assertEqual(doc["days"]["2026-10-06"]["note"], "yesterday")
            self.assertEqual(
                doc["days"][TODAY]["tags"], ["work"]
            )  # kept through accrual
        self.assertEqual(doc["days"][TODAY]["total"], 25 * MIN)

    def test_a_newer_schema_is_never_written(self):
        before = self.raw_write(
            {"schema": 2, "days": {TODAY: {"total": HOUR, "apps": {"editor": HOUR}}}}
        )
        log, _ = self.run_tracker(
            "2026-10-07 10:00", "2026-10-07 10:05", expect_write=False
        )
        self.assertIn("ST-READONLY history.json is from a newer Kanso (schema 2)", log)
        with open(self.history, "rb") as f:
            self.assertEqual(f.read(), before)  # byte for byte

    def test_unknown_top_level_keys_are_never_dropped(self):
        before = self.raw_write(
            {"days": {}, "months": {}, "years": {}, "goals": {"daily": 6}}
        )
        log, _ = self.run_tracker(
            "2026-10-07 10:00", "2026-10-07 10:05", expect_write=False
        )
        self.assertIn(
            "ST-READONLY history.json has fields this Kanso can't keep (goals)", log
        )
        with open(self.history, "rb") as f:
            self.assertEqual(f.read(), before)


if __name__ == "__main__":
    unittest.main()
