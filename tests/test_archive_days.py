#!/usr/bin/env python3
"""python/archive_days.py, the D20 archiver: merge, idempotence, atomicity,
corrupt files moved aside, partial runs confirm only what they wrote."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVER = os.environ.get(
    "SCREEN_TIME_ARCHIVER",
    os.path.join(os.path.dirname(HERE), "python", "archive_days.py"),
)
HOUR = 3_600_000


def run(directory, days):
    p = subprocess.run(
        [sys.executable, "-I", ARCHIVER],
        input=json.dumps({"dir": directory, "days": days}) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return (
        p.returncode,
        json.loads(p.stdout.strip().splitlines()[-1])["archived"],
        p.stderr,
    )


def read(directory, year):
    with open(os.path.join(directory, f"{year}.json")) as f:
        return json.load(f)


class ArchiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "archive")

    def tearDown(self):
        os.chmod(self.tmp.name, stat.S_IRWXU)
        if os.path.isdir(self.dir):
            os.chmod(self.dir, stat.S_IRWXU)
        self.tmp.cleanup()

    def test_days_keep_their_apps_in_per_year_files(self):
        days = {
            "2024-12-31": {"total": 2 * HOUR, "apps": {"editor": HOUR, "web": HOUR}},
            "2025-01-01": {"total": HOUR, "apps": {"chat": HOUR}},
        }
        rc, archived, _ = run(self.dir, days)
        self.assertEqual((rc, archived), (0, ["2024-12-31", "2025-01-01"]))
        self.assertEqual(
            read(self.dir, 2024),
            {"schema": 1, "days": {"2024-12-31": days["2024-12-31"]}},
        )
        self.assertEqual(
            read(self.dir, 2025)["days"]["2025-01-01"]["apps"], {"chat": HOUR}
        )
        self.assertEqual(
            sorted(os.listdir(self.dir)), ["2024.json", "2025.json"]
        )  # no temp file

    def test_running_twice_changes_nothing(self):
        day = {"2025-03-01": {"total": HOUR, "apps": {"editor": HOUR}}}
        run(self.dir, day)
        first = read(self.dir, 2025)
        rc, archived, _ = run(self.dir, day)
        self.assertEqual(
            (rc, archived, read(self.dir, 2025)), (0, ["2025-03-01"], first)
        )

    def test_new_days_merge_with_the_year_already_there(self):
        run(self.dir, {"2025-03-01": {"total": HOUR, "apps": {"a": HOUR}}})
        run(self.dir, {"2025-03-02": {"total": HOUR, "apps": {"b": HOUR}}})
        self.assertEqual(
            sorted(read(self.dir, 2025)["days"]), ["2025-03-01", "2025-03-02"]
        )

    def test_a_corrupt_year_file_is_moved_aside_never_overwritten(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, "2025.json"), "w") as f:
            f.write("{oops")
        rc, archived, _ = run(
            self.dir, {"2025-03-01": {"total": HOUR, "apps": {"a": HOUR}}}
        )
        self.assertEqual((rc, archived), (0, ["2025-03-01"]))
        aside = [n for n in os.listdir(self.dir) if n.startswith("2025.json.corrupt-")]
        self.assertEqual(len(aside), 1)
        with open(os.path.join(self.dir, aside[0])) as f:
            self.assertEqual(f.read(), "{oops")

    def test_a_year_that_cannot_be_written_is_not_confirmed(self):
        run(self.dir, {"2024-06-01": {"total": HOUR, "apps": {"a": HOUR}}})
        os.chmod(
            os.path.join(self.dir, "2024.json"), stat.S_IRUSR
        )  # readable, not replaceable? replace needs the dir
        os.chmod(self.dir, stat.S_IRUSR | stat.S_IXUSR)  # no new files in the directory
        rc, archived, err = run(
            self.dir, {"2024-06-02": {"total": HOUR, "apps": {"b": HOUR}}}
        )
        self.assertEqual((rc, archived), (1, []))
        self.assertIn("2024", err)
        os.chmod(self.dir, stat.S_IRWXU)
        self.assertEqual(
            sorted(read(self.dir, 2024)["days"]), ["2024-06-01"]
        )  # untouched, no partial

    def test_a_bad_request_archives_nothing(self):
        p = subprocess.run(
            [sys.executable, "-I", ARCHIVER],
            input="not json\n",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(p.returncode, 2)
        self.assertEqual(json.loads(p.stdout.strip())["archived"], [])

    def test_hours_are_kept_and_malformed_hours_dropped(self):
        # D42: a day's 24 hourly totals go to the archive with it
        h = [0] * 24
        h[9], h[10] = HOUR, HOUR
        rc, archived, _ = run(self.dir, {
            "2025-03-01": {"total": 2 * HOUR, "apps": {"a": 2 * HOUR}, "hours": h},
            "2025-03-02": {"total": HOUR, "apps": {"a": HOUR}, "hours": [1, 2]},
            "2025-03-03": {"total": HOUR, "apps": {"a": HOUR}, "hours": [-1] + [0] * 23},
            "2025-03-04": {"total": HOUR, "apps": {"a": HOUR}, "hours": [True] + [0] * 23},
        })  # fmt: skip
        self.assertEqual((rc, len(archived)), (0, 4))
        days = read(self.dir, 2025)["days"]
        self.assertEqual(days["2025-03-01"]["hours"], h)
        for k in ("2025-03-02", "2025-03-03", "2025-03-04"):
            self.assertEqual(days[k], {"total": HOUR, "apps": {"a": HOUR}}, k)

    def test_empty_and_invalid_days_are_skipped(self):
        rc, archived, _ = run(
            self.dir,
            {
                "2025-13-01": {"total": HOUR},
                "2025-02-01": {"total": 0, "apps": {}},
                "x": {},
            },
        )
        self.assertEqual((rc, archived), (0, []))


if __name__ == "__main__":
    unittest.main()
