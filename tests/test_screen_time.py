#!/usr/bin/env python3
"""Unit tests for python/screen_time.py, the engine. Zero dependencies:

    python3 -m unittest discover -s tests

Every verb runs through the real CLI in a subprocess with TZ pinned to
America/Chicago, against made-up fixtures written to a temp dir (never
your history). Fixture dates are in 2025. Every run also gets its own
XDG and flatpak dirs (D17), empty unless a test writes desktop entries, so
this machine's applications never change a label.
"""

import contextlib
import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.environ.get(
    "SCREEN_TIME_ENGINE",
    os.path.join(os.path.dirname(HERE), "python", "screen_time.py"),
)
sys.path.insert(0, os.path.dirname(ENGINE))

import screen_time as st

MIN = 60_000
HOUR = 3_600_000

# Upstream agx's Model.fmt (2c7b75a), run under node: input -> output.
FMT_VECTORS = [
    [0, "0m"], [-5, "0m"], [0.4, "0m"], [499, "1s"], [500, "1s"], [1499, "1s"],
    [1500, "2s"], [29999, "30s"], [30000, "30s"], [59499, "59s"], [59500, "60s"],
    [60000, "1m"], [89999, "1m"], [90000, "2m"], [149999, "2m"], [150000, "3m"],
    [3599999, "1h"], [3600000, "1h"], [3630000, "1h 1m"], [3660000, "1h 1m"],
    [5400000, "1h 30m"], [86399999, "24h"], ["x", "0m"], [None, "0m"],
    [1000000000000, "277777h 47m"],
]  # fmt: skip


def day(*apps):
    a = dict(apps)
    return {"total": sum(a.values()), "apps": a}


def write_files(base, files):
    """{relative path: text | bytes | ("symlink", target) | ("fifo",) | ("dir",)}."""
    os.makedirs(base, exist_ok=True)
    for rel, content in files.items():
        path = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(content, tuple):
            if content[0] == "symlink":
                os.symlink(content[1], path)
            elif content[0] == "fifo":
                os.mkfifo(path)
            else:
                os.makedirs(path)
            continue
        with open(path, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))


def desktop_env(
    root, home=None, dirs=(), flatpak_user=None, flatpak_system=None, theme=None
):
    """Write desktop entries under root and return the env that points at them."""
    dirs = list(dirs) or [{}]
    write_files(os.path.join(root, "home", "applications"), home or {})
    for i, d in enumerate(dirs):
        write_files(os.path.join(root, f"sys{i}", "applications"), d)
    for sub, files in (("fpu", flatpak_user), ("fps", flatpak_system)):
        write_files(
            os.path.join(root, sub, "exports", "share", "applications"), files or {}
        )
    return {
        "XDG_DATA_HOME": os.path.join(root, "home"),
        "XDG_DATA_DIRS": ":".join(
            os.path.join(root, f"sys{i}") for i in range(len(dirs))
        ),
        "FLATPAK_USER_DIR": os.path.join(root, "fpu"),
        "FLATPAK_SYSTEM_DIR": os.path.join(root, "fps"),
        "HOME": write_home(os.path.join(root, "userhome"), theme),
    }


def write_home(home, theme):
    """A HOME of its own (D30: the engine reads the theme from HOME), holding
    `theme`: {"colors.toml" | "shell.toml": text or bytes, "user_shell.toml": ...}."""
    state = os.path.join(home, ".local", "state", "omarchy", "current", "theme")
    files = dict(theme or {})
    user = files.pop("user_shell.toml", None)
    write_files(state, files)
    if user is not None:
        write_files(os.path.join(home, ".config", "omarchy"), {"shell.toml": user})
    return home


def entry(name=None, *extra):
    lines = ["[Desktop Entry]", "Type=Application"] + ([f"Name={name}"] if name else [])
    return "\n".join(lines + list(extra)) + "\n"


class Fixture:
    """A temp dir with history.json (and optionally names.json), plus its own
    XDG/flatpak dirs holding `desktop` (desktop_env's keywords) or nothing, and
    its own HOME holding `theme` (D30) or nothing."""

    def __init__(self, history, names=None, raw=None, desktop=None, theme=None):
        self.dir = tempfile.TemporaryDirectory()
        # its own temp dir, so the read-only test still sees only the two files
        self.xdg = tempfile.TemporaryDirectory()
        self.env = desktop_env(self.xdg.name, theme=theme, **(desktop or {}))
        self.history = os.path.join(self.dir.name, "history.json")
        self.names = os.path.join(self.dir.name, "names.json")
        if raw is not None:
            with open(self.history, "wb") as f:
                f.write(raw)
        elif history is not None:
            with open(self.history, "w") as f:
                json.dump(history, f)
        if names is not None:
            with open(self.names, "w") as f:
                json.dump(names, f)

    def run(self, *args, as_of="2025-06-11T15:00"):
        cmd = [sys.executable, "-I", ENGINE, *args, "--history", self.history]
        cmd += ["--names", self.names]
        if as_of:
            cmd += ["--as-of", as_of]
        env = dict(os.environ, TZ="America/Chicago", **self.env)
        p = subprocess.run(
            cmd, capture_output=True, text=True, env=env, timeout=30, check=False
        )
        return p.returncode, p.stdout, p.stderr

    def card(self, **kw):
        rc, out, err = self.run("card", **kw)
        assert rc == 0, err
        lines = out.strip().splitlines()
        assert len(lines) == 1, out
        return json.loads(lines[0])

    def page(self, spec, **kw):
        """`card --page spec` (V1): the whole document."""
        rc, out, err = self.run("card", "--page", spec, **kw)
        assert rc == 0, err
        lines = out.strip().splitlines()
        assert len(lines) == 1, out
        return json.loads(lines[0])

    def close(self):
        self.dir.cleanup()
        self.xdg.cleanup()


def snapshot(path):
    if not os.path.exists(path):
        return None
    st_ = os.stat(path)
    with open(path, "rb") as f:
        return (st_.st_mtime_ns, st_.st_size, hashlib.sha256(f.read()).hexdigest())


BASIC = {
    "days": {
        "2025-06-11": day(
            ("editor", 2 * HOUR), ("browser", 30 * MIN), ("term", 45_000)
        ),
        "2025-06-10": day(("editor", HOUR)),
    },
    "months": {},
    "years": {},
}

CARD_KEYS = {
    "schema",
    "state",
    "message",
    "as_of",
    "today",
    "rows",
    "segments",
    "names",
    "footer",
    "day",
    "week",
    "month",
    "year",
    "palette",
}

# the Monarch bar plugin's PALETTE, copied as-is.
NO_THEME_OTHER = "#7c7e7f"  # readable muted of #cacccc on #101315 (D30)
MONARCH_PALETTE = [
    "#0072B2",
    "#56B4E9",
    "#CC79A7",
    "#F0E442",
    "#9085E9",
    "#D9D9D9",
    "#7A5FD0",
    "#9A9A9A",
]


def today_card(apps, names=None, total=None, extra_days=None):
    """The card for a made-up 2025-06-11 holding `apps` ({key: ms})."""
    rec = {"total": sum(apps.values()) if total is None else total, "apps": dict(apps)}
    days = {"2025-06-11": rec}
    days.update(extra_days or {})
    fx = Fixture({"days": days}, names=names)
    try:
        return fx.card()
    finally:
        fx.close()


def row_view(card):
    return [(r["name"], r["keys"], r["value_ms"], r["other"]) for r in card["rows"]]


class FormatTests(unittest.TestCase):
    def test_fmt_matches_upstream(self):
        for value, want in FMT_VECTORS:
            self.assertEqual(st.fmt(value), want, value)


class ReadOnlyTests(unittest.TestCase):
    def test_no_verb_writes_history_or_names(self):
        fx = Fixture(BASIC, names={"rename": {"editor": "Editor"}, "hide": ["term"]})
        try:
            before = (snapshot(fx.history), snapshot(fx.names))
            for args in (
                ["card"],
                ["card", "--page", "day:2025-06-10"],
                ["card", "--page", "month:2025-06"],
                ["report", "today"],
                ["report", "week"],
                ["report", "year"],
                ["report", "all"],
                ["keys", "--days", "30"],
                ["log", "--date", "2025-06-10"],
            ):
                rc, _, err = fx.run(*args)
                self.assertEqual(rc, 0, (args, err))
                self.assertEqual(
                    (snapshot(fx.history), snapshot(fx.names)), before, args
                )
            self.assertEqual(
                sorted(os.listdir(fx.dir.name)), ["history.json", "names.json"]
            )
        finally:
            fx.close()


class HourLoadTests(unittest.TestCase):
    """D42: the engine reads a day's hours from history.json and the archive."""

    def test_hours_are_read_and_malformed_ones_dropped(self):
        h = [0] * 24
        h[9] = HOUR
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.json")
            os.makedirs(os.path.join(tmp, "archive"))
            with open(os.path.join(tmp, "archive", "2024.json"), "w") as f:
                json.dump(
                    {
                        "schema": 1,
                        "days": {
                            "2024-06-01": {
                                "total": HOUR,
                                "apps": {"a": HOUR},
                                "hours": h,
                            }
                        },
                    },
                    f,
                )
            with open(path, "w") as f:
                json.dump({"days": {
                    "2025-06-10": {"total": HOUR, "apps": {"a": HOUR}, "hours": h},
                    "2025-06-09": {"total": HOUR, "apps": {"a": HOUR}, "hours": h[:23]},
                    "2025-06-08": {"total": HOUR, "apps": {"a": HOUR}, "hours": [True] + h[1:]},
                    "2025-06-07": {"total": HOUR, "apps": {"a": HOUR}},
                }}, f)  # fmt: skip
            hist, state = st.load_history(path)
        self.assertEqual(state, "ok")
        self.assertEqual(hist["days"]["2025-06-10"]["hours"], h)
        self.assertEqual(hist["days"]["2024-06-01"]["hours"], h)  # from the archive
        for k in ("2025-06-09", "2025-06-08", "2025-06-07"):
            self.assertNotIn("hours", hist["days"][k], k)
            self.assertEqual(hist["days"][k]["total"], HOUR, k)


class CardStateTests(unittest.TestCase):
    def assert_fallback(self, card, state):
        self.assertEqual(set(card), CARD_KEYS)
        self.assertEqual(card["schema"], 11)
        self.assertEqual(card["state"], state)
        self.assertTrue(card["message"])
        self.assertNotIn("\n", card["message"])
        self.assertEqual(card["rows"], [])
        self.assertEqual(card["today"]["total_ms"], 0)

    def test_missing_history(self):
        fx = Fixture(None)
        try:
            self.assert_fallback(fx.card(), "missing")
        finally:
            fx.close()

    def test_corrupt_history(self):
        for raw in (b"{not json", b"[1, 2]", b"\xff\xfe\x00", b"", b"null", b'"str"'):
            fx = Fixture(None, raw=raw)
            try:
                self.assert_fallback(fx.card(), "corrupt")
            finally:
                fx.close()

    def test_empty_history(self):
        for doc in (
            {},
            {"days": {}, "months": {}, "years": {}},
            {"days": {"2025-06-11": day()}},
        ):
            fx = Fixture(doc)
            try:
                self.assert_fallback(fx.card(), "empty")
            finally:
                fx.close()

    def test_hostile_shapes_still_give_a_document(self):
        doc = {
            "days": {
                "2025-06-11": {"total": "lots", "apps": {"editor": "x", "ok": 2 * MIN}},
                "2025-02-30": day(("phantom", HOUR)),
                "not-a-day": day(("x", HOUR)),
                "2025-06-10": [1, 2],
                "2025-06-09": {"total": 5 * MIN, "apps": "nope"},
            },
            "months": {"2025-13": 5, "2025-05": "x", "bad": 1},
            "years": {"2025": {"2025-01-01": HOUR, "2024-01-01": HOUR}, "x": 3},
        }
        fx = Fixture(doc)
        try:
            card = fx.card()
            self.assertEqual(card["state"], "ok")
            self.assertEqual(card["today"]["total_ms"], 2 * MIN)
            self.assertEqual([r["keys"] for r in card["rows"]], [["ok"]])
        finally:
            fx.close()

    def test_nan_and_infinity_literals_count_as_zero(self):
        raw = b'{"days": {"2025-06-11": {"total": NaN, "apps": {"a": Infinity, "b": 120000}}}}'
        fx = Fixture(None, raw=raw)
        try:
            card = fx.card()
            self.assertEqual(card["today"]["total_ms"], 2 * MIN)
            self.assertEqual([r["name"] for r in card["rows"]], ["b"])
        finally:
            fx.close()

    def test_a_bad_as_of_still_gives_a_document(self):
        fx = Fixture(BASIC)
        try:
            card = fx.card(as_of="not-a-time")
            self.assertEqual(card["schema"], 11)
            self.assertIn(card["state"], ("ok", "empty"))
        finally:
            fx.close()


class CardTodayTests(unittest.TestCase):
    def test_hero_and_rows(self):
        fx = Fixture(BASIC)
        try:
            card = fx.card()
            self.assertEqual(set(card), CARD_KEYS)
            self.assertEqual(card["state"], "ok")
            self.assertEqual(card["message"], "")
            self.assertEqual(
                card["today"],
                {
                    "key": "2025-06-11",
                    "total_ms": 2 * HOUR + 30 * MIN + 45_000,
                    "total_text": "2h 31m",
                    "label": "today",
                    "pill": "Wed · Jun 11",
                },
            )
            self.assertEqual(card["rows"][0]["keys"], ["editor"])
        finally:
            fx.close()

    def test_today_is_the_local_day(self):
        doc = {"days": {"2025-06-11": day(("a", HOUR)), "2025-06-12": day(("b", MIN))}}
        fx = Fixture(doc)
        try:
            self.assertEqual(
                fx.card(as_of="2025-06-11T23:59")["today"]["key"], "2025-06-11"
            )
            self.assertEqual(
                fx.card(as_of="2025-06-12T00:01")["today"]["key"], "2025-06-12"
            )
            # 03:30 UTC on the 12th is 22:30 CDT on the 11th.
            utc = fx.card(as_of="2025-06-12T03:30:00+00:00")
            self.assertEqual(utc["today"]["key"], "2025-06-11")
            self.assertEqual(utc["as_of"], "2025-06-11T22:30:00")
        finally:
            fx.close()

    def test_stored_total_never_below_its_apps(self):
        doc = {"days": {"2025-06-11": {"total": MIN, "apps": {"a": 3 * MIN}}}}
        fx = Fixture(doc)
        try:
            self.assertEqual(fx.card()["today"]["total_ms"], 3 * MIN)
        finally:
            fx.close()


class NameTests(unittest.TestCase):
    def test_fallback_names(self):
        keys = {
            "com.anthropic.Claude": "Claude",
            "chrome-x.com": "x.com",
            "chrome-www.example.com": "example.com",
            "chrome-WWW.Example.com": "Example.com",  # the case is kept
            "Bitwarden": "Bitwarden",
            "com.github.user.Codium": "Codium",
            "chrome-chatgpt.com__-Profile_2": "chatgpt.com",
            "imv-wayland": "imv",
            "foo-x11": "foo",
            "-wayland": "-wayland",
            "com.example.Tool-wayland": "Tool",
            "ssh": "ssh",
        }
        for key, want in keys.items():
            self.assertEqual(st.fallback_name(key), want, key)

    def test_built_in_labels_without_desktop_entries(self):
        apps = {"claude": 9 * MIN, "google-chrome": 8 * MIN, "org.telegram.desktop": 7 * MIN,
                "nvim": 6 * MIN, "bash": 3 * MIN, "zsh": 2 * MIN}  # fmt: skip
        card = today_card(apps)
        self.assertEqual(
            [r["name"] for r in card["rows"]],
            ["Claude Code", "Chrome", "Telegram", "Neovim", "Terminal"],
        )

    def test_default_names_never_merge_two_apps(self):
        card = today_card({"com.anthropic.Claude": 10 * MIN, "claude": 5 * MIN})
        self.assertEqual([r["name"] for r in card["rows"]], ["Claude", "Claude Code"])

    def test_rename_exact_and_case_insensitive(self):
        apps = {"editor": 10 * MIN, "browser": 5 * MIN}
        names = {"rename": {"editor": "Editor", "BROWSER": "Web"}}
        card = today_card(apps, names)
        self.assertEqual([r["name"] for r in card["rows"]], ["Editor", "Web"])

    def test_case_insensitive_rename_needs_exactly_one_key(self):
        # "VIEWER" matches both stored keys without case: neither is renamed.
        apps = {"Viewer": 10 * MIN, "viewer": 5 * MIN}
        card = today_card(apps, {"rename": {"VIEWER": "X"}})
        self.assertEqual([r["name"] for r in card["rows"]], ["Viewer", "viewer"])

    def test_case_insensitive_uniqueness_counts_keys_on_other_days(self):
        # Only "viewer" is stored today, but "Viewer" exists on another day.
        card = today_card(
            {"viewer": 5 * MIN},
            {"rename": {"VIEWER": "X"}},
            extra_days={"2025-06-01": day(("Viewer", MIN))},
        )
        self.assertEqual([r["name"] for r in card["rows"]], ["viewer"])

    def test_exact_rename_wins_over_case_insensitive(self):
        card = today_card(
            {"Editor": 10 * MIN}, {"rename": {"EDITOR": "B", "Editor": "A"}}
        )
        self.assertEqual([r["name"] for r in card["rows"]], ["A"])

    def test_same_label_is_one_row(self):
        apps = {"firefox": 10 * MIN, "zen": 7 * MIN, "editor": 12 * MIN}
        card = today_card(apps, {"rename": {"firefox": "Web", "zen": "Web"}})
        self.assertEqual(
            row_view(card),
            [
                ("Web", ["firefox", "zen"], 17 * MIN, False),
                ("editor", ["editor"], 12 * MIN, False),
            ],
        )

    def test_hide_folds_into_other_and_keeps_the_total(self):
        apps = {"editor": 10 * MIN, "bash": 4 * MIN}
        plain = today_card(apps)
        hidden = today_card(apps, {"hide": ["bash"]})
        self.assertEqual(hidden["today"]["total_ms"], plain["today"]["total_ms"])
        self.assertEqual(
            row_view(hidden),
            [
                ("editor", ["editor"], 10 * MIN, False),
                ("Other", ["bash"], 4 * MIN, True),
            ],
        )

    def test_sub_minute_apps_fold_into_other(self):
        apps = {"editor": 10 * MIN, "ssh": 30_000, "mise": 20_000}
        card = today_card(apps)
        self.assertEqual(
            row_view(card),
            [
                ("editor", ["editor"], 10 * MIN, False),
                ("Other", ["mise", "ssh"], 50_000, True),
            ],
        )
        self.assertEqual(
            sum(r["value_ms"] for r in card["rows"]), card["today"]["total_ms"]
        )

    def test_labels_are_summed_before_the_minute_floor(self):
        # Two 40 s keys sharing a label make an 80 s row, not two folded ones.
        card = today_card(
            {"a": 40_000, "b": 40_000}, {"rename": {"a": "AB", "b": "AB"}}
        )
        self.assertEqual(row_view(card), [("AB", ["a", "b"], 80_000, False)])

    def test_no_other_row_when_nothing_is_left(self):
        card = today_card({"editor": 10 * MIN})
        self.assertFalse(any(r["other"] for r in card["rows"]))

    def test_six_named_rows_then_other(self):
        apps = {f"app{i}": (20 - i) * MIN for i in range(8)}
        card = today_card(apps)
        self.assertEqual(len(card["rows"]), 7)
        self.assertEqual(card["rows"][-1]["keys"], ["app6", "app7"])
        self.assertEqual(card["rows"][-1]["value_ms"], (14 + 13) * MIN)

    def test_rows_always_add_up_to_the_hero(self):
        # A stored total above the apps' sum: the remainder lands in Other.
        card = today_card({"editor": 10 * MIN, "x": 30_000}, total=15 * MIN)
        self.assertEqual(card["today"]["total_ms"], 15 * MIN)
        self.assertEqual(sum(r["value_ms"] for r in card["rows"]), 15 * MIN)
        self.assertEqual(card["rows"][-1], {
            "name": "Other", "keys": ["x"], "value_ms": 5 * MIN, "value_text": "5m",
            "swatch": NO_THEME_OTHER, "other": True, "vs_text": "",
        })  # fmt: skip

    def test_equal_rows_sort_by_name(self):
        card = today_card({"b": 5 * MIN, "a": 5 * MIN, "c": 9 * MIN})
        self.assertEqual([r["name"] for r in card["rows"]], ["c", "a", "b"])

    def test_without_a_theme_rows_take_okabe_ito_by_rank(self):
        # D30's fallback: no colors.toml, so Okabe-Ito adapted to Color.qml's own
        # default surface #101315 (all six already clear 3:1 there) and Other the
        # readable muted of its default text #cacccc
        self.assertEqual(st.PALETTE, MONARCH_PALETTE)
        apps = {f"app{i}": (20 - i) * MIN for i in range(8)}
        card = today_card(apps)
        self.assertEqual(
            [r["swatch"] for r in card["rows"]],
            [c.lower() for c in MONARCH_PALETTE[:6]] + [NO_THEME_OTHER],
        )
        self.assertEqual(
            (card["palette"]["source"], card["palette"]["reason"]),
            ("okabe-ito", "missing"),
        )

    def test_segments_and_no_chips(self):
        apps = {"a": 40 * MIN, "b": 30 * MIN, "c": 20 * MIN, "d": 10 * MIN, "e": 30_000}
        card = today_card(apps, {"hide": ["a"]})
        self.assertNotIn("chips", card)  # D11: the card has no chips
        weights = [s["weight"] for s in card["segments"]]
        self.assertEqual(len(weights), len(card["rows"]))
        self.assertAlmostEqual(sum(weights), 1.0, places=9)
        self.assertTrue(all(0 <= w <= 1 for w in weights))

    def test_an_empty_today_has_no_rows_or_segments(self):
        card = today_card({}, extra_days={"2025-06-10": day(("a", HOUR))})
        self.assertEqual(
            (card["state"], card["rows"], card["segments"]), ("ok", [], [])
        )

    def test_names_file_states(self):
        self.assertEqual(today_card({"a": MIN})["names"], {"state": "missing"})
        self.assertEqual(today_card({"a": MIN}, {})["names"], {"state": "ok"})
        for bad in ([1], {"rename": [1]}, {"hide": "a"}, {"rename": {"a": 3}}):
            self.assertEqual(
                today_card({"a": MIN}, bad)["names"], {"state": "invalid"}, bad
            )

    def test_usable_entries_still_apply_when_some_are_bad(self):
        card = today_card({"a": MIN, "b": MIN}, {"rename": {"a": "A", "b": ""}})
        self.assertEqual([r["name"] for r in card["rows"]], ["A", "b"])
        self.assertEqual(card["names"], {"state": "invalid"})

    def test_keys_verb_reports_names_and_flags(self):
        doc = {
            "days": {
                "2025-06-11": day(
                    ("editor", 3 * MIN),
                    ("Browser", 2 * MIN),
                    ("bash", MIN),
                    ("zen", MIN),
                )
            }
        }
        names = {"rename": {"editor": "Editor", "BROWSER": "Web"}, "hide": ["bash"]}
        fx = Fixture(doc, names=names)
        try:
            rc, out, _ = fx.run("keys", "--days", "3")
            rows = [
                re.split(r"\s{2,}", line.strip()) for line in out.strip().splitlines()
            ]
        finally:
            fx.close()
        self.assertEqual(rc, 0)
        self.assertEqual(
            [(r[0], r[2], r[3], " ".join(r[4:])) for r in rows],
            [("editor", "Editor", "rename", ""), ("Browser", "Web", "rename", "case"),
             ("bash", "Terminal", "builtin", "hidden"), ("zen", "Zen", "builtin", "")],
        )  # fmt: skip


def keys_rows(apps, names=None, desktop=None):
    """The keys verb's (key, label, source) rows for one made-up day."""
    fx = Fixture(
        {"days": {"2025-06-11": day(*apps.items())}}, names=names, desktop=desktop
    )
    try:
        rc, out, err = fx.run("keys", "--days", "1")
    finally:
        fx.close()
    assert rc == 0, err
    rows = [re.split(r"\s{2,}", line.strip()) for line in out.strip().splitlines()]
    return {r[0]: (r[2], r[3]) for r in rows}


class DesktopNameTests(unittest.TestCase):
    """D17: a desktop entry's plain Name labels a key that no rename or curated
    built-in covers. Fixture XDG dirs only (written per test)."""

    def test_user_entry_overrides_system_entries(self):
        got = keys_rows({"viewer": MIN, "lower": MIN}, desktop={
            "home": {"viewer.desktop": entry("User Viewer")},
            "dirs": [{"viewer.desktop": entry("System Viewer")},
                     {"viewer.desktop": entry("Lowest"), "lower.desktop": entry("Lower Only")}],
        })  # fmt: skip
        self.assertEqual(got["viewer"], ("User Viewer", "desktop"))
        self.assertEqual(got["lower"], ("Lower Only", "desktop"))

    def test_file_id_then_startup_wm_class_without_case(self):
        got = keys_rows({"MIXED": MIN, "noteswin": MIN, "kde-kfoo": MIN}, desktop={
            "dirs": [{"Mixed.desktop": entry("Mixed Case"),
                      "notes.desktop": entry("Notes", "StartupWMClass=NotesWin"),
                      "kde/kfoo.desktop": entry("KFoo")}],
        })  # fmt: skip
        self.assertEqual(got["MIXED"], ("Mixed Case", "desktop"))
        self.assertEqual(got["noteswin"], ("Notes", "desktop"))
        self.assertEqual(got["kde-kfoo"], ("KFoo", "desktop"))

    def test_ambiguous_match_is_no_match(self):
        got = keys_rows({"dup": MIN, "twin": MIN}, desktop={"dirs": [{
            "dup-a.desktop": entry("Dup One", "StartupWMClass=dup"),
            "dup-b.desktop": entry("Dup Two", "StartupWMClass=dup"),
            "twin-a.desktop": entry("Twin", "StartupWMClass=twin"),
            "twin-b.desktop": entry("Twin", "StartupWMClass=twin"),
        }]})  # fmt: skip
        self.assertEqual(got["dup"], ("dup", "fallback"))
        self.assertEqual(got["twin"], ("Twin", "desktop"))  # same Name: not ambiguous

    def test_hidden_entries_are_skipped_and_delete_lower_copies(self):
        got = keys_rows({"ghost": MIN, "shadow": MIN}, desktop={
            "home": {"shadow.desktop": entry("Shadow Override", "Hidden=true")},
            "dirs": [{"ghost.desktop": entry("Ghost", "Hidden=true"),
                      "shadow.desktop": entry("Shadow")}],
        })  # fmt: skip
        self.assertEqual(got["ghost"], ("ghost", "fallback"))
        self.assertEqual(got["shadow"], ("shadow", "fallback"))

    def test_localized_names_and_other_groups_are_ignored(self):
        got = keys_rows({"lang": MIN, "onlyde": MIN, "action": MIN}, desktop={"dirs": [{
            "lang.desktop": "[Desktop Entry]\nName[de]=Sprache\nName=Lang\n",
            "onlyde.desktop": "[Desktop Entry]\nName[de]=Nur Deutsch\n",
            "action.desktop": "[Desktop Action new]\nName=New Window\n[Desktop Entry]\nName=Act\n",
        }]})  # fmt: skip
        self.assertEqual(got["lang"], ("Lang", "desktop"))
        self.assertEqual(got["onlyde"], ("onlyde", "fallback"))
        self.assertEqual(got["action"], ("Act", "desktop"))

    def test_malformed_files_are_skipped_without_a_crash(self):
        # Every one of these must neither crash nor hang the engine, and a
        # malformed user file must not hide the system copy of its id.
        got = keys_rows({"broken": MIN, "nogroup": MIN, "dangling": MIN, "fifo": MIN,
                         "adir": MIN, "shade": MIN}, desktop={
            "home": {"shade.desktop": b"[Desktop Entry]\nName=Bad \xff\n"},
            "dirs": [{"broken.desktop": b"[Desktop Entry]\nName=Broken\xfe\n",
                      "nogroup.desktop": "Name=No Group\n",
                      "dangling.desktop": ("symlink", "/nonexistent/x.desktop"),
                      "fifo.desktop": ("fifo",),
                      "adir.desktop": ("dir",),
                      "shade.desktop": entry("Shade")}],
        })  # fmt: skip
        for k in ("broken", "nogroup", "dangling", "fifo", "adir"):
            self.assertEqual(got[k], (k, "fallback"), k)
        self.assertEqual(got["shade"], ("Shade", "desktop"))

    def test_flatpak_exports_count(self):
        got = keys_rows({"org.flat.User": MIN, "org.flat.App": MIN}, desktop={
            "flatpak_user": {"org.flat.User.desktop": entry("Flat User")},
            "flatpak_system": {"org.flat.App.desktop": entry("Flat App")},
        })  # fmt: skip
        self.assertEqual(got["org.flat.User"], ("Flat User", "desktop"))
        self.assertEqual(got["org.flat.App"], ("Flat App", "desktop"))

    def test_rename_then_builtin_then_desktop_then_fallback(self):
        got = keys_rows(
            {"nvim": MIN, "claude": MIN, "google-chrome": MIN, "zen": MIN, "ssh": MIN},
            names={"rename": {"nvim": "My Editor"}},
            desktop={"dirs": [{"nvim.desktop": entry("Neovim Desktop"),
                               "google-chrome.desktop": entry("Google Chrome")}]},
        )  # fmt: skip
        self.assertEqual(got["nvim"], ("My Editor", "rename"))
        self.assertEqual(got["claude"], ("Claude Code", "builtin"))
        self.assertEqual(got["google-chrome"], ("Google Chrome", "desktop"))
        self.assertEqual(got["zen"], ("Zen", "builtin"))  # legacy, no entry
        self.assertEqual(got["ssh"], ("ssh", "fallback"))

    def test_claude_desktop_entry_never_names_claude_code(self):
        # The amendment: a claude.desktop with Name=Claude is present; the key
        # "claude" still reads Claude Code, and the Claude desktop app's key is
        # its own row.
        desktop = {"home": {"claude.desktop": entry("Claude")},
                   "dirs": [{"com.anthropic.Claude.desktop": entry("Claude")}]}  # fmt: skip
        got = keys_rows({"claude": MIN, "com.anthropic.Claude": MIN}, desktop=desktop)
        self.assertEqual(got["claude"], ("Claude Code", "builtin"))
        self.assertEqual(got["com.anthropic.Claude"], ("Claude", "desktop"))
        fx = Fixture({"days": {"2025-06-11": day(("claude", 5 * MIN),
                                                 ("com.anthropic.Claude", 7 * MIN))}},
                     desktop=desktop)  # fmt: skip
        try:
            rows = [(r["name"], r["keys"]) for r in fx.card()["rows"]]
        finally:
            fx.close()
        self.assertEqual(rows, [("Claude", ["com.anthropic.Claude"]),
                                ("Claude Code", ["claude"])])  # fmt: skip

    def test_web_apps_match_the_host_on_their_exec_line(self):
        def web(name, url):
            return entry(name, f"Exec=omarchy-launch-webapp {url}")

        # Omarchy web apps: Exec=omarchy-launch-webapp <url>, no StartupWMClass (D19b)
        got = keys_rows(
            {"chrome-x.com": MIN, "chrome-app.monarch.com__-Default": MIN,
             "chrome-youtube.com": MIN, "chrome-homelab": MIN, "chrome-dup.example": MIN,
             "chrome-none.example": MIN},
            desktop={"home": {
                "X.desktop": web("X", "https://x.com/"),
                "Monarch.desktop": web("Monarch", '"https://app.monarch.com"'),
                "YouTube.desktop": web("YouTube", "https://www.youtube.com/"),
                "Grafana.desktop": web("Homelab", "http://homelab:3000"),
                "dup-a.desktop": web("Dup A", "https://dup.example/a"),
                "dup-b.desktop": web("Dup B", "https://dup.example/b"),
            }},
        )  # fmt: skip
        self.assertEqual(got["chrome-x.com"], ("X", "desktop"))
        self.assertEqual(
            got["chrome-app.monarch.com__-Default"], ("Monarch", "desktop")
        )
        self.assertEqual(
            got["chrome-youtube.com"], ("YouTube", "desktop")
        )  # www. ignored
        self.assertEqual(got["chrome-homelab"], ("Homelab", "desktop"))  # port ignored
        self.assertEqual(
            got["chrome-dup.example"], ("dup.example", "fallback")
        )  # ambiguous
        self.assertEqual(got["chrome-none.example"], ("none.example", "fallback"))

    def test_a_toolkit_suffix_retries_the_desktop_lookup(self):
        got = keys_rows(
            {"viewer-wayland": MIN, "paint-x11": MIN, "other-wayland": MIN},
            desktop={"home": {"viewer.desktop": entry("Image Viewer"),
                              "paint.desktop": entry("Paint")}},
        )  # fmt: skip
        self.assertEqual(got["viewer-wayland"], ("Image Viewer", "desktop"))
        self.assertEqual(got["paint-x11"], ("Paint", "desktop"))
        self.assertEqual(got["other-wayland"], ("other", "fallback"))

    def test_same_label_keys_stay_one_row(self):
        fx = Fixture({"days": {"2025-06-11": day(("imv", 2 * MIN), ("imv-wayland", MIN))}},
                     desktop={"dirs": [{"imv.desktop": entry("imv")}]})  # fmt: skip
        try:
            rows = [(r["name"], r["keys"], r["value_ms"]) for r in fx.card()["rows"]]
        finally:
            fx.close()
        self.assertEqual(rows, [("imv", ["imv", "imv-wayland"], 3 * MIN)])

    def test_desktop_dirs_follow_xdg(self):
        env = {"XDG_DATA_HOME": "/h", "XDG_DATA_DIRS": "/a:rel:/b:/a/",
               "FLATPAK_USER_DIR": "/fu", "FLATPAK_SYSTEM_DIR": "/b/../fs"}  # fmt: skip
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            dirs = st.desktop_dirs()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(dirs, ["/h/applications", "/a/applications", "/b/applications",
                                "/fu/exports/share/applications",
                                "/fs/exports/share/applications"])  # fmt: skip


def mon_to_wed(extra=None, as_of="2025-06-11T15:00", spec=None):
    """D38's fixture: 3h every Monday, Tuesday and Wednesday from Mon 2025-03-03 to
    Wed 2025-05-28 (39 days, 13 whole weeks; March began before tracking, April and
    May are whole months), plus `extra` {day key: hours}. The card, or one page."""
    days = {}
    d = datetime.date(2025, 3, 3)
    while d <= datetime.date(2025, 5, 28):
        if d.weekday() < 3:
            days[d.isoformat()] = {"total": 3 * HOUR, "apps": {"editor": 3 * HOUR}}
        d += datetime.timedelta(days=1)
    for k, h in (extra or {}).items():
        days[k] = {"total": int(h * HOUR), "apps": {"editor": int(h * HOUR)}}
    fx = Fixture({"days": days})
    try:
        return fx.page(spec, as_of=as_of)["page"] if spec else fx.card(as_of=as_of)
    finally:
        fx.close()


UNDER = {"2025-06-09": 1, "2025-06-10": 1, "2025-06-11": 1}


class AverageTests(unittest.TestCase):
    """D38, worked out by hand: every page against the all-time average of its kind.
    Today is Wed 2025-06-11."""

    def test_under_so_far_on_every_page(self):
        card = mon_to_wed(UNDER)
        # days: (38 x 3h + Jun 9 1h + Jun 10 1h) / 40 = 2h 54m (Mon Mar 3, the
        # install day, is left out: D45); today is not over
        day = card["day"]["line"]
        self.assertEqual(
            (day["state"], day["text"], day["periods"]),
            ("below", "average 2h 54m a day", 40),
        )
        self.assertNotIn("head", day)  # D41: the line is only the comparison
        # weeks: Mon..Wed of the 13 whole weeks is 9h; Jun 2-8 had nothing: not a week
        week = card["week"]["line"]
        self.assertEqual((week["text"], week["periods"]), ("average 9h by Wed", 13))  # fmt: skip
        # months: Apr 1-11 (Apr 1, 2, 7, 8, 9) 15h, May 1-11 (May 5, 6, 7) 9h: 12h
        month = card["month"]["line"]
        self.assertEqual((month["text"], month["periods"]), ("average 12h by Jun 11", 2))  # fmt: skip

    def test_one_average_day_on_every_current_chart(self):
        # D41: Day, Week and Month draw the same number, the Day's average day:
        # (39 x 3h + 2h) / 41 = 2h 54m; the label says the unit, not the count
        card = mon_to_wed(UNDER)
        # D42: the Day's chart is hourly and draws none; its line's average is it
        means = [
            card["day"]["line"]["average_ms"],
            card["week"]["mean_ms"],
            card["month"]["mean_ms"],
        ]
        self.assertEqual(len(set(means)), 1, means)
        self.assertEqual(card["day"]["line"]["average_text"], "2h 54m")
        self.assertEqual(card["week"]["mean_text"], "avg 2h 54m a day")
        self.assertEqual(card["month"]["mean_text"], "avg 2h 54m a day")
        # the Year's bars are months: (42h + 36h) / 2 = 39h
        self.assertEqual(card["year"]["mean_text"], "avg 39h a month")
        # the scale reaches the line
        w = card["week"]
        self.assertAlmostEqual(w["mean_y"], w["mean_ms"] / w["y_max_ms"])

    def test_past_pages_draw_the_same_average_day(self):
        # a past Week or Month: every completed day; a past Day leaves itself out,
        # its line and its dashed line alike: (117h + 1h) / 40 = 2h 57m
        self.assertEqual(
            mon_to_wed(UNDER, spec="week:2025-05-26")["mean_text"], "avg 2h 54m a day"
        )
        self.assertEqual(
            mon_to_wed(UNDER, spec="month:2025-04")["mean_text"], "avg 2h 54m a day"
        )
        past = mon_to_wed(UNDER, spec="day:2025-06-10")
        self.assertEqual(past["line"]["average_text"], "2h 57m")

    def test_near_and_above(self):
        card = mon_to_wed({"2025-06-02": 3, "2025-06-03": 3, "2025-06-04": 3,
                           "2025-06-09": 3, "2025-06-10": 3, "2025-06-11": 3})  # fmt: skip
        self.assertEqual(card["day"]["line"]["text"], "≈ average")
        self.assertEqual(card["week"]["line"]["text"], "≈ average by Wed")
        # June so far 18h against 12h; completed Jun 1-10 hold 15h: 1h 30m a day
        self.assertEqual(card["month"]["line"]["text"], "▲ 6h above average by Jun 11")  # fmt: skip

    def test_the_dead_band_is_max_five_minutes_or_five_percent(self):
        # the average day is 3h: the band is max(5 min, 9 min) = 9 min
        self.assertEqual(
            mon_to_wed({"2025-06-11": 3 + 8 / 60})["day"]["line"]["text"], "≈ average"
        )
        self.assertEqual(mon_to_wed({"2025-06-11": 3 + 10 / 60})["day"]["line"]["text"],
                         "▲ 10m above average")  # fmt: skip

    def test_a_finished_period_is_compared_whole_without_itself(self):
        # week of May 26: 9h; the other 12 whole weeks average 9h
        w = mon_to_wed(UNDER, spec="week:2025-05-26")
        self.assertEqual((w["line"]["text"], w["line"]["periods"]), ("≈ average", 12))
        # Jun 10: 1h; the other 40 days: (117h + 1h) / 40 = 2h 57m
        d = mon_to_wed(UNDER, spec="day:2025-06-10")
        self.assertEqual(d["line"]["text"], "▼ 1h 57m below average")
        self.assertEqual(d["rows"][0]["vs_text"], "▼ 1h 57m")
        # April without itself rests on May alone, one month of the two needed; June,
        # a whole month, ends Jun 30
        self.assertEqual(
            mon_to_wed(UNDER, spec="month:2025-04")["line"]["text"],
            "average starts Jul 1",
        )

    def test_rows_against_the_apps_own_average(self):
        rows = mon_to_wed(UNDER)["day"]["rows"]
        self.assertEqual(
            (rows[0]["name"], rows[0]["vs_text"]), ("editor", "avg 2h 54m")
        )

    def test_the_date_an_average_starts(self):
        # tracked from today, Wed Jun 11: today is the install day (D45), so Jun 12,
        # 13 and 14 make three -> Jun 15; this
        # week began before tracking, so two whole weeks end Jun 29 -> Jun 30; June
        # too, so July and August -> Sep 1
        fx = Fixture({"days": {"2025-06-11": day(("editor", HOUR))}})
        try:
            card = fx.card()
        finally:
            fx.close()
        lines = [card[k]["line"] for k in ("day", "week", "month")]
        self.assertEqual([x["state"] for x in lines], ["baseline"] * 3)
        self.assertEqual([x["text"] for x in lines],
                         ["average starts Jun 15", "average starts Jun 30", "average starts Sep 1"])  # fmt: skip
        self.assertEqual([x["average_text"] for x in lines], ["", "", ""])
        self.assertEqual(card["rows"][0]["vs_text"], "")
        # D42: a day recorded before hours: no hourly chart until tomorrow
        self.assertEqual(card["day"]["hours"]["text"], "hourly from Jun 12")

    def test_a_past_day_leaves_itself_out_of_the_count(self):
        days = {
            k: {"total": HOUR, "apps": {"e": HOUR}}
            for k in ("2025-06-09", "2025-06-10", "2025-06-11")
        }
        fx = Fixture({"days": days})
        try:
            # Jun 9 is the install day and leaves itself out: Jun 10 alone, so today
            # and Jun 12 make three
            self.assertEqual(
                fx.page("day:2025-06-09")["page"]["line"]["text"],
                "average starts Jun 13",
            )
            # today: Jun 9 is the install day (D45): Jun 10 alone again -> Jun 13
            self.assertEqual(fx.card()["day"]["line"]["text"], "average starts Jun 13")
        finally:
            fx.close()


def hour_day(spec, extra=0):
    """D42: a day recorded by hour, {hour: minutes}; `extra` ms counted before hours
    were (a partial day)."""
    h = [0] * 24
    for i, m in spec.items():
        h[i] = m * MIN
    return {"total": sum(h) + extra, "apps": {"editor": sum(h) + extra}, "hours": h}


def hours_page(days, spec=None, as_of="2025-06-11T15:20", years=None):
    fx = Fixture({"days": days, "years": years or {}})
    try:
        return (
            fx.page(spec, as_of=as_of)["page"]["hours"]
            if spec
            else fx.card(as_of=as_of)["day"]["hours"]
        )
    finally:
        fx.close()


class TimeScaleTests(unittest.TestCase):
    """D44, by hand: two evenly spaced gridlines on a scaling chart, clear of the
    dashed average; the fixed scale by hour (D43) unchanged."""

    def test_the_largest_pair_with_room_below_the_top(self):
        for y_max, mean, want in (
            (9 * HOUR + 13 * MIN, None, ["4h", "8h"]),  # 5h / 10h: 10h is over the top
            (82 * HOUR + 37 * MIN, None, ["30h", "60h"]),  # 50h / 100h: over
            (112 * HOUR, None, ["50h", "100h"]),  # 100h is 89 % up
            (34 * MIN, None, ["15m", "30m"]),
            (33 * MIN, None, []),  # 30m would be 91 % up
            (10 * MIN, None, []),
        ):
            mean_y = mean / y_max if mean else None
            ticks = st.scale_ticks(y_max, mean_y)
            self.assertEqual([t["text"] for t in ticks], want, st.fmt(y_max))
            for t in ticks:
                self.assertEqual(t["y"], t["ms"] / y_max)
            if ticks:
                self.assertEqual(ticks[1]["ms"], 2 * ticks[0]["ms"])  # evenly spaced
                self.assertLessEqual(ticks[1]["y"], 0.9)

    def test_a_pair_meeting_the_dashed_average_gives_way_to_the_next(self):
        # 9h 13m with the average at 8h: the 8h line would sit on it -> 3h / 6h
        ticks = st.scale_ticks(9 * HOUR + 13 * MIN, 8 * HOUR / (9 * HOUR + 13 * MIN))
        self.assertEqual([t["text"] for t in ticks], ["3h", "6h"])
        # 10h with the average at 4h: 4h / 8h meets it at 4h -> 3h / 6h (3h is 10 % away)
        self.assertEqual(
            [t["text"] for t in st.scale_ticks(10 * HOUR, 0.4)], ["3h", "6h"]
        )
        # 8 % is "within": 4h at 0.4 against an average at 0.48 still gives way
        self.assertEqual(
            [t["text"] for t in st.scale_ticks(10 * HOUR, 0.48)], ["3h", "6h"]
        )
        # ... and 8.1 % does not
        self.assertEqual(
            [t["text"] for t in st.scale_ticks(10 * HOUR, 0.481)], ["4h", "8h"]
        )

    def test_every_scaling_chart_has_two_gridlines_clear_of_its_average(self):
        card = mon_to_wed(UNDER)
        for k in ("week", "month", "year"):
            p = card[k]
            self.assertEqual(len(p["ticks"]), 2, k)
            for t in p["ticks"]:
                self.assertEqual(
                    (t["y"], st.fmt(t["ms"])), (t["ms"] / p["y_max_ms"], t["text"]), k
                )
                if p["mean_y"] is not None:
                    self.assertGreater(abs(t["y"] - p["mean_y"]), 0.08, k)

    def test_the_hourly_scale_is_fixed_at_an_hour(self):
        h = hours_page({"2025-06-11": hour_day({9: 5})})
        self.assertEqual(
            [(t["text"], t["y"]) for t in h["ticks"]], [("30m", 0.5), ("1h", 1.0)]
        )
        self.assertEqual(h["y_max_ms"], HOUR)
        # D48: a partial day draws on the same scale; a day without hours has none
        self.assertEqual(
            hours_page({"2025-06-11": hour_day({9: 5}, extra=HOUR)})["ticks"],
            h["ticks"],
        )
        self.assertEqual(hours_page({"2025-06-11": day(("editor", HOUR))})["ticks"], [])


# A first week, made up: the install day (Tue Mar 4, 20m), then two whole days
FIRST_DAYS = {"2025-03-04": {"e": 20 * MIN}, "2025-03-05": {"e": 5 * HOUR},
              "2025-03-06": {"e": 10 * HOUR}}  # fmt: skip


class InstallDayTests(unittest.TestCase):
    """D45, by hand: the install day is only partly tracked, so no day average
    counts it."""

    def test_the_average_waits_a_day_for_the_install_day(self):
        days = dict(FIRST_DAYS, **{"2025-03-07": {"e": 2 * HOUR}})
        card = week_card(days, as_of="2025-03-07T12:00")
        # Mar 5 and 6 count, two of three: today is the third once over -> Mar 8
        self.assertEqual(card["day"]["line"]["text"], "average starts Mar 8")
        self.assertEqual(card["rows"][0]["vs_text"], "")

    def test_once_there_the_install_day_stays_out(self):
        days = dict(
            FIRST_DAYS, **{"2025-03-07": {"e": 6 * HOUR}, "2025-03-08": {"e": HOUR}}
        )
        card = week_card(days, as_of="2025-03-08T12:00")
        # (5h + 10h + 6h) / 3 = 7h, not (20m + 5h + 10h + 6h) / 4 = 5h 20m
        line = card["day"]["line"]
        self.assertEqual((line["average_text"], line["periods"]), ("7h", 3))
        self.assertEqual(
            card["rows"][0]["vs_text"], "avg 7h"
        )  # the app's own average too
        self.assertEqual(card["week"]["mean_text"], "avg 7h a day")


class HourChartTests(unittest.TestCase):
    """D42, by hand: the Day page's 24 bars. Today is Wed 2025-06-11, 3:20 PM."""

    def test_today_by_hour_this_hour_current_later_ones_empty(self):
        h = hours_page({"2025-06-11": hour_day({9: 50, 14: 60, 15: 10})})
        self.assertEqual((h["state"], h["text"], h["y_max_ms"]), ("ok", "", HOUR))
        bars = h["bars"]
        self.assertEqual(len(bars), 24)
        self.assertEqual(
            [b["state"] for b in bars], ["past"] * 15 + ["current"] + ["future"] * 8
        )
        self.assertEqual(
            [(b["hour"], b["y"]) for b in bars if b["ms"]],
            [(9, 50 / 60), (14, 1.0), (15, 10 / 60)],
        )
        self.assertEqual(
            [b["label"] for b in bars if b["label"]], ["12a", "6a", "12p", "6p"]
        )
        self.assertEqual(
            [bars[i]["label"] for i in (0, 6, 12, 18)], ["12a", "6a", "12p", "6p"]
        )
        self.assertEqual((bars[0]["x"], bars[23]["x"]), (0, 1))
        self.assertEqual((bars[15]["text"], bars[16]["text"]), ("10m", ""))
        self.assertEqual(
            (h["mean_text"], h["mean_y"]), ("", None)
        )  # no dashed line here

    def test_a_past_day_shows_all_its_hours(self):
        days = {"2025-06-10": hour_day({0: 5, 23: 30}), "2025-06-11": hour_day({9: 10})}
        h = hours_page(days, "day:2025-06-10")
        self.assertEqual({b["state"] for b in h["bars"]}, {"past"})
        self.assertEqual([b["ms"] for b in h["bars"]][::23], [5 * MIN, 30 * MIN])

    def test_a_day_without_hours_has_no_bars(self):
        # Jun 9 whole; Jun 10 has no hours; today began 2h before hours were
        days = {"2025-06-09": hour_day({9: 30}), "2025-06-10": day(("editor", HOUR)),
                "2025-06-11": hour_day({15: 10}, extra=2 * HOUR)}  # fmt: skip
        h = hours_page(days, "day:2025-06-10")
        self.assertEqual(
            (h["state"], h["text"], h["bars"], h["ticks"]),
            ("pending", "hourly from Jun 9", [], []),
        )
        # D48: today, recorded in part, draws its hours over a caption
        h = hours_page(days)
        self.assertEqual((h["state"], h["text"]), ("partial", "hours from 3 PM"))
        self.assertEqual(h["bars"][15]["ms"], 10 * MIN)
        self.assertEqual(len(h["bars"]), 24)

    def test_none_whole_yet_means_tomorrow(self):
        h = hours_page({"2025-06-11": day(("editor", HOUR))})
        self.assertEqual(h["text"], "hourly from Jun 12")
        # a partial today is no whole day either
        h = hours_page({"2025-06-10": day(("editor", HOUR)),
                        "2025-06-11": hour_day({15: 10}, extra=HOUR)}, "day:2025-06-10")  # fmt: skip
        self.assertEqual(h["text"], "hourly from Jun 12")
        # an archive day of totals only is not whole either
        h = hours_page(
            {"2025-06-11": hour_day({15: 10})},
            "day:2025-06-10",
            years={"2025": {"2025-06-10": HOUR}},
        )
        self.assertEqual((h["state"], h["text"]), ("pending", "hourly from Jun 11"))

    def test_d48_a_partial_day_draws_the_hours_it_has(self):
        # hour 0 has time: "12 AM"; every past hour is "past", exact ms
        days = {
            "2025-06-10": hour_day({0: 10, 9: 20}, extra=HOUR),
            "2025-06-11": hour_day({9: 5}),
        }
        h = hours_page(days, "day:2025-06-10")
        self.assertEqual((h["state"], h["text"]), ("partial", "hours from 12 AM"))
        self.assertEqual(
            [b["ms"] for b in h["bars"]], [10 * MIN] + [0] * 8 + [20 * MIN] + [0] * 14
        )
        self.assertEqual({b["state"] for b in h["bars"]}, {"past"})
        # an empty hour before the first with time is no start: 11 PM
        h = hours_page({"2025-06-10": hour_day({22: 0, 23: 30}, extra=HOUR),
                        "2025-06-11": hour_day({9: 5})}, "day:2025-06-10")  # fmt: skip
        self.assertEqual(h["text"], "hours from 11 PM")
        # installed at noon: hours only from then, today at 1:30 PM
        h = hours_page({"2025-06-11": hour_day({12: 25, 13: 30}, extra=2 * HOUR)},
                       as_of="2025-06-11T13:30")  # fmt: skip
        self.assertEqual((h["state"], h["text"]), ("partial", "hours from 12 PM"))
        self.assertEqual(
            [b["state"] for b in h["bars"]][12:15], ["past", "current", "future"]
        )

    def test_d48_a_whole_day_has_no_caption_and_bad_hours_stay_pending(self):
        h = hours_page({"2025-06-11": hour_day({9: 5})})
        self.assertEqual((h["state"], h["text"]), ("ok", ""))
        # hours holding no time, or more than the day's total: pending
        zero = dict(hour_day({}), total=HOUR, apps={"editor": HOUR})
        over = dict(hour_day({9: 90}), total=HOUR, apps={"editor": HOUR})
        for rec in (zero, over):
            h = hours_page(
                {"2025-06-10": rec, "2025-06-11": hour_day({9: 5})}, "day:2025-06-10"
            )
            self.assertEqual(
                (h["state"], h["text"], h["bars"]),
                ("pending", "hourly from Jun 11", []),
            )

    def test_a_day_with_no_time_is_whole_zeros(self):
        days = {"2025-06-09": hour_day({9: 30}), "2025-06-11": hour_day({9: 10})}
        h = hours_page(days, "day:2025-06-10")
        self.assertEqual((h["state"], sum(b["ms"] for b in h["bars"])), ("ok", 0))

    def test_the_repeated_fall_back_hour_is_drawn_full(self):
        h = hours_page({"2025-11-02": hour_day({1: 120}), "2025-11-03": hour_day({8: 5})},
                       "day:2025-11-02", as_of="2025-11-03T09:00")  # fmt: skip
        self.assertEqual(
            (h["bars"][1]["ms"], h["bars"][1]["y"], h["bars"][1]["text"]),
            (2 * HOUR, 1, "2h"),
        )


def totals_card(days, months=None, years=None, as_of="2025-06-11T15:00"):
    """Card for days {key: ms} (one app each), legacy months and archive."""
    recs = {k: {"total": ms, "apps": {"editor": ms}} for k, ms in days.items()}
    fx = Fixture({"days": recs, "months": months or {}, "years": years or {}})
    try:
        return fx.card(as_of=as_of)
    finally:
        fx.close()


def week_card(days, as_of="2025-06-11T15:00", years=None, names=None, months=None):
    """Card for days {key: {app: ms}} (totals = the apps' sum unless "total" given)."""
    recs = {}
    for k, apps in days.items():
        apps = dict(apps)
        total = apps.pop("total", None)
        recs[k] = {
            "total": sum(apps.values()) if total is None else total,
            "apps": apps,
        }
    fx = Fixture(
        {"days": recs, "months": months or {}, "years": years or {}}, names=names
    )
    try:
        return fx.card(as_of=as_of)
    finally:
        fx.close()


def this_week(card):
    return card["week"]


def fetch(days, spec, as_of="2025-06-11T15:00", years=None, months=None, names=None):
    """The page `spec` (V1) for days {key: {app: ms}}, as week_card builds them."""
    recs = {}
    for k, apps in days.items():
        apps = dict(apps)
        total = apps.pop("total", None)
        recs[k] = {
            "total": sum(apps.values()) if total is None else total,
            "apps": apps,
        }
    fx = Fixture(
        {"days": recs, "months": months or {}, "years": years or {}}, names=names
    )
    try:
        doc = fx.page(spec, as_of=as_of)
    finally:
        fx.close()
    assert doc["state"] == "ok", doc
    return doc["page"]


class WeekTests(unittest.TestCase):
    """D19, worked out by hand. 2025-06-11 is a Wednesday; its week is Mon 06-09
    to Sun 06-15, and the week before is 06-02 to 06-08."""

    def test_weeks_start_on_monday_and_mark_every_day(self):
        card = week_card({"2025-06-02": {"e": 30 * MIN}, "2025-06-08": {"e": HOUR},
                          "2025-06-09": {"e": 2 * HOUR}, "2025-06-11": {"e": 3 * HOUR}})  # fmt: skip
        w = this_week(card)
        self.assertEqual((w["key"], w["range_text"], w["label"]),
                         ("2025-06-09", "Jun 9 – 15", "this week"))  # fmt: skip
        self.assertEqual(
            [(b["label"], b["state"], b["ms"]) for b in w["bars"]],
            [("M", "past", 2 * HOUR), ("T", "past", 0), ("W", "today", 3 * HOUR),
             ("T", "future", 0), ("F", "future", 0), ("S", "future", 0), ("S", "future", 0)],
        )  # fmt: skip
        self.assertEqual([b["x"] for b in w["bars"]], [i / 6 for i in range(7)])
        self.assertEqual([b["y"] for b in w["bars"]][:3], [2 / 3, 0, 1])
        self.assertEqual((w["total_ms"], w["total_text"]), (5 * HOUR, "5h"))
        # completed days Mon and Tue (C5: today left out): (5h - 3h) / 2 = 1h
        self.assertEqual(
            w["avg_ms"], HOUR
        )  # D41: not painted: "avg" is the all-time average
        # last week's Mon..Wed (06-02..06-04) had 30m: above by 4h 30m
        # D38: one whole week (Jun 2-8) is over; this one ends Sunday -> Jun 16
        self.assertEqual(
            w["line"]["text"],
            "average starts Jun 16",
        )
        # the week before (Mon 30m, Sun 1h) is "last week"; tracked from its Monday
        self.assertEqual(w["prev"], "2025-06-02")
        prev = fetch({"2025-06-02": {"e": 30 * MIN}, "2025-06-08": {"e": HOUR},
                      "2025-06-09": {"e": 2 * HOUR}, "2025-06-11": {"e": 3 * HOUR}}, "week:2025-06-02")  # fmt: skip
        self.assertEqual(
            (prev["key"], prev["label"], prev["total_ms"]),
            ("2025-06-02", "last week", 90 * MIN),
        )
        self.assertEqual([b["state"] for b in prev["bars"]], ["past"] * 7)

    def test_history_must_reach_the_compared_days(self):
        # tracking began Sunday 06-08: last week's Mon..Wed are before it, so
        # there is nothing fair to compare against (a baseline, not "▲ 5h")
        days = {"2025-06-08": {"e": HOUR}, "2025-06-11": {"e": 5 * HOUR}}
        w = this_week(week_card(days))
        self.assertEqual(w["line"]["state"], "baseline")
        prev = fetch(days, "week:2025-06-02")
        self.assertEqual([b["state"] for b in prev["bars"]], ["before"] * 6 + ["past"])
        self.assertEqual(
            (prev["prev"], prev["next"], w["prev"], w["next"]),
            (None, "2025-06-09", "2025-06-02", None),
        )

    def test_a_new_install_is_a_baseline_with_empty_slots(self):
        # tracked from today, Wed: next week's Mon and Tue would compare with days
        # before tracking, so the comparison begins Wed Jun 18 (audit C4)
        w = this_week(week_card({"2025-06-11": {"e": 50 * MIN}}))
        self.assertEqual(
            [b["state"] for b in w["bars"]][:3], ["before", "before", "today"]
        )
        # D38: this week began before tracking (Wed), so two whole weeks end Jun 29
        self.assertEqual(w["line"], {"state": "baseline",
                                     "text": "average starts Jun 30", "average_ms": 0,
                                     "average_text": "", "periods": 0})  # fmt: skip

    def test_a_week_begun_after_the_first_day_counts_whole(self):
        # tracked from last Thursday 06-05; today is Tue 06-10. Last week was only
        # partly tracked; this one is whole: it and the next -> Jun 23
        w = this_week(week_card({"2025-06-05": {"e": HOUR}, "2025-06-10": {"e": HOUR}},
                                as_of="2025-06-10T12:00"))  # fmt: skip
        self.assertEqual(w["line"]["text"], "average starts Jun 23")

    def test_a_week_with_nothing_tracked_is_not_an_average_week(self):
        # whole weeks May 19 (2h) and Jun 2 (4h); May 26 had nothing, so it is left out:
        # Mon..Wed average (2h + 4h) / 2 = 3h, not (2h + 0 + 4h) / 3 = 2h
        days = {"2025-05-19": {"e": 2 * HOUR}, "2025-06-02": {"e": 4 * HOUR},
                "2025-06-11": {"e": 2 * HOUR}}  # fmt: skip
        line = this_week(week_card(days))["line"]
        self.assertEqual((line["text"], line["periods"]), ("average 3h by Wed", 2))

    def test_on_a_sunday_the_week_still_says_by(self):
        # the week is not over until Sunday ends; the span is the whole week
        card = mon_to_wed(
            {"2025-06-09": 3, "2025-06-10": 3, "2025-06-11": 3},
            as_of="2025-06-15T20:00",
        )
        self.assertEqual(card["week"]["line"]["text"], "≈ average by Sun")
        # with one whole week, the Sunday's own week ends tonight: from tomorrow
        days = {
            "2025-06-02": {"e": 10 * HOUR},
            "2025-06-15": {"e": 10 * HOUR + 4 * MIN},
        }
        w = this_week(week_card(days, as_of="2025-06-15T20:00"))
        self.assertEqual(w["line"]["text"], "average starts Jun 16")

    def test_an_earlier_week_compares_whole_weeks_without_itself(self):
        # the week of Jun 2 (3h + 1h) against May 19 (8h) and May 26 (10h): 9h
        days = {"2025-05-19": {"e": 8 * HOUR}, "2025-05-26": {"e": 10 * HOUR},
                "2025-06-02": {"e": 3 * HOUR}, "2025-06-08": {"e": HOUR},
                "2025-06-11": {"e": HOUR}}  # fmt: skip
        prev = fetch(days, "week:2025-06-02")
        self.assertEqual(prev["line"]["text"], "▼ 5h below average")  # fmt: skip

    def test_rows_fold_like_today_and_add_up_to_the_week(self):
        # an archive day in the week has a total but no apps: it lands in Other
        days = {"2025-06-09": {"a": 3 * HOUR, "b": 2 * HOUR, "bash": HOUR, "tiny": 30_000},
                "2025-06-11": {"c": HOUR, "d": 50 * MIN, "e": 40 * MIN, "f": 30 * MIN,
                               "g": 20 * MIN, "total": 4 * HOUR}}  # fmt: skip
        w = this_week(week_card(days, years={"2025": {"2025-06-10": HOUR}},
                                names={"hide": ["bash"]}))  # fmt: skip
        self.assertEqual(
            w["total_ms"], 3 * HOUR + 2 * HOUR + HOUR + 30_000 + 4 * HOUR + HOUR
        )
        self.assertEqual(
            [r["name"] for r in w["rows"]], ["a", "b", "c", "d", "e", "f", "Other"]
        )
        self.assertEqual(sum(r["value_ms"] for r in w["rows"]), w["total_ms"])
        other = w["rows"][-1]
        # bash (hidden) 1h + tiny 30s + g (7th label) 20m + day 2's 40m unlisted + archive 1h
        self.assertEqual(other["value_ms"], HOUR + 30_000 + 20 * MIN + 40 * MIN + HOUR)
        self.assertEqual(sum(sg["weight"] for sg in w["segments"]), 1)

    def test_range_text_across_months_and_years(self):
        days = {"2024-12-23": {"e": HOUR}, "2025-01-03": {"e": HOUR}}
        card = week_card(days, as_of="2025-01-03T12:00")
        before = fetch(days, "week:2024-12-23", as_of="2025-01-03T12:00")
        self.assertEqual([before["range_text"], card["week"]["range_text"]],
                         ["Dec 23 – 29, 2024", "Dec 30 – Jan 5"])  # fmt: skip

    def test_weeks_go_back_to_the_first_tracked_one_with_no_cap(self):
        # V1: no 52-week limit; ‹ stops at the week holding the first tracked day
        days = {"2023-01-04": {"e": HOUR}, "2025-06-11": {"e": HOUR}}
        first = fetch(days, "week:2023-01-04")  # a Wednesday: its Monday's week
        self.assertEqual((first["key"], first["prev"], first["next"]),
                         ("2023-01-02", None, "2023-01-09"))  # fmt: skip
        fx = Fixture({"days": {"2023-01-04": day(("e", HOUR))}})
        try:
            self.assertEqual(fx.page("week:2022-12-26")["state"], "error")
            self.assertEqual(fx.page("week:2025-06-16")["state"], "error")  # the future
        finally:
            fx.close()

    def test_fallback_cards_carry_no_pages(self):
        fx = Fixture(None)
        try:
            card = fx.card()
            page = fx.page("week:2025-06-09")
        finally:
            fx.close()
        self.assertEqual(
            [card[k] for k in ("day", "week", "month", "year")], [None] * 4
        )
        self.assertEqual((page["state"], page["page"]), ("missing", None))


class YearPageTests(unittest.TestCase):
    """D19b, worked out by hand. Today is Wed 2025-06-11."""

    def test_month_states_follow_today_and_the_first_tracked_day(self):
        y = week_card({"2025-03-03": {"e": HOUR}, "2025-06-11": {"e": 2 * HOUR}})[
            "year"
        ]
        self.assertEqual(
            [(b["label"], b["state"]) for b in y["bars"]],
            [("Jan", "before"), ("Feb", "before"), ("Mar", "past"), ("Apr", "past"),
             ("May", "past"), ("Jun", "current"), ("Jul", "future"), ("Aug", "future"),
             ("Sep", "future"), ("Oct", "future"), ("Nov", "future"), ("Dec", "future")],
        )  # fmt: skip
        self.assertEqual([b["x"] for b in y["bars"]], [m / 11 for m in range(12)])
        self.assertEqual([b["y"] for b in y["bars"]][2:6], [0.5, 0, 0, 1])
        self.assertEqual(
            (y["label"], y["total_text"], y["y_max_text"]), ("this year", "3h", "2h")
        )
        # completed days Mar 4 .. Jun 10 = 28 + 30 + 31 + 10 = 99 (C5: today left
        # out; D47: the install day Mar 3 too); 3h - today's 2h - Mar 3's 1h = 0
        self.assertEqual(
            y["line"],
            {"state": "", "text": "0m a day since Mar 4", "days": 99},
        )

    def test_d47_the_install_day_is_in_no_year_line(self):
        # installed Mar 4 (20m); completed Mar 5 and 6; today Mar 7
        days = {"2025-03-04": {"e": 20 * MIN}, "2025-03-05": {"e": 5 * HOUR},
                "2025-03-06": {"e": 10 * HOUR}, "2025-03-07": {"e": 2 * HOUR}}  # fmt: skip
        y = week_card(days, as_of="2025-03-07T13:00")["year"]
        # (5h + 10h) / 2 = 7h 30m; the year starts the day after the install day
        self.assertEqual(
            y["line"], {"state": "", "text": "7h 30m a day since Mar 5", "days": 2}
        )
        self.assertEqual(y["total_ms"], 20 * MIN + 17 * HOUR)  # the total keeps it
        # the day after the install: no completed day but the install day, so it stays
        day2 = {"2025-03-04": {"e": 20 * MIN}, "2025-03-05": {"e": HOUR}}
        self.assertEqual(
            week_card(day2, as_of="2025-03-05T13:00")["year"]["line"],
            {"state": "", "text": "20m a day since Mar 4", "days": 1},
        )

    def test_d47_with_month_lumps_the_install_day_leaves_mid_year(self):
        # Jan 1 .. Mar 7 = 31 + 28 + 7 = 66 days; less today and the install day
        # Mar 4: 64. 54h (January lump) + 10h (Mar 6) = 64h -> 1h a day, from Jan 1
        days = {"2025-03-04": {"e": 20 * MIN}, "2025-03-06": {"e": 10 * HOUR},
                "2025-03-07": {"e": HOUR}}  # fmt: skip
        y = week_card(days, as_of="2025-03-07T13:00", months={"2025-01": 54 * HOUR})
        self.assertEqual(
            y["year"]["line"], {"state": "", "text": "1h a day", "days": 64}
        )
        # a past year's line never loses a later year's install day: Dec 1 .. 31
        y2024 = fetch(days, "year:2024", as_of="2025-03-07T13:00",
                      months={"2024-12": 31 * HOUR, "2025-01": 54 * HOUR})  # fmt: skip
        self.assertEqual(
            y2024["line"],
            {"state": "", "text": "1h a day since Dec 1, 2024", "days": 31},
        )

    def test_rows_add_up_to_the_year_with_archive_and_lumps_in_other(self):
        days = {"2025-06-09": {"a": 3 * HOUR, "bash": HOUR, "tiny": 30_000},
                "2025-06-11": {"b": 2 * HOUR}}  # fmt: skip
        fx = Fixture(
            {"days": {k: {"total": sum(v.values()), "apps": v} for k, v in days.items()},
             "months": {"2025-01": 2 * HOUR}, "years": {"2025": {"2025-02-03": HOUR}}},
            names={"hide": ["bash"]},
        )  # fmt: skip
        try:
            y = fx.card()["year"]
        finally:
            fx.close()
        self.assertEqual(
            y["total_ms"], 3 * HOUR + HOUR + 30_000 + 2 * HOUR + 2 * HOUR + HOUR
        )
        self.assertEqual([r["name"] for r in y["rows"]], ["a", "b", "Other"])
        self.assertEqual(y["rows"][-1]["value_ms"], HOUR + 30_000 + 2 * HOUR + HOUR)
        self.assertEqual(sum(r["value_ms"] for r in y["rows"]), y["total_ms"])
        # a January lump starts tracking on the 1st: no "since", Jan is past
        self.assertEqual(y["line"]["text"][-6:], " a day")
        self.assertEqual(y["bars"][0]["state"], "past")

    def test_a_full_past_year_and_the_labels(self):
        days = {"2023-12-31": {"e": HOUR}, "2024-07-01": {"e": 366 * MIN},
                "2025-06-11": {"e": HOUR}}  # fmt: skip
        years = [fetch(days, f"year:{y}") for y in (2023, 2024)] + [
            week_card(days)["year"]
        ]
        self.assertEqual(
            [(y["year"], y["label"]) for y in years],
            [(2023, "year"), (2024, "last year"), (2025, "this year")],
        )
        y2024 = years[1]
        # leap year, fully covered: 366m / 366 days = 1m
        self.assertEqual(
            y2024["line"],
            {"state": "", "text": "1m a day", "days": 366},
        )
        self.assertEqual({b["state"] for b in y2024["bars"]}, {"past"})
        self.assertEqual(years[0]["line"]["text"], "1h a day since Dec 31, 2023")
        self.assertEqual([(y["prev"], y["next"]) for y in years],
                         [(None, "2024"), ("2023", "2025"), ("2024", None)])  # fmt: skip


class MonthPageTests(unittest.TestCase):
    """V1, worked out by hand."""

    def test_a_bar_per_day_today_in_the_accent_and_a_baseline_date(self):
        # today Wed Jun 11; tracked from Fri May 30
        days = {"2025-05-30": {"e": 30 * MIN}, "2025-06-02": {"e": HOUR},
                "2025-06-11": {"e": 2 * HOUR}}  # fmt: skip
        m = week_card(days)["month"]
        self.assertEqual(
            (m["key"], m["range_text"], m["label"]), ("2025-06", "June", "this month")
        )
        self.assertEqual(len(m["bars"]), 30)
        self.assertEqual(
            [b["state"] for b in m["bars"]][9:12], ["past", "today", "future"]
        )
        self.assertEqual(
            [b["label"] for b in m["bars"] if b["label"]], ["1", "8", "15", "22", "29"]
        )
        self.assertEqual((m["bars"][0]["x"], m["bars"][-1]["x"]), (0, 1))
        self.assertEqual((m["total_text"], m["y_max_text"]), ("3h", "2h"))
        # completed Jun 1..10 (C5: today left out): (3h - 2h) / 10 = 6m. May began
        # before tracking (May 30), so no whole month is over: June and July -> Aug 1
        self.assertEqual(
            m["line"]["text"],
            "average starts Aug 1",
        )
        self.assertEqual((m["prev"], m["next"]), ("2025-05", None))
        may = fetch(days, "month:2025-05")
        self.assertEqual(
            (may["label"], may["prev"], may["next"]), ("last month", None, "2025-06")
        )
        self.assertEqual(
            [b["state"] for b in may["bars"]][28:], ["before", "past", "past"]
        )
        self.assertEqual(
            may["line"]["text"],
            "average starts Aug 1",
        )

    def test_the_last_day_of_a_month_counts_it_whole_from_the_first(self):
        # Mar 31: February began before tracking (Feb 10), March is whole and ends
        # today, so March and April -> May 1
        days = {"2025-02-10": {"e": 10 * HOUR}, "2025-03-31": {"e": HOUR}}
        m = week_card(days, as_of="2025-03-31T12:00")["month"]
        self.assertEqual(
            m["line"]["text"],
            "average starts May 1",
        )
        self.assertEqual(len(m["bars"]), 31)

    def test_a_legacy_lump_month_is_never_cut_short(self):
        # January is a 30h lump (no days), February holds 3h. On Mar 31 the span is 31
        # days, all of January: (30h + 3h) / 2 = 16h 30m. On Mar 30 January cannot be
        # cut to 30 days, so February alone: not yet an average
        months = {"2025-01": 30 * HOUR}
        days = {"2025-02-03": {"e": 2 * HOUR}, "2025-02-23": {"e": HOUR}, "2025-03-30": {"e": HOUR},
                "2025-03-31": {"e": HOUR}}  # fmt: skip
        m31 = week_card(days, as_of="2025-03-31T12:00", months=months)["month"]["line"]
        self.assertEqual(
            (m31["text"], m31["periods"]), ("average 16h 30m by Mar 31", 2)
        )
        m30 = week_card(days, as_of="2025-03-30T12:00", months=months)["month"]["line"]
        self.assertEqual((m30["state"], m30["periods"]), ("baseline", 1))
        jan = fetch(days, "month:2025-01", as_of="2025-03-31T12:00", months=months)
        self.assertEqual(
            (jan["total_ms"], [r["name"] for r in jan["rows"]]), (30 * HOUR, ["Other"])
        )
        # whole, without itself: February alone, one of the two needed; March, a whole
        # month, ends today -> Apr 1
        self.assertEqual(jan["line"]["text"], "average starts Apr 1")
        self.assertEqual({b["state"] for b in jan["bars"]}, {"past"})


DAY_PAGE_DAYS = {"2025-05-14": {"e": 10 * MIN},  # the install day: in no average (D45)
                 "2025-05-21": {"e": 3 * HOUR}, "2025-05-28": {"e": 3 * HOUR},
                 "2025-06-04": {"e": HOUR}, "2025-06-10": {"e": 30 * MIN}}  # fmt: skip


class DayPageTests(unittest.TestCase):
    """V1: the Day page for any day, worked out by hand. Today is Wed 2025-06-11."""

    def test_a_past_day_is_over_so_below_its_average_reads_below(self):
        d = fetch(DAY_PAGE_DAYS, "day:2025-06-04")
        self.assertEqual(
            (d["label"], d["pill"], d["total_text"]), ("day", "Wed · Jun 4", "1h")
        )
        # without itself: May 21, May 28, Jun 10 = (3h + 3h + 30m) / 3 = 2h 10m
        self.assertEqual(d["line"]["text"], "▼ 1h 10m below average")
        self.assertEqual(d["line"]["average_text"], "2h 10m")
        self.assertEqual(
            (d["hours"]["state"], d["hours"]["text"]), ("pending", "hourly from Jun 12")
        )
        self.assertEqual((d["prev"], d["next"]), ("2025-06-03", "2025-06-05"))

    def test_yesterday_the_first_day_and_another_year(self):
        self.assertEqual(fetch(DAY_PAGE_DAYS, "day:2025-06-10")["label"], "yesterday")
        first = fetch(DAY_PAGE_DAYS, "day:2025-05-21")
        # every day is in its average, later ones too, but the install day:
        # (3h + 1h + 30m) / 3 = 1h 30m
        self.assertEqual(
            (first["prev"], first["line"]["text"]),
            ("2025-05-20", "▲ 1h 30m above average"),
        )
        install = fetch(DAY_PAGE_DAYS, "day:2025-05-14")
        self.assertIsNone(install["prev"])
        self.assertTrue(
            install["line"]["text"].startswith("▼ "), install["line"]["text"]
        )
        old = fetch({"2024-12-31": {"e": HOUR}, "2025-01-02": {"e": HOUR}}, "day:2024-12-31",
                    as_of="2025-01-02T12:00")  # fmt: skip
        self.assertEqual(old["pill"], "Tue · Dec 31, 2024")

    def test_todays_day_page_is_the_glances_today(self):
        card = week_card(DAY_PAGE_DAYS)
        day = card["day"]
        self.assertEqual(
            (day["label"], day["prev"], day["next"]), ("today", "2025-06-10", None)
        )
        for key in ("rows", "segments"):
            self.assertEqual(day[key], card[key], key)
        # no time today yet: a whole day of 24 empty hours, this one current
        self.assertEqual(
            (day["hours"]["state"], day["hours"]["bars"][15]["state"]),
            ("ok", "current"),
        )


class PageVerbTests(unittest.TestCase):
    """V1: `card --page kind:key` prints one document and exits 0, whatever it is asked."""

    def test_anything_but_a_page_is_one_quiet_error_line(self):
        fx = Fixture(BASIC)
        try:
            for spec in ("day:2025-06-12", "day:2025-06-09", "month:2025-13", "month:0000-01",
                         "year:25", "week:bogus", "hour:2025", "day", ":", ""):  # fmt: skip
                doc = fx.page(spec)
                self.assertEqual((doc["state"], doc["page"]), ("error", None), spec)
                self.assertEqual(doc["message"], f"Not a page: {spec}")
                self.assertEqual(doc["schema"], 11)
        finally:
            fx.close()

    def test_the_card_stays_small_whatever_the_history(self):
        # three years of days: the card holds four pages, never a list per period
        start = datetime.date(2022, 6, 12)
        days = {(start + datetime.timedelta(days=i)).isoformat(): {"a": HOUR, "b": 20 * MIN}
                for i in range(1096)}  # fmt: skip
        recs = {k: {"total": sum(v.values()), "apps": v} for k, v in days.items()}
        fx = Fixture({"days": recs})
        try:
            rc, out, _ = fx.run("card")
        finally:
            fx.close()
        self.assertEqual(rc, 0)
        self.assertLess(len(out.encode()), 25_000)

        def longest(node):
            if isinstance(node, dict):
                return max([longest(v) for v in node.values()] + [0])
            if isinstance(node, list):
                return max([len(node)] + [longest(v) for v in node])
            return 0

        self.assertLessEqual(longest(json.loads(out)), 31)  # a month's bars


AUDIT_DOC = {
    "days": {
        "2025-05-31": {"total": HOUR, "apps": {"editor": HOUR}},  # Sat: last day of May
        "2025-06-01": {"total": 2 * HOUR, "apps": {"editor": 2 * HOUR}},  # Sun
        "2025-06-02": {"total": 3 * HOUR, "apps": {"web": 2 * HOUR, "bash": 30 * MIN, "tiny": 20_000}},
        "2025-06-11": {"total": 5 * HOUR, "apps": {"editor": 4 * HOUR}},  # 1h untracked
    },
    "months": {"2025-01": 7 * HOUR, "2024-11": 9 * HOUR},
    "years": {"2025": {"2025-02-03": 6 * HOUR}},
}  # fmt: skip


def audit(*args, names=None):
    fx = Fixture(AUDIT_DOC, names={"hide": ["bash"]} if names is None else names)
    try:
        rc, out, err = fx.run("report", *args)
    finally:
        fx.close()
    return rc, out, err


def audit_json(*args):
    rc, out, err = audit(*args, "--json")
    assert rc == 0, err
    return json.loads(out)


class AuditReportTests(unittest.TestCase):
    """D19b, worked out by hand. Today is Wed 2025-06-11."""

    def test_a_week_is_the_mon_sun_week_holding_the_date(self):
        # Sunday 06-01 belongs to the week of Mon 05-26 (May 31 and Jun 1 in it)
        w = audit_json("--week", "2025-06-01")
        self.assertEqual(
            (w["start"], w["end"], w["label"]),
            ("2025-05-26", "2025-06-01", "May 26 – Jun 1, 2025"),
        )
        self.assertEqual(w["total_ms"], 3 * HOUR)
        self.assertEqual(
            [d["key"] for d in w["days"]][-2:], ["2025-05-31", "2025-06-01"]
        )
        # Mon 06-02 starts the next week
        self.assertEqual(audit_json("--week", "2025-06-02")["start"], "2025-06-02")

    def test_month_boundaries_and_lumps(self):
        may = audit_json("--month", "2025-05")
        self.assertEqual(
            (may["start"], may["end"], may["total_ms"]),
            ("2025-05-01", "2025-05-31", HOUR),
        )
        jan = audit_json("--month", "2025-01")
        # a legacy month lump is all "no app detail"
        self.assertEqual(jan["rows"], [{"name": "(no app detail)", "keys": [], "ms": 7 * HOUR,
                                        "text": "7h", "hidden": False, "detail": False}])  # fmt: skip

    def test_hidden_is_flagged_not_dropped_and_rows_add_up(self):
        d = audit_json("--day", "2025-06-02")
        self.assertEqual(
            [(r["name"], r["ms"], r["hidden"]) for r in d["rows"]],
            [("web", 2 * HOUR, False), ("Terminal", 30 * MIN, True), ("tiny", 20_000, False),
             ("(no app detail)", 30 * MIN - 20_000, False)],
        )  # fmt: skip
        self.assertEqual(sum(r["ms"] for r in d["rows"]), d["total_ms"])

    def test_a_year_takes_archive_lumps_and_days_but_not_other_years(self):
        y = audit_json("--year", "2025")
        self.assertEqual(
            y["total_ms"], 7 * HOUR + 6 * HOUR + HOUR + 2 * HOUR + 3 * HOUR + 5 * HOUR
        )
        self.assertEqual(
            [m["label"] for m in y["months"]][-1], "June"
        )  # up to this month
        self.assertEqual(sum(r["ms"] for r in y["rows"]), y["total_ms"])
        self.assertEqual(audit_json("--year", "2024")["total_ms"], 9 * HOUR)

    def test_md_tables_match_the_json(self):
        rc, md, _ = audit("--week", "2025-06-02", "--md")
        self.assertEqual(rc, 0)
        w = audit_json("--week", "2025-06-02")
        cells = [
            line.split(" | ")
            for line in md.splitlines()
            if line.startswith("| ") and "---" not in line
        ]
        cells = [(a[2:], b[:-2]) for a, b in cells]
        want = [("Day", "Time")] + [(x["label"], x["text"]) for x in w["days"]]
        want += [("App", "Time")] + [
            (r["name"] + (" (hidden)" if r["hidden"] else ""), r["text"])
            for r in w["rows"]
        ]
        want += [("**Total**", f"**{w['total_text']}**")]
        self.assertEqual(cells, want)
        self.assertIn("# Screen time — Jun 2 – 8, 2025", md)
        self.assertIn("Terminal (hidden)", md)
        self.assertIn(
            "\nTimes are rounded to the minute; exact values: `screen-time report --json`.\n",
            md,
        )

    def test_the_whole_picture(self):
        doc = audit_json()
        self.assertEqual(
            sorted(doc), ["all_time", "generated", "today", "week", "year"]
        )
        self.assertEqual(doc["today"]["label"], "Wed Jun 11, 2025")
        self.assertEqual(doc["all_time"]["since_text"], "since Nov 1, 2024")
        rc, md, _ = audit("--md")
        self.assertEqual(rc, 0)
        for head in (
            "## Today — ",
            "## This week — ",
            "## This year — ",
            "## All-time — ",
        ):
            self.assertIn(head, md)

    def test_bad_periods_are_refused_and_old_scopes_still_work(self):
        for bad in (["--month", "2025-13"], ["--day", "2025-02-30"], ["--year", "25"]):
            rc, _, err = audit(*bad)
            self.assertEqual(rc, 2, bad)
            self.assertIn("not a valid period", err)
        rc, out, _ = audit("week")
        self.assertEqual((rc, out.splitlines()[0].split()[:2]), (0, ["Week", "of"]))


class LifetimeAverageReportTests(unittest.TestCase):
    """D38 in the report: the All-time section's average day, week and month, the
    card's baselines with nothing left out. Today is Wed 2025-06-11."""

    def report(self, history, *args):
        fx = Fixture(history)
        try:
            return fx.run("report", *args)
        finally:
            fx.close()

    def test_the_averages_by_hand(self):
        days = {}
        d = datetime.date(2025, 3, 3)
        while d <= datetime.date(2025, 5, 28):
            if d.weekday() < 3:
                days[d.isoformat()] = {"total": 3 * HOUR, "apps": {"editor": 3 * HOUR}}
            d += datetime.timedelta(days=1)
        for k in ("2025-06-09", "2025-06-10", "2025-06-11"):
            days[k] = {"total": HOUR, "apps": {"editor": HOUR}}
        rc, out, _ = self.report({"days": days}, "--json")
        self.assertEqual(rc, 0)
        a = json.loads(out)["all_time"]["averages"]
        # 39 days of 3h + Jun 9, 10 at 1h: 119h / 41; 13 whole weeks of 9h; April 42h
        # and May 36h. Today, this week and June are not over: not in them
        self.assertEqual((a["day"]["text"], a["day"]["periods"]), ("2h 54m", 40))
        self.assertEqual((a["week"]["text"], a["week"]["periods"]), ("9h", 13))
        self.assertEqual((a["month"]["text"], a["month"]["periods"]), ("39h", 2))
        _, md, _ = self.report({"days": days}, "--md")
        self.assertIn("\n## All-time — 120h since Mar 3\n\n- Average day: 2h 54m (40 days)\n"
                      "- Average week: 9h (13 weeks)\n- Average month: 39h (2 months)\n", md)  # fmt: skip

    def test_before_enough_history_the_date_it_starts(self):
        _, md, _ = self.report({"days": {"2025-06-11": day(("e", HOUR))}}, "--md")
        self.assertIn("- Average day: starts Jun 15\n- Average week: starts Jun 30\n"
                      "- Average month: starts Sep 1\n", md)  # fmt: skip
        _, out, _ = self.report({"days": {"2025-06-11": day(("e", HOUR))}}, "--json")
        self.assertEqual(json.loads(out)["all_time"]["averages"]["week"],
                         {"ms": 0, "text": "", "periods": 0, "starts_key": "2025-06-30", "starts_text": "Jun 30"})  # fmt: skip


class ByDayReportTests(unittest.TestCase):
    """V1 Part B: --by-day, worked out by hand. Today is Wed 2025-06-11."""

    def test_every_day_of_the_month_up_to_today_with_its_own_rows(self):
        jun = audit_json("--month", "2025-06", "--by-day")
        self.assertEqual(
            [(d["key"], d["total_ms"]) for d in jun["by_day"]][:3],
            [("2025-06-01", 2 * HOUR), ("2025-06-02", 3 * HOUR), ("2025-06-03", 0)],
        )
        self.assertEqual(jun["by_day"][-1]["key"], "2025-06-11")  # never a future day
        self.assertEqual(len(jun["by_day"]), 11)
        jun11 = jun["by_day"][-1]
        self.assertEqual(
            [(r["name"], r["ms"], r["detail"]) for r in jun11["rows"]],
            [("editor", 4 * HOUR, True), ("(no app detail)", HOUR, False)],
        )
        self.assertEqual(jun["by_day"][2]["rows"], [])  # a day with no time
        for d in jun["by_day"]:
            self.assertEqual(sum(r["ms"] for r in d["rows"]), d["total_ms"], d["key"])
        self.assertEqual((jun["undated_ms"], jun["undated_text"]), (0, ""))

    def test_a_year_adds_archive_days_and_keeps_lumps_undated(self):
        y = audit_json("--year", "2025", "--by-day")
        feb3 = next(d for d in y["by_day"] if d["key"] == "2025-02-03")
        self.assertEqual(feb3["rows"], [{"name": "(no app detail)", "keys": [], "ms": 6 * HOUR,
                                         "text": "6h", "hidden": False, "detail": False}])  # fmt: skip
        self.assertEqual(y["undated_ms"], 7 * HOUR)  # the January lump has no day
        self.assertEqual(
            sum(d["total_ms"] for d in y["by_day"]) + y["undated_ms"], y["total_ms"]
        )
        self.assertEqual(len(y["by_day"]), 162)  # Jan 1 .. Jun 11

    def test_hidden_apps_stay_flagged_inside_a_day(self):
        jun2 = audit_json("--month", "2025-06", "--by-day")["by_day"][1]
        self.assertIn(
            ("Terminal", True), [(r["name"], r["hidden"]) for r in jun2["rows"]]
        )

    def test_by_day_is_refused_without_a_month_or_a_year(self):
        for args in ([], ["--day", "2025-06-11"], ["--week", "2025-06-11"]):
            rc, _, err = audit(*args, "--by-day")
            self.assertEqual(rc, 2, args)
            self.assertIn("--by-day goes with --month or --year", err)

    def test_text_and_md_show_each_day(self):
        rc, text, _ = audit("--month", "2025-06", "--by-day")
        self.assertEqual(rc, 0)
        self.assertIn("By day\n  Sun Jun 1, 2025  2h\n    editor", text)
        rc, md, _ = audit("--month", "2025-06", "--by-day", "--md")
        self.assertEqual(rc, 0)
        self.assertIn("# Screen time — June 2025, by day", md)
        self.assertIn("\n## By day\n", md)
        self.assertIn("\n### Tue Jun 3, 2025 · 0m\n\nNo time tracked.\n", md)
        self.assertIn(
            "### Wed Jun 11, 2025 · 5h\n\n| App | Time |\n|---|---|\n| editor | 4h |\n"
            "| (no app detail) | 1h |\n| **Total** | **5h** |\n",
            md,
        )
        rc, md, _ = audit("--year", "2025", "--by-day", "--md")
        self.assertIn("Without a day (legacy month totals): 7h", md)


THEME_TOKYO = 'accent = "#7aa2f7"\nselection = "#292e42"\nmuted = "#414868"\nbackground = "#1a1b26"\nforeground = "#a9b1d6"\nred = "#f7768e"\nyellow = "#e0af68"\norange = "#eb927b"\ngreen = "#9ece6a"\ncyan = "#449dab"\nblue = "#7aa2f7"\nmagenta = "#ad8ee6"\nbrown = "#75493d"\nbright_red = "#ff7a93"\nbright_yellow = "#ff9e64"\nbright_green = "#b9f27c"\nbright_cyan = "#0db9d7"\nbright_blue = "#7da6ff"\nbright_magenta = "#bb9af7"\n'
THEME_LATTE = 'mode = "light"\naccent = "#1e66f5"\nmuted = "#acb0be"\nbackground = "#eff1f5"\nforeground = "#4c4f69"\nred = "#d20f39"\nyellow = "#df8e1d"\norange = "#d84e2b"\ngreen = "#40a02b"\ncyan = "#179299"\nblue = "#1e66f5"\nmagenta = "#ea76cb"\nbrown = "#6c2715"\nbright_red = "#d20f39"\nbright_yellow = "#df8e1d"\nbright_green = "#40a02b"\nbright_cyan = "#179299"\nbright_blue = "#1e66f5"\nbright_magenta = "#ea76cb"\n'


def palette_card(theme):
    fx = Fixture(
        {"days": {"2025-06-11": day(("a", 5 * MIN), ("b", 4 * MIN))}}, theme=theme
    )
    try:
        return fx.card()
    finally:
        fx.close()


class ThemePaletteTests(unittest.TestCase):
    """D30: the rows take the active theme's own colours."""

    def test_the_theme_is_used_red_never_and_every_swatch_reads_at_3_to_1(self):
        p = palette_card({"colors.toml": THEME_TOKYO})["palette"]
        self.assertEqual(
            (p["source"], p["reason"], p["surface"]), ("theme", "", "#1a1b26")
        )
        self.assertEqual(len(p["swatches"]), 6)
        self.assertFalse(any("red" in k for k in p["keys"]))
        for c in p["swatches"]:
            self.assertGreaterEqual(st.contrast_ratio(c, p["surface"]), 3.0, c)
        self.assertGreaterEqual(st.contrast_ratio(p["other"], p["surface"]), 4.5)

    def test_the_same_theme_always_gives_the_same_colours_in_the_same_order(self):
        a = palette_card({"colors.toml": THEME_LATTE})["palette"]
        b = palette_card({"colors.toml": THEME_LATTE})["palette"]
        self.assertEqual((a["keys"], a["swatches"]), (b["keys"], b["swatches"]))

    def test_a_light_theme_darkens_to_3_to_1(self):
        p = palette_card({"colors.toml": THEME_LATTE})["palette"]
        self.assertEqual(p["surface"], "#eff1f5")
        # Latte's yellow #df8e1d is 2.2:1 on its background; it is darkened to 3:1
        self.assertLess(st.contrast_ratio("#df8e1d", "#eff1f5"), 3)
        for c in p["swatches"]:
            self.assertGreaterEqual(st.contrast_ratio(c, "#eff1f5"), 3.0, c)

    def test_missing_file_falls_back(self):
        p = palette_card(None)["palette"]
        self.assertEqual(
            (p["source"], p["reason"], p["surface"]),
            ("okabe-ito", "missing", "#101315"),
        )

    def test_malformed_file_falls_back(self):
        for bad in ("background = #1a1b26 [oops\n", b"\xff\xfe not toml"):
            p = palette_card({"colors.toml": bad})["palette"]
            self.assertEqual((p["source"], p["reason"]), ("okabe-ito", "invalid"), bad)

    def test_too_few_colours_falls_back(self):
        five = 'background = "#1a1b26"\nforeground = "#a9b1d6"\nred = "#f7768e"\nbright_red = "#ff7a93"\n'
        five += "".join(
            f'{k} = "{v}"\n'
            for k, v in [
                ("yellow", "#e0af68"),
                ("green", "#9ece6a"),
                ("cyan", "#449dab"),
                ("blue", "#7aa2f7"),
                ("magenta", "#ad8ee6"),
            ]
        )
        p = palette_card({"colors.toml": five})["palette"]
        self.assertEqual(
            (p["source"], p["reason"], p["surface"]),
            ("okabe-ito", "too few colours", "#1a1b26"),
        )
        for c in p["swatches"]:
            self.assertGreaterEqual(st.contrast_ratio(c, "#1a1b26"), 3.0, c)

    def test_the_popup_surface_follows_shell_toml_like_the_shell(self):
        p = palette_card(
            {
                "colors.toml": THEME_LATTE,
                "shell.toml": '[popups]\nbackground = "background"\n',
                "user_shell.toml": '[popups]\nbackground = "#e6e9ef"\n',
            }
        )["palette"]
        self.assertEqual(
            p["surface"], "#e6e9ef"
        )  # the user's file wins, as in Color.qml

    def test_rows_and_segments_use_it(self):
        card = palette_card({"colors.toml": THEME_TOKYO})
        self.assertEqual(
            [r["swatch"] for r in card["rows"]], card["palette"]["swatches"][:2]
        )
        self.assertEqual(
            [s["swatch"] for s in card["segments"]], card["palette"]["swatches"][:2]
        )


class TotalsTests(unittest.TestCase):
    def test_week_is_monday_through_today(self):
        days = {
            "2025-06-08": 9 * HOUR,  # Sunday: last week
            "2025-06-09": HOUR,  # Monday
            "2025-06-11": 2 * HOUR,  # Wednesday, today
            "2025-06-12": 9 * HOUR,  # tomorrow
        }
        f = totals_card(days)["footer"]
        self.assertEqual((f["week_ms"], f["week_text"]), (3 * HOUR, "3h"))

    def test_week_on_a_monday_and_a_sunday(self):
        days = {"2025-06-08": HOUR, "2025-06-09": 2 * HOUR}
        self.assertEqual(
            totals_card(days, as_of="2025-06-09T09:00")["footer"]["week_ms"], 2 * HOUR
        )
        self.assertEqual(
            totals_card(days, as_of="2025-06-08T09:00")["footer"]["week_ms"], HOUR
        )

    def test_all_time_sums_days_archive_and_month_lumps(self):
        card = totals_card(
            {"2025-06-11": HOUR, "2025-06-12": 9 * HOUR},
            months={"2024-11": 2 * HOUR, "2025-07": 9 * HOUR},
            years={"2025": {"2025-03-01": 3 * HOUR}, "2024": {"2024-12-01": 4 * HOUR}},
        )
        f = card["footer"]
        self.assertEqual(f["all_time_ms"], 10 * HOUR)
        self.assertEqual(
            (f["since_key"], f["since_text"]), ("2024-11-01", "since Nov 1, 2024")
        )
        self.assertEqual(f["text"], "all-time 10h since Nov 1, 2024")

    def test_since_drops_the_year_within_this_year(self):
        f = totals_card({"2025-06-10": HOUR, "2025-06-11": HOUR})["footer"]
        self.assertEqual(f["since_text"], "since Jun 10")

    def test_years_with_gaps_and_neighbours(self):
        card = totals_card(
            {"2025-06-11": HOUR},
            years={"2022": {"2022-05-01": HOUR}, "2024": {"2024-02-01": 2 * HOUR}},
        )
        # V1: ‹ steps through every year back to the first tracked one, gaps included
        self.assertEqual((card["year"]["key"], card["year"]["prev"]), ("2025", "2024"))
        fx = Fixture({"days": {"2025-06-11": day(("e", HOUR))},
                      "years": {"2022": {"2022-05-01": HOUR}, "2024": {"2024-02-01": 2 * HOUR}}})  # fmt: skip
        try:
            gap, first = fx.page("year:2023")["page"], fx.page("year:2022")["page"]
            before = fx.page("year:2021")
        finally:
            fx.close()
        self.assertEqual(
            (gap["total_ms"], gap["prev"], gap["next"]), (0, "2022", "2024")
        )
        self.assertEqual((first["total_ms"], first["prev"]), (HOUR, None))
        self.assertEqual(
            (before["state"], before["message"]), ("error", "Not a page: year:2021")
        )

    def test_todays_year_is_always_there(self):
        card = totals_card({}, years={"2024": {"2024-02-01": HOUR}})
        self.assertEqual((card["year"]["year"], card["year"]["prev"]), (2025, "2024"))
        self.assertEqual(card["year"]["total_ms"], 0)

    def test_month_bars(self):
        card = totals_card(
            {"2025-06-11": HOUR, "2025-06-01": HOUR},
            months={"2025-01": 4 * HOUR},
            years={"2025": {"2025-03-15": 2 * HOUR}},
        )
        year = card["year"]
        self.assertEqual(year["total_ms"], 8 * HOUR)
        self.assertEqual(
            [(b["label"], b["ms"], b["y"]) for b in year["bars"] if b["ms"]],
            [("Jan", 4 * HOUR, 1.0), ("Mar", 2 * HOUR, 0.5), ("Jun", 2 * HOUR, 0.5)],
        )
        self.assertEqual([b["label"] for b in year["bars"]][:3], ["Jan", "Feb", "Mar"])
        self.assertEqual(len(year["bars"]), 12)


class TextVerbTests(unittest.TestCase):
    def test_text_verbs_need_a_history(self):
        fx = Fixture(None)
        try:
            rc, out, _ = fx.run("report", "today")
            self.assertEqual((rc, out.strip()), (1, "No screen time recorded yet"))
        finally:
            fx.close()

    def test_log_line(self):
        fx = Fixture(BASIC)
        try:
            rc, out, _ = fx.run("log", "--date", "2025-06-10")
            self.assertEqual(rc, 0)
            self.assertEqual(
                out.strip().splitlines(), ["- 2025-06-10 Tue · 1h · editor 1h"]
            )
        finally:
            fx.close()

    def test_log_needs_a_valid_date(self):
        fx = Fixture(BASIC)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                rc, _, _ = fx.run("log", "--date", "2025-02-30")
            self.assertNotEqual(rc, 0)
        finally:
            fx.close()

    def test_report_all_and_week(self):
        fx = Fixture(BASIC)
        try:
            _, out, _ = fx.run("report", "all")
            self.assertEqual(
                out.strip().splitlines(),
                ["All time  3h 31m  since Jun 10", "  2025  3h 31m"],
            )
            _, out, _ = fx.run("report", "week")
            self.assertEqual(out.splitlines()[0], "Week of Mon · Jun 9  3h 31m")
        finally:
            fx.close()


if __name__ == "__main__":
    unittest.main()
