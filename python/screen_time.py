#!/usr/bin/env python3
"""screen_time.py: the screen-time engine. Python computes, QML paints.

Reads ~/.config/omarchy/screen-time/history.json (written by the tracker in
qml/Service.qml) and names.json (written by you or an agent). It opens both
READ-ONLY and never writes anything.

    screen_time.py card                       one JSON document for the hover card
    screen_time.py card --page KIND:KEY       one page of it (V1): day:YYYY-MM-DD,
                                              week:YYYY-MM-DD (the Mon-Sun week holding
                                              it), month:YYYY-MM, year:YYYY
    screen_time.py report today|week|year|all plain text (the old summary)
    screen_time.py report [--day D | --week D | --month YYYY-MM | --year YYYY] [--by-day] [--json | --md]
                                              the audit report (D19b): every app, hidden
                                              ones marked, rows adding up to the total;
                                              no period = today, this week, this year,
                                              all-time
    screen_time.py keys [--days N]            stored app keys, totals, display names
    screen_time.py log --date YYYY-MM-DD      one markdown line for that day

Options for every verb:
    --history PATH   history file (default ~/.config/omarchy/screen-time/history.json)
    --names PATH     names file   (default ~/.config/omarchy/screen-time/names.json)
    --as-of ISO      pin "now" (local time, e.g. 2026-10-05T21:30), for tests

Day keys are LOCAL calendar days (YYYY-MM-DD), the same as Model.dayKey in the
tracker. Day arithmetic is done on dates, never on timestamps, so a 23- or
25-hour DST day cannot shift a key.

CARD CONTRACT (schema 11, D48: a Day's `hours` drawn in part are "partial",
captioned "hours from 12 PM". Schema 10, D44: Week, Month and Year draw two gridlines, clear of the
dashed average. Schema 9, D43: every chart has `ticks`, its time scale. Schema 8, D42: the Day page's `chart` (30 days) is `hours`, the day by
hour. Schema 7, D41: "avg" only ever means the all-time average: the
line has no `head`, the week page no `avg_text`, the Year's line drops "avg", and
every dashed line is the one average day (Year: the average month) labelled
with its unit. Schema 6, D38: every page's `line` compares it with the all-time
average of its kind, and its chart's dashed line is that average (`mean_*` on the
Day's chart and on every period page); the top-level `usual` and `chart` are gone,
the Day page has them. Schema 5, V1: the card holds this day, week, month and year
as `day`, `week`, `month`, `year`, one page each, and `card --page` gives any
other; `years`, `year_index` and the list of week pages are gone; week bars'
`letter` is `label`; chart.end_label. Schema 4: D30 added `palette`, the rows' colours
from the theme; schema 3: D19 added `week` and took the week out of footer.text;
schema 1 also had chips, dropped by D11). The card prints exactly one JSON object and exits 0,
whatever happens. QML paints the strings and the 0..1 numbers as given and
does no arithmetic beyond pixels.

    schema   int     11
    state    str     "ok" | "empty" | "missing" | "corrupt" | "error"
    message  str     "" when state is "ok"; otherwise ONE line to show instead
                     of the card body ("No screen time recorded yet", ...)
    as_of    str     local ISO time the card was computed for
    today    object  hero:
        key         str  "2026-10-05"
        total_ms    int
        total_text  str  "2h 12m" (agx's format: "0m", "45s", "12m", "2h", "2h 5m")
        label       str  "today"
        pill        str  "Mon · Oct 5"
    rows     list    the glance's rows (= day.rows): up to 6 named apps, largest first, then
                     "Other" when anything is left. They always add up to
                     today.total_ms.
        name        str  label to show ("Other" for the fold row)
        keys        list stored keys folded into this row
        value_ms    int
        value_text  str
        swatch      str  "#RRGGBB" (Monarch's palette; Other is its neutral)
        other       bool true only for the Other row
        vs_text     str  the row against the app's own all-time average day: "▲ 12m"
                         | "≈" | today under it "avg 40m" | a finished day under it
                         "▼ 25m"; "" for Other or without an average
    segments list    every row as {name, swatch, weight}; weights are 0..1 and
                     sum to 1 (the segmented bar), [] when today is 0
    names    object  {"state": "ok" | "missing" | "invalid"}: how names.json
                     loaded ("" on fallback cards)
    footer   object  the card's last line:
        week_ms, week_text          Monday through today (the report verb's;
                                    the card shows the week on its Week page)
        all_time_ms, all_time_text  days + archive + legacy month lumps
        since_key, since_text       first tracked day: "since Oct 4"
                                    ("since Oct 4, 2025" in another year)
        text        str  "all-time 41h since Oct 4" (D19: no week)
    PAGES (V1). Each of day, week, month and year is one page (null on fallback
    cards); every page has:
        key         str  "2026-10-05" (day), the Monday "2026-09-28" (week),
                         "2026-09" (month), "2026" (year)
        label       str  "today" | "yesterday" | "day"; "this week" | "last week" |
                         "week"; "this month" | "last month" | "month"; "this year"
                         | "last year" | "year"
        total_ms, total_text   the period's days so far (archive days included;
                         Month and Year add legacy month lumps)
        rows, segments   that period's top six + Other, as today's, adding up to
                         total_ms (archive days and lumps without apps land in
                         Other); vs_text is "" except on the Day page
        prev, next  str | null: the neighbouring page's key. ‹ stops at the
                    first tracked period (Day and Week at the first tracked day;
                    Month and Year at the first day or legacy month lump), ›
                    at today's. No other cap.
        line        object, the page's comparison with the all-time average of its
                    kind (D38; the Year's is its daily average only):
            state       "baseline" (not enough history yet) | "near" | "above" |
                        "below" ("" on the Year)
            text        "average starts Oct 19" (baseline: muted, not bold);
                        "≈ average"; "▲ 40m above average"; a finished period
                        under it "▼ 25m below average"; today under it "average
                        3h 40m a day"; this week or month under it "average 9h
                        20m by Wed" / "average 18h by Oct 6". This week and month
                        always end in " by <today>" ("by Sun" on a Sunday).
            average_ms, average_text   the average compared with (0, "" baseline)
            periods     how many periods it rests on
        mean_ms, mean_text, mean_y   the chart's dashed line on Week, Month and
                    Year (D41): the one average day, the mean of the completed
                    days with a minute, "avg 2h 36m a day" on a Week or Month; the
                    Year's average month, "avg 22h 51m a month"; 0, "", null
                    without one. The Day's hourly chart has none (its line
                    compares the day with the average day).
                    the chart.
        y_max_ms    what y = 1 means (y_max_text is no longer painted, D38)
        ticks       the time scale: list of {ms, text, y}, each a faint gridline at
                    y (= ms / y_max_ms) labelled in the left gutter. Week, Month
                    and Year (D44): two, step and 2 x step, the largest pair from
                    15m, 30m, 1h-5h, 10h, 15h, 20h, 25h, 30h, 50h, 100h ... whose
                    top line is at most 90 % up and neither line within 8 % of the
                    chart's height of the dashed average (else the next smaller
                    pair), or none when no pair fits. The Day's hours: fixed, 30m
                    at 0.5 and 1h at 1 (none while pending).
    day      object  the Day page (Today's layout for any day): pill "Mon · Oct 5"
                     (", 2025" in another year), line, hours, rows with vs_text.
                     The card's `day` is today: its rows and segments equal the
                     top-level ones.
        hours       the day by hour (D42), the 12 AM..12 AM chart: state "ok" |
                    "partial" (D48: hours for only part of the day, 0 < their
                    sum < its total) | "pending" (no hours, none with time, or
                    more than its total), text "" | "hours from 12 PM" (partial:
                    the first hour with time, a muted caption under the bars) |
                    "hourly from Oct 7" (pending: the first day recorded whole,
                    else tomorrow), bars [] (pending) or 24 x
                    {hour, label "12a" | "6a" | "12p" | "6p" | "", ms, text, x =
                    i/23, y = ms / 60 min (at most 1: the repeated fall-back hour
                    is drawn full), state "past" | "current" (this hour, today) |
                    "future" (later hours today: empty slots)}, y_max_ms 60 min,
                    mean_ms 0, mean_text "", mean_y null. A day with no time at
                    all is whole: 24 empty bars.
    week     object  the Week page (D19): range_text "Oct 5 – 11" (en dash; ", 2025"
                     when its Sunday is in another year than today), avg_ms,
                     line, seven bars, y_max_ms, y_max_text, mean_*.
        avg_ms      the average over its completed counted days (from the first
                    tracked day, up to yesterday; today only when it is the only
                    day, audit C5)
        bars        list 7 x {key, label "M".."S", ms, text, x = i/6, y 0..1 of
                         y_max_ms, state}: state "past" | "today" | "future" (an
                         empty slot) | "before" (before the first tracked day: an
                         empty slot, no fake zero)
    month    object  the Month page (V1, the Week's layout): range_text "October"
                     ("October 2025" in another year), avg_ms, line, a bar per day
                     of the month, y_max_ms, y_max_text, mean_*.
        bars        list n x {key, label ("1", "8", "15", "22", "29", else ""), ms,
                         text, x = i/(n-1), y, state} with the week's states
    year     object  the Year page (D19b, the Week's layout): year (int), avg_ms,
                     line {state "", text, days} (the daily average over
                     the year's completed days, Jan 1 or the first tracked day ..
                     Dec 31 or yesterday; today only when it is the only day, audit
                     C5: "2h 11m a day since Oct 4"), twelve bars {label "Jan",
                     ms, text, x = m/11, y, state "past" | "current" (this month) |
                     "future" | "before"}, y_max_ms, y_max_text, mean_* (the
                     average month)

    card --page KIND:KEY prints one JSON object and exits 0, whatever happens:
        schema, state ("ok" | "missing" | "corrupt" | "empty" | "error"), message
        ("Not a page: <spec>" for anything that is not a page from the first
        tracked period to today), as_of, kind, key (the page's own: a week key is
        its Monday), page (as the card's, or null)
    palette  object  the rows' colours this run (D30), used by every rows[] and
                     segments[] swatch on every page:
        source      str  "theme": six of the active theme's own colours
                         (~/.local/state/omarchy/current/theme/colors.toml, red
                         excluded), picked and ordered for the largest colour-blind
                         distance | "okabe-ito": the fallback
        reason      str  "" | "missing" | "invalid" | "too few colours"
        keys        list the colors.toml keys picked, rank order ([] on fallback)
        swatches    list 6 x "#rrggbb", rank 1..6, each at least 3:1 on surface
        other       str  Other's swatch: the card's readable muted (4.5:1)
        surface     str  the popup background the card is painted on, resolved
                         as Commons/Color.qml does (user shell.toml, theme
                         shell.toml, colors.toml; Color.qml's defaults without a
                         theme)

ARCHIVE (D20). Days past the tracker's 365-day window keep their apps in
archive/<year>.json beside history.json and are read as ordinary days; a day
history.json still holds wins over its archived copy, so it counts once.

TOTALS. Days, the per-day archive and legacy month lumps are disjoint by
construction (upstream's mergeYear makes the same assumption) and are summed;
days and months after today never count. Values that are not numbers count
as 0: upstream also coerces numeric strings, but the tracker never writes
them and rewrites any it loads on its next save.

AVERAGES (D38). A page's average is the mean of ALL completed periods of its kind
in the history (archive included), the period shown left out: days (3 needed;
never the first tracked day, the install day, only ever partly tracked: D45),
Mon-Sun weeks (2) and months (2). A period counts when it is over, began on or
after the first tracked day (Day and Week: the first day; Month: the first day or
legacy month lump), and holds at least a minute (time off is not a period). This
week and this month compare their days so far with the same span of every other:
the mean of Monday..today's weekday, or of the 1st..today's day (a month holding a
legacy lump has no days, so it joins only a whole-month span). Today compares so
far with a whole average day. Within max(5 min, 5 %) reads "≈". Before enough
history: the date it will exist, if today and every coming period are tracked.
All history, never a rolling window: the ‹ › pages show recent periods one by one.

NAMES (D17). A row's label is the first of: (1) names.json "rename" on the
exact stored key, else without case when it hits exactly one stored key;
(2) a curated built-in (claude -> Claude Code, the shells -> Terminal,
nvim -> Neovim); (3) a desktop entry's plain Name= (XDG data dirs and the
flatpak exports; matched by file id, else StartupWMClass, both without case,
else for a web-app key by the host of a URL on the entry's Exec= line, the way
Omarchy web apps launch; an ambiguous match is no match); (4) the pre-D17
defaults (google-chrome -> Chrome, ...), only when no desktop entry covers the
key; (4b) for a key ending -wayland / -x11, the desktop lookup again without
it (D19b); (5) a fallback: a
web app's host without "www.", a reverse-DNS id's last segment, a trailing
"-wayland"/"-x11" dropped, case kept. Keys with the same label are one
row. "hide" (same matching) folds an app into Other; so do apps under a
minute and anything past six rows. Other is today's total minus the named
rows, so the total never changes.

Only "ok" carries a meaningful body. For every other state the other fields
are present with empty values, so the QML never meets a missing key.
"""

import argparse
import datetime
import itertools
import json
import math
import os
import re
import stat
import sys

import tomllib

DATA_DIR = os.path.join(os.path.expanduser("~"), ".config", "omarchy", "screen-time")
DEFAULT_HISTORY = os.path.join(DATA_DIR, "history.json")
DEFAULT_NAMES = os.path.join(DATA_DIR, "names.json")
SCHEMA = 11

DAY_KEY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_KEY_RE = re.compile(r"^\d{4}-\d{2}$")
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]  # fmt: skip

# Anything the card's computation can raise on hostile input; caught so the
# card still prints a document (state "error") instead of a traceback.
CARD_FAILURES = (ValueError, TypeError, KeyError, IndexError, AttributeError,
                 ArithmeticError, OSError, RecursionError)  # fmt: skip

MESSAGES = {
    "missing": "No screen time recorded yet",
    "corrupt": "Screen time history could not be read",
    "empty": "Nothing tracked yet",
    "error": "Screen time could not be computed",
}


# ---- time -------------------------------------------------------------------


def parse_now(as_of):
    """The local wall-clock 'now' as a naive datetime."""
    if not as_of:
        return local_now()
    dt = datetime.datetime.fromisoformat(as_of)
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def local_now():
    """Now on the local wall clock, naive (day keys are local days)."""
    return datetime.datetime.now(datetime.UTC).astimezone().replace(tzinfo=None)


def parse_day(key):
    """A date for a strict YYYY-MM-DD key, else None ("2026-02-30" is None)."""
    if not isinstance(key, str) or not DAY_KEY_RE.match(key):
        return None
    try:
        return datetime.date.fromisoformat(key)
    except ValueError:
        return None


def day_key(d):
    return d.isoformat()


def pill(d):
    return f"{WEEKDAYS[d.weekday()]} · {MONTHS[d.month - 1]} {d.day}"


# ---- format (a port of agx's Model.fmt, so the card reads like the bar did) --


def fmt(ms):
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        ms = 0.0
    if not math.isfinite(ms):
        ms = 0.0
    ms = max(0, js_round(ms))
    if ms <= 0:
        return "0m"
    if ms < 60000:
        return f"{max(1, js_round(ms / 1000))}s"
    mins = js_round(ms / 60000)
    if mins < 60:
        return f"{mins}m"
    h, m = divmod(mins, 60)
    return f"{h}h" if m == 0 else f"{h}h {m}m"


def js_round(x):
    """JavaScript's Math.round: halves go up (Python's round() goes to even)."""
    return int(x // 1 + (1 if x % 1 >= 0.5 else 0))


# ---- history ------------------------------------------------------------------


def as_ms(v):
    """A non-negative integer millisecond count, or 0 for anything else."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    if not math.isfinite(v) or v <= 0:
        return 0
    return int(v)


def load_history(path):
    """(history, state). state is "ok", "missing" or "corrupt". Opens read-only."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "corrupt"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "corrupt"
    if not isinstance(doc, dict):
        return None, "corrupt"
    hist = sanitize(doc)
    merge_archive(hist, os.path.join(os.path.dirname(path), "archive"))
    return hist, "ok"


ARCHIVE_FILE_RE = re.compile(r"^\d{4}\.json$")


def merge_archive(hist, directory):
    """D20: days past the tracker's 365-day window live, apps and all, in
    <directory>/<year>.json ({"schema": 1, "days": {...}}). They join
    hist["days"], cleaned like history's own; a day history.json still holds
    wins (after a crash a day can sit in both, and must count once). Unreadable
    files are skipped: the archiver moves them aside, the engine never writes."""
    try:
        names = sorted(n for n in os.listdir(directory) if ARCHIVE_FILE_RE.match(n))
    except OSError:
        return
    for name in names:
        try:
            with open(os.path.join(directory, name), "rb") as f:
                doc = json.loads(f.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("schema") != 1:
            continue
        days = sanitize({"days": doc.get("days")})["days"]
        for key, rec in days.items():
            if key not in hist["days"]:
                hist["days"][key] = rec


def sanitize(doc):
    """Keep well-formed entries only, as the tracker's sanitizeHistory does."""
    days = {}
    raw_days = doc.get("days")
    for k, v in raw_days.items() if isinstance(raw_days, dict) else ():
        if parse_day(k) is None or not isinstance(v, dict):
            continue
        apps = {}
        raw_apps = v.get("apps")
        for app, ms in raw_apps.items() if isinstance(raw_apps, dict) else ():
            if isinstance(app, str) and app and as_ms(ms) > 0:
                apps[app] = as_ms(ms)
        # The hero is never smaller than its rows: a stored total below the
        # apps' sum (it should never happen) is raised to the sum.
        days[k] = {
            "total": max(as_ms(v.get("total")), sum(apps.values())),
            "apps": apps,
        }
        hours = v.get("hours")  # D42: 24 ms by local hour, kept when well-formed
        if (
            isinstance(hours, list)
            and len(hours) == 24
            and all(
                isinstance(h, (int, float))
                and not isinstance(h, bool)
                and math.isfinite(h)
                and h >= 0
                for h in hours
            )
        ):
            days[k]["hours"] = [int(h) for h in hours]
    years = {}
    raw_years = doc.get("years")
    for y, entries in raw_years.items() if isinstance(raw_years, dict) else ():
        if not isinstance(entries, dict):
            continue
        for k, ms in entries.items():
            d = parse_day(k)
            if d is not None and str(d.year) == str(y) and as_ms(ms) > 0:
                years[k] = years.get(k, 0) + as_ms(ms)
    months = {}
    raw_months = doc.get("months")
    for k, ms in raw_months.items() if isinstance(raw_months, dict) else ():
        month_ok = (
            isinstance(k, str) and MONTH_KEY_RE.match(k) and 1 <= int(k[5:]) <= 12
        )
        if month_ok and as_ms(ms) > 0:
            months[k] = as_ms(ms)
    return {"days": days, "years": years, "months": months}


def daily_totals(hist, today):
    """{day key: ms} for every past or present day with time: per-app days plus
    the per-day archive, summed per key (the two are disjoint by construction,
    as in upstream's mergeYear). Days after today are dropped."""
    out = {}
    for k, rec in hist["days"].items():
        if parse_day(k) <= today and rec["total"] > 0:
            out[k] = out.get(k, 0) + rec["total"]
    for k, ms in hist["years"].items():
        if parse_day(k) <= today:
            out[k] = out.get(k, 0) + ms
    return out


def month_lumps(hist, today):
    """Legacy {YYYY-MM: ms} lumps, months after today's dropped."""
    cutoff = today.strftime("%Y-%m")
    return {k: ms for k, ms in hist["months"].items() if k <= cutoff}


def day_apps(hist, d):
    rec = hist["days"].get(day_key(d))
    return dict(rec["apps"]) if rec else {}


def day_total(hist, d):
    rec = hist["days"].get(day_key(d))
    return rec["total"] if rec else 0


# ---- names --------------------------------------------------------------------

# Monarch's eight swatches, in its order: the Monarch bar plugin's
# PALETTE, validated there against the popup surface
# #1a1b26. Named rows take the first six by rank; Other takes the last, the
# neutral Monarch gives its own Other row.
PALETTE = [
    "#0072B2",
    "#56B4E9",
    "#CC79A7",
    "#F0E442",
    "#9085E9",
    "#D9D9D9",
    "#7A5FD0",
    "#9A9A9A",
]
OTHER_SWATCH = PALETTE[7]
OTHER = "Other"
MAX_ROWS = 6
MIN_ROW_MS = 60_000

# Curated names (D17): they win over any desktop entry, so "claude" (the
# Claude Code CLI) never takes the Claude desktop app's Name.
CURATED_NAMES = {
    "claude": "Claude Code",
    "bash": "Terminal",
    "zsh": "Terminal",
    "fish": "Terminal",
    "sh": "Terminal",
    "nvim": "Neovim",
}
# The pre-D17 defaults: used only when no desktop entry covers the key.
LEGACY_NAMES = {
    "google-chrome": "Chrome",
    "chromium": "Chromium",
    "firefox": "Firefox",
    "zen": "Zen",
    "brave": "Brave",
    "microsoft-edge": "Edge",
    "vivaldi": "Vivaldi",
    "librewolf": "LibreWolf",
    "org.telegram.desktop": "Telegram",
}
# agx's Model.js patterns: a Chromium web app shows its host, a reverse-DNS id
# its last segment. Unlike agx the case is kept, so "com.anthropic.Claude"
# and "claude" no longer both read "claude".
WEB_APP_RE = re.compile(
    r"^((?:chrome|chromium|brave|msedge|vivaldi)-([a-z0-9](?:[a-z0-9.-]*[a-z0-9])?))"
    r"(__.*-(?:Default|Profile_[0-9]+))?$",
    re.IGNORECASE,
)
REVERSE_DNS_RE = re.compile(r"^(?:[a-z][a-z0-9-]*\.){2,}[a-z0-9_-]+$", re.IGNORECASE)
TOOLKIT_SUFFIXES = ("-wayland", "-x11")
DESKTOP_MAX_BYTES = 256 * 1024


# ---- row colours (D30): the active theme's own colours ---------------------------
# Each run reads the theme the shell paints with (Commons/Color.qml's paths), so a
# theme switch reaches the rows within one engine run. Red is the theme's "urgent"
# and never a row colour. Six colours are picked and ordered for the largest
# colour-blind distance, each lightened or darkened to 3:1 on the popup surface;
# Other is the card's muted. Without a usable theme: Okabe-Ito, adapted the same way.

THEME_DIR = os.path.join(
    os.path.expanduser("~"), ".local", "state", "omarchy", "current", "theme"
)
USER_SHELL = os.path.join(os.path.expanduser("~"), ".config", "omarchy", "shell.toml")
SHELL_BACKGROUND = "#101315"  # Color.qml's own defaults, used when no theme loads
SHELL_FOREGROUND = "#cacccc"
HUES = ("yellow", "orange", "green", "cyan", "blue", "magenta", "brown", "purple")
OKABE_ITO = ["#0072b2", "#56b4e9", "#cc79a7", "#f0e442", "#9085e9", "#d9d9d9"]
SWATCH_MIN_RATIO = 3.0  # WCAG 1.4.11
MUTED_MIN_RATIO = 4.5  # WCAG 1.4.3, as js/Contrast.js
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
# Machado, Oliveira, Fernandes 2009, severity 1.0, on linear RGB
CVD_MATRICES = (
    ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
)  # fmt: skip


def rgb8(h):
    return tuple(int(h[i : i + 2], 16) for i in (1, 3, 5))


def hex8(rgb):
    return "#" + "".join(f"{c:02x}" for c in rgb)


def to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(h):
    r, g, b = (to_linear(c / 255) for c in rgb8(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = rel_luminance(a), rel_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix_step(toward, base, step):
    """step % of toward over base, per channel in integers, halves up."""
    return hex8(
        (t * step + b * (100 - step) + 50) // 100
        for t, b in zip(rgb8(toward), rgb8(base))
    )


def to_three_to_one(h, bg):
    """The colour, lightened or darkened (toward whichever of black and white
    contrasts more with bg) by the least whole step that reaches 3:1."""
    toward = (
        "#000000"
        if contrast_ratio("#000000", bg) >= contrast_ratio("#ffffff", bg)
        else "#ffffff"
    )
    for step in range(101):
        c = mix_step(toward, h, step)
        if contrast_ratio(c, bg) >= SWATCH_MIN_RATIO:
            return c
    return toward


def readable_muted(text, bg):
    """js/Contrast.js readableMix(text, bg, 4.5, 0.4), as a hex colour."""
    t, b = [c / 255 for c in rgb8(text)], [c / 255 for c in rgb8(bg)]
    for step in range(40, 100):
        c = [x * step / 100 + y * (1 - step / 100) for x, y in zip(t, b)]
        h = hex8(int(v * 255 + 0.5) for v in c)
        lum = (
            0.2126 * to_linear(c[0])
            + 0.7152 * to_linear(c[1])
            + 0.0722 * to_linear(c[2])
        )
        lb = rel_luminance(bg)
        if (max(lum, lb) + 0.05) / (min(lum, lb) + 0.05) >= MUTED_MIN_RATIO:
            return h
    return text


def lab(h):
    r, g, b = (to_linear(c / 255) for c in rgb8(h))
    xyz = ((0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047,
           0.2126729 * r + 0.7151522 * g + 0.0721750 * b,
           (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883)  # fmt: skip
    fx, fy, fz = (
        t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116 for t in xyz
    )
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e2000(c1, c2):
    """CIEDE2000 (Sharma, Wu, Dalal 2005)."""
    L1, a1, b1 = lab(c1)
    L2, a2, b2 = lab(c2)
    cb = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(cb**7 / (cb**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360
    h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dh = h2p - h1p
    if c1p * c2p == 0:
        dh = 0
    elif dh > 180:
        dh -= 360
    elif dh < -180:
        dh += 360
    d_hue = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    lbp, cbp = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63)))  # fmt: skip
    rot = (
        -math.sin(math.radians(60 * math.exp(-(((hbp - 275) / 25) ** 2))))
        * 2
        * math.sqrt(cbp**7 / (cbp**7 + 25**7))
    )
    sl = 1 + 0.015 * (lbp - 50) ** 2 / math.sqrt(20 + (lbp - 50) ** 2)
    sc, sh = 1 + 0.045 * cbp, 1 + 0.015 * cbp * t
    return math.sqrt(((L2 - L1) / sl) ** 2 + ((c2p - c1p) / sc) ** 2 + (d_hue / sh) ** 2
                     + rot * ((c2p - c1p) / sc) * (d_hue / sh))  # fmt: skip


def simulate_cvd(h, m):
    lin = [to_linear(c / 255) for c in rgb8(h)]
    out = []
    for row in m:
        v = min(1.0, max(0.0, sum(k * x for k, x in zip(row, lin))))
        s = 12.92 * v if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055
        out.append(int(s * 255 + 0.5))
    return hex8(out)


def cvd_distance(a, b):
    """The smallest CIEDE2000 between two colours under protan, deutan, tritan."""
    return min(
        delta_e2000(simulate_cvd(a, m), simulate_cvd(b, m)) for m in CVD_MATRICES
    )


def load_toml(path):
    """(dict, "") or (None, "missing" | "invalid"); read-only."""
    try:
        with open(path, "rb") as f:
            doc = tomllib.load(f)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):  # tomllib.TOMLDecodeError is a ValueError
        return None, "invalid"
    return (doc, "") if isinstance(doc, dict) else (None, "invalid")


def popup_colours(colors):
    """The popup surface and text the card is painted with, the way
    Commons/Color.qml resolves popups.background and popups.text: the user's
    shell.toml over the theme's, a role word taken from the theme, else colors.toml."""
    bg = colors.get("background", SHELL_BACKGROUND)
    fg = colors.get("foreground", SHELL_FOREGROUND)
    roles = {"background": bg, "foreground": fg, "text": fg, "accent": colors.get("accent", fg),
             "muted": colors.get("muted", fg), "urgent": colors.get("red", fg)}  # fmt: skip
    chosen = {}
    for path in (os.path.join(THEME_DIR, "shell.toml"), USER_SHELL):
        doc, _ = load_toml(path)
        popups = doc.get("popups") if doc else None
        if isinstance(popups, dict):
            chosen.update(
                {
                    k: v
                    for k, v in popups.items()
                    if k in ("background", "text") and isinstance(v, str)
                }
            )

    def resolve(value, fallback):
        v = value.strip().lower()
        if v in roles:
            return roles[v]
        m = HEX_RE.match(v)
        return m.group(0) if m else fallback

    return resolve(chosen.get("background", bg), bg), resolve(
        chosen.get("text", fg), fg
    )


def theme_candidates(colors, bg):
    """{key: colour at 3:1} for the theme's hue colours (base and bright_), red
    never; sorted by key, a colour already taken under another key dropped."""
    out = {}
    for key in sorted(colors):
        base = key.removeprefix("bright_")
        if base not in HUES:
            continue
        c = to_three_to_one(colors[key], bg)
        if c not in out.values():
            out[key] = c
    return out


def pick_six(cands):
    """The 6 keys whose smallest colour-blind distance (then normal-vision
    distance) is largest, ordered so neighbours are as far apart as possible.
    Strict comparisons over a fixed order: the first best wins, every run."""
    keys = list(cands)
    cvd = {
        (a, b): cvd_distance(cands[a], cands[b])
        for a, b in itertools.combinations(keys, 2)
    }
    norm = {
        (a, b): delta_e2000(cands[a], cands[b])
        for a, b in itertools.combinations(keys, 2)
    }

    def pair(table, a, b):
        return table[(a, b)] if (a, b) in table else table[(b, a)]

    best, best_score = None, None
    for subset in itertools.combinations(keys, 6):
        pairs = list(itertools.combinations(subset, 2))
        score = (
            min(pair(cvd, a, b) for a, b in pairs),
            min(pair(norm, a, b) for a, b in pairs),
        )
        if best_score is None or score > best_score:
            best, best_score = subset, score
    order, order_score = None, None
    for perm in itertools.permutations(best):
        steps = list(itertools.pairwise(perm))
        score = (
            min(pair(cvd, a, b) for a, b in steps),
            min(pair(norm, a, b) for a, b in steps),
        )
        if order_score is None or score > order_score:
            order, order_score = perm, score
    return list(order)


def row_palette():
    """{source, reason, swatches[6], other, surface} for this run's rows."""
    doc, reason = load_toml(os.path.join(THEME_DIR, "colors.toml"))
    colors = {}
    if doc is not None:
        colors = {
            k: v.lower()
            for k, v in doc.items()
            if isinstance(v, str) and HEX_RE.fullmatch(v)
        }
    bg, text = popup_colours(colors)
    other = readable_muted(text, bg)
    if doc is not None:
        cands = theme_candidates(colors, bg)
        if len(cands) >= 6:
            keys = pick_six(cands)
            return {"source": "theme", "reason": "", "keys": keys,
                    "swatches": [cands[k] for k in keys], "other": other, "surface": bg}  # fmt: skip
        reason = "too few colours"
    return {"source": "okabe-ito", "reason": reason, "keys": [],
            "swatches": [to_three_to_one(c, bg) for c in OKABE_ITO], "other": other, "surface": bg}  # fmt: skip


def fallback_name(key):
    """The last resort: a web app's host (no "www."), a reverse-DNS id's last
    segment, a trailing -wayland/-x11 dropped; the case is never changed."""
    web = WEB_APP_RE.match(key)
    if web:
        host = web.group(2)
        return host[4:] if host.lower().startswith("www.") and len(host) > 4 else host
    base = strip_toolkit(key)
    if REVERSE_DNS_RE.match(base):
        return base.rsplit(".", 1)[1]
    return base


# ---- desktop entries (D17) ----------------------------------------------------


def desktop_dirs():
    """The applications dirs in XDG precedence order: $XDG_DATA_HOME, each of
    $XDG_DATA_DIRS, then the flatpak exports if they are not already listed.
    Relative paths are ignored, as the XDG spec says."""
    env = os.environ
    home = os.path.expanduser("~")
    data_home = env.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    data_dirs = (env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    flatpak_user = env.get("FLATPAK_USER_DIR") or os.path.join(data_home, "flatpak")
    flatpak_system = env.get("FLATPAK_SYSTEM_DIR") or "/var/lib/flatpak"
    out = []
    for d in [data_home, *data_dirs,
              os.path.join(flatpak_user, "exports", "share"),
              os.path.join(flatpak_system, "exports", "share")]:  # fmt: skip
        if d and os.path.isabs(d):
            a = os.path.join(os.path.normpath(d), "applications")
            if a not in out:
                out.append(a)
    return out


def desktop_unescape(v):
    out, i = [], 0
    while i < len(v):
        if v[i] == "\\" and i + 1 < len(v):
            out.append(
                {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(
                    v[i + 1], v[i + 1]
                )
            )
            i += 2
        else:
            out.append(v[i])
            i += 1
    return "".join(out)


def read_desktop_entry(path):
    """(Name, StartupWMClass, Hidden, Exec) from the [Desktop Entry] group, or None
    for anything unusable: not a regular file, unreadable, not UTF-8, no
    [Desktop Entry] group. Only the plain Name counts, never Name[xx]."""
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            return None
        with open(path, "rb") as f:
            text = f.read(DESKTOP_MAX_BYTES).decode("utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    group, seen, name, wm, hidden, exe = None, False, None, None, False, None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
            seen = seen or group == "Desktop Entry"
            continue
        if group != "Desktop Entry" or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), desktop_unescape(v.strip())
        if k == "Name" and name is None:
            name = v.strip()
        elif k == "StartupWMClass" and wm is None:
            wm = v.strip()
        elif k == "Hidden":
            hidden = v.strip() == "true"
        elif k == "Exec" and exe is None:
            exe = v
    if not seen:
        return None
    return name, wm, hidden, exe


def desktop_index(dirs):
    """[(file id, Name, StartupWMClass, Exec URL hosts)] for the entries that count. The first
    dir holding a file id wins (XDG); a winning Hidden=true entry deletes the
    id, and an entry without a Name is skipped. An unusable file claims no id,
    so a lower dir's copy still counts."""
    claimed = {}
    for base in dirs:
        for root, subdirs, files in os.walk(base):
            subdirs.sort()
            for fn in sorted(files):
                if not fn.endswith(".desktop"):
                    continue
                path = os.path.join(root, fn)
                fid = os.path.relpath(path, base)[: -len(".desktop")].replace(
                    os.sep, "-"
                )
                if fid in claimed:
                    continue
                entry = read_desktop_entry(path)
                if entry is not None:
                    claimed[fid] = entry
    return [
        (fid, e[0], e[1], exec_hosts(e[3]))
        for fid, e in sorted(claimed.items())
        if e[0] and not e[2]
    ]


EXEC_URL_RE = re.compile(r"https?://([^/\s\"'?#]+)", re.IGNORECASE)


def bare_host(host):
    """A host without case, port or a leading "www."."""
    host = host.lower().split(":", 1)[0]
    return host[4:] if host.startswith("www.") and len(host) > 4 else host


def exec_hosts(exe):
    """The hosts of the URLs on an Exec= line (Omarchy web apps launch with
    `omarchy-launch-webapp "https://app.monarch.com"`, and have no
    StartupWMClass to match)."""
    return frozenset(bare_host(h) for h in EXEC_URL_RE.findall(exe or ""))


def desktop_name(key, index):
    """The Name of the one entry whose file id, else whose StartupWMClass,
    equals the key without case; else, for a web-app key (chrome-<host>...), of
    the one entry whose Exec= opens a URL on that host (D19b). Several entries
    with different Names are no match (never guess); several with the same
    Name are that Name."""
    k = key.lower()
    for field in (0, 2):
        names = {e[1] for e in index if e[field] and e[field].lower() == k}
        if len(names) == 1:
            return names.pop()
    web = WEB_APP_RE.match(key)
    if web:
        host = bare_host(web.group(2))
        by_host = {e[1] for e in index if host in e[3]}
        if len(by_host) == 1:
            return by_host.pop()
    return None


def strip_toolkit(key):
    """The key without a trailing -wayland / -x11 (when something is left)."""
    for suffix in TOOLKIT_SUFFIXES:
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)]
    return key


def load_names(path):
    """(rename, hide, state). state: "ok", "missing" or "invalid" (unreadable, or
    some entries unusable; the usable ones still apply). Opens read-only."""
    try:
        with open(path, "rb") as f:
            doc = json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        return {}, [], "missing"
    except (OSError, UnicodeDecodeError, ValueError):
        return {}, [], "invalid"
    if not isinstance(doc, dict):
        return {}, [], "invalid"
    ok = True
    rename = {}
    raw_rename = doc.get("rename", {})
    if not isinstance(raw_rename, dict):
        ok, raw_rename = False, {}
    for k, v in raw_rename.items():
        if isinstance(k, str) and k and isinstance(v, str) and v.strip():
            rename[k] = v.strip()
        else:
            ok = False
    hide = []
    raw_hide = doc.get("hide", [])
    if not isinstance(raw_hide, list):
        ok, raw_hide = False, []
    for k in raw_hide:
        if isinstance(k, str) and k:
            hide.append(k)
        else:
            ok = False
    return rename, hide, "ok" if ok else "invalid"


def match_keys(entries, stored):
    """{stored key: (value, "exact"|"case")}. An entry matches its exact stored
    key; failing that, it matches without case, but only when that hits exactly
    one stored key ("CLAUDE" never catches both "Claude" and "claude"). An exact
    entry always wins over a case-insensitive one."""
    out = {}
    by_lower = {}
    for k in stored:
        by_lower.setdefault(k.lower(), []).append(k)
    for entry, value in entries.items():
        if entry in stored:
            out[entry] = (value, "exact")
    for entry, value in entries.items():
        if entry in stored:
            continue
        hits = by_lower.get(entry.lower(), [])
        if len(hits) == 1 and hits[0] not in out:
            out[hits[0]] = (value, "case")
    return out


class Names:
    """Display names for one history: renames and hides resolved against every
    key the history has ever stored."""

    def __init__(self, rename, hide, state, stored):
        self.state = state
        self.renamed = match_keys(rename, stored)
        self.hidden = match_keys(dict.fromkeys(hide, True), stored)
        self._desktop = None  # read on first use, once per run
        self._resolved = {}

    def desktop(self):
        if self._desktop is None:
            self._desktop = desktop_index(desktop_dirs())
        return self._desktop

    def resolve(self, key):
        """(label, source): rename, builtin, desktop or fallback (D17)."""
        if key not in self._resolved:
            self._resolved[key] = self._first_hit(key)
        return self._resolved[key]

    def _first_hit(self, key):
        hit = self.renamed.get(key)
        if hit:
            return hit[0], "rename"
        if key in CURATED_NAMES:
            return CURATED_NAMES[key], "builtin"
        name = desktop_name(key, self.desktop())
        if name:
            return name, "desktop"
        if key in LEGACY_NAMES:
            return LEGACY_NAMES[key], "builtin"
        # imv-wayland is imv: retry the desktop lookup without the toolkit (D19b)
        base = strip_toolkit(key)
        if base != key:
            name = desktop_name(base, self.desktop())
            if name:
                return name, "desktop"
        return fallback_name(key), "fallback"

    def label(self, key):
        return self.resolve(key)[0]


def stored_keys(hist):
    keys = set()
    for rec in hist["days"].values():
        keys.update(rec["apps"])
    return keys


# ---- vs the all-time average (V1 polish, D38) ------------------------------------
# Every page compares its period with the mean of ALL completed periods of its
# kind in the history (archive included), the one shown excluded. A period
# counts when it is over, began on or after the first tracked day (a week or
# month only partly tracked is not a whole one) and holds at least a minute
# (time off is not a period). The current week or month is compared over the
# same span: the mean of the completed weeks' Monday..today's weekday, or the
# completed months' 1st..today's day.

PERIOD_MIN_MS = 60_000
AVERAGE_NEEDS = {"day": 3, "week": 2, "month": 2}
AVERAGE_UNITS = {"day": "days", "week": "weeks", "month": "months"}
PER_DAY = " a day"  # the Week and Month dashed lines: an average day on day bars
BAND_FLOOR_MS = 5 * 60_000
BAND_SHARE = 0.05


def compare(now_ms, average_ms):
    """ "near" inside the dead band max(5 min, 5 %), else "above" or "below"."""
    band = max(BAND_FLOOR_MS, BAND_SHARE * average_ms)
    if abs(now_ms - average_ms) <= band:
        return "near"
    return "above" if now_ms > average_ms else "below"


def period(key, days, lump=0):
    return {"key": key, "days": days, "lump": lump, "total": sum(days) + lump}


def day_periods(totals, today):
    """Every completed day (before today) with at least a minute, but the first
    tracked day: the install day is always only partly tracked (D45), as a week
    or month begun before tracking is not a whole one."""
    first = min(totals) if totals else None
    return [
        period(k, [ms])
        for k, ms in sorted(totals.items())
        if parse_day(k) < today and ms >= PERIOD_MIN_MS and k != first
    ]


def week_periods(totals, today, first):
    """Every completed Mon-Sun week that began on or after the first tracked day."""
    out = []
    if first is None:
        return out
    monday = monday_of(first)
    if monday < first:
        monday += datetime.timedelta(days=7)
    while monday + datetime.timedelta(days=6) < today:
        days = [
            totals.get(day_key(monday + datetime.timedelta(days=i)), 0)
            for i in range(7)
        ]
        if sum(days) >= PERIOD_MIN_MS:
            out.append(period(day_key(monday), days))
        monday += datetime.timedelta(days=7)
    return out  # fmt: skip


def month_periods(totals, lumps, today, first):
    """Every completed month that began on or after `first` (first_day: a legacy
    month lump counts from its 1st, and is part of its month's total)."""
    out = []
    if first is None:
        return out
    first_of = first.replace(day=1)
    if first_of < first:
        first_of = month_after(first_of)
    while month_after(first_of) <= today:
        n = month_length(first_of)
        days = [
            totals.get(day_key(first_of + datetime.timedelta(days=i)), 0)
            for i in range(n)
        ]
        p = period(
            first_of.strftime("%Y-%m"), days, lumps.get(first_of.strftime("%Y-%m"), 0)
        )
        if p["total"] >= PERIOD_MIN_MS:
            out.append(p)
        first_of = month_after(first_of)
    return out  # fmt: skip


def span_value(p, span):
    """A period's time over its first `span` days (None or a span past its end:
    all of it). A legacy month lump has no days, so a month holding one cannot
    be cut short: None."""
    if span is None or span >= len(p["days"]):
        return p["total"]
    if p["lump"]:
        return None
    return sum(p["days"][:span])


def average(periods, shown_key, span=None):
    """(the mean, how many periods it rests on), the period shown left out."""
    values = [span_value(p, span) for p in periods if p["key"] != shown_key]
    values = [v for v in values if v is not None]
    return (js_round(sum(values) / len(values)) if values else 0), len(values)


def average_starts(kind, have, today, first):
    """The first date a page of `kind` will have an average, if today and every
    coming period are tracked: the day after the period that brings `have` up to
    AVERAGE_NEEDS. This week or month counts only if it began on or after the
    first tracked day."""
    need = AVERAGE_NEEDS[kind] - have
    if kind == "day":
        # today counts once it is over, unless it is the install day (D45)
        late = 1 if first is not None and first == today else 0
        return today + datetime.timedelta(days=need + late)
    if kind == "week":
        start = monday_of(today)
        if first is not None and start < first:
            start += datetime.timedelta(days=7)
        return start + datetime.timedelta(days=7 * need)
    start = today.replace(day=1)
    if first is not None and start < first:
        start = month_after(start)
    for _ in range(need):
        start = month_after(start)
    return start


def average_line(kind, total, mean, n, current, by, today, first):
    """The comparison under a page's hero, the whole line (D41: "avg" is only
    ever the all-time average, so a period's own daily average is not shown)."""
    line = {"state": "baseline", "text": "", "average_ms": 0,
            "average_text": "", "periods": n}  # fmt: skip
    if n < AVERAGE_NEEDS[kind]:
        start = average_starts(kind, n, today, first)
        line["text"] = f"average starts {short_date(start, today)}"
        return line
    state = compare(total, mean)
    line.update(state=state, average_ms=mean, average_text=fmt(mean))
    if state == "near":
        line["text"] = f"≈ average{by}"
    elif state == "above":
        line["text"] = f"▲ {fmt(total - mean)} above average{by}"
    elif current and kind == "day":
        # not over yet: under the average is "so far", so it shows the average
        line["text"] = f"average {fmt(mean)} a day"
    elif current:
        line["text"] = f"average {fmt(mean)}{by}"
    else:
        line["text"] = f"▼ {fmt(mean - total)} below average"
    return line


def row_vs_text(hist, row, day_keys, names, current):
    """A Day row against the app's own average over the same days; "" for Other
    or without an average."""
    if row["other"] or len(day_keys) < AVERAGE_NEEDS["day"]:
        return ""
    total = 0
    for k in day_keys:
        for key, ms in day_apps(hist, parse_day(k)).items():
            if key not in names.hidden and names.label(key) == row["name"]:
                total += ms
    mean = js_round(total / len(day_keys))
    state = compare(row["value_ms"], mean)
    if state == "near":
        return "≈"
    if state == "above":
        return f"▲ {fmt(row['value_ms'] - mean)}"
    return f"avg {fmt(mean)}" if current else f"▼ {fmt(mean - row['value_ms'])}"


def mean_fields(mean, n, kind, y_max, unit=""):
    """A chart's dashed line: its value, its label with the unit its bars measure
    ("avg 2h 36m" on the Day's day bars, " a day" on a Week's or Month's, " a
    month" on the Year's), and its height ("" / None without one). D41: Day,
    Week and Month all draw the same average day."""
    if n < AVERAGE_NEEDS[kind]:
        return {"mean_ms": 0, "mean_text": "", "mean_y": None}
    return {
        "mean_ms": mean,
        "mean_text": f"avg {fmt(mean)}{unit}",
        "mean_y": mean / max(1, y_max),
    }


# ---- the time scale (D43) -----------------------------------------------------
# One faint gridline per Week, Month and Year chart, at the largest round value
# below the chart's top with room for its label; the hourly chart's scale is
# fixed. The engine picks every tick and its y; QML paints them.

TICK_STEPS_MS = [15 * 60_000, 30 * 60_000] + [
    int(h * 3_600_000) for h in (1, 2, 3, 4, 5) + tuple(m * 10**k for k in range(1, 4) for m in (1, 1.5, 2, 2.5, 3, 5))
]  # fmt: skip
TICK_ROOM = 0.9  # the top gridline at most 90 % up: its label never meets the top
TICK_CLEAR = 0.08  # no gridline within 8 % of the chart's height of the dashed average


def scale_ticks(y_max, mean_y=None):
    """D44: two gridlines, step and 2 x step, the largest pair whose top line is at
    most 90 % up and neither line within 8 % of the dashed average (else the next
    smaller pair); [] when no pair fits."""
    for step in sorted(TICK_STEPS_MS, reverse=True):
        ys = [step / y_max, 2 * step / y_max] if y_max > 0 else []
        if not ys or ys[1] > TICK_ROOM:
            continue
        if mean_y is not None and any(abs(y - mean_y) <= TICK_CLEAR for y in ys):
            continue
        return [{"ms": step, "text": fmt(step), "y": ys[0]},
                {"ms": 2 * step, "text": fmt(2 * step), "y": ys[1]}]  # fmt: skip
    return []


# ---- the hourly chart (D42) ----------------------------------------------------

HOUR_MS = 3_600_000
HOUR_LABELS = {0: "12a", 6: "6a", 12: "12p", 18: "6p"}
# D43: the hourly chart's fixed scale, 0..60 min: gridlines at 30m and 1h
HOUR_TICKS = [
    {"ms": 1_800_000, "text": "30m", "y": 0.5},
    {"ms": 3_600_000, "text": "1h", "y": 1.0},
]


def whole_hours(hist, totals, key):
    """The day's 24 hourly totals when it was recorded whole (sum == its total),
    else None. A day with no time at all is whole: 24 zeros."""
    rec = hist["days"].get(key)
    total = totals.get(key, 0)
    hours = rec.get("hours") if rec else None
    if hours is None:
        return [0] * 24 if total == 0 else None
    return hours if sum(hours) == total else None


def first_whole_day(hist, totals, today):
    """The first day recorded whole with time, or tomorrow when there is none."""
    whole = [
        k
        for k, rec in hist["days"].items()
        if parse_day(k) <= today
        and rec.get("hours") is not None
        and whole_hours(hist, totals, k) is not None
    ]
    return parse_day(min(whole)) if whole else today + datetime.timedelta(days=1)  # fmt: skip


def partial_hours(hist, totals, key):
    """D48: the day's 24 hourly totals when they hold only part of the day (time
    by hour, but less than its total: the install day, or hours lost earlier),
    else None."""
    rec = hist["days"].get(key)
    hours = rec.get("hours") if rec else None
    if hours is None:
        return None
    return hours if 0 < sum(hours) < totals.get(key, 0) else None


def hour_name(i):
    """D48: hour i (0-23) as "12 AM", "9 AM", "12 PM", "6 PM"."""
    return f"{(i + 11) % 12 + 1} {'AM' if i < 12 else 'PM'}"


def hour_chart(hist, totals, d, today, now_hour):
    """The Day page's chart: 24 bars, 12 AM to 12 AM, on a fixed 0..60 min scale
    (the repeated fall-back hour, which can hold more, is drawn full). On today,
    hours before now are "past", this hour "current", later ones "future" (empty
    slots); a past day's are all "past". A day recorded in part draws the hours
    it has, captioned "hours from <its first hour with time>" (D48); a day with
    no usable hours has no bars: state "pending" and "hourly from <the first
    whole recorded day>"."""
    hours = whole_hours(hist, totals, day_key(d))
    shown, caption = "ok", ""
    if hours is None:
        hours = partial_hours(hist, totals, day_key(d))
        if hours is not None:
            first = next(i for i, ms in enumerate(hours) if ms > 0)
            shown, caption = "partial", f"hours from {hour_name(first)}"
    if hours is None:
        when = first_whole_day(hist, totals, today)
        return {"state": "pending", "text": f"hourly from {short_date(when, today)}", "bars": [],
                "y_max_ms": HOUR_MS, "ticks": [], "mean_ms": 0, "mean_text": "", "mean_y": None}  # fmt: skip
    bars = []
    for i, ms in enumerate(hours):
        if d < today or i < now_hour:
            state = "past"
        else:
            state = "current" if i == now_hour else "future"
        ms = ms if state != "future" else 0
        bars.append({"hour": i, "label": HOUR_LABELS.get(i, ""), "ms": ms,
                     "text": fmt(ms) if state != "future" else "", "x": i / 23,
                     "y": min(1, ms / HOUR_MS), "state": state})  # fmt: skip
    return {"state": shown, "text": caption, "bars": bars, "y_max_ms": HOUR_MS,
            "ticks": [dict(t) for t in HOUR_TICKS], "mean_ms": 0, "mean_text": "", "mean_y": None}  # fmt: skip


# ---- totals and the year page --------------------------------------------------


def week_days(today):
    """Monday of today's week through today (Mon-Sun weeks)."""
    monday = today - datetime.timedelta(days=today.weekday())
    return [monday + datetime.timedelta(days=i) for i in range(today.weekday() + 1)]


def year_months(totals, lumps, year):
    """Twelve month totals: days and archive plus legacy month lumps, the same
    disjoint sum as upstream's mergeYear."""
    months = [0] * 12
    prefix = f"{year}-"
    for k, ms in totals.items():
        if k.startswith(prefix):
            months[int(k[5:7]) - 1] += ms
    for k, ms in lumps.items():
        if k.startswith(prefix):
            months[int(k[5:7]) - 1] += ms
    return months


def first_day(totals, lumps):
    """The earliest tracked day; a legacy month lump counts from its 1st."""
    firsts = list(totals) + [k + "-01" for k in lumps]
    return parse_day(min(firsts)) if firsts else None


def short_date(d, today):
    text = f"{MONTHS[d.month - 1]} {d.day}"
    return text if d.year == today.year else f"{text}, {d.year}"


def footer(totals, lumps, today):
    week = sum(totals.get(day_key(d), 0) for d in week_days(today))
    all_time = sum(totals.values()) + sum(lumps.values())
    first = first_day(totals, lumps)
    since = f"since {short_date(first, today)}" if first else ""
    return {
        "week_ms": week,
        "week_text": fmt(week),
        "all_time_ms": all_time,
        "all_time_text": fmt(all_time),
        "since_key": day_key(first) if first else "",
        "since_text": since,
        "text": f"all-time {fmt(all_time)} {since}".rstrip(),
    }


def year_bars(months, year, today, first, scale=0):
    """Twelve month slots, January first. Months after this month are "future"
    and months before the first tracked month "before": empty slots."""
    slots = []
    for m in range(12):
        if (year, m + 1) > (today.year, today.month):
            state = "future"
        elif first is None or (year, m + 1) < (first.year, first.month):
            state = "before"
        else:
            state = "current" if (year, m + 1) == (today.year, today.month) else "past"
        ms = months[m] if state in ("past", "current") else 0
        slots.append((m, state, ms))
    y_max = max([ms for _, _, ms in slots] + [scale])
    bars = [
        {
            "label": MONTHS[m],
            "ms": ms,
            "text": fmt(ms) if state in ("past", "current") else "",
            "x": m / 11,
            "y": ms / max(1, y_max),
            "state": state,
        }
        for m, state, ms in slots
    ]
    return bars, y_max


def year_line(year, today, first, install, total, totals):
    """The daily average over the year's completed days: January 1 (or the
    first tracked day) to December 31 (or yesterday). Today counts only when it
    is the only day (audit C5: a half-done day pulls an average down), and so
    does the install day, the first tracked day (D47, as D45)."""
    start = datetime.date(year, 1, 1)
    end = min(datetime.date(year, 12, 31), today)
    if first is not None and first > start:
        start = first
    days = (end - start).days + 1 if first is not None and start <= end else 0
    covered = days
    if end == today and days > 1:
        total, days = total - totals.get(day_key(today), 0), days - 1
    if install is not None and start <= install <= end and install < today and days > 1:
        total, days = total - totals.get(day_key(install), 0), days - 1
        if install == start:
            start = install + datetime.timedelta(days=1)
    avg = js_round(total / days) if days > 0 else 0
    starts_late = covered > 0 and start != datetime.date(year, 1, 1)
    since = f" since {short_date(start, today)}" if starts_late else ""
    return avg, {
        "state": "",
        "text": f"{fmt(avg)} a day{since}",
        "days": days,
    }


def segments_of(rows, total):
    """The segmented bar: every row's share of the total; [] for an empty period."""
    if total <= 0:
        return []
    return [
        {"name": r["name"], "swatch": r["swatch"], "weight": r["value_ms"] / total}
        for r in rows
    ]


def apps_of(hist, days):
    apps = {}
    for d in days:
        for key, ms in day_apps(hist, d).items():
            apps[key] = apps.get(key, 0) + ms
    return apps


def average_of(totals, counted, today, total):
    """The daily average over the completed counted days; today only when it is
    the only one (audit C5: a half-done day pulls an average down)."""
    done = [d for d in counted if d < today]
    if done:
        today_ms = totals.get(day_key(today), 0) if today in counted else 0
        return js_round((total - today_ms) / len(done))
    return js_round(total / len(counted)) if counted else 0


# ---- pages (V1, schema 5). The card holds this day, week, month and year; any
# other is one `card --page kind:key` run. ‹ › are the neighbours' keys, back to
# the first tracked day and never past today.

PAGE_KINDS = ("day", "week", "month", "year")
YEAR_KEY_RE = re.compile(r"^\d{4}$")


def first_tracked(totals):
    """The earliest day with time (Day and Week go back to it)."""
    return parse_day(min(totals)) if totals else None


def year_page(hist, totals, lumps, y, today, first, names, palette, periods):
    """One Year page (D19b, the Week's layout): bars, line, rows. `first` counts
    legacy month lumps from their 1st (first_day). Its dashed line is the
    all-time average month (D38); its line is unchanged, the daily average."""
    months = year_months(totals, lumps, y)
    total = sum(months)
    mean, n = average(periods["month"], None)
    has_mean = n >= AVERAGE_NEEDS["month"]
    bars, y_max = year_bars(months, y, today, first, mean if has_mean else 0)
    avg, line = year_line(y, today, first, first_tracked(totals), total, totals)
    days = [k for k in hist["days"] if k.startswith(f"{y}-") and parse_day(k) <= today]
    rows = fold_rows(apps_of(hist, [parse_day(k) for k in days]), total, names, palette)
    label = (
        "this year"
        if y == today.year
        else "last year"
        if y == today.year - 1
        else "year"
    )
    return {
        "key": str(y),
        "year": y,
        "label": label,
        "total_ms": total,
        "total_text": fmt(total),
        "avg_ms": avg,
        "line": line,
        "bars": bars,
        "y_max_ms": y_max,
        "y_max_text": fmt(y_max) if y_max > 0 else "",
        **mean_fields(mean, n, "month", y_max, " a month"),
        "ticks": scale_ticks(y_max, mean_fields(mean, n, "month", y_max)["mean_y"]),
        "rows": rows,
        "segments": segments_of(rows, total),
        "prev": str(y - 1) if first is not None and first.year < y else None,
        "next": str(y + 1) if y < today.year else None,
    }  # fmt: skip


# ---- the week page (D19) -------------------------------------------------------

WEEK_LETTERS = ["M", "T", "W", "T", "F", "S", "S"]


def monday_of(d):
    return d - datetime.timedelta(days=d.weekday())


def week_range_text(monday, today):
    sunday = monday + datetime.timedelta(days=6)
    end = (
        str(sunday.day)
        if sunday.month == monday.month
        else f"{MONTHS[sunday.month - 1]} {sunday.day}"
    )
    text = f"{MONTHS[monday.month - 1]} {monday.day} – {end}"
    return text if sunday.year == today.year else f"{text}, {sunday.year}"


def week_bars(totals, monday, today, first, scale=0):
    """Seven slots, Monday first. Days after today are "future" and days before
    the first tracked day "before": empty slots, never a bar."""
    slots = []
    for i in range(7):
        d = monday + datetime.timedelta(days=i)
        if d > today:
            state = "future"
        elif first is None or d < first:
            state = "before"
        else:
            state = "today" if d == today else "past"
        ms = totals.get(day_key(d), 0) if state in ("past", "today") else 0
        slots.append((i, d, state, ms))
    y_max = max([ms for _, _, _, ms in slots] + [scale])
    bars = [
        {
            "key": day_key(d),
            "label": WEEK_LETTERS[i],
            "ms": ms,
            "text": fmt(ms) if state in ("past", "today") else "",
            "x": i / 6,
            "y": ms / max(1, y_max),
            "state": state,
        }
        for i, d, state, ms in slots
    ]
    return bars, y_max


def week_page(hist, totals, monday, today, first, names, palette, periods):
    """One Week page. The line: the week's daily average, then the week against
    the all-time average week (D38); this week over Monday..today, "by Wed"."""
    days = [monday + datetime.timedelta(days=i) for i in range(7)]
    counted = [d for d in days if d <= today and first is not None and d >= first]
    total = sum(totals.get(day_key(d), 0) for d in counted)
    rows = fold_rows(apps_of(hist, counted), total, names, palette)
    avg = average_of(totals, counted, today, total)
    key = day_key(monday)
    now = monday == monday_of(today)
    mean, n = average(periods["week"], key, today.weekday() + 1 if now else None)
    by = f" by {WEEKDAYS[today.weekday()]}" if now else ""
    line = average_line("week", total, mean, n, now, by, today, first)
    # the dashed line: the one average day, as the Day page's (D41)
    day_mean, day_n = average(periods["day"], None)
    has_mean = day_n >= AVERAGE_NEEDS["day"]
    bars, y_max = week_bars(totals, monday, today, first, day_mean if has_mean else 0)
    current = monday_of(today)
    week = datetime.timedelta(days=7)
    label = (
        "this week" if monday == current
        else "last week" if monday == current - week
        else "week"
    )  # fmt: skip
    return {
        "key": day_key(monday),
        "range_text": week_range_text(monday, today),
        "label": label,
        "total_ms": total,
        "total_text": fmt(total),
        "avg_ms": avg,
        "line": line,
        "bars": bars,
        "y_max_ms": y_max,
        "y_max_text": fmt(y_max) if y_max > 0 else "",
        **mean_fields(day_mean, day_n, "day", y_max, PER_DAY),
        "ticks": scale_ticks(y_max, mean_fields(day_mean, day_n, "day", y_max)["mean_y"]),
        "rows": rows,
        "segments": segments_of(rows, total),
        "prev": day_key(monday - week) if first is not None and first < monday else None,
        "next": day_key(monday + week) if monday < current else None,
    }  # fmt: skip


# ---- the month page (V1) -------------------------------------------------------

MONTH_LABELS = (1, 8, 15, 22, 29)  # the day numbers under the bars


def month_after(d):
    """The first of the month after d's."""
    return datetime.date(d.year + d.month // 12, d.month % 12 + 1, 1)


def month_before(d):
    """The first of the month before d's."""
    return datetime.date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)


def month_length(d):
    return (month_after(d) - d.replace(day=1)).days


def month_text(first_of, today):
    name = FULL_MONTHS[first_of.month - 1]
    return name if first_of.year == today.year else f"{name} {first_of.year}"


def month_bars(totals, first_of, today, first, scale=0):
    """One slot per day of the month. Days after today are "future" and days
    before the first tracked day "before": empty slots, never a bar."""
    n = month_length(first_of)
    slots = []
    for i in range(n):
        d = first_of + datetime.timedelta(days=i)
        if d > today:
            state = "future"
        elif first is None or d < first:
            state = "before"
        else:
            state = "today" if d == today else "past"
        ms = totals.get(day_key(d), 0) if state in ("past", "today") else 0
        slots.append((i, d, state, ms))
    y_max = max([ms for _, _, _, ms in slots] + [scale])
    bars = [
        {
            "key": day_key(d),
            "label": str(d.day) if d.day in MONTH_LABELS else "",
            "ms": ms,
            "text": fmt(ms) if state in ("past", "today") else "",
            "x": i / (n - 1),
            "y": ms / max(1, y_max),
            "state": state,
        }
        for i, d, state, ms in slots
    ]
    return bars, y_max


def month_total(totals, lumps, first_of, days):
    return sum(totals.get(day_key(d), 0) for d in days) + lumps.get(
        first_of.strftime("%Y-%m"), 0
    )


def month_page(hist, totals, lumps, first_of, today, first, names, palette, periods):
    """One Month page, the Week's layout: a bar per day of the month. `first`
    counts legacy month lumps from their 1st (first_day), so a lump month's days
    are counted (as zeros) and its lump lands in Other. The line: the month's
    daily average, then the month against the all-time average month (D38);
    this month over the 1st..today, "by Oct 6"."""
    n = month_length(first_of)
    days = [first_of + datetime.timedelta(days=i) for i in range(n)]
    counted = [d for d in days if d <= today and first is not None and d >= first]
    total = month_total(totals, lumps, first_of, counted)
    rows = fold_rows(apps_of(hist, counted), total, names, palette)
    avg = average_of(totals, counted, today, total)
    key = first_of.strftime("%Y-%m")
    now = first_of == today.replace(day=1)
    mean, n_used = average(periods["month"], key, today.day if now else None)
    by = f" by {MONTHS[today.month - 1]} {today.day}" if now else ""
    line = average_line("month", total, mean, n_used, now, by, today, first)
    # the dashed line: the one average day, as the Day page's (D41)
    day_mean, day_n = average(periods["day"], None)
    has_mean = day_n >= AVERAGE_NEEDS["day"]
    bars, y_max = month_bars(
        totals, first_of, today, first, day_mean if has_mean else 0
    )
    current = today.replace(day=1)
    label = (
        "this month" if first_of == current
        else "last month" if first_of == month_before(current)
        else "month"
    )  # fmt: skip
    return {
        "key": first_of.strftime("%Y-%m"),
        "range_text": month_text(first_of, today),
        "label": label,
        "total_ms": total,
        "total_text": fmt(total),
        "avg_ms": avg,
        "line": line,
        "bars": bars,
        "y_max_ms": y_max,
        "y_max_text": fmt(y_max) if y_max > 0 else "",
        **mean_fields(day_mean, day_n, "day", y_max, PER_DAY),
        "ticks": scale_ticks(y_max, mean_fields(day_mean, day_n, "day", y_max)["mean_y"]),
        "rows": rows,
        "segments": segments_of(rows, total),
        "prev": month_before(first_of).strftime("%Y-%m") if first is not None and first < first_of else None,
        "next": month_after(first_of).strftime("%Y-%m") if first_of < current else None,
    }  # fmt: skip


# ---- the day page (V1): Today's layout for any day -----------------------------


def day_page(hist, totals, d, today, first, names, palette, periods, now_hour):
    """Today's layout for any day. The line: the day against the all-time
    average day (D38); today, not over, compares so far with a whole day."""
    total = totals.get(day_key(d), 0)
    rows = fold_rows(day_apps(hist, d), total, names, palette)
    mean, n = average(periods["day"], day_key(d))
    line = average_line("day", total, mean, n, d == today, "", today, first)
    day_keys = [p["key"] for p in periods["day"] if p["key"] != day_key(d)]
    for r in rows:
        r["vs_text"] = row_vs_text(hist, r, day_keys, names, d == today)
    one = datetime.timedelta(days=1)
    label = "today" if d == today else "yesterday" if d == today - one else "day"
    return {
        "key": day_key(d),
        "label": label,
        "pill": pill(d) if d.year == today.year else f"{pill(d)}, {d.year}",
        "total_ms": total,
        "total_text": fmt(total),
        "line": line,
        "hours": hour_chart(hist, totals, d, today, now_hour),
        "rows": rows,
        "segments": segments_of(rows, total),
        "prev": day_key(d - one) if first is not None and d - one >= first else None,
        "next": day_key(d + one) if d < today else None,
    }  # fmt: skip


def all_periods(totals, lumps, today):
    """Every completed day, week and month the averages rest on (D38)."""
    first, first_all = first_tracked(totals), first_day(totals, lumps)
    return {
        "day": day_periods(totals, today),
        "week": week_periods(totals, today, first),
        "month": month_periods(totals, lumps, today, first_all),
    }


def page_for(hist, kind, key, today, names, palette=None, now_hour=0):
    """The page `kind:key`, or None when the key is not one, or names a period
    after today or wholly before the first tracked one (the arrows never offer
    either). Day and Week start at the first tracked day; Month and Year at the
    first day or legacy month lump (first_day)."""
    totals = daily_totals(hist, today)
    lumps = month_lumps(hist, today)
    first, first_all = first_tracked(totals), first_day(totals, lumps)
    periods = all_periods(totals, lumps, today)
    d = parse_day(key) if kind in ("day", "week") else None
    if kind == "day" and d is not None and first is not None and first <= d <= today:
        return day_page(
            hist, totals, d, today, first, names, palette, periods, now_hour
        )
    if kind == "week" and d is not None and first is not None and d <= today:
        monday = monday_of(d)
        if monday_of(first) <= monday:
            return week_page(
                hist, totals, monday, today, first, names, palette, periods
            )
    if (
        kind == "month"
        and MONTH_KEY_RE.match(key)
        and 1 <= int(key[5:]) <= 12
        and first_all is not None
    ):
        y, m = int(key[:4]), int(key[5:])
        if (first_all.year, first_all.month) <= (y, m) <= (today.year, today.month):
            first_of = datetime.date(y, m, 1)
            return month_page(
                hist, totals, lumps, first_of, today, first_all, names, palette, periods
            )
    year = int(key) if kind == "year" and YEAR_KEY_RE.match(key) else None
    if (
        year is not None
        and first_all is not None
        and first_all.year <= year <= today.year
    ):
        return year_page(
            hist, totals, lumps, int(key), today, first_all, names, palette, periods
        )
    return None  # fmt: skip


# ---- the card -----------------------------------------------------------------


def empty_card(state, now, message=None):
    return {
        "schema": SCHEMA,
        "state": state,
        "message": MESSAGES.get(state, "") if message is None else message,
        "as_of": now.isoformat(timespec="seconds"),
        "today": {
            "key": "",
            "total_ms": 0,
            "total_text": "",
            "label": "today",
            "pill": "",
        },
        "rows": [],
        "segments": [],
        "names": {"state": ""},
        "footer": {
            "week_ms": 0,
            "week_text": "",
            "all_time_ms": 0,
            "all_time_text": "",
            "since_key": "",
            "since_text": "",
            "text": "",
        },
        "day": None,
        "week": None,
        "month": None,
        "year": None,
        "palette": {
            "source": "",
            "reason": "",
            "keys": [],
            "swatches": [],
            "other": "",
            "surface": "",
        },
    }


def make_row(name, keys, ms, swatch, other):
    return {
        "name": name,
        "keys": sorted(keys),
        "value_ms": ms,
        "value_text": fmt(ms),
        "swatch": swatch,
        "other": other,
        "vs_text": "",
    }


def day_rows(hist, d, names, palette=None):
    return fold_rows(day_apps(hist, d), day_total(hist, d), names, palette)


def fold_rows(apps, total, names, palette=None):
    """Named rows largest first (at most MAX_ROWS), then Other. Hidden apps, apps
    under a minute and anything past MAX_ROWS fold into Other, which takes what
    the named rows leave of `total`: the rows always add up to it."""
    # this run's row colours (D30); without them, the fixed Monarch set
    swatches = palette["swatches"] if palette else PALETTE[:MAX_ROWS]
    other_swatch = palette["other"] if palette else OTHER_SWATCH
    by_label, keys_of, other_keys = {}, {}, []
    for key, ms in apps.items():
        if key in names.hidden:
            other_keys.append(key)
            continue
        label = names.label(key)
        by_label[label] = by_label.get(label, 0) + ms
        keys_of.setdefault(label, []).append(key)
    rows = []
    for label, ms in sorted(by_label.items(), key=lambda kv: (-kv[1], kv[0])):
        if ms >= MIN_ROW_MS and len(rows) < MAX_ROWS:
            rows.append(
                make_row(label, keys_of[label], ms, swatches[len(rows)], other=False)
            )
        else:
            other_keys.extend(keys_of[label])
    other = total - sum(r["value_ms"] for r in rows)
    if other > 0:
        rows.append(make_row(OTHER, other_keys, other, other_swatch, other=True))
    return rows


def build_card(hist, state, now, names_doc):
    if state != "ok":
        return empty_card(state, now)
    today = now.date()
    if not daily_totals(hist, today) and not month_lumps(hist, today):
        return empty_card("empty", now)
    card = empty_card("ok", now)
    total = day_total(hist, today)
    card["today"] = {
        "key": day_key(today),
        "total_ms": total,
        "total_text": fmt(total),
        "label": "today",
        "pill": pill(today),
    }
    names = Names(*names_doc, stored_keys(hist))
    palette = row_palette()
    card["palette"] = palette
    card["names"] = {"state": names.state}
    totals = daily_totals(hist, today)
    lumps = month_lumps(hist, today)
    card["footer"] = footer(totals, lumps, today)
    # this day, week, month and year only: any other page is one `--page` run
    first, first_all = first_tracked(totals), first_day(totals, lumps)
    periods = all_periods(totals, lumps, today)
    card["day"] = day_page(
        hist, totals, today, today, first, names, palette, periods, now.hour
    )
    card["week"] = week_page(
        hist, totals, monday_of(today), today, first, names, palette, periods
    )
    card["month"] = month_page(
        hist,
        totals,
        lumps,
        today.replace(day=1),
        today,
        first_all,
        names,
        palette,
        periods,
    )
    card["year"] = year_page(
        hist, totals, lumps, today.year, today, first_all, names, palette, periods
    )
    # the glance: today's rows and bar, the Day page's own
    card["rows"] = [dict(r) for r in card["day"]["rows"]]
    card["segments"] = [dict(sg) for sg in card["day"]["segments"]]
    return card  # fmt: skip


def page_doc(state, now, message=None, kind="", key="", page=None):
    return {
        "schema": SCHEMA,
        "state": state,
        "message": MESSAGES.get(state, "") if message is None else message,
        "as_of": now.isoformat(timespec="seconds"),
        "kind": kind,
        "key": key,
        "page": page,
    }


def build_page(hist, state, now, names_doc, spec):
    """`card --page kind:key`: one page, as the card's own (V1)."""
    if state != "ok":
        return page_doc(state, now)
    today = now.date()
    if not daily_totals(hist, today) and not month_lumps(hist, today):
        return page_doc("empty", now)
    kind, _, key = spec.partition(":")
    names = Names(*names_doc, stored_keys(hist))
    page = (
        page_for(hist, kind, key, today, names, row_palette(), now.hour)
        if kind in PAGE_KINDS
        else None
    )
    if page is None:
        return page_doc("error", now, f"Not a page: {spec}")
    return page_doc("ok", now, "", kind, page["key"], page)  # fmt: skip


# ---- text verbs ----------------------------------------------------------------


def report(hist, now, scope, names):
    today = now.date()
    totals = daily_totals(hist, today)
    if scope == "today":
        lines = [f"{pill(today)}  {fmt(day_total(hist, today))}"]
        for r in day_rows(hist, today, names):
            lines.append(f"  {r['name']:<28} {r['value_text']}")
        return lines
    lumps = month_lumps(hist, today)
    if scope == "week":
        days = week_days(today)
        lines = [
            f"Week of {pill(days[0])}  {footer(totals, lumps, today)['week_text']}"
        ]
        for d in days:
            lines.append(f"  {pill(d):<12} {fmt(totals.get(day_key(d), 0))}")
        return lines
    if scope == "year":
        months = year_months(totals, lumps, today.year)
        lines = [f"{today.year}  {fmt(sum(months))}"]
        for i, ms in enumerate(months[: today.month]):
            lines.append(f"  {MONTHS[i]} {fmt(ms)}")
        return lines
    # all
    foot = footer(totals, lumps, today)
    years = sorted(
        {int(k[:4]) for k in totals} | {int(k[:4]) for k in lumps} | {today.year}
    )
    lines = [f"All time  {foot['all_time_text']}  {foot['since_text']}".rstrip()]
    for y in years:
        lines.append(f"  {y}  {fmt(sum(year_months(totals, lumps, y)))}")
    return lines  # fmt: skip


# ---- the audit report (D19b) ---------------------------------------------------
# Every app, no cap, sub-minute included; hidden apps flagged, never dropped.
# Time with no app detail (archive days, legacy month lumps, a day's untracked
# remainder) is its own row, so the rows always add up to the total exactly.

FULL_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]  # fmt: skip
NO_DETAIL = "(no app detail)"
ROUNDED = "Times are rounded to the minute; exact values: `screen-time report --json`."


def audit_day_label(d):
    return f"{WEEKDAYS[d.weekday()]} {MONTHS[d.month - 1]} {d.day}, {d.year}"


def audit_week_label(monday):
    sunday = monday + datetime.timedelta(days=6)
    if monday.year != sunday.year:
        return (
            f"{MONTHS[monday.month - 1]} {monday.day}, {monday.year} – "
            f"{MONTHS[sunday.month - 1]} {sunday.day}, {sunday.year}"
        )
    return f"{week_range_text(monday, sunday)}, {sunday.year}"


def audit_rows(hist, days, names, total):
    """One row per (label, hidden), largest first; then the no-detail row."""
    by = {}
    for d in days:
        for key, ms in day_apps(hist, d).items():
            hidden = key in names.hidden
            name = names.label(key)
            row = by.setdefault(
                (name, hidden), {"name": name, "keys": [], "ms": 0, "hidden": hidden}
            )
            row["ms"] += ms
            if key not in row["keys"]:
                row["keys"].append(key)
    rows = sorted(by.values(), key=lambda r: (-r["ms"], r["name"], r["hidden"]))
    for r in rows:
        r["keys"].sort()
        r["text"] = fmt(r["ms"])
        r["detail"] = True
    rest = total - sum(r["ms"] for r in rows)
    if rest > 0:
        rows.append({"name": NO_DETAIL, "keys": [], "ms": rest, "text": fmt(rest),
                     "hidden": False, "detail": False})  # fmt: skip
    return rows


def audit_period(hist, names, today, kind, start, end, label, by_day=False):
    """One period's audit: its days up to today, plus month lumps for a month or
    a year (a lump is a whole month, so a day or a week never takes one).
    by_day (a month or a year): every day up to today, with its own rows
    adding up to its own total; legacy month lumps have no day, so they are
    `undated_ms` and the days plus it make the period's total."""
    days = []
    d = start
    while d <= end and d <= today:
        days.append(d)
        d += datetime.timedelta(days=1)
    totals = daily_totals(hist, today)
    lumps = month_lumps(hist, today) if kind in ("month", "year") else {}
    lump_ms = sum(
        ms for k, ms in lumps.items() if day_key(start)[:7] <= k <= day_key(end)[:7]
    )
    total = sum(totals.get(day_key(x), 0) for x in days) + lump_ms
    out = {
        "kind": kind,
        "label": label,
        "start": day_key(start),
        "end": day_key(end),
        "total_ms": total,
        "total_text": fmt(total),
        "rows": audit_rows(hist, days, names, total),
    }
    if kind in ("week", "month"):
        out["days"] = [
            {"key": day_key(x), "label": audit_day_label(x),
             "ms": totals.get(day_key(x), 0), "text": fmt(totals.get(day_key(x), 0))}
            for x in days
        ]  # fmt: skip
    if by_day:
        out["by_day"] = [
            {"key": day_key(x), "label": audit_day_label(x),
             "total_ms": totals.get(day_key(x), 0),
             "total_text": fmt(totals.get(day_key(x), 0)),
             "rows": audit_rows(hist, [x], names, totals.get(day_key(x), 0))}
            for x in days
        ]  # fmt: skip
        out["undated_ms"] = lump_ms
        out["undated_text"] = fmt(lump_ms) if lump_ms else ""
    if kind == "year":
        months = year_months(totals, lumps, start.year)
        last = 12 if start.year < today.year else today.month
        out["months"] = [
            {"key": f"{start.year}-{m + 1:02d}", "label": FULL_MONTHS[m],
             "ms": months[m], "text": fmt(months[m])}
            for m in range(last)
        ]  # fmt: skip
    return out


def audit_for(hist, names, today, kind, value, by_day=False):
    start = (
        parse_day(value + "-01" if kind == "month" else value)
        if kind != "year"
        else None
    )
    if kind != "year" and start is None:
        raise ValueError(value)
    if kind == "day":
        d = start
        return audit_period(hist, names, today, "day", d, d, audit_day_label(d))
    if kind == "week":
        monday = monday_of(start)
        sunday = monday + datetime.timedelta(days=6)
        return audit_period(
            hist, names, today, "week", monday, sunday, audit_week_label(monday)
        )
    if kind == "month":
        first = start
        nxt = datetime.date(first.year + first.month // 12, first.month % 12 + 1, 1)
        last = nxt - datetime.timedelta(days=1)
        label = f"{FULL_MONTHS[first.month - 1]} {first.year}"
        return audit_period(hist, names, today, "month", first, last, label, by_day)
    y = int(value)
    return audit_period(
        hist,
        names,
        today,
        "year",
        datetime.date(y, 1, 1),
        datetime.date(y, 12, 31),
        str(y),
        by_day,
    )


def audit_all(hist, names, now):
    """The whole picture: today, this week, this year, all-time."""
    today = now.date()
    totals = daily_totals(hist, today)
    lumps = month_lumps(hist, today)
    foot = footer(totals, lumps, today)
    periods = all_periods(totals, lumps, today)
    firsts = {
        "day": first_tracked(totals),
        "week": first_tracked(totals),
        "month": first_day(totals, lumps),
    }
    averages = {}
    for kind in ("day", "week", "month"):
        mean, n = average(periods[kind], None)
        enough = n >= AVERAGE_NEEDS[kind]
        starts = None if enough else average_starts(kind, n, today, firsts[kind])
        averages[kind] = {
            "ms": mean if enough else 0,
            "text": fmt(mean) if enough else "",
            "periods": n,
            "starts_key": day_key(starts) if starts else "",
            "starts_text": short_date(starts, today) if starts else "",
        }
    return {
        "generated": now.isoformat(timespec="seconds"),
        "today": audit_for(hist, names, today, "day", day_key(today)),
        "week": audit_for(hist, names, today, "week", day_key(today)),
        "year": audit_for(hist, names, today, "year", str(today.year)),
        "all_time": {
            "total_ms": foot["all_time_ms"],
            "total_text": foot["all_time_text"],
            "since_key": foot["since_key"],
            "since_text": foot["since_text"],
            # D38: the card's baselines, every completed day, week and month
            "averages": averages,
        },
    }


def audit_row_name(r):
    return f"{r['name']} (hidden)" if r["hidden"] else r["name"]


def audit_text(p):
    lines = [f"{p['label']}  {p['total_text']}"]
    for sub in p.get("days", []) + p.get("months", []):
        lines.append(f"  {sub['label']:<22} {sub['text']}")
    if p.get("days") or p.get("months"):
        lines.append("Apps")
    for r in p["rows"]:
        lines.append(f"  {audit_row_name(r):<34} {r['text']}")
    if "by_day" in p:
        lines.append("By day")
        for d in p["by_day"]:
            lines.append(f"  {d['label']}  {d['total_text']}")
            for r in d["rows"]:
                lines.append(f"    {audit_row_name(r):<32} {r['text']}")
        if p["undated_ms"]:
            lines.append(f"  Without a day (legacy month totals)  {p['undated_text']}")
    return lines


def md_cell(text):
    return text.replace("|", "\\|")


def audit_md_tables(p):
    out = []
    breakdown = p.get("days") or p.get("months")
    if breakdown:
        head = "Day" if p.get("days") else "Month"
        out += [f"| {head} | Time |", "|---|---|"]
        out += [f"| {md_cell(x['label'])} | {x['text']} |" for x in breakdown]
        out.append("")
    out += ["| App | Time |", "|---|---|"]
    out += [f"| {md_cell(audit_row_name(r))} | {r['text']} |" for r in p["rows"]]
    out.append(f"| **Total** | **{p['total_text']}** |")
    if "by_day" in p:
        out += ["", "## By day", ""]
        for d in p["by_day"]:
            out += [f"### {md_cell(d['label'])} · {d['total_text']}", ""]
            if not d["rows"]:
                out += ["No time tracked.", ""]
                continue
            out += ["| App | Time |", "|---|---|"]
            out += [
                f"| {md_cell(audit_row_name(r))} | {r['text']} |" for r in d["rows"]
            ]
            out += [f"| **Total** | **{d['total_text']}** |", ""]
        if p["undated_ms"]:
            out.append(f"Without a day (legacy month totals): {p['undated_text']}")
    return out


def audit_md(doc, now, title):
    note = (
        f"Generated {now.strftime('%Y-%m-%d %H:%M')} (local time). Focused time per "
        "app; hidden apps are marked, and each table's rows add up to its total."
    )
    lines = [f"# Screen time — {title}", "", note, ROUNDED, ""]
    if "kind" in doc:
        return lines + audit_md_tables(doc)
    sections = [
        ("Today", doc["today"]),
        ("This week", doc["week"]),
        ("This year", doc["year"]),
    ]
    for name, p in sections:
        lines += [f"## {name} — {p['label']} · {p['total_text']}", ""]
        lines += audit_md_tables(p) + [""]
    a = doc["all_time"]
    lines += [f"## All-time — {a['total_text']} {a['since_text']}".rstrip(), ""]
    return lines + [f"- {line}" for line in average_lines(a["averages"])]


def average_lines(averages):
    """ "Average day: 2h 37m (12 days)", or "Average week: starts Oct 19"."""
    out = []
    for kind in ("day", "week", "month"):
        a = averages[kind]
        if a["text"]:
            out.append(
                f"Average {kind}: {a['text']} ({a['periods']} {AVERAGE_UNITS[kind]})"
            )
        else:
            out.append(f"Average {kind}: starts {a['starts_text']}")
    return out


def keys_table(hist, now, days, names):
    today = now.date()
    sums = {}
    for i in range(days):
        for k, ms in day_apps(hist, today - datetime.timedelta(days=i)).items():
            sums[k] = sums.get(k, 0) + ms
    # Columns are separated by two or more spaces: key, time, label, source
    # (rename | builtin | desktop | fallback), then notes ("case" for a
    # case-insensitive rename, "hidden").
    out = []
    for k, ms in sorted(sums.items(), key=lambda kv: (-kv[1], kv[0])):
        label, source = names.resolve(k)
        notes = []
        if source == "rename" and names.renamed[k][1] == "case":
            notes.append("case")
        if k in names.hidden:
            notes.append("hidden")
        line = f"{k:<32}  {fmt(ms):>10}  {label:<20}  {source:<8}  {' '.join(notes)}"
        out.append(line.rstrip())
    return out


def log_line(hist, date_str, names):
    d = parse_day(date_str)
    if d is None:
        raise ValueError("--date must be YYYY-MM-DD")
    rows = day_rows(hist, d, names)
    parts = [f"{r['name']} {r['value_text']}" for r in rows]
    tail = (" · " + ", ".join(parts)) if parts else ""
    return f"- {day_key(d)} {WEEKDAYS[d.weekday()]} · {fmt(day_total(hist, d))}{tail}"


# ---- CLI ------------------------------------------------------------------------


def audit_report(ap, args, hist, now, names):
    """`report`: the old scopes as before, or the audit report (D19b) when a
    period or a format is asked for."""
    kinds = [k for k in ("day", "week", "month", "year") if getattr(args, k)]
    if args.by_day and (not kinds or kinds[0] not in ("month", "year")):
        ap.error("--by-day goes with --month or --year")
    if not kinds and not (args.json or args.md):
        return report(hist, now, args.scope or "today", names)
    today = now.date()
    try:
        if kinds:
            value = getattr(args, kinds[0])
            if kinds[0] == "month" and not MONTH_KEY_RE.match(value):
                raise ValueError(value)
            if kinds[0] == "year" and not re.fullmatch(r"\d{4}", value):
                raise ValueError(value)
            doc = audit_for(hist, names, today, kinds[0], value, args.by_day)
            title = doc["label"] + (", by day" if args.by_day else "")
        elif args.scope in ("today", "week", "year"):
            kind = "day" if args.scope == "today" else args.scope
            value = str(today.year) if kind == "year" else day_key(today)
            doc = audit_for(hist, names, today, kind, value)
            title = doc["label"]
        else:
            doc = audit_all(hist, names, now)
            title = "the whole picture"
    except ValueError as err:
        ap.error(f"not a valid period: {err}")
    if args.json:
        return [json.dumps(doc, ensure_ascii=False)]
    if args.md:
        return audit_md(doc, now, title)
    return (
        audit_text(doc)
        if "kind" in doc
        else [
            line
            for p in (doc["today"], doc["week"], doc["year"])
            for line in audit_text(p) + [""]
        ]
        + [
            f"All-time  {doc['all_time']['total_text']}  {doc['all_time']['since_text']}".rstrip()
        ]
        + [f"  {line}" for line in average_lines(doc["all_time"]["averages"])]
    )


def main(argv=None):
    ap = argparse.ArgumentParser(prog="screen_time.py", add_help=True)
    ap.add_argument("verb", choices=["card", "report", "keys", "log"])
    ap.add_argument("scope", nargs="?", choices=["today", "week", "year", "all"])
    ap.add_argument("--history", default=DEFAULT_HISTORY)
    ap.add_argument("--names", default=DEFAULT_NAMES)
    ap.add_argument("--as-of", dest="as_of")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--date")
    # the audit report (D19b): one period, and a format
    period = ap.add_mutually_exclusive_group()
    period.add_argument("--day", metavar="YYYY-MM-DD")
    period.add_argument(
        "--week", metavar="YYYY-MM-DD", help="the Mon-Sun week holding it"
    )
    period.add_argument("--month", metavar="YYYY-MM")
    period.add_argument("--year", metavar="YYYY")
    ap.add_argument("--page", metavar="KIND:KEY",
                    help="card: one page, e.g. day:2026-10-05, week:2026-09-28, month:2026-09, year:2025")  # fmt: skip
    ap.add_argument("--by-day", dest="by_day", action="store_true",
                    help="with --month or --year: every day and its apps")  # fmt: skip
    form = ap.add_mutually_exclusive_group()
    form.add_argument("--json", action="store_true")
    form.add_argument("--md", action="store_true")
    args = ap.parse_args(argv)

    if args.verb == "card":
        try:
            now = parse_now(args.as_of)
        except ValueError:
            now = local_now()
        try:
            hist, state = load_history(args.history)
            if args.page is not None:
                card = build_page(hist, state, now, load_names(args.names), args.page)
            else:
                card = build_card(hist, state, now, load_names(args.names))
        except CARD_FAILURES:  # the card must always be a document
            card = (
                page_doc("error", now)
                if args.page is not None
                else empty_card("error", now)
            )
        print(json.dumps(card, ensure_ascii=False))
        return 0

    now = parse_now(args.as_of)
    hist, state = load_history(args.history)
    if state != "ok":
        print(MESSAGES[state])
        return 1
    names = Names(*load_names(args.names), stored_keys(hist))
    if args.verb == "report":
        lines = audit_report(ap, args, hist, now, names)
    elif args.verb == "keys":
        lines = keys_table(hist, now, max(1, args.days), names)
    else:
        if not args.date:
            ap.error("log needs --date YYYY-MM-DD")
        lines = [log_line(hist, args.date, names)]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
