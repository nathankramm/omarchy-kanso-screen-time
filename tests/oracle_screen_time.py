#!/usr/bin/env python3
"""The INDEPENDENT ORACLE for python/screen_time.py (stdlib only, no engine import).

    oracle_screen_time.py <engine.py>     exit 0 when every scenario matches

For each made-up scenario it writes a history.json (and names.json) to a temp
dir, runs the engine's real CLI (`card --as-of ...`, TZ=America/Chicago), and
compares the WHOLE document with a card it recomputes itself from the raw
fixture. It imports nothing from the engine and shares none of its code: days
are walked by ordinal, durations formatted with integer arithmetic, ratios
built as Fractions, names resolved by its own loops, desktop entries read
with configparser from the fixture's own text (the engine has a line parser
and walks the files). Every run gets its own XDG and flatpak dirs, so this
machine's applications never leak in. A hand-pinned scenario
whose strings were worked out by hand guards against a mistake the engine and
the oracle could share.

The last stdout line is a JSON list of failures (tools/mutants.py reads it).
Fixture dates are in 2025 and every app is made up; nothing reads your
history.
"""

import configparser
import copy
import datetime
import itertools
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from fractions import Fraction

MIN = 60_000
HOUR = 3_600_000
D = datetime.date


# ---- the oracle's own primitives ------------------------------------------------


def day_of(key):
    """A date for a real YYYY-MM-DD key, else None."""
    if not isinstance(key, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        return None
    y, m, d = int(key[:4]), int(key[5:7]), int(key[8:])
    if not 1 <= m <= 12:
        return None
    days_in = [31, 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28,
               31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]  # fmt: skip
    return D(y, m, d) if 1 <= d <= days_in else None


def key_of(d):
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def back(d, n):
    """n days before d, by ordinal (never by timestamp)."""
    return D.fromordinal(d.toordinal() - n)


def o_fmt(ms):
    """agx's duration format, by integer arithmetic (ms is a non-negative int)."""
    if ms <= 0:
        return "0m"
    if ms < MIN:
        return f"{max(1, (ms + 500) // 1000)}s"
    mins = (ms + 30_000) // MIN
    if mins < 60:
        return f"{mins}m"
    return f"{mins // 60}h" if mins % 60 == 0 else f"{mins // 60}h {mins % 60}m"


def ms_of(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    if not math.isfinite(v) or v <= 0:
        return 0
    return int(v)


def half_up(fr):
    """Round a Fraction half up, as JavaScript's Math.round does."""
    return int((fr + Fraction(1, 2)) // 1)


WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WD_LONG = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MON = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]
SWATCHES = ["#0072B2", "#56B4E9", "#CC79A7", "#F0E442", "#9085E9", "#D9D9D9", "#7A5FD0"]
OTHER_SWATCH = "#9A9A9A"
# D17 label order: rename, CURATED, a desktop entry, LEGACY, then name_fallback().
CURATED = {"claude": "Claude Code", "bash": "Terminal", "zsh": "Terminal", "fish": "Terminal",
           "sh": "Terminal", "nvim": "Neovim"}  # fmt: skip
LEGACY = {
    "google-chrome": "Chrome", "chromium": "Chromium", "firefox": "Firefox", "zen": "Zen",
    "brave": "Brave", "microsoft-edge": "Edge", "vivaldi": "Vivaldi", "librewolf": "LibreWolf",
    "org.telegram.desktop": "Telegram",
}  # fmt: skip
MESSAGES = {
    "missing": "No screen time recorded yet",
    "corrupt": "Screen time history could not be read",
    "empty": "Nothing tracked yet",
}


def name_fallback(key):
    m = re.match(r"(?i)^(chrome|chromium|brave|msedge|vivaldi)-([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)"
                 r"(__.*-(Default|Profile_[0-9]+))?$", key)  # fmt: skip
    if m:
        host = m.group(2)
        if host[:4].lower() == "www." and host[4:]:
            host = host[4:]
        return host
    for suffix in ["-wayland", "-x11"]:
        if key.endswith(suffix) and key != suffix:
            key = key[: len(key) - len(suffix)]
            break
    parts = key.split(".")
    head_ok = all(re.fullmatch(r"(?i)[a-z][a-z0-9-]*", p) for p in parts[:-1])
    if len(parts) >= 3 and head_ok and re.fullmatch(r"(?i)[a-z0-9_-]+", parts[-1]):
        return parts[-1]
    return key


# ---- desktop entries, the oracle's way (D17) ---------------------------------------
# A scenario's "desktop" is {"home": {...}, "dirs": [{...}, ...], "flatpak_user": {...},
# "flatpak_system": {...}}, each {relative path: text or bytes}; the layers are in
# that order of precedence.


def o_entry(content):
    """(Name, StartupWMClass, Hidden) or None, via configparser."""
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    cp = configparser.RawConfigParser(strict=False, delimiters=("=",), comment_prefixes=("#",),
                                      interpolation=None)  # fmt: skip
    cp.optionxform = str
    try:
        cp.read_string(text)
    except configparser.Error:
        return None
    if "Desktop Entry" not in cp:
        return None
    sec = cp["Desktop Entry"]
    return (
        sec.get("Name"),
        sec.get("StartupWMClass"),
        sec.get("Hidden") == "true",
        sec.get("Exec"),
    )


def o_hosts(exe):
    """URL hosts on an Exec= line, via shlex and urlsplit (the engine uses a regex)."""
    try:
        words = shlex.split(exe or "")
    except ValueError:
        return set()
    out = set()
    for w in words:
        w = w.split("=", 1)[1] if w.startswith("--app=") else w
        if w.lower().startswith(("http://", "https://")):
            h = urllib.parse.urlsplit(w).hostname or ""
            out.add(h[4:] if h.startswith("www.") and len(h) > 4 else h)
    return out


def o_desktop(desk):
    if not desk:
        return []
    layers = [desk.get("home", {})] + list(desk.get("dirs", [])) + [
        desk.get("flatpak_user", {}), desk.get("flatpak_system", {})]  # fmt: skip
    taken = {}
    for layer in layers:
        for rel in sorted(layer):
            if not rel.endswith(".desktop"):
                continue
            fid = rel[: -len(".desktop")].replace("/", "-")
            e = o_entry(layer[rel])
            if fid not in taken and e is not None:
                taken[fid] = e
    return [
        (fid, e[0], e[1], o_hosts(e[3]))
        for fid, e in taken.items()
        if e[0] and not e[2]
    ]


def o_desktop_name(key, entries):
    for by_id in [True, False]:
        hits = set()
        for fid, name, wm, _hosts in entries:
            probe = fid if by_id else (wm or "")
            if probe.lower() == key.lower():
                hits.add(name)
        if len(hits) == 1:
            return next(iter(hits))
    m = re.match(r"(?i)^(chrome|chromium|brave|msedge|vivaldi)-([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)"
                 r"(__.*-(Default|Profile_[0-9]+))?$", key)  # fmt: skip
    if m:
        host = m.group(2).lower()
        host = host[4:] if host.startswith("www.") and len(host) > 4 else host
        named = {name for _f, name, _w, hosts in entries if host in hosts}
        if len(named) == 1:
            return next(iter(named))
    return None


def o_label(key, labels, entries):
    if key in labels:
        return labels[key]
    if key in CURATED:
        return CURATED[key]
    found = o_desktop_name(key, entries)
    if found is not None:
        return found
    if key in LEGACY:
        return LEGACY[key]
    for suffix in ["-wayland", "-x11"]:
        if key.endswith(suffix) and key != suffix:
            again = o_desktop_name(key[: len(key) - len(suffix)], entries)
            if again is not None:
                return again
    return name_fallback(key)


def write_desktop(desk, root):
    """The fixture's files under root; returns the env that points the engine at
    them (always set, so the machine's own dirs never count)."""
    shutil.rmtree(root, ignore_errors=True)
    desk = desk or {}
    dirs = list(desk.get("dirs", [])) or [{}]
    places = [(os.path.join(root, "home", "applications"), desk.get("home", {}))]
    places += [
        (os.path.join(root, f"sys{i}", "applications"), d) for i, d in enumerate(dirs)
    ]
    places += [(os.path.join(root, "fpu", "exports", "share", "applications"), desk.get("flatpak_user", {})),
               (os.path.join(root, "fps", "exports", "share", "applications"), desk.get("flatpak_system", {}))]  # fmt: skip
    for base, files in places:
        os.makedirs(base, exist_ok=True)
        for rel, content in files.items():
            path = os.path.join(base, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(
                    content if isinstance(content, bytes) else content.encode("utf-8")
                )
    return {"XDG_DATA_HOME": os.path.join(root, "home"),
            "XDG_DATA_DIRS": ":".join(os.path.join(root, f"sys{i}") for i in range(len(dirs))),
            "FLATPAK_USER_DIR": os.path.join(root, "fpu"),
            "FLATPAK_SYSTEM_DIR": os.path.join(root, "fps")}  # fmt: skip


def write_theme(theme, home):
    """A HOME of its own holding the scenario's theme files (D30)."""
    state = os.path.join(home, ".local", "state", "omarchy", "current", "theme")
    os.makedirs(state, exist_ok=True)
    os.makedirs(os.path.join(home, ".config", "omarchy"), exist_ok=True)
    for name, content in (theme or {}).items():
        path = (
            os.path.join(home, ".config", "omarchy", "shell.toml")
            if name == "user_shell.toml"
            else os.path.join(state, name)
        )
        with open(path, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    return {"HOME": home}


# ---- reading the fixture the oracle's way ------------------------------------------


def o_with_archive(days, archive):
    """D20: archive/<year>.json days join the history's days, cleaned the same way;
    a day the history holds wins. Names that are not YYYY.json, unparsable text and
    other schemas are skipped."""
    out = dict(days)
    for name in sorted(archive or {}):
        if not re.fullmatch(r"[0-9]{4}\.json", name):
            continue
        content = archive[name]
        try:
            doc = json.loads(content) if isinstance(content, str) else content
        except ValueError:
            continue
        if not isinstance(doc, dict) or doc.get("schema") != 1:
            continue
        got, _, _ = read_history(
            {"days": doc.get("days") if isinstance(doc.get("days"), dict) else {}}
        )
        for k, rec in got.items():
            if k not in out:
                out[k] = rec
    return out


def read_history(doc):
    days, archive, lumps = {}, {}, {}
    for k, rec in (doc.get("days") or {}).items():
        if day_of(k) is None or not isinstance(rec, dict):
            continue
        apps = {}
        for app, v in (
            rec.get("apps") if isinstance(rec.get("apps"), dict) else {}
        ).items():
            if isinstance(app, str) and app and ms_of(v) > 0:
                apps[app] = ms_of(v)
        days[k] = {
            "total": max(ms_of(rec.get("total")), sum(apps.values())),
            "apps": apps,
        }
        h = rec.get("hours")  # D42: 24 numbers >= 0, else the day has none
        if (
            isinstance(h, list)
            and len(h) == 24
            and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in h)
        ):
            days[k]["hours"] = [int(v) for v in h]  # fmt: skip
    for y, entries in (doc.get("years") or {}).items():
        for k, v in (entries if isinstance(entries, dict) else {}).items():
            if day_of(k) is not None and k[:4] == str(y) and ms_of(v) > 0:
                archive[k] = archive.get(k, 0) + ms_of(v)
    for k, v in (doc.get("months") or {}).items():
        if re.fullmatch(r"\d{4}-\d{2}", k) and 1 <= int(k[5:]) <= 12 and ms_of(v) > 0:
            lumps[k] = ms_of(v)
    return days, archive, lumps


def read_names(names):
    """(rename, hide, state) with the contract's rules."""
    if names is None:
        return {}, [], "missing"
    if not isinstance(names, dict):
        return {}, [], "invalid"
    ok, rename, hide = True, {}, []
    rn = names.get("rename", {})
    if not isinstance(rn, dict):
        ok, rn = False, {}
    for k, v in rn.items():
        if isinstance(v, str) and v.strip() and k:
            rename[k] = v.strip()
        else:
            ok = False
    hd = names.get("hide", [])
    if not isinstance(hd, list):
        ok, hd = False, []
    for k in hd:
        if isinstance(k, str) and k:
            hide.append(k)
        else:
            ok = False
    return rename, hide, "ok" if ok else "invalid"


def resolve(entries, stored):
    """stored key -> value: exact entries first, then case-insensitive entries
    that hit exactly one stored key not already taken."""
    out = {k: v for k, v in entries.items() if k in stored}
    for k, v in entries.items():
        if k in stored:
            continue
        hits = sorted(s for s in stored if s.lower() == k.lower())
        if len(hits) == 1 and hits[0] not in out:
            out[hits[0]] = v
    return out


# ---- the expected card ------------------------------------------------------------


def fallback(state, as_of):
    return {
        "schema": 11, "state": state, "message": MESSAGES[state], "as_of": as_of,
        "today": {"key": "", "total_ms": 0, "total_text": "", "label": "today", "pill": ""},
        "rows": [], "segments": [], "names": {"state": ""},
        "footer": {"week_ms": 0, "week_text": "", "all_time_ms": 0, "all_time_text": "",
                   "since_key": "", "since_text": "", "text": ""},
        "day": None, "week": None, "month": None, "year": None,
        "palette": {"source": "", "reason": "", "keys": [], "swatches": [], "other": "", "surface": ""},
    }  # fmt: skip


def near(a, b):
    band = max(Fraction(5 * MIN), Fraction(5, 100) * b)
    if abs(Fraction(a) - b) <= band:
        return "near"
    return "above" if a > b else "below"


def expected_card(sc):
    now = datetime.datetime.fromisoformat(sc["as_of"])
    as_of = now.isoformat(timespec="seconds")
    if sc.get("missing"):
        return fallback("missing", as_of)
    if "raw" in sc:
        return fallback("corrupt", as_of)
    today = now.date()
    days, archive, lumps_all = read_history(sc["history"])
    days = o_with_archive(days, sc.get("archive"))
    # day totals up to today: per-app days plus the archive
    per_day = {}
    for k, rec in days.items():
        if day_of(k) <= today and rec["total"] > 0:
            per_day[k] = per_day.get(k, 0) + rec["total"]
    for k, v in archive.items():
        if day_of(k) <= today:
            per_day[k] = per_day.get(k, 0) + v
    this_month = f"{today.year:04d}-{today.month:02d}"
    lumps = {k: v for k, v in lumps_all.items() if k <= this_month}
    if not per_day and not lumps:
        return fallback("empty", as_of)
    card = fallback("empty", as_of)
    card["state"], card["message"] = "ok", ""
    tk = key_of(today)
    trec = days.get(tk, {"total": 0, "apps": {}})
    total = trec["total"]
    card["today"] = {"key": tk, "total_ms": total, "total_text": o_fmt(total), "label": "today",
                     "pill": f"{WD[today.weekday()]} · {MON[today.month - 1]} {today.day}"}  # fmt: skip

    c = o_card_context(sc, today, days, per_day, lumps)
    dp = o_day(c, today)
    card["rows"], card["segments"] = (
        copy.deepcopy(dp["rows"]),
        copy.deepcopy(dp["segments"]),
    )
    card["names"] = {"state": c["nstate"]}

    # totals
    monday = back(today, today.weekday())
    week = sum(
        per_day.get(key_of(back(today, n)), 0)
        for n in range(today.toordinal() - monday.toordinal() + 1)
    )
    all_time = sum(per_day.values()) + sum(lumps.values())
    first_all = c["first_all"]
    since = ""
    if first_all:
        since = f"since {MON[first_all.month - 1]} {first_all.day}"
        if first_all.year != today.year:
            since += f", {first_all.year}"
    card["footer"] = {
        "week_ms": week, "week_text": o_fmt(week), "all_time_ms": all_time,
        "all_time_text": o_fmt(all_time), "since_key": key_of(first_all) if first_all else "",
        "since_text": since,
        "text": (f"all-time {o_fmt(all_time)} {since}").rstrip(),
    }  # fmt: skip
    # V1: this day, week, month and year; any other is a `--page` run
    t = today.toordinal()
    card["day"] = dp
    card["week"] = o_week_page(c, t - (t - 1) % 7)
    card["month"] = o_month_page(c, today.year, today.month)
    card["year"] = o_year_page(c, today.year)
    # D30: this run's row colours, then every row and segment repainted with them
    pal = o_palette(sc.get("theme"))
    card["palette"] = pal
    repaint = {SWATCHES[i]: pal["swatches"][i] for i in range(6)}
    repaint[OTHER_SWATCH] = pal["other"]
    o_recolour(card, repaint)
    return card


def o_recolour(node, repaint):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "swatch" and v in repaint:
                node[k] = repaint[v]
            else:
                o_recolour(v, repaint)
    elif isinstance(node, list):
        for v in node:
            o_recolour(v, repaint)


# ---- the Year pages (D19b), the oracle's way: months as y * 12 + m indexes,
# covered days by ordinal, Fractions for every ratio.


def o_card_context(sc, today, days, per_day, lumps):
    """What every page needs: the history read the oracle's way, names, firsts."""
    rename, hide, nstate = read_names(sc.get("names"))
    stored = set()
    for rec in days.values():
        stored |= set(rec["apps"])
    labels = resolve(rename, stored)
    hidden = set(resolve({h: 1 for h in hide}, stored))
    entries = o_desktop(sc.get("desktop"))
    starts = [day_of(k) for k in per_day] + [
        D(int(k[:4]), int(k[5:]), 1) for k in lumps
    ]
    ctx = {"today": today, "days": days, "per_day": per_day, "lumps": lumps, "hidden": hidden,
           "label": lambda k: o_label(k, labels, entries), "nstate": nstate,
           "first": min(day_of(k) for k in per_day) if per_day else None,
           "first_all": min(starts) if starts else None}  # fmt: skip
    ctx["periods"] = o_periods(ctx)
    ctx["now_hour"] = datetime.datetime.fromisoformat(sc["as_of"]).hour
    return ctx


def o_short(d, today):
    return f"{MON[d.month - 1]} {d.day}" + (
        "" if d.year == today.year else f", {d.year}"
    )


def o_segments(rows, total):
    return [{"name": r["name"], "swatch": r["swatch"], "weight": float(Fraction(r["value_ms"], total))}
            for r in rows] if total > 0 else []  # fmt: skip


O_EMPTY_CHART = {"points": [], "today": None, "mean_ms": 0, "mean_text": "", "mean_y": None,
                 "y_max_ms": 0, "y_max_text": "", "start_label": "", "end_label": "", "first_key": ""}  # fmt: skip

# ---- D38: the all-time averages, the oracle's way. Periods as (key, day values,
# lump); weeks by Monday ordinal, months by y * 12 + (m - 1); Fractions for means.
O_NEEDS = {"day": 3, "week": 2, "month": 2}
O_UNITS = {"day": "days", "week": "weeks", "month": "months"}


def o_periods(c):
    t, pd = c["today"].toordinal(), c["per_day"]

    def v(o):
        return pd.get(key_of(D.fromordinal(o)), 0)

    install = min(pd) if pd else None  # D45: the install day is only partly tracked
    out = {"day": [(k, [x], 0) for k, x in sorted(pd.items())
                   if day_of(k).toordinal() < t and x >= MIN and k != install],
           "week": [], "month": []}  # fmt: skip
    if c["first"]:
        f = c["first"].toordinal()
        m = f - (f - 1) % 7
        m += 7 if m < f else 0  # a week begun before tracking is not a whole one
        while m + 6 < t:
            vals = [v(m + i) for i in range(7)]
            if sum(vals) >= MIN:
                out["week"].append((key_of(D.fromordinal(m)), vals, 0))
            m += 7
    if c["first_all"]:
        fa = c["first_all"]
        idx = fa.year * 12 + fa.month - 1 + (0 if fa.day == 1 else 1)
        while o_month_first(idx + 1).toordinal() <= t:
            s0, n = o_month_first(idx).toordinal(), o_month_len(idx)
            vals = [v(s0 + i) for i in range(n)]
            mk = f"{idx // 12:04d}-{idx % 12 + 1:02d}"
            lump = c["lumps"].get(mk, 0)
            if sum(vals) + lump >= MIN:
                out["month"].append((mk, vals, lump))
            idx += 1
    return out


def o_mean(periods, shown, span):
    """(mean, how many) over the periods but the one shown, the first `span` days of
    each; a month holding a lump only whole."""
    vals = []
    for k, days, lump in periods:
        if k == shown:
            continue
        if span is None or span >= len(days):
            vals.append(sum(days) + lump)
        elif not lump:
            vals.append(sum(days[:span]))
    return (half_up(Fraction(sum(vals), len(vals))) if vals else 0), len(vals)


def o_starts(kind, have, c, first):
    today = c["today"]
    t, need = today.toordinal(), O_NEEDS[kind] - have
    if kind == "day":  # today counts once over, unless it is the install day (D45)
        return D.fromordinal(
            t + need + (1 if first is not None and first.toordinal() == t else 0)
        )
    if kind == "week":
        m = t - (t - 1) % 7
        if first is not None and m < first.toordinal():
            m += 7
        return D.fromordinal(m + 7 * need)
    idx = today.year * 12 + today.month - 1
    if first is not None and o_month_first(idx) < first:
        idx += 1
    return o_month_first(idx + need)


def o_line(kind, total, mean, n, current, by, c, first):
    line = {
        "state": "baseline",
        "text": "",
        "average_ms": 0,
        "average_text": "",
        "periods": n,
    }
    if n < O_NEEDS[kind]:
        line["text"] = "average starts " + o_short(
            o_starts(kind, n, c, first), c["today"]
        )
        return line
    st = near(total, mean)
    line.update(state=st, average_ms=mean, average_text=o_fmt(mean))
    if st == "near":
        line["text"] = "≈ average" + by
    elif st == "above":
        line["text"] = f"▲ {o_fmt(total - mean)} above average{by}"
    elif current:
        line["text"] = (
            f"average {o_fmt(mean)} a day"
            if kind == "day"
            else f"average {o_fmt(mean)}{by}"
        )
    else:
        line["text"] = f"▼ {o_fmt(mean - total)} below average"
    return line  # fmt: skip


def o_dashed(mean, n, kind, top, unit=""):
    """D41: the label says what its bars measure, never how many periods."""
    if n < O_NEEDS[kind]:
        return {"mean_ms": 0, "mean_text": "", "mean_y": None}
    return {"mean_ms": mean, "mean_text": f"avg {o_fmt(mean)}{unit}",
            "mean_y": float(Fraction(mean, max(1, top)))}  # fmt: skip


def o_one_day(c):
    """D41: the one average day every Week and Month line draws: every completed
    day with a minute, by ordinal straight from the day totals."""
    t = c["today"].toordinal()
    first = min(c["per_day"]) if c["per_day"] else None  # D45: not the install day
    vals = [
        v
        for k, v in c["per_day"].items()
        if day_of(k).toordinal() < t and v >= MIN and k != first
    ]
    return (half_up(Fraction(sum(vals), len(vals))) if vals else 0), len(vals)


def o_ticks(top, mean=None):
    """D44, the oracle's way: steps 15m, 30m, 1-5h, then 1, 1.5, 2, 2.5, 3 and 5 of
    each decade of hours; the largest step whose double is at most 9/10 of the
    top and neither line within 8/100 of the top from the dashed mean (exact)."""
    hours = [1, 2, 3, 4, 5] + [
        m * 10**k for k in range(1, 4) for m in (1, 1.5, 2, 2.5, 3, 5)
    ]
    for step in sorted(
        [15 * MIN, 30 * MIN] + [int(h * HOUR) for h in hours], reverse=True
    ):
        if top <= 0 or 10 * 2 * step > 9 * top:
            continue
        if mean is not None and any(
            100 * abs(v - mean) <= 8 * top for v in (step, 2 * step)
        ):
            continue
        return [
            {"ms": v, "text": o_fmt(v), "y": float(Fraction(v, top))}
            for v in (step, 2 * step)
        ]
    return []


def o_hours(c, d):
    """D42, the oracle's way: a day is whole when its hours add up to its day
    total (no record and no time: 24 zeros); bars by the scenario's clock hour."""
    k = key_of(d)
    rec = c["days"].get(k)
    total = c["per_day"].get(k, 0)
    h = rec.get("hours") if rec else None
    whole = (
        ([0] * 24 if total == 0 else None)
        if h is None
        else (h if sum(h) == total else None)
    )
    state, text = "ok", ""
    if whole is None and h is not None and 0 < sum(h) < total:  # D48: in part
        whole = h
        i0 = min(i for i in range(24) if h[i] > 0)
        state = "partial"
        clock = 12 if i0 % 12 == 0 else i0 % 12
        text = f"hours from {clock} {'AM' if i0 < 12 else 'PM'}"
    if whole is None:
        firsts = sorted(x for x, r in c["days"].items() if "hours" in r and day_of(x) <= c["today"]
                        and sum(r["hours"]) == c["per_day"].get(x, 0))  # fmt: skip
        when = (
            day_of(firsts[0]) if firsts else D.fromordinal(c["today"].toordinal() + 1)
        )
        return {"state": "pending", "text": "hourly from " + o_short(when, c["today"]), "bars": [],
                "y_max_ms": HOUR, "ticks": [], "mean_ms": 0, "mean_text": "", "mean_y": None}  # fmt: skip
    past_day = d.toordinal() < c["today"].toordinal()
    bars = []
    for i in range(24):
        st = (
            "past"
            if past_day or i < c["now_hour"]
            else ("current" if i == c["now_hour"] else "future")
        )
        ms = 0 if st == "future" else whole[i]
        bars.append({"hour": i, "label": {0: "12a", 6: "6a", 12: "12p", 18: "6p"}.get(i, ""), "ms": ms,
                     "text": "" if st == "future" else o_fmt(ms), "x": float(Fraction(i, 23)),
                     "y": float(min(Fraction(1), Fraction(ms, HOUR))), "state": st})  # fmt: skip
    return {
        "state": state,
        "text": text,
        "bars": bars,
        "y_max_ms": HOUR,
        "ticks": [
            {"ms": HOUR // 2, "text": "30m", "y": 0.5},
            {"ms": HOUR, "text": "1h", "y": 1.0},
        ],
        "mean_ms": 0,
        "mean_text": "",
        "mean_y": None,
    }


def o_day(c, d):
    """The Day page for any day up to today (V1): today's hero, usual, chart, rows."""
    today, per_day, days, hidden, label = (
        c["today"],
        c["per_day"],
        c["days"],
        c["hidden"],
        c["label"],
    )
    t, o = today.toordinal(), d.toordinal()
    total = per_day.get(key_of(d), 0)
    rows = o_week_rows(days.get(key_of(d), {"apps": {}})["apps"], total, hidden, label)
    # D38: against every other completed day with a minute, the oracle's way
    shown = key_of(d)
    mean, n = o_mean(c["periods"]["day"], shown, None)
    line = o_line("day", total, mean, n, o == t, "", c, c["first"])
    used = [k for k, _, _ in c["periods"]["day"] if k != shown]
    if n >= O_NEEDS["day"]:
        for r in rows:
            if r["other"]:
                continue
            got = sum(v for k in used for a, v in days.get(k, {"apps": {}})["apps"].items()
                      if a not in hidden and label(a) == r["name"])  # fmt: skip
            ru = half_up(Fraction(got, len(used)))
            rs = near(r["value_ms"], ru)
            if rs == "near":
                r["vs_text"] = "≈"
            elif rs == "above":
                r["vs_text"] = "▲ " + o_fmt(r["value_ms"] - ru)
            else:
                r["vs_text"] = (
                    ("avg " + o_fmt(ru))
                    if o == t
                    else ("▼ " + o_fmt(ru - r["value_ms"]))
                )
    first = c["first"]
    ch = o_hours(c, d)
    pill = f"{WD[d.weekday()]} · {MON[d.month - 1]} {d.day}" + (
        "" if d.year == today.year else f", {d.year}"
    )
    return {"key": key_of(d), "label": "today" if o == t else "yesterday" if o == t - 1 else "day",
            "pill": pill, "total_ms": total, "total_text": o_fmt(total), "line": line, "hours": ch,
            "rows": rows, "segments": o_segments(rows, total),
            "prev": key_of(D.fromordinal(o - 1)) if first is not None and o - 1 >= first.toordinal() else None,
            "next": key_of(D.fromordinal(o + 1)) if o < t else None}  # fmt: skip


# ---- the Year page (D19b), the oracle's way: months as y * 12 + m indexes,
# covered days by ordinal, Fractions for every ratio.


def o_year_page(c, y):
    today, per_day, lumps, days = c["today"], c["per_day"], c["lumps"], c["days"]
    first_all = c["first_all"]
    t = today.toordinal()
    now_m = today.year * 12 + today.month - 1
    first_m = first_all.year * 12 + first_all.month - 1 if first_all else None
    months = [0] * 12
    for k, v in list(per_day.items()) + list(lumps.items()):
        if int(k[:4]) == y:
            months[int(k[5:7]) - 1] += v
    total = sum(months)
    slots = []
    for m in range(12):
        idx = y * 12 + m
        if idx > now_m:
            st = "future"
        elif first_m is None or idx < first_m:
            st = "before"
        else:
            st = "current" if idx == now_m else "past"
        slots.append((m, st, months[m] if st in ("past", "current") else 0))
    month_mean, month_n = o_mean(c["periods"]["month"], None, None)
    ymax = max(
        [ms for _, _, ms in slots] + [month_mean if month_n >= O_NEEDS["month"] else 0]
    )
    bars = [{"label": MON[m], "ms": ms, "text": o_fmt(ms) if st in ("past", "current") else "",
             "x": float(Fraction(m, 11)), "y": float(Fraction(ms, max(1, ymax))), "state": st}
            for m, st, ms in slots]  # fmt: skip
    s_ord = D(y, 1, 1).toordinal()
    e_ord = min(D(y, 12, 31).toordinal(), t)
    if first_all is not None and first_all.toordinal() > s_ord:
        s_ord = first_all.toordinal()
    n = e_ord - s_ord + 1 if first_all is not None and s_ord <= e_ord else 0
    covered = n
    avg_total = total
    if e_ord == t and n > 1:  # completed days only (C5)
        avg_total, n = total - per_day.get(key_of(today), 0), n - 1
    inst = c["first"].toordinal() if c["first"] else None
    if inst is not None and s_ord <= inst <= e_ord and inst != t and n > 1:  # D47
        avg_total, n = avg_total - per_day.get(key_of(c["first"]), 0), n - 1
        if inst == s_ord:
            s_ord += 1
    avg = half_up(Fraction(avg_total, n)) if n else 0
    since = ""
    if covered and s_ord != D(y, 1, 1).toordinal():
        since = " since " + o_short(D.fromordinal(s_ord), today)
    app_ms = {}
    for k, rec in days.items():
        if int(k[:4]) == y and day_of(k).toordinal() <= t:
            for a, v in rec["apps"].items():
                app_ms[a] = app_ms.get(a, 0) + v
    rows = o_week_rows(app_ms, total, c["hidden"], c["label"])
    return {
        "key": str(y), "year": y,
        "label": "this year" if y == today.year else ("last year" if y == today.year - 1 else "year"),
        "total_ms": total, "total_text": o_fmt(total), "avg_ms": avg,
        "line": {"state": "", "text": f"{o_fmt(avg)} a day{since}", "days": n},
        "bars": bars, "y_max_ms": ymax, "y_max_text": o_fmt(ymax) if ymax > 0 else "",
        "ticks": o_ticks(ymax, month_mean if month_n >= O_NEEDS["month"] else None),
        **o_dashed(month_mean, month_n, "month", ymax, " a month"),
        "rows": rows, "segments": o_segments(rows, total),
        "prev": str(y - 1) if first_all is not None and first_all.year < y else None,
        "next": str(y + 1) if y < today.year else None,
    }  # fmt: skip


# ---- row colours (D30), the oracle's way --------------------------------------------
# The theme's hue colours, red never, each taken to 3:1 on the popup surface; the 6
# with the largest colour-blind distance, ordered for the largest distance between
# neighbours; Other the readable muted. Values read with a line regex (the engine
# uses tomllib), maths written out again; the same written selection rule.

O_HUES = {"yellow", "orange", "green", "cyan", "blue", "magenta", "brown", "purple"}
O_OKABE = ["#0072b2", "#56b4e9", "#cc79a7", "#f0e442", "#9085e9", "#d9d9d9"]
O_MACHADO = [
    [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
    [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]],
    [[1.255528, -0.076749, -0.178779], [-0.078411, 0.930809, 0.147602], [0.004733, 0.691367, 0.303900]],
]  # fmt: skip


def o_ch(h):
    return [int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)]


def o_lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def o_lum(h):
    r, g, b = [o_lin(c / 255) for c in o_ch(h)]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def o_ratio(a, b):
    hi, lo = sorted([o_lum(a), o_lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def o_step(toward, base, step):
    return "#" + "".join(
        f"{(t * step + c * (100 - step) + 50) // 100:02x}"
        for t, c in zip(o_ch(toward), o_ch(base))
    )


def o_three(h, bg):
    toward = (
        "#000000" if o_ratio("#000000", bg) >= o_ratio("#ffffff", bg) else "#ffffff"
    )
    return next(
        (
            o_step(toward, h, s)
            for s in range(101)
            if o_ratio(o_step(toward, h, s), bg) >= 3
        ),
        toward,
    )


def o_muted(text, bg):
    t, b = [c / 255 for c in o_ch(text)], [c / 255 for c in o_ch(bg)]
    lb = o_lum(bg)
    for s in range(40, 100):
        c = [x * s / 100 + y * (1 - s / 100) for x, y in zip(t, b)]
        lum = 0.2126 * o_lin(c[0]) + 0.7152 * o_lin(c[1]) + 0.0722 * o_lin(c[2])
        if (max(lum, lb) + 0.05) / (min(lum, lb) + 0.05) >= 4.5:
            return "#" + "".join(f"{int(v * 255 + 0.5):02x}" for v in c)
    return text


def o_lab(h):
    r, g, b = [o_lin(c / 255) for c in o_ch(h)]
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    f = [
        v ** (1 / 3) if v > 216 / 24389 else (24389 / 27 * v + 16) / 116
        for v in (x, y, z)
    ]
    return 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])


def o_de(p, q):
    (L1, A1, B1), (L2, A2, B2) = o_lab(p), o_lab(q)
    cm = (math.hypot(A1, B1) + math.hypot(A2, B2)) / 2
    g = 0.5 * (1 - math.sqrt(cm**7 / (cm**7 + 25**7)))
    a1, a2 = A1 * (1 + g), A2 * (1 + g)
    c1, c2 = math.hypot(a1, B1), math.hypot(a2, B2)
    h1, h2 = (
        math.degrees(math.atan2(B1, a1)) % 360,
        math.degrees(math.atan2(B2, a2)) % 360,
    )
    if c1 * c2 == 0:
        dh, hm = 0.0, h1 + h2
    else:
        dh = (
            h2 - h1
            if abs(h2 - h1) <= 180
            else (h2 - h1 - 360 if h2 > h1 else h2 - h1 + 360)
        )
        hm = (
            (h1 + h2) / 2
            if abs(h1 - h2) <= 180
            else ((h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2)
        )
    dH = 2 * math.sqrt(c1 * c2) * math.sin(math.radians(dh) / 2)
    lm, cmp_ = (L1 + L2) / 2, (c1 + c2) / 2
    T = (
        1
        - 0.17 * math.cos(math.radians(hm - 30))
        + 0.24 * math.cos(math.radians(2 * hm))
        + 0.32 * math.cos(math.radians(3 * hm + 6))
        - 0.20 * math.cos(math.radians(4 * hm - 63))
    )
    rt = (
        -2
        * math.sqrt(cmp_**7 / (cmp_**7 + 25**7))
        * math.sin(math.radians(60 * math.exp(-(((hm - 275) / 25) ** 2))))
    )
    sl = 1 + 0.015 * (lm - 50) ** 2 / math.sqrt(20 + (lm - 50) ** 2)
    sc, sh = 1 + 0.045 * cmp_, 1 + 0.015 * cmp_ * T
    dl, dc = (L2 - L1) / sl, (c2 - c1) / sc
    return math.sqrt(dl**2 + dc**2 + (dH / sh) ** 2 + rt * dc * (dH / sh))  # fmt: skip


def o_sim(h, m):
    v = [o_lin(c / 255) for c in o_ch(h)]
    out = ""
    for row in m:
        s = min(1.0, max(0.0, row[0] * v[0] + row[1] * v[1] + row[2] * v[2]))
        s = 12.92 * s if s <= 0.0031308 else 1.055 * s ** (1 / 2.4) - 0.055
        out += f"{int(s * 255 + 0.5):02x}"
    return "#" + out


def o_toml_values(text):
    """Top-level key = "#rrggbb" lines, lower-cased."""
    out = {}
    for line in text.splitlines():
        if line.strip().startswith("["):
            break
        m = re.match(r'\s*([A-Za-z0-9_-]+)\s*=\s*"(#[0-9a-fA-F]{6})"\s*(#.*)?$', line)
        if m:
            out[m.group(1)] = m.group(2).lower()
    return out


def o_palette(theme):
    import tomllib  # validity only: is it TOML at all

    theme = theme or {}
    raw = theme.get("colors.toml")
    reason, colors = "", {}
    if raw is None:
        reason = "missing"
    else:
        text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
        try:
            tomllib.loads(text) if isinstance(raw, str) else tomllib.loads(
                raw.decode("utf-8")
            )
            colors = o_toml_values(text)
        except (ValueError, UnicodeDecodeError):
            reason = "invalid"
    bg = colors.get("background", "#101315")
    fg = colors.get("foreground", "#cacccc")
    roles = {"background": bg, "foreground": fg, "text": fg, "accent": colors.get("accent", fg),
             "muted": colors.get("muted", fg), "urgent": colors.get("red", fg)}  # fmt: skip
    pick = {}
    for name in ("shell.toml", "user_shell.toml"):
        if name in theme:
            try:
                popups = tomllib.loads(theme[name]).get("popups", {})
            except ValueError:
                popups = {}
            pick.update(
                {
                    k: v
                    for k, v in popups.items()
                    if k in ("background", "text") and isinstance(v, str)
                }
            )

    def res(v, fb):
        v = v.strip().lower()
        if v in roles:
            return roles[v]
        return v[:7] if re.match(r"#[0-9a-f]{6}", v) else fb

    surface, text_c = res(pick.get("background", bg), bg), res(pick.get("text", fg), fg)
    other = o_muted(text_c, surface)
    if not reason:
        cands = {}
        for k in sorted(colors):
            if k.removeprefix("bright_") in O_HUES:
                c = o_three(colors[k], surface)
                if c not in cands.values():
                    cands[k] = c
        if len(cands) >= 6:

            def cvd(a, b):
                return min(
                    o_de(o_sim(cands[a], m), o_sim(cands[b], m)) for m in O_MACHADO
                )

            def score(pairs):
                return (
                    min(cvd(a, b) for a, b in pairs),
                    min(o_de(cands[a], cands[b]) for a, b in pairs),
                )

            best = None
            for sub in itertools.combinations(list(cands), 6):
                sc = score(list(itertools.combinations(sub, 2)))
                if best is None or sc > best[0]:
                    best = (sc, sub)
            order = None
            for perm in itertools.permutations(best[1]):
                sc = score(list(itertools.pairwise(perm)))
                if order is None or sc > order[0]:
                    order = (sc, perm)
            keys = list(order[1])
            return {"source": "theme", "reason": "", "keys": keys, "swatches": [cands[k] for k in keys],
                    "other": other, "surface": surface}  # fmt: skip
        reason = "too few colours"
    return {"source": "okabe-ito", "reason": reason, "keys": [],
            "swatches": [o_three(c, surface) for c in O_OKABE], "other": other, "surface": surface}  # fmt: skip


# ---- the Week page (D19), the oracle's way ---------------------------------------
# Weeks by ordinal: day 1 of the proleptic calendar (0001-01-01) is a Monday, so a
# day's Monday is o - (o - 1) % 7, never weekday(). Fractions for every ratio.


def o_week_rows(app_ms, total, hidden, label):
    sums, members, other_keys = {}, {}, []
    for k, v in app_ms.items():
        if k in hidden:
            other_keys.append(k)
        else:
            sums[label(k)] = sums.get(label(k), 0) + v
            members.setdefault(label(k), []).append(k)
    order = sorted(sums, key=lambda n: (-sums[n], n))
    named = [n for n in order if sums[n] >= MIN][:6]
    rows = [{"name": n, "keys": sorted(members[n]), "value_ms": sums[n],
             "value_text": o_fmt(sums[n]), "swatch": SWATCHES[i], "other": False, "vs_text": ""}
            for i, n in enumerate(named)]  # fmt: skip
    for n in order:
        if n not in named:
            other_keys += members[n]
    rest = total - sum(sums[n] for n in named)
    if rest > 0:
        rows.append({"name": "Other", "keys": sorted(other_keys), "value_ms": rest,
                     "value_text": o_fmt(rest), "swatch": OTHER_SWATCH, "other": True,
                     "vs_text": ""})  # fmt: skip
    return rows


def o_range(mon, today):
    sun = D.fromordinal(mon.toordinal() + 6)
    end = f"{sun.day}" if sun.month == mon.month else f"{MON[sun.month - 1]} {sun.day}"
    text = f"{MON[mon.month - 1]} {mon.day} – {end}"
    return text if sun.year == today.year else f"{text}, {sun.year}"


def o_week_page(c, mo):
    today, per_day, days = c["today"], c["per_day"], c["days"]
    t = today.toordinal()
    cur = t - (t - 1) % 7
    first = c["first"].toordinal() if c["first"] else None
    counted = [
        o for o in range(mo, mo + 7) if o <= t and first is not None and o >= first
    ]
    total = sum(per_day.get(key_of(D.fromordinal(o)), 0) for o in counted)
    app_ms = {}
    for o in counted:
        rec = days.get(key_of(D.fromordinal(o)))
        for k, v in (rec["apps"] if rec else {}).items():
            app_ms[k] = app_ms.get(k, 0) + v
    rows = o_week_rows(app_ms, total, c["hidden"], c["label"])
    slots = []
    for i in range(7):
        o = mo + i
        if o > t:
            st = "future"
        elif first is None or o < first:
            st = "before"
        else:
            st = "today" if o == t else "past"
        slots.append(
            (
                i,
                o,
                st,
                per_day.get(key_of(D.fromordinal(o)), 0)
                if st in ("past", "today")
                else 0,
            )
        )
    wkey = key_of(D.fromordinal(mo))
    day_mean, day_n = o_one_day(c)
    top = max(
        [ms for _, _, _, ms in slots] + [day_mean if day_n >= O_NEEDS["day"] else 0]
    )
    bars = [{"key": key_of(D.fromordinal(o)), "label": "MTWTFSS"[i], "ms": ms,
             "text": o_fmt(ms) if st in ("past", "today") else "",
             "x": float(Fraction(i, 6)), "y": float(Fraction(ms, max(1, top))), "state": st}
            for i, o, st, ms in slots]  # fmt: skip
    avg = o_average(per_day, counted, t, total)
    this = mo == cur
    mean, n = o_mean(c["periods"]["week"], wkey, t - cur + 1 if this else None)
    by = f" by {WD[today.weekday()]}" if this else ""
    line = o_line("week", total, mean, n, this, by, c, c["first"])
    return {
        "key": key_of(D.fromordinal(mo)), "range_text": o_range(D.fromordinal(mo), today),
        "label": "this week" if mo == cur else ("last week" if mo == cur - 7 else "week"),
        "total_ms": total, "total_text": o_fmt(total), "avg_ms": avg,
        "line": line, "bars": bars,
        "y_max_ms": top, "y_max_text": o_fmt(top) if top > 0 else "", "rows": rows,
        "ticks": o_ticks(top, day_mean if day_n >= O_NEEDS["day"] else None),
        **o_dashed(day_mean, day_n, "day", top, " a day"),
        "segments": o_segments(rows, total),
        "prev": key_of(D.fromordinal(mo - 7)) if first is not None and first < mo else None,
        "next": key_of(D.fromordinal(mo + 7)) if mo < cur else None,
    }  # fmt: skip


def o_average(per_day, counted, t, total):
    """Completed days only; today only when it is the only day (C5)."""
    done = [o for o in counted if o < t]
    today_ms = per_day.get(key_of(D.fromordinal(t)), 0) if t in counted else 0
    if done:
        return half_up(Fraction(total - today_ms, len(done)))
    return half_up(Fraction(total, len(counted))) if counted else 0


# ---- the Month page (V1), the oracle's way: months as y * 12 + (m - 1) indexes,
# days by ordinal, month lengths from the next month's first day.


def o_month_first(idx):
    return D(idx // 12, idx % 12 + 1, 1)


def o_month_len(idx):
    return o_month_first(idx + 1).toordinal() - o_month_first(idx).toordinal()


def o_month_page(c, y, m):
    today, per_day, lumps, days = c["today"], c["per_day"], c["lumps"], c["days"]
    first = c["first_all"].toordinal() if c["first_all"] else None
    t = today.toordinal()
    idx, now_idx = y * 12 + m - 1, today.year * 12 + today.month - 1
    s_ord, n = o_month_first(idx).toordinal(), o_month_len(idx)
    mkey = f"{y:04d}-{m:02d}"
    counted = [
        o
        for o in range(s_ord, s_ord + n)
        if o <= t and first is not None and o >= first
    ]
    total = sum(per_day.get(key_of(D.fromordinal(o)), 0) for o in counted) + lumps.get(
        mkey, 0
    )
    app_ms = {}
    for o in counted:
        rec = days.get(key_of(D.fromordinal(o)))
        for k, v in (rec["apps"] if rec else {}).items():
            app_ms[k] = app_ms.get(k, 0) + v
    rows = o_week_rows(app_ms, total, c["hidden"], c["label"])
    slots = []
    for i in range(n):
        o = s_ord + i
        if o > t:
            st = "future"
        elif first is None or o < first:
            st = "before"
        else:
            st = "today" if o == t else "past"
        slots.append(
            (
                i,
                o,
                st,
                per_day.get(key_of(D.fromordinal(o)), 0)
                if st in ("past", "today")
                else 0,
            )
        )
    day_mean, day_n = o_one_day(c)
    top = max(
        [ms for _, _, _, ms in slots] + [day_mean if day_n >= O_NEEDS["day"] else 0]
    )
    bars = [{"key": key_of(D.fromordinal(o)), "label": str(i + 1) if i % 7 == 0 else "", "ms": ms,
             "text": o_fmt(ms) if st in ("past", "today") else "",
             "x": float(Fraction(i, n - 1)), "y": float(Fraction(ms, max(1, top))), "state": st}
            for i, o, st, ms in slots]  # fmt: skip
    avg = o_average(per_day, counted, t, total)
    this = idx == now_idx
    pkey = f"{(idx - 1) // 12:04d}-{(idx - 1) % 12 + 1:02d}"
    mean, n_used = o_mean(c["periods"]["month"], mkey, today.day if this else None)
    by = f" by {MON[today.month - 1]} {today.day}" if this else ""
    line = o_line(
        "month",
        total,
        mean,
        n_used,
        this,
        by,
        c,
        c["first_all"],
    )
    return {
        "key": mkey, "range_text": FULL_MON[m - 1] + ("" if y == today.year else f" {y}"),
        "label": "this month" if idx == now_idx else ("last month" if idx == now_idx - 1 else "month"),
        "total_ms": total, "total_text": o_fmt(total), "avg_ms": avg, "line": line, "bars": bars,
        "y_max_ms": top, "y_max_text": o_fmt(top) if top > 0 else "", "rows": rows,
        "ticks": o_ticks(top, day_mean if day_n >= O_NEEDS["day"] else None),
        **o_dashed(day_mean, day_n, "day", top, " a day"),
        "segments": o_segments(rows, total),
        "prev": pkey if first is not None and first < s_ord else None,
        "next": f"{(idx + 1) // 12:04d}-{(idx + 1) % 12 + 1:02d}" if idx < now_idx else None,
    }  # fmt: skip


# ---- scenarios (all made up) -----------------------------------------------------


def day_rec(apps, extra=0):
    return {"total": sum(apps.values()) + extra, "apps": dict(apps)}


def busy_history():
    """Ten weeks before Wednesday 2025-06-11 plus edges: decoy weekdays, a missing
    and a sub-minute Wednesday, a ninth-week Wednesday, a future day, a phantom
    key, archive years with a gap, a legacy lump and a future lump."""
    days = {}
    first = D(2025, 3, 28)
    for n in range((D(2025, 6, 10) - first).days + 1):
        d = D.fromordinal(first.toordinal() + n)
        seed = (d.toordinal() * 7919) % 97
        apps = {"editor": (40 + seed) * MIN, "web-a": (5 + seed % 13) * MIN,
                "browser": (3 + seed % 7) * MIN, "Claude": (seed % 5) * MIN + 20_000}  # fmt: skip
        if d.weekday() == 1:  # Tuesdays: a decoy for a weekday off by one
            apps["editor"] += 3 * HOUR
        if d.weekday() == 3:  # Thursdays too
            apps["editor"] += 2 * HOUR
        days[key_of(d)] = day_rec(apps)
    days.pop("2025-05-28")  # a missing Wednesday
    days["2025-05-21"] = day_rec({"editor": 30_000})  # a sub-minute Wednesday
    days["2025-04-09"] = day_rec({"editor": 9 * HOUR})  # ninth week back
    days["2025-04-16"] = day_rec(
        {"editor": 70 * MIN, "claude": 5 * MIN, "bash": 3 * MIN}
    )
    days["2025-06-11"] = day_rec(
        {"editor": 2 * HOUR, "web-a": 20 * MIN, "web-b": 25 * MIN, "browser": 30 * MIN,
         "Claude": 15 * MIN, "claude": 12 * MIN, "Exact": 11 * MIN, "term": 9 * MIN,
         "chat": 8 * MIN, "notes": 7 * MIN, "bash": 50 * MIN, "ssh": 40_000, "mise": 20_000},
        extra=3 * MIN,
    )  # fmt: skip
    days["2025-06-12"] = day_rec({"editor": 9 * HOUR})  # the future
    days["2025-02-29"] = day_rec({"editor": 9 * HOUR})  # a phantom date
    return {
        "days": days,
        "months": {"2024-11": 6 * HOUR, "2025-07": 9 * HOUR},
        "years": {"2022": {"2022-05-01": 2 * HOUR, "2022-12-31": HOUR},
                  "2024": {"2024-02-29": 3 * HOUR, "2024-12-30": HOUR},
                  "2025": {"2025-03-01": 4 * HOUR}},
    }  # fmt: skip


BUSY_NAMES = {
    "rename": {"editor": "Editor", "web-a": "Web", "web-b": "Web", "BROWSER": "Browser",
               "CLAUDE": "X", "Exact": "A", "EXACT": "B"},
    "hide": ["bash"],
}  # fmt: skip

WEDS = {"2025-06-04", "2025-05-28"}


def two_weds(ms_a, ms_b, today_ms):
    return {"days": {"2025-06-04": day_rec({"editor": ms_a}),
                     "2025-05-28": day_rec({"editor": ms_b}),
                     "2025-06-11": day_rec({"editor": today_ms})}}  # fmt: skip


def with_hours(spec):
    """D42: a day record whose hours are {hour: minutes}, recorded whole."""
    h = [0] * 24
    for i, m in spec.items():
        h[i] = m * MIN
    return {"total": sum(h), "apps": {"editor": sum(h)}, "hours": h}


def mon_to_wed(extra):
    """D38: 3h every Monday, Tuesday and Wednesday from Mon 2025-03-03 to Wed
    2025-05-28 (13 whole weeks; April and May whole months), plus `extra` hours."""
    days = {}
    for o in range(D(2025, 3, 3).toordinal(), D(2025, 5, 28).toordinal() + 1):
        if D.fromordinal(o).weekday() < 3:
            days[key_of(D.fromordinal(o))] = day_rec({"editor": 3 * HOUR})
    for k, h in extra.items():
        days[k] = day_rec({"editor": h * HOUR})
    return {"days": days}


def eight_weds():
    """All eight usual Wednesdays present: the sample size is complete (D12)."""
    days = {
        key_of(back(D(2025, 6, 11), 7 * w)): day_rec({"editor": (w + 1) * HOUR})
        for w in range(1, 9)
    }
    days["2025-06-11"] = day_rec({"editor": 9 * HOUR})  # above the 5h 30m usual
    return {"days": days}


def dst_history():
    days = {}
    for n in range(40):
        d = D.fromordinal(D(2025, 1, 31).toordinal() + n)
        days[key_of(d)] = day_rec({"editor": (n + 1) * MIN * 7})
    return {"days": days}


# D30 theme fixtures: copies of two installed Omarchy themes' colours (dark, light)
THEME_TOKYO = 'accent = "#7aa2f7"\nselection = "#292e42"\nmuted = "#414868"\nbackground = "#1a1b26"\nforeground = "#a9b1d6"\nred = "#f7768e"\nyellow = "#e0af68"\norange = "#eb927b"\ngreen = "#9ece6a"\ncyan = "#449dab"\nblue = "#7aa2f7"\nmagenta = "#ad8ee6"\nbrown = "#75493d"\nbright_red = "#ff7a93"\nbright_yellow = "#ff9e64"\nbright_green = "#b9f27c"\nbright_cyan = "#0db9d7"\nbright_blue = "#7da6ff"\nbright_magenta = "#bb9af7"\n'
THEME_LATTE = 'mode = "light"\naccent = "#1e66f5"\nmuted = "#acb0be"\nbackground = "#eff1f5"\nforeground = "#4c4f69"\nred = "#d20f39"\nyellow = "#df8e1d"\norange = "#d84e2b"\ngreen = "#40a02b"\ncyan = "#179299"\nblue = "#1e66f5"\nmagenta = "#ea76cb"\nbrown = "#6c2715"\nbright_red = "#d20f39"\nbright_yellow = "#df8e1d"\nbright_green = "#40a02b"\nbright_cyan = "#179299"\nbright_blue = "#1e66f5"\nbright_magenta = "#ea76cb"\n'
THEME_FIVE = 'background = "#1a1b26"\nforeground = "#a9b1d6"\nred = "#f7768e"\nyellow = "#e0af68"\ngreen = "#9ece6a"\ncyan = "#449dab"\nblue = "#7aa2f7"\nmagenta = "#ad8ee6"\nbright_red = "#ff7a93"\n'

SCENARIOS = [
    {"name": "busy Wednesday", "history": busy_history(), "names": BUSY_NAMES,
     "as_of": "2025-06-11T15:00:00", "theme": {"colors.toml": THEME_TOKYO}},
    {"name": "new install, evening", "as_of": "2025-06-11T21:30:00", "names": None,
     "theme": {"colors.toml": THEME_LATTE, "shell.toml": '[popups]\nbackground = "background"\ntext = "#5c5f77"\n',
               "user_shell.toml": '[popups]\nbackground = "#e6e9ef"\n'},
     "history": {"days": {"2025-06-04": day_rec({"editor": 2 * HOUR}),
                          "2025-06-09": day_rec({"editor": HOUR}),
                          "2025-06-11": day_rec({"editor": 50 * MIN, "ssh": 30_000})}}},
    {"name": "at the band's edge", "history": two_weds(3 * HOUR, 3 * HOUR, 3 * HOUR + 9 * MIN),
     "names": {}, "as_of": "2025-06-11T15:00:00"},
    {"name": "inside the 5-minute floor", "history": two_weds(HOUR, HOUR, HOUR + 4 * MIN),
     "names": {}, "as_of": "2025-06-11T15:00:00"},
    {"name": "all eight usual days", "history": eight_weds(), "names": None,
     "as_of": "2025-06-11T10:00:00"},
    {"name": "below the usual, so far", "history": two_weds(4 * HOUR, 2 * HOUR, HOUR),
     "names": {}, "as_of": "2025-06-11T10:00:00"},
    {"name": "just after a DST night", "history": dst_history(), "names": None,
     "as_of": "2025-03-10T00:30:00"},
    {"name": "across a year boundary", "names": None, "as_of": "2025-01-03T12:00:00",
     "history": {"days": {"2024-12-28": day_rec({"editor": HOUR}),
                          "2024-12-30": day_rec({"editor": 2 * HOUR}),
                          "2025-01-02": day_rec({"editor": 3 * HOUR}),
                          "2025-01-03": day_rec({"editor": 25 * MIN})},
                 "months": {"2022-05": 5 * HOUR},
                 "years": {"2023": {"2023-07-04": HOUR}}}},
    {"name": "names.json partly invalid", "as_of": "2025-06-11T15:00:00",
     "names": {"rename": {"editor": 5, "web": "Web"}},
     "history": {"days": {"2025-06-11": day_rec({"editor": HOUR, "web": 20 * MIN})}}},
    {"name": "no history file", "missing": True, "names": None, "as_of": "2025-06-11T15:00:00"},
    {"name": "corrupt history", "raw": b"{oops", "names": None, "as_of": "2025-06-11T15:00:00"},
    {"name": "empty history", "history": {"days": {}, "months": {}, "years": {}},
     "names": None, "as_of": "2025-06-11T15:00:00"},
    # C4: tracking began this Wednesday; today is Friday. The usual Friday needs two
    # Fridays (today, Jun 20) -> "from Jun 27"; last week can be compared from next
    # week's Wednesday (Jun 18), not its Monday.
    {"name": "C4: tracking began midweek", "as_of": "2025-06-13T15:00:00", "names": None,
     "history": {"days": {"2025-06-11": day_rec({"editor": HOUR}),
                          "2025-06-13": day_rec({"editor": 2 * HOUR})}}},
    # C4: only Apr 16 (8 weeks back) qualifies; by Jun 18 it has aged out of the
    # window, so the usual Wednesday needs today and Jun 18: "from Jun 25"
    {"name": "C4: an old usual day ages out", "as_of": "2025-06-11T15:00:00", "names": None,
     "history": {"days": {"2025-04-16": day_rec({"editor": HOUR}),
                          "2025-06-11": day_rec({"editor": HOUR})}}},
    # C4: just after midnight, today has 20 s. It still counts as tracked (it is
    # being tracked), so the usual Wednesday needs today and Jun 18: "from Jun 25"
    # (counting today by its time would say Jul 2)
    {"name": "C4: today under a minute so far", "as_of": "2025-06-11T00:05:00", "names": None,
     "history": {"days": {"2025-06-11": day_rec({"editor": 20_000})}}},
    # D30 fallbacks: five usable hues (red excluded), a broken file
    {"name": "D30: too few colours", "as_of": "2025-06-11T15:00:00", "names": None,
     "theme": {"colors.toml": THEME_FIVE},
     "history": {"days": {"2025-06-11": day_rec({"a": 6 * MIN, "b": 5 * MIN, "c": 4 * MIN,
                                                  "d": 3 * MIN, "e": 2 * MIN, "f": MIN, "g": 30_000})}}},
    {"name": "D30: invalid colors.toml", "as_of": "2025-06-11T15:00:00", "names": None,
     "theme": {"colors.toml": "background = #1a1b26 [oops\n"},
     "history": {"days": {"2025-06-11": day_rec({"a": 6 * MIN, "b": 5 * MIN})}}},
    # D20: days past the window keep their apps in archive/<year>.json. 2025-01-10
    # is in both (a crash after the archive write): the history copy wins.
    # 2023.json is corrupt (skipped); notes.txt is not a year file (ignored).
    {"name": "D20: the archive keeps apps", "as_of": "2026-01-15T12:00:00", "names": None,
     "history": {"days": {"2025-01-10": day_rec({"editor": 3 * HOUR}),
                          "2025-06-01": day_rec({"editor": 2 * HOUR, "web": 20 * MIN}),
                          "2026-01-14": day_rec({"chat": 50 * MIN}),
                          "2026-01-15": day_rec({"editor": HOUR, "web": 30 * MIN})},
                 "years": {"2023": {"2023-06-01": 2 * HOUR}}},
     "archive": {"2024.json": {"schema": 1, "days": {
                     "2024-12-30": {"total": HOUR + 5 * MIN, "apps": {"editor": 40 * MIN, "chat": 20 * MIN}},
                     "2024-12-31": {"total": 90 * MIN, "apps": {"web": 90 * MIN}}}},
                 "2025.json": {"schema": 1, "days": {
                     "2025-01-05": {"total": 40 * MIN, "apps": {"chat": 40 * MIN}},
                     "2025-01-10": {"total": 999_999, "apps": {"editor": 999_999}}}},
                 "2023.json": "{not json",
                 "notes.txt": {"schema": 1, "days": {"2023-01-01": {"total": HOUR, "apps": {"x": HOUR}}}}}},
    # C4: tracking began last Thursday; today is Tuesday. Last week can be compared
    # from this Thursday (Jun 12), not next Monday
    {"name": "C4: tracking began late last week", "as_of": "2025-06-10T12:00:00", "names": None,
     "history": {"days": {"2025-06-05": day_rec({"editor": HOUR}),
                          "2025-06-10": day_rec({"editor": HOUR})}}},
    # V1 Month: January is a legacy lump (no days). Feb 1..23 is compared with Jan
    # 1..23, which the lump cannot be split into: 3h over 0, "by the 23rd"
    {"name": "month: last month a legacy lump", "as_of": "2025-02-23T12:00:00", "names": None,
     "history": {"days": {"2025-02-03": day_rec({"editor": 2 * HOUR}),
                          "2025-02-23": day_rec({"editor": HOUR})},
                 "months": {"2025-01": 30 * HOUR}}},
    # D38: under so far on every page (today 1h, this week 3h by Wed, June 3h by the 11th)
    {"name": "D38: under so far", "as_of": "2025-06-11T15:00:00", "names": None,
     "history": mon_to_wed({"2025-06-09": 1, "2025-06-10": 1, "2025-06-11": 1})},
    # D38: a day and a week near the average, June above it by the 11th
    {"name": "D38: near the average", "as_of": "2025-06-11T15:00:00", "names": None,
     "history": mon_to_wed({"2025-06-02": 3, "2025-06-03": 3, "2025-06-04": 3, "2025-06-09": 3,
                            "2025-06-10": 3, "2025-06-11": 3})},
    # D38: June near the average by the 11th (12h against Apr 1-11 15h, May 1-11 9h)
    {"name": "D38: a month near, mid-month", "as_of": "2025-06-11T15:00:00", "names": None,
     "history": mon_to_wed({"2025-06-02": 3, "2025-06-03": 3, "2025-06-04": 3, "2025-06-09": 3})},
    # D42: whole days by hour; today at 14:20, its 2 PM hour current
    {"name": "D42: whole days by hour", "as_of": "2025-06-11T14:20:00", "names": None,
     "history": {"days": {"2025-06-09": with_hours({9: 50, 10: 60, 15: 20}),
                          "2025-06-10": with_hours({0: 5, 23: 30, 12: 45}),
                          "2025-06-11": with_hours({8: 40, 13: 60, 14: 20})}}},
    # D42: whole days before a partial today (installed mid-day: 2h before hours)
    {"name": "D42: a partial today", "as_of": "2025-06-11T14:20:00", "names": None,
     "history": {"days": {"2025-06-08": with_hours({10: 15}),  # the first whole day
                          "2025-06-09": {"total": HOUR, "apps": {"editor": HOUR}},
                          "2025-06-10": with_hours({9: 30}),
                          "2025-06-11": dict(with_hours({14: 20}), total=2 * HOUR + 20 * MIN,
                                             apps={"editor": 2 * HOUR + 20 * MIN})}}},
    # D42: the install day: today partial, nothing whole yet -> tomorrow
    {"name": "D42: the install day", "as_of": "2025-06-11T22:40:00", "names": None,
     "history": {"days": {"2025-06-10": {"total": HOUR, "apps": {"editor": HOUR}},
                          "2025-06-11": dict(with_hours({22: 4}), total=3 * HOUR + 4 * MIN,
                                             apps={"editor": 3 * HOUR + 4 * MIN})}}},
    # D48: days recorded in part draw the hours they have, "hours from <first>":
    # Jun 5 from 12 AM (hour 0 has time), Jun 6 from 3 PM (an empty hour before is
    # no start), today from 12 PM (installed at noon); Jun 7 has hours with no time
    # and Jun 9 more than its total: both stay "hourly from" (Jun 8, the whole one)
    {"name": "D48: partial days", "as_of": "2025-06-11T13:30:00", "names": None,
     "history": {"days": {"2025-06-04": {"total": HOUR, "apps": {"editor": HOUR}},
                          "2025-06-05": dict(with_hours({0: 10, 9: 20}), total=2 * HOUR,
                                             apps={"editor": 2 * HOUR}),
                          "2025-06-06": dict(with_hours({14: 0, 15: 45, 22: 5}), total=3 * HOUR,
                                             apps={"editor": 3 * HOUR}),
                          "2025-06-07": dict(with_hours({}), total=HOUR, apps={"editor": HOUR}),
                          "2025-06-08": with_hours({10: 15}),
                          "2025-06-09": dict(with_hours({9: 90}), total=HOUR, apps={"editor": HOUR}),
                          "2025-06-11": dict(with_hours({12: 25, 13: 30}), total=2 * HOUR + 48 * MIN,
                                             apps={"editor": 2 * HOUR + 48 * MIN})}}},
    # D42: the fall-back day's repeated 1 AM holds two hours: drawn full
    {"name": "D42: a fall-back day", "as_of": "2025-11-03T09:00:00", "names": None,
     "history": {"days": {"2025-11-02": with_hours({0: 30, 1: 120, 2: 30}),
                          "2025-11-03": with_hours({8: 30, 9: 0})}}},
    {"name": "week: near, on a Sunday", "as_of": "2025-06-15T20:00:00", "names": None,
     "history": {"days": {"2025-06-02": day_rec({"editor": 10 * HOUR}),
                          "2025-06-15": day_rec({"editor": 10 * HOUR + 4 * MIN})}}},
    {"name": "week: below last week, midweek", "as_of": "2025-06-11T15:00:00", "names": None,
     "history": {"days": {"2025-06-02": day_rec({"editor": 4 * HOUR}),
                          "2025-06-03": day_rec({"editor": 4 * HOUR}),
                          "2025-06-04": day_rec({"editor": 2 * HOUR}),
                          "2025-06-05": day_rec({"editor": 9 * HOUR}),
                          "2025-06-11": day_rec({"editor": 2 * HOUR})}}},
]  # fmt: skip


def entry(name=None, *extra):
    lines = ["[Desktop Entry]", "Type=Application"] + ([f"Name={name}"] if name else [])
    return "\n".join(lines + list(extra)) + "\n"


# D17: one desktop fixture, five cards (a card shows at most six rows).
DESKTOP = {
    "home": {
        "viewer.desktop": entry("User Viewer"),  # overrides both system copies
        "claude.desktop": entry("Claude"),  # must never name "claude"
        "shadow.desktop": entry("Shadow Override", "Hidden=true"),  # deletes the id
        "shade.desktop": b"[Desktop Entry]\nName=Bad \xff\n",  # malformed: claims nothing
        # D19b: Omarchy web apps (Exec=omarchy-launch-webapp <url>, no StartupWMClass)
        "X.desktop": entry("X", "Exec=omarchy-launch-webapp https://x.com/"),
        "Monarch.desktop": entry(
            "Monarch", 'Exec=omarchy-launch-webapp "https://app.monarch.com"'
        ),
        "YouTube.desktop": entry(
            "YouTube", "Exec=omarchy-launch-webapp https://www.youtube.com/"
        ),
        "Grafana.desktop": entry(
            "Homelab", "Exec=omarchy-launch-webapp http://homelab:3000"
        ),
        "dupweb-a.desktop": entry(
            "Dup Web A", "Exec=omarchy-launch-webapp https://dupweb.example/a"
        ),
        "dupweb-b.desktop": entry(
            "Dup Web B", "Exec=omarchy-launch-webapp https://dupweb.example/b"
        ),
    },
    "dirs": [
        {
            "viewer.desktop": entry("System Viewer"),
            "shadow.desktop": entry("Shadow"),
            "shade.desktop": entry("Shade"),
            "notes.desktop": entry("Notes", "StartupWMClass=NotesWin"),
            "dup-a.desktop": entry("Dup One", "StartupWMClass=dup"),
            "dup-b.desktop": entry("Dup Two", "StartupWMClass=dup"),
            "twin-a.desktop": entry("Twin", "StartupWMClass=twin"),
            "twin-b.desktop": entry("Twin", "StartupWMClass=twin"),
            "ghost.desktop": entry("Ghost", "Hidden=true"),
            "lang.desktop": "[Desktop Entry]\nName[de]=Sprache\nName=Lang\n",
            "onlyde.desktop": "[Desktop Entry]\nName[de]=Nur Deutsch\n",
            "nogroup.desktop": "Name=No Group\n",
            "broken.desktop": b"[Desktop Entry]\nName=Broken\xfe\n",
            "kde/kfoo.desktop": entry("KFoo"),
            "Mixed.desktop": entry("Mixed Case"),
            "action.desktop": entry("Act") + "[Desktop Action new]\nName=New Window\n",
            "com.anthropic.Claude.desktop": entry("Claude"),
            "google-chrome.desktop": entry("Google Chrome"),
            "imv.desktop": entry("imv"),
            "notes.txt": "[Desktop Entry]\nName=Not An Entry\n",
        },
        {
            "viewer.desktop": entry("Lowest Viewer"),
            "lower.desktop": entry("Lower Only"),
        },
    ],
    "flatpak_user": {"org.flat.User.desktop": entry("Flat User")},
    "flatpak_system": {"org.flat.App.desktop": entry("Flat App")},
}


def desk_scenario(name, apps, names=None):
    return {"name": "desktop: " + name, "desktop": DESKTOP, "names": names,
            "as_of": "2025-06-11T15:00:00",
            "history": {"days": {"2025-06-11": day_rec(
                {k: m * MIN for k, m in apps.items()})}}}  # fmt: skip


SCENARIOS += [
    desk_scenario("XDG precedence", {"viewer": 10, "shadow": 9, "shade": 8, "lower": 7,
                                     "kde-kfoo": 6, "mixed": 5}),
    desk_scenario("matching rules", {"noteswin": 10, "dup": 9, "twin": 8, "ghost": 7,
                                     "lang": 6, "onlyde": 5}),
    desk_scenario("malformed, flatpak, actions", {"nogroup": 10, "broken": 9, "org.flat.User": 8,
                                                  "org.flat.App": 7, "action": 6, "notes": 5}),
    desk_scenario("built-in before desktop", {"claude": 10, "com.anthropic.Claude": 9,
                                              "google-chrome": 8, "zen": 7, "bash": 6, "zsh": 5,
                                              "nvim": 4}, {"rename": {"nvim": "My Editor"}}),
    desk_scenario("web apps and toolkits", {"chrome-x.com": 10, "chrome-app.monarch.com__-Default": 9,
                                            "chrome-dupweb.example": 8, "chrome-youtube.com": 7,
                                            "viewer-wayland": 6, "chrome-homelab": 5}),
    desk_scenario("fallback and merge", {"imv": 10, "imv-wayland": 2, "chrome-www.example.com": 9,
                                         "foo-x11": 8, "com.example.Tool-wayland": 7, "lang": 6},
                  {"rename": {"lang": "Renamed Lang"}}),
]  # fmt: skip

# Worked out by hand, not by the code above (see the comments): Wednesday
# 2025-06-11, usual Wednesdays 06-04 (2h) and 05-28 (4h).
PINNED = {
    "name": "hand-pinned",
    "as_of": "2025-06-11T15:00:00",
    "names": {"rename": {"editor": "Editor"}, "hide": ["bash"]},
    "history": {"days": {
        "2025-06-04": day_rec({"editor": 2 * HOUR}),
        "2025-05-28": day_rec({"editor": 4 * HOUR}),
        "2025-06-11": day_rec({"editor": 200 * MIN, "Claude": 30 * MIN, "claude": 10 * MIN,
                               "bash": 4 * MIN, "ssh": 40_000}),
    }},
}  # fmt: skip
PINNED_EXPECT = [
    # 200 + 30 + 10 + 4 min + 40 s = 244 min 40 s, shown rounded: 4h 5m
    (("today", "total_text"), "4h 5m"),
    # D38/D45: completed days with a minute: Jun 4 only (May 28 is the install day,
    # only partly tracked); today and Jun 12 make three once over: Jun 13
    (("day", "line", "text"), "average starts Jun 13"),
    (("day", "line", "state"), "baseline"),
    # Other = bash 4m + ssh 40s (bash hidden, ssh under a minute); no average: no vs
    (
        ("rows",),
        [
            ("Editor", "3h 20m", ""),
            ("Claude", "30m", ""),
            # "claude" is the curated Claude Code (D17); no desktop entries here
            ("Claude Code", "10m", ""),
            ("Other", "5m", ""),
        ],
    ),
    # all time 2h + 4h + 4h 4m 40s = 10h 4m 40s; the week is only today
    (("footer", "text"), "all-time 10h 5m since May 28"),
    # D19 weeks (Mon-Sun): May 26-Jun 1, Jun 2-8, Jun 9-15; tracked from Wed May 28.
    # This week: the completed days Mon and Tue are 0 (C5: today left out) -> "0m";
    # against last week's Mon..Wed (Jun 4: 2h): band max(5m, 6m) = 6m, above by
    # 2h 4m 40s -> "2h 5m"
    # Weeks (Mon-Sun): May 26 began before tracking (May 28), so the only whole
    # completed week is Jun 2-8; a second one, this week, ends Sunday: from Jun 16.
    # The completed days Mon and Tue are 0 (C5: today left out) -> "avg 0m a day"
    (("week", "key"), "2025-06-09"),
    (("week", "range_text"), "Jun 9 – 15"),
    (("week", "line", "text"), "average starts Jun 16"),
    (("week", "mean_text"), ""),
    (("week", "prev"), "2025-06-02"),
    (("week", "next"), None),
    # the year: completed days May 29..Jun 10 = 3 + 10 = 13 (C5: today left out; D47:
    # the install day May 28 too), only Jun 4's 2h; 120m / 13 = 9.2m -> "9m"
    (("year", "line", "text"), "9m a day since May 29"),
    (("year", "prev"), None),
    (("week", "bars", 2, "state"), "today"),
    (("week", "bars", 3, "state"), "future"),
    # the Day page is today's: the same rows as the glance, the chart ends "today"
    (("day", "label"), "today"),
    (("day", "prev"), "2025-06-10"),
    # D42: no day of this history has hours, so none is whole: "hourly from" tomorrow
    (("day", "hours", "state"), "pending"),
    (("day", "hours", "text"), "hourly from Jun 12"),
    (("day", "hours", "bars"), []),
]


# ---- the audit report (D19b), the oracle's way ----------------------------------
# Periods by ordinal; months as y * 12 + m; rows grouped by (label, hidden); the
# no-detail remainder its own row. The --md tables are then checked against the
# engine's own --json for the same period.

FULL_MON = ["January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December"]  # fmt: skip

AUDITS = [
    ("busy Wednesday", ["--day", "2025-06-11"]),
    ("busy Wednesday", ["--day", "2025-03-01"]),  # an archive day: no app detail
    ("busy Wednesday", ["--week", "2025-06-01"]),  # a Sunday: the week of May 26
    ("busy Wednesday", ["--week", "2025-06-11"]),
    ("busy Wednesday", ["--month", "2025-05"]),
    ("busy Wednesday", ["--month", "2024-11"]),  # a legacy lump
    ("busy Wednesday", ["--month", "2025-07"]),  # a future lump: not counted
    ("busy Wednesday", ["--year", "2024"]),
    ("busy Wednesday", ["--year", "2025"]),
    ("busy Wednesday", []),  # the whole picture
    ("across a year boundary", ["--week", "2025-01-01"]),  # Dec 30 - Jan 5
    ("across a year boundary", []),
    ("hand-pinned", ["--week", "2025-06-11"]),
    (
        "D20: the archive keeps apps",
        ["--year", "2024"],
    ),  # per-app detail from the archive
    ("D20: the archive keeps apps", ["--day", "2025-01-10"]),  # the history copy wins
    ("D20: the archive keeps apps", ["--year", "2025"]),
    ("D20: the archive keeps apps", []),
    (
        "C4: tracking began midweek",
        [],
    ),  # D38: this week only partly tracked: not a whole week
    ("busy Wednesday", ["--month", "2025-06", "--by-day"]),  # up to today, Jun 11
    ("busy Wednesday", ["--month", "2024-11", "--by-day"]),  # a legacy lump: undated
    ("busy Wednesday", ["--year", "2025", "--by-day"]),  # archive totals, lumps, apps
    ("across a year boundary", ["--year", "2024", "--by-day"]),
    ("hand-pinned", ["--month", "2025-06", "--by-day"]),  # renames and hidden apps
    (
        "D20: the archive keeps apps",
        ["--year", "2024", "--by-day"],
    ),  # archive days keep apps
    (
        "D20: the archive keeps apps",
        ["--year", "2025", "--by-day"],
    ),  # the history copy wins
]


def o_context(sc):
    now = datetime.datetime.fromisoformat(sc["as_of"])
    today = now.date()
    days, archive, lumps_all = read_history(sc["history"])
    days = o_with_archive(days, sc.get("archive"))
    per_day = {}
    for k, rec in days.items():
        if day_of(k) <= today and rec["total"] > 0:
            per_day[k] = per_day.get(k, 0) + rec["total"]
    for k, v in archive.items():
        if day_of(k) <= today:
            per_day[k] = per_day.get(k, 0) + v
    this_m = today.year * 12 + today.month - 1
    lumps = {
        k: v for k, v in lumps_all.items() if int(k[:4]) * 12 + int(k[5:]) - 1 <= this_m
    }
    rename, hide, _ = read_names(sc.get("names"))
    stored = set()
    for rec in days.values():
        stored |= set(rec["apps"])
    labels = resolve(rename, stored)
    hidden = set(resolve({h: 1 for h in hide}, stored))
    entries = o_desktop(sc.get("desktop"))
    return {"now": now, "today": today, "days": days, "per_day": per_day, "lumps": lumps,
            "hidden": hidden, "label": lambda k: o_label(k, labels, entries)}  # fmt: skip


def o_day_label(d):
    return f"{WD[d.weekday()]} {MON[d.month - 1]} {d.day}, {d.year}"


def o_rows(ctx, ords, total):
    """The app rows of these days, then whatever of `total` they don't explain."""
    by = {}
    for o in ords:
        rec = ctx["days"].get(key_of(D.fromordinal(o)))
        for k, v in (rec["apps"] if rec else {}).items():
            h = k in ctx["hidden"]
            r = by.setdefault(
                (ctx["label"](k), h),
                {"name": ctx["label"](k), "keys": set(), "ms": 0, "hidden": h},
            )
            r["ms"] += v
            r["keys"].add(k)
    rows = [{"name": r["name"], "keys": sorted(r["keys"]), "ms": r["ms"], "hidden": r["hidden"]}
            for r in sorted(by.values(), key=lambda r: (-r["ms"], r["name"], r["hidden"]))]  # fmt: skip
    for r in rows:
        r["text"], r["detail"] = o_fmt(r["ms"]), True
    rest = total - sum(r["ms"] for r in rows)
    if rest > 0:
        rows.append({"name": "(no app detail)", "keys": [], "ms": rest, "text": o_fmt(rest),
                     "hidden": False, "detail": False})  # fmt: skip
    return rows


def o_period(ctx, kind, s_ord, e_ord, label, by_day=False):
    t = ctx["today"].toordinal()
    ords = [o for o in range(s_ord, e_ord + 1) if o <= t]
    sd, ed = D.fromordinal(s_ord), D.fromordinal(e_ord)
    lo, hi = sd.year * 12 + sd.month - 1, ed.year * 12 + ed.month - 1
    lump = 0
    if kind in ("month", "year"):
        lump = sum(
            v
            for k, v in ctx["lumps"].items()
            if lo <= int(k[:4]) * 12 + int(k[5:]) - 1 <= hi
        )
    total = sum(ctx["per_day"].get(key_of(D.fromordinal(o)), 0) for o in ords) + lump
    rows = o_rows(ctx, ords, total)
    out = {"kind": kind, "label": label, "start": key_of(sd), "end": key_of(ed),
           "total_ms": total, "total_text": o_fmt(total), "rows": rows}  # fmt: skip
    if kind in ("week", "month"):
        out["days"] = [{"key": key_of(D.fromordinal(o)), "label": o_day_label(D.fromordinal(o)),
                        "ms": ctx["per_day"].get(key_of(D.fromordinal(o)), 0),
                        "text": o_fmt(ctx["per_day"].get(key_of(D.fromordinal(o)), 0))} for o in ords]  # fmt: skip
    if kind == "year":
        y = sd.year
        months = [0] * 12
        for k, v in list(ctx["per_day"].items()) + list(ctx["lumps"].items()):
            if int(k[:4]) == y:
                months[int(k[5:7]) - 1] += v
        n = 12 if y < ctx["today"].year else ctx["today"].month
        out["months"] = [{"key": f"{y}-{m + 1:02d}", "label": FULL_MON[m], "ms": months[m],
                          "text": o_fmt(months[m])} for m in range(n)]  # fmt: skip
    if by_day:  # every day up to today; the lumps have no day
        out["by_day"] = []
        for o in ords:
            k, ms = (
                key_of(D.fromordinal(o)),
                ctx["per_day"].get(key_of(D.fromordinal(o)), 0),
            )
            out["by_day"].append({"key": k, "label": o_day_label(D.fromordinal(o)), "total_ms": ms,
                                  "total_text": o_fmt(ms), "rows": o_rows(ctx, [o], ms)})  # fmt: skip
        out["undated_ms"], out["undated_text"] = lump, o_fmt(lump) if lump else ""
    return out


def o_audit_for(ctx, kind, value, by_day=False):
    if kind == "day":
        d = D.fromisoformat(value)
        return o_period(ctx, "day", d.toordinal(), d.toordinal(), o_day_label(d))
    if kind == "week":
        o = D.fromisoformat(value).toordinal()
        mo = o - (o - 1) % 7
        m, su = D.fromordinal(mo), D.fromordinal(mo + 6)
        if m.year != su.year:
            label = f"{MON[m.month - 1]} {m.day}, {m.year} – {MON[su.month - 1]} {su.day}, {su.year}"
        else:
            end = (
                f"{su.day}" if su.month == m.month else f"{MON[su.month - 1]} {su.day}"
            )
            label = f"{MON[m.month - 1]} {m.day} – {end}, {su.year}"
        return o_period(ctx, "week", mo, mo + 6, label)
    if kind == "month":
        y, mm = int(value[:4]), int(value[5:])
        first = D(y, mm, 1)
        nxt = D(y + 1, 1, 1) if mm == 12 else D(y, mm + 1, 1)
        return o_period(
            ctx,
            "month",
            first.toordinal(),
            nxt.toordinal() - 1,
            f"{FULL_MON[mm - 1]} {y}",
            by_day,
        )
    y = int(value)
    return o_period(
        ctx, "year", D(y, 1, 1).toordinal(), D(y, 12, 31).toordinal(), str(y), by_day
    )


def o_audit(sc, args):
    ctx = o_context(sc)
    if args:
        return o_audit_for(ctx, args[0][2:], args[1], "--by-day" in args)
    today = ctx["today"]
    all_ms = sum(ctx["per_day"].values()) + sum(ctx["lumps"].values())
    starts = [day_of(k) for k in ctx["per_day"]] + [
        D(int(k[:4]), int(k[5:]), 1) for k in ctx["lumps"]
    ]
    first = min(starts) if starts else None
    since = ""
    if first:
        since = f"since {MON[first.month - 1]} {first.day}" + (
            "" if first.year == today.year else f", {first.year}"
        )
    return {
        "generated": ctx["now"].isoformat(timespec="seconds"),
        "today": o_audit_for(ctx, "day", key_of(today)),
        "week": o_audit_for(ctx, "week", key_of(today)),
        "year": o_audit_for(ctx, "year", str(today.year)),
        "all_time": {"total_ms": all_ms, "total_text": o_fmt(all_ms),
                     "since_key": key_of(first) if first else "", "since_text": since,
                     "averages": o_report_averages(ctx)},
    }  # fmt: skip


def o_report_averages(ctx):
    """D38 in the report: every completed period, nothing left out."""
    c = {"today": ctx["today"], "per_day": ctx["per_day"], "lumps": ctx["lumps"]}
    starts = [day_of(k) for k in ctx["per_day"]] + [
        D(int(k[:4]), int(k[5:]), 1) for k in ctx["lumps"]
    ]
    c["first"] = min(day_of(k) for k in ctx["per_day"]) if ctx["per_day"] else None
    c["first_all"] = min(starts) if starts else None
    periods = o_periods(c)
    out = {}
    for kind, first in (
        ("day", c["first"]),
        ("week", c["first"]),
        ("month", c["first_all"]),
    ):
        mean, n = o_mean(periods[kind], None, None)
        when = None if n >= O_NEEDS[kind] else o_starts(kind, n, c, first)
        out[kind] = {
            "ms": 0 if when else mean,
            "text": "" if when else o_fmt(mean),
            "periods": n,
            "starts_key": key_of(when) if when else "",
            "starts_text": o_short(when, ctx["today"]) if when else "",
        }
    return out  # fmt: skip


def md_cells(md):
    """Every Markdown table row as (cell, cell)."""
    out = []
    for line in md.splitlines():
        if line.startswith("| ") and not line.startswith("|---"):
            out.append(tuple(c.replace("\\|", "|") for c in line[2:-2].split(" | ")))
    return out


def md_want(doc):
    """The table rows the --md output must hold, from the --json document."""
    want = []
    periods = [doc] if "kind" in doc else [doc["today"], doc["week"], doc["year"]]
    for p in periods:
        sub = p.get("days") or p.get("months")
        if sub:
            want.append(("Day" if p.get("days") else "Month", "Time"))
            want += [(x["label"], x["text"]) for x in sub]
        want.append(("App", "Time"))
        want += [
            (r["name"] + (" (hidden)" if r["hidden"] else ""), r["text"])
            for r in p["rows"]
        ]
        want.append(("**Total**", f"**{p['total_text']}**"))
        for d in p.get("by_day", []):
            if d["rows"]:
                want.append(("App", "Time"))
                want += [
                    (r["name"] + (" (hidden)" if r["hidden"] else ""), r["text"])
                    for r in d["rows"]
                ]
                want.append(("**Total**", f"**{d['total_text']}**"))
    return want


def check_audits(engine, tmp):
    fails = []
    scenarios = {sc["name"]: sc for sc in SCENARIOS + [PINNED]}
    for name, args in AUDITS:
        sc, tag = scenarios[name], "audit " + name + " " + (" ".join(args) or "(whole)")
        try:
            got = json.loads(run_cli(engine, sc, tmp, ["report", *args, "--json"]))
            md = run_cli(engine, sc, tmp, ["report", *args, "--md"])
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            fails.append(f"{tag}: {e}")
            continue
        fails += [f"{tag}: {d}" for d in diff(got, o_audit(sc, args))]
        if md_cells(md) != md_want(got):
            fails.append(f"{tag}: --md tables differ from --json")
        if "all_time" in got:  # D38: the lifetime averages under All-time
            want = []
            for kind, unit in (("day", "days"), ("week", "weeks"), ("month", "months")):
                a = got["all_time"]["averages"][kind]
                want.append(f"- Average {kind}: " + (f"{a['text']} ({a['periods']} {unit})" if a["text"]
                                                     else f"starts {a['starts_text']}"))  # fmt: skip
            if [x for x in md.splitlines() if x.startswith("- Average ")] != want:
                fails.append(f"{tag}: --md averages differ from --json")
        if (
            "--by-day" in args
        ):  # the rows add up exactly, day by day, then to the period
            for d in got.get("by_day", []):
                if sum(r["ms"] for r in d["rows"]) != d["total_ms"]:
                    fails.append(f"{tag}: {d['key']} rows do not add up to its total")
            if (
                sum(d["total_ms"] for d in got.get("by_day", []))
                + got.get("undated_ms", 0)
                != got["total_ms"]
            ):
                fails.append(f"{tag}: the days and the undated part do not add up")
            heads = [x for x in md.splitlines() if x.startswith("### ")]
            if heads != [
                f"### {d['label']} · {d['total_text']}" for d in got.get("by_day", [])
            ]:
                fails.append(f"{tag}: --md day headings differ from --json")
        # D33: the header says the times are rounded, right under "Generated ..."
        lines = md.splitlines()
        gen = next(
            (i for i, line in enumerate(lines) if line.startswith("Generated ")), None
        )
        want_line = "Times are rounded to the minute; exact values: `screen-time report --json`."
        if gen is None or gen + 1 >= len(lines) or lines[gen + 1] != want_line:
            fails.append(f"{tag}: --md header lacks the rounding line under Generated")
    return fails


# ---- one page at a time (V1): `card --page kind:key` -------------------------------
# The same pages as the card's own, for any period from the first tracked one to
# today; anything else is state "error" with one line. Keys by regex and ordinal.

PAGES = [
    ("busy Wednesday", "day:2025-06-10"),  # yesterday
    ("busy Wednesday", "day:2025-06-04"),
    ("busy Wednesday", "day:2025-06-03"),  # a decoy Tuesday
    ("busy Wednesday", "day:2025-05-14"),
    ("busy Wednesday", "day:2025-04-16"),  # hidden and curated apps on a past day
    (
        "busy Wednesday",
        "day:2025-03-28",
    ),  # the first day with apps; archive days before it
    (
        "busy Wednesday",
        "day:2022-05-01",
    ),  # the first tracked day (an archive day): no ‹
    ("busy Wednesday", "day:2022-04-30"),  # before it: refused
    ("busy Wednesday", "day:2025-06-12"),  # after today: refused
    ("busy Wednesday", "day:2025-06-11"),  # today: the card's own day page
    ("busy Wednesday", "week:2025-06-05"),  # a Thursday: the week of Mon Jun 2
    ("busy Wednesday", "week:2025-05-26"),
    ("busy Wednesday", "week:2025-03-24"),
    (
        "busy Wednesday",
        "week:2022-05-01",
    ),  # a Sunday: the first tracked week, Apr 25: no ‹
    ("busy Wednesday", "week:2022-04-24"),  # the week before it: refused
    ("busy Wednesday", "month:2025-05"),
    ("busy Wednesday", "month:2025-03"),  # archive day Mar 1, tracking from Mar 28
    ("busy Wednesday", "month:2025-02"),  # a phantom Feb 29 never counts
    (
        "busy Wednesday",
        "month:2024-11",
    ),  # a legacy lump: its days are zeros, it is Other
    ("busy Wednesday", "month:2024-12"),  # the month after a lump
    ("busy Wednesday", "month:2025-07"),  # after today: refused
    ("busy Wednesday", "month:2022-04"),  # before the first tracked month: refused
    ("busy Wednesday", "month:2025-13"),
    ("busy Wednesday", "month:0000-01"),
    ("busy Wednesday", "year:2024"),
    ("busy Wednesday", "year:2022"),  # the first year with time
    ("busy Wednesday", "year:2021"),  # before it: refused
    ("busy Wednesday", "year:2026"),
    ("busy Wednesday", "year:25"),
    ("busy Wednesday", "week:bogus"),
    ("busy Wednesday", "hour:2025"),
    ("busy Wednesday", "day"),
    ("all eight usual days", "day:2025-06-04"),  # a past day with a usual
    ("all eight usual days", "day:2025-05-07"),
    ("all eight usual days", "month:2025-05"),
    ("below the usual, so far", "day:2025-06-04"),
    ("across a year boundary", "week:2024-12-30"),  # Dec 30 - Jan 5, across the year
    ("across a year boundary", "day:2024-12-31"),
    ("across a year boundary", "month:2024-12"),
    ("across a year boundary", "year:2023"),
    ("across a year boundary", "year:2022"),  # D47: a lump year before the install day
    ("across a year boundary", "month:2022-05"),  # a legacy lump, the first month
    ("D20: the archive keeps apps", "day:2024-12-30"),  # an archived day keeps its apps
    ("D20: the archive keeps apps", "month:2025-01"),  # the history copy of Jan 10 wins
    ("D20: the archive keeps apps", "year:2024"),
    ("C4: tracking began late last week", "month:2025-06"),
    ("D38: under so far", "week:2025-05-26"),  # a whole week, as its average: ≈
    ("D38: under so far", "week:2025-03-03"),  # the first week, left out of its own
    ("D38: under so far", "month:2025-04"),  # April against May alone
    (
        "D38: under so far",
        "month:2025-03",
    ),  # March began before tracking: no whole month
    ("D38: under so far", "day:2025-06-10"),  # a finished day under: ▼
    ("D38: near the average", "week:2025-06-02"),
    ("D42: whole days by hour", "day:2025-06-10"),  # a past whole day: every hour past
    (
        "D42: a partial today",
        "day:2025-06-09",
    ),  # no hours: pending, from the first whole day
    ("D42: a partial today", "day:2025-06-10"),
    ("D42: a fall-back day", "day:2025-11-02"),
    ("D48: partial days", "day:2025-06-05"),  # from 12 AM
    ("D48: partial days", "day:2025-06-06"),  # from 3 PM
    ("D48: partial days", "day:2025-06-07"),  # hours, no time: pending
    ("D48: partial days", "day:2025-06-08"),  # whole: no caption
    ("D48: partial days", "day:2025-06-09"),  # more than its total: pending
    ("month: last month a legacy lump", "month:2025-01"),  # the lump month itself
    ("C4: tracking began midweek", "week:2025-06-09"),
    ("hand-pinned", "day:2025-06-04"),
    ("hand-pinned", "day:2025-06-10"),
    ("hand-pinned", "week:2025-06-02"),
    ("hand-pinned", "week:2025-05-26"),
    ("hand-pinned", "month:2025-05"),
    ("no history file", "day:2025-06-10"),
    ("corrupt history", "week:2025-06-02"),
    ("empty history", "month:2025-06"),
]


def o_page(sc, spec):
    as_of = datetime.datetime.fromisoformat(sc["as_of"]).isoformat(timespec="seconds")

    def doc(state, message=None, kind="", key="", page=None):
        return {"schema": 11, "state": state, "message": MESSAGES[state] if message is None else message,
                "as_of": as_of, "kind": kind, "key": key, "page": page}  # fmt: skip

    if sc.get("missing"):
        return doc("missing")
    if "raw" in sc:
        return doc("corrupt")
    today = datetime.datetime.fromisoformat(sc["as_of"]).date()
    days, archive, lumps_all = read_history(sc["history"])
    days = o_with_archive(days, sc.get("archive"))
    per_day = {}
    for k, rec in days.items():
        if day_of(k) <= today and rec["total"] > 0:
            per_day[k] = per_day.get(k, 0) + rec["total"]
    for k, v in archive.items():
        if day_of(k) <= today:
            per_day[k] = per_day.get(k, 0) + v
    lumps = {
        k: v for k, v in lumps_all.items() if k <= f"{today.year:04d}-{today.month:02d}"
    }
    if not per_day and not lumps:
        return doc("empty")
    c = o_card_context(sc, today, days, per_day, lumps)
    kind, _, key = spec.partition(":")
    first, first_all = c["first"], c["first_all"]
    page = None
    d = None
    if kind in ("day", "week") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
        try:
            d = D.fromisoformat(key)
        except ValueError:
            d = None
    if kind == "day" and d and first and first <= d <= today:
        page = o_day(c, d)
    elif kind == "week" and d and first and d <= today:
        mo = d.toordinal() - (d.toordinal() - 1) % 7
        if mo >= first.toordinal() - (first.toordinal() - 1) % 7:
            page = o_week_page(c, mo)
    elif kind == "month" and re.fullmatch(r"\d{4}-\d{2}", key) and first_all:
        idx = int(key[:4]) * 12 + int(key[5:]) - 1
        lo, hi = (
            first_all.year * 12 + first_all.month - 1,
            today.year * 12 + today.month - 1,
        )
        if 1 <= int(key[5:]) <= 12 and lo <= idx <= hi:
            page = o_month_page(c, int(key[:4]), int(key[5:]))
    elif (
        kind == "year"
        and re.fullmatch(r"\d{4}", key)
        and first_all
        and first_all.year <= int(key) <= today.year
    ):
        page = o_year_page(c, int(key))
    if page is None:
        return doc("error", f"Not a page: {spec}")
    pal = o_palette(sc.get("theme"))
    repaint = {SWATCHES[i]: pal["swatches"][i] for i in range(6)}
    repaint[OTHER_SWATCH] = pal["other"]
    o_recolour(page, repaint)
    return doc("ok", "", kind, page["key"], page)


# Worked by hand on the hand-pinned scenario (as of Wed 2025-06-11; tracked from
# Wed May 28: 4h; Jun 4: 2h; today 4h 4m 40s).
PINNED_PAGES = [
    # June so far: 2h + 4h 4m 40s; completed Jun 1..10 = 10 days, 2h / 10 = 12m. May
    # 1..11 is before tracking: the comparison starts a month after May 28
    ("month", ("total_text",), "6h 5m"),
    # no whole month is over (May began before tracking): June and July -> Aug 1
    ("month", ("line", "text"), "average starts Aug 1"),
    ("month", ("bars", 3, "state"), "past"),
    ("month", ("bars", 10, "state"), "today"),
    ("month", ("bars", 11, "state"), "future"),
    ("month", ("bars", 14, "label"), "15"),
    ("month", ("prev",), "2025-05"),
    # May: May 28..31 counted, 4h / 4 = 1h; April is before tracking
    ("month:2025-05", ("label",), "last month"),
    ("month:2025-05", ("line", "text"), "average starts Aug 1"),
    ("month:2025-05", ("prev",), None),
    ("month:2025-05", ("next",), "2025-06"),
    ("month:2025-05", ("bars", 26, "state"), "before"),
    ("month:2025-05", ("bars", 27, "state"), "past"),
    # Jun 4 leaves itself out and May 28 is the install day: none, so today, Jun 12
    # and Jun 13 make three: Jun 14
    ("day:2025-06-04", ("label",), "day"),
    ("day:2025-06-04", ("pill",), "Wed · Jun 4"),
    ("day:2025-06-04", ("line", "text"), "average starts Jun 14"),
    ("day:2025-06-04", ("hours", "text"), "hourly from Jun 12"),
    ("day:2025-06-04", ("prev",), "2025-06-03"),
    ("day:2025-06-04", ("next",), "2025-06-05"),
    ("day:2025-06-10", ("label",), "yesterday"),
    ("day:2025-06-10", ("total_text",), "0m"),
    # the first week: May 28..Jun 1 counted, 4h / 5 = 48m; May 19-25 is before tracking
    # the first week (partly before tracking): Jun 2-8 is one whole week; this one, Jun 16
    ("week:2025-05-26", ("line", "text"), "average starts Jun 16"),
    ("week:2025-05-26", ("bars", 1, "state"), "before"),
    ("week:2025-05-26", ("bars", 2, "state"), "past"),
    ("week:2025-05-26", ("prev",), None),
    # last week (2h over 7 days = 17.1m) leaves itself out: none left; this week and
    # the next -> Jun 23
    ("week:2025-06-02", ("line", "text"), "average starts Jun 23"),
    ("week:2025-06-02", ("label",), "last week"),
    ("week:2025-06-02", ("next",), "2025-06-09"),
]


def check_pages(engine, tmp):
    fails = []
    scenarios = {sc["name"]: sc for sc in SCENARIOS + [PINNED]}
    for name, spec in PAGES:
        tag = f"page {name} {spec}"
        try:
            lines = (
                run_cli(engine, scenarios[name], tmp, ["card", "--page", spec])
                .strip()
                .splitlines()
            )
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            fails.append(f"{tag}: {e}")
            continue
        if len(lines) != 1:
            fails.append(f"{tag}: engine printed {len(lines)} lines")
            continue
        got = json.loads(lines[0])
        fails += [f"{tag}: {d}" for d in diff(got, o_page(scenarios[name], spec))]
        # a page fetched for today is exactly the card's own
        if (
            got.get("state") == "ok"
            and spec.split(":")[1] == scenarios[name]["as_of"][:10]
            and spec.startswith("day:")
        ):
            card = run_engine(engine, scenarios[name], tmp)
            if card["day"] != got["page"]:
                fails.append(f"{tag}: differs from the card's own day page")
    for spec, path, value in PINNED_PAGES:
        if ":" in spec:
            page = json.loads(run_cli(engine, PINNED, tmp, ["card", "--page", spec]))[
                "page"
            ]
            want = o_page(PINNED, spec)["page"]
        else:
            page = run_engine(engine, PINNED, tmp)[spec]
            want = expected_card(PINNED)[spec]
        tag = "hand-pinned " + spec + " " + "/".join(map(str, path))
        try:
            have = pinned_value(page, path)
        except (KeyError, IndexError, TypeError):
            fails.append(f"{tag}: missing from the engine's page")
            continue
        if have != value:
            fails.append(f"{tag}: engine {have!r}, by hand {value!r}")
        if pinned_value(want, path) != value:
            fails.append(f"ORACLE disagrees with the {tag}")
    return fails


# ---- running the engine and comparing --------------------------------------------


def run_engine(engine, sc, tmp):
    lines = run_cli(engine, sc, tmp, ["card"]).strip().splitlines()
    if len(lines) != 1:
        raise RuntimeError(f"engine printed {len(lines)} lines")
    return json.loads(lines[0])


def run_cli(engine, sc, tmp, args):
    """The engine's stdout for `args` on the scenario's files (rc 0 required)."""
    shutil.rmtree(os.path.join(tmp, "archive"), ignore_errors=True)
    if sc.get("archive") is not None:  # D20: archive/<year>.json beside history.json
        os.makedirs(os.path.join(tmp, "archive"))
        for name, content in sc["archive"].items():
            with open(os.path.join(tmp, "archive", name), "w") as f:
                f.write(content if isinstance(content, str) else json.dumps(content))
    hist = os.path.join(tmp, "history.json")
    names = os.path.join(tmp, "names.json")
    for p in (hist, names):
        if os.path.exists(p):
            os.unlink(p)
    if "raw" in sc:
        with open(hist, "wb") as f:
            f.write(sc["raw"])
    elif not sc.get("missing"):
        with open(hist, "w") as f:
            json.dump(sc["history"], f)
    if sc.get("names") is not None:
        with open(names, "w") as f:
            json.dump(sc["names"], f)
    cmd = [sys.executable, "-I", engine, *args, "--history", hist, "--names", names,
           "--as-of", sc["as_of"]]  # fmt: skip
    env = dict(os.environ, TZ="America/Chicago")
    env.update(write_desktop(sc.get("desktop"), os.path.join(tmp, "xdg")))
    shutil.rmtree(os.path.join(tmp, "userhome"), ignore_errors=True)
    env.update(write_theme(sc.get("theme"), os.path.join(tmp, "userhome")))
    p = subprocess.run(
        cmd, capture_output=True, text=True, env=env, timeout=60, check=False
    )
    if p.returncode != 0:
        raise RuntimeError(f"engine rc={p.returncode} stderr={p.stderr.strip()[-300:]}")
    return p.stdout


def diff(a, b, path=""):
    if isinstance(a, float) or isinstance(b, float):
        if (
            isinstance(a, (int, float))
            and isinstance(b, (int, float))
            and abs(a - b) < 1e-12
        ):
            return []
        return ["{}: engine {!r}, oracle {!r}".format(path or "/", a, b)]
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(
                    "{}/{}: {}".format(
                        path,
                        k,
                        "missing in engine" if k not in a else "extra in engine",
                    )
                )
            else:
                out += diff(a[k], b[k], path + "/" + k)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items, oracle {len(b)}"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff(x, y, f"{path}[{i}]")
        return out
    return (
        []
        if a == b and type(a) is type(b)
        else ["{}: engine {!r}, oracle {!r}".format(path or "/", a, b)]
    )


def pinned_value(card, path):
    if path == ("rows",):
        return [(r["name"], r["value_text"], r["vs_text"]) for r in card["rows"]]
    v = card
    for p in path:
        v = v[p]
    return v


def check(engine):
    fails = []
    with tempfile.TemporaryDirectory() as tmp:
        for sc in SCENARIOS + [PINNED]:
            try:
                got = run_engine(engine, sc, tmp)
            except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
                fails.append("{}: {}".format(sc["name"], e))
                continue
            want = expected_card(sc)
            fails += ["{}: {}".format(sc["name"], d) for d in diff(got, want)]
            if sc is PINNED:
                for path, value in PINNED_EXPECT:
                    try:
                        have = pinned_value(got, path)
                    except (KeyError, IndexError, TypeError):
                        fails.append(
                            "hand-pinned {}: missing from the engine's card".format(
                                "/".join(map(str, path))
                            )
                        )
                        continue
                    if have != value and not (
                        isinstance(value, float) and abs(have - value) < 1e-12
                    ):
                        fails.append(
                            "hand-pinned {}: engine {!r}, by hand {!r}".format(
                                "/".join(map(str, path)), have, value
                            )
                        )
                    if pinned_value(want, path) != value and not isinstance(
                        value, float
                    ):
                        fails.append(
                            "ORACLE disagrees with the hand-pinned {}".format(
                                "/".join(map(str, path))
                            )
                        )
        fails += check_audits(engine, tmp)
        fails += check_pages(engine, tmp)
    return fails


def main(argv):
    if len(argv) != 2:
        print("usage: oracle_screen_time.py <engine.py>", file=sys.stderr)
        return 2
    fails = check(os.path.abspath(argv[1]))
    for f in fails:
        print("FAIL " + f)
    print(
        f"oracle: {len(SCENARIOS)} scenarios + 1 hand-pinned + {len(AUDITS)} reports + {len(PAGES)} pages, "
        + ("PASS" if not fails else f"{len(fails)} failures")
    )
    print(json.dumps(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
