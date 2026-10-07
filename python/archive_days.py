#!/usr/bin/env python3
"""archive_days.py: keep every day's per-app detail forever (D20).

The tracker (qml/Service.qml) keeps 365 days in history.json, which it rewrites
every few seconds. Days that roll out of that window are handed to this script,
once, as ONE line of JSON on stdin:

    {"dir": "<archive dir>", "days": {"YYYY-MM-DD": {"total": ms, "apps": {...}, "hours": [24 x ms]}}}

and merged into <dir>/<year>.json:

    {"schema": 1, "days": {"YYYY-MM-DD": {"total": ms, "apps": {...}}}}

Rules (the tracker removes a day from history.json only after this confirms it):
- a day is REPLACED, never added to, so running twice with the same day changes
  nothing (a crash after the archive write and before the history save is safe);
- each year file is written atomically: a temp file in the same directory,
  fsync, then a rename. A partial file never exists under the real name;
- an unreadable or invalid year file is moved aside to <year>.json.corrupt-<epoch>,
  never overwritten, and a fresh one is started;
- the last stdout line is JSON: {"archived": [keys whose year file was written]}.
  Exit 0 when every year was written, 1 otherwise (the confirmed keys still count).

It reads nothing else and never touches history.json.
"""

import datetime
import json
import os
import re
import sys
import tempfile
import time

DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_day_key(key):
    """A real calendar date as YYYY-MM-DD (the engine skips anything else)."""
    if not isinstance(key, str) or not DAY_KEY_RE.match(key):
        return False
    try:
        datetime.date.fromisoformat(key)
    except ValueError:
        return False
    return True


SCHEMA = 1


def clean_hours(h):
    """D42: 24 non-negative ms totals by local hour, or None (a day without)."""
    if not isinstance(h, list) or len(h) != 24:
        return None
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in h
    ):
        return None
    return [int(v) for v in h]


def clean_day(rec):
    """{"total": int, "apps": {str: int}, "hours"?: [24 x int]} or None: what a
    tracked day can hold. Hours are kept when well-formed (D42)."""
    if not isinstance(rec, dict):
        return None
    apps = {}
    for k, v in (rec.get("apps") if isinstance(rec.get("apps"), dict) else {}).items():
        if isinstance(k, str) and k and isinstance(v, (int, float)) and v > 0:
            apps[k] = int(v)
    total = rec.get("total")
    total = int(total) if isinstance(total, (int, float)) and total > 0 else 0
    total = max(total, sum(apps.values()))
    if total <= 0:
        return None
    day = {"total": total, "apps": apps}
    hours = clean_hours(rec.get("hours"))
    if hours is not None:
        day["hours"] = hours
    return day


def read_year(path):
    """The year file's days, or {} for a missing file. A file that cannot be
    read as a schema-1 archive is moved aside first (never overwritten)."""
    try:
        with open(path, "rb") as f:
            doc = json.loads(f.read().decode("utf-8"))
        if (
            isinstance(doc, dict)
            and doc.get("schema") == SCHEMA
            and isinstance(doc.get("days"), dict)
        ):
            return dict(doc["days"])
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    os.replace(path, f"{path}.corrupt-{int(time.time())}")
    return {}


def write_year(path, days):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".archive-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(
                {"schema": SCHEMA, "days": dict(sorted(days.items()))},
                f,
                separators=(",", ":"),
            )
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def archive(directory, days):
    """{year: ok} after merging `days` into the year files; the keys written."""
    by_year = {}
    for key, rec in days.items():
        day = clean_day(rec)
        if is_day_key(key) and day is not None:
            by_year.setdefault(key[:4], {})[key] = day
    os.makedirs(directory, exist_ok=True)
    written, ok = [], True
    for year in sorted(by_year):
        path = os.path.join(directory, f"{year}.json")
        try:
            merged = read_year(path)
            merged.update(by_year[year])  # replace, never add: idempotent
            write_year(path, merged)
            written += sorted(by_year[year])
        except OSError as e:
            print(f"archive_days: {year}: {e}", file=sys.stderr)
            ok = False
    return written, ok


def main():
    line = sys.stdin.readline()
    try:
        req = json.loads(line)
        directory, days = req["dir"], req["days"]
        if (
            not isinstance(directory, str)
            or not directory
            or not isinstance(days, dict)
        ):
            raise ValueError("dir and days")
    except (ValueError, KeyError, TypeError) as e:
        print(f"archive_days: bad request: {e}", file=sys.stderr)
        print(json.dumps({"archived": []}))
        return 2
    written, ok = archive(directory, days)
    print(json.dumps({"archived": written}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
