#!/usr/bin/env python3
"""Mutation run for the screen-time engine (every mutant must fail the oracle).

    tools/mutants.py                 every mutant against tests/oracle_screen_time.py

Each mutant is ONE defect: an exact-text replacement in a copy of
python/screen_time.py under a temp dir. The oracle runs against the copy and
must report at least one failure (the mutant is "killed"). A mutant whose
source text does not match exactly once is an ERROR, never a survivor, and so
is an oracle run that ends without its JSON verdict line (an instrument fault
must not pass for a kill). Exit 1 on any survivor or error. Mutants run in
parallel (one oracle process each, SCREEN_TIME_MUTANT_JOBS at a time, default the
CPU count); the report keeps the list's order.
"""

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "python", "screen_time.py")
ORACLE = os.path.join(ROOT, "tests", "oracle_screen_time.py")

# (name, old text, new text). The first thirteen are the original list.
MUTANTS = [
    (
        "sub-minute apps dropped (rows stop adding up)",
        '    other = total - sum(r["value_ms"] for r in rows)\n',
        (
            '    other = total - sum(r["value_ms"] for r in rows)\n'
            "    other -= sum(ms for ms in by_label.values() if ms < MIN_ROW_MS)\n"
        ),
    ),
    (
        "Other wrong: it ignores the stored total's remainder",
        '    other = total - sum(r["value_ms"] for r in rows)\n',
        "    other = sum(apps[k] for k in other_keys)\n",
    ),
    (
        "archive missing from all-time (and everywhere)",
        '    for k, ms in hist["years"].items():\n        if parse_day(k) <= today:\n',
        "    for k, ms in {}.items():\n        if parse_day(k) <= today:\n",
    ),
    (
        "months counted twice",
        "    all_time = sum(totals.values()) + sum(lumps.values())\n",
        "    all_time = sum(totals.values()) + 2 * sum(lumps.values())\n",
    ),
    (
        "rename case collision (case-insensitive without the one-key rule)",
        "        if len(hits) == 1 and hits[0] not in out:\n",
        "        if hits and hits[0] not in out:\n",
    ),
    (
        "hide changes the total",
        '    total = day_total(hist, today)\n    card["today"] = {\n',
        (
            "    total = day_total(hist, today) - sum(\n"
            "        day_apps(hist, today).get(k, 0)\n"
            "        for k in Names(*names_doc, stored_keys(hist)).hidden\n"
            '    )\n    card["today"] = {\n'
        ),
    ),
    (
        "midnight key taken from UTC, not local time",
        "        return empty_card(state, now)\n    today = now.date()\n",
        "        return empty_card(state, now)\n    today = (now + datetime.timedelta(hours=5)).date()\n",
    ),
    (
        "future days counted",
        '        if parse_day(k) <= today and rec["total"] > 0:\n',
        '        if rec["total"] > 0:\n',
    ),
    # ---- beyond the original list ----
    (
        "future month lumps counted",
        '    return {k: ms for k, ms in hist["months"].items() if k <= cutoff}\n',
        '    return dict(hist["months"])\n',
    ),
    (
        "overflow past six rows not folded into Other",
        "        if ms >= MIN_ROW_MS and len(rows) < MAX_ROWS:\n",
        "        if ms >= MIN_ROW_MS:\n",
    ),
    (
        "hide ignored",
        "        self.hidden = match_keys(dict.fromkeys(hide, True), stored)\n",
        "        self.hidden = {}\n",
    ),
    (
        "case-insensitive rename never applies",
        "        hits = by_lower.get(entry.lower(), [])\n",
        "        hits = by_lower.get(entry, [])\n",
    ),
    (
        "a case-insensitive rename beats an exact one",
        "        if len(hits) == 1 and hits[0] not in out:\n",
        "        if len(hits) == 1:\n",
    ),
    (
        "keys sharing a label not summed",
        "        by_label[label] = by_label.get(label, 0) + ms\n",
        "        by_label[label] = ms\n",
    ),
    (
        "the minute floor at 30 s",
        "MIN_ROW_MS = 60_000\n",
        "MIN_ROW_MS = 30_000\n",
    ),
    (
        "at the band's edge reads above",
        "    if abs(now_ms - average_ms) <= band:\n",
        "    if abs(now_ms - average_ms) < band:\n",
    ),
    (
        "no five-minute floor on the band",
        "    band = max(BAND_FLOOR_MS, BAND_SHARE * average_ms)\n",
        "    band = BAND_SHARE * average_ms\n",
    ),
    (
        "the week leaves out today",
        "    return [monday + datetime.timedelta(days=i) for i in range(today.weekday() + 1)]\n",
        "    return [monday + datetime.timedelta(days=i) for i in range(today.weekday())]\n",
    ),
    (
        "since ignores legacy month lumps",
        '    firsts = list(totals) + [k + "-01" for k in lumps]\n',
        "    firsts = list(totals)\n",
    ),
    (
        "year page: › on this year",
        '        "next": str(y + 1) if y < today.year else None,\n',
        '        "next": str(y + 1),\n',
    ),
    # ---- D17: default app names ----
    (
        "D17 built-in and desktop swapped back (claude takes a desktop Name)",
        '        if key in CURATED_NAMES:\n            return CURATED_NAMES[key], "builtin"\n        name = desktop_name(key, self.desktop())\n        if name:\n            return name, "desktop"\n',
        '        name = desktop_name(key, self.desktop())\n        if name:\n            return name, "desktop"\n        if key in CURATED_NAMES:\n            return CURATED_NAMES[key], "builtin"\n',
    ),
    (
        "D17 precedence swapped: a desktop entry beats names.json rename",
        '        hit = self.renamed.get(key)\n        if hit:\n            return hit[0], "rename"\n',
        '        name = desktop_name(key, self.desktop())\n        if name:\n            return name, "desktop"\n        hit = self.renamed.get(key)\n        if hit:\n            return hit[0], "rename"\n',
    ),
    (
        "D17 legacy defaults before desktop (google-chrome reads Chrome)",
        '        name = desktop_name(key, self.desktop())\n        if name:\n            return name, "desktop"\n        if key in LEGACY_NAMES:\n            return LEGACY_NAMES[key], "builtin"\n',
        '        if key in LEGACY_NAMES:\n            return LEGACY_NAMES[key], "builtin"\n        name = desktop_name(key, self.desktop())\n        if name:\n            return name, "desktop"\n',
    ),
    (
        "D17 guess on ambiguity (first Name of several)",
        "        if len(names) == 1:\n            return names.pop()\n",
        "        if names:\n            return sorted(names)[0]\n",
    ),
    (
        "D17 Hidden=true ignored",
        "        if e[0] and not e[2]\n",
        "        if e[0]\n",
    ),
    (
        "D17 localized Name[xx] used",
        '        if k == "Name" and name is None:\n',
        '        if (k == "Name" or k.startswith("Name[")) and name is None:\n',
    ),
    (
        "D17 later dirs override earlier ones",
        "                if fid in claimed:\n                    continue\n",
        "",
    ),
    (
        "D17 a malformed file claims its id (hides the system copy)",
        "                if entry is not None:\n                    claimed[fid] = entry\n",
        "                claimed[fid] = entry or (None, None, True)\n",
    ),
    # ---- D19: the week page ----
    (
        "D19 wrong week start (Sunday-first weeks)",
        "    return d - datetime.timedelta(days=d.weekday())\n",
        "    return d - datetime.timedelta(days=(d.weekday() + 1) % 7)\n",
    ),
    (
        "D19 today not marked on the week bars",
        '        d = monday + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n        elif first is None or d < first:\n            state = "before"\n        else:\n            state = "today" if d == today else "past"\n',
        '        d = monday + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n        elif first is None or d < first:\n            state = "before"\n        else:\n            state = "past"\n',
    ),
    (
        "D19 week rows do not add up to the week total (archive and untracked time lost)",
        "    total = sum(totals.get(day_key(d), 0) for d in counted)\n    rows = fold_rows(apps_of(hist, counted), total, names, palette)\n",
        "    total = sum(totals.get(day_key(d), 0) for d in counted)\n    rows = fold_rows(apps_of(hist, counted), sum(apps_of(hist, counted).values()), names, palette)\n",
    ),
    (
        "D19 a future day given a bar",
        '        d = monday + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n',
        '        d = monday + datetime.timedelta(days=i)\n        if False:\n            state = "future"\n',
    ),
    # ---- D19b: the year page ----
    (
        "D19b year rows do not add up to the year total (archive and lumps lost)",
        "    rows = fold_rows(apps_of(hist, [parse_day(k) for k in days]), total, names, palette)\n",
        "    rows = fold_rows(apps_of(hist, [parse_day(k) for k in days]), sum(apps_of(hist, [parse_day(k) for k in days]).values()), names, palette)\n",
    ),
    (
        "D19b this month not marked on the year bars",
        '            state = "current" if (year, m + 1) == (today.year, today.month) else "past"\n',
        '            state = "past"\n',
    ),
    (
        "D19b a future month given a bar",
        '        if (year, m + 1) > (today.year, today.month):\n            state = "future"\n',
        '        if False:\n            state = "future"\n',
    ),
    (
        "D19b the year average counts days before tracking began",
        "    if first is not None and first > start:\n        start = first\n",
        "",
    ),
    (
        "D19b a year's rows take other years' apps",
        '    days = [k for k in hist["days"] if k.startswith(f"{y}-") and parse_day(k) <= today]\n',
        '    days = [k for k in hist["days"] if parse_day(k) <= today]\n',
    ),
    # ---- D19b: the audit report ----
    (
        "D19b report: a week runs to the next Monday",
        "        sunday = monday + datetime.timedelta(days=6)\n",
        "        sunday = monday + datetime.timedelta(days=7)\n",
    ),
    (
        "D19b report: a month takes the next month's 1st",
        "        last = nxt - datetime.timedelta(days=1)\n",
        "        last = nxt\n",
    ),
    (
        "D19b report: hidden apps dropped instead of flagged",
        "            hidden = key in names.hidden\n",
        "            hidden = key in names.hidden\n            if hidden:\n                continue\n",
    ),
    (
        "D19b report: no remainder row (rows stop adding up)",
        "    if rest > 0:\n",
        "    if False:\n",
    ),
    (
        "D19b report: --md drops the hidden marker (tables differ from --json)",
        '    out += [f"| {md_cell(audit_row_name(r))} | {r[\'text\']} |" for r in p["rows"]]\n',
        "    out += [f\"| {md_cell(r['name'])} | {r['text']} |\" for r in p[\"rows\"]]\n",
    ),
    (
        "D19b report: a year takes other years' month lumps",
        "        ms for k, ms in lumps.items() if day_key(start)[:7] <= k <= day_key(end)[:7]\n",
        "        ms for k, ms in lumps.items() if k <= day_key(end)[:7]\n",
    ),
    # ---- D19b: Kanso naming ----
    (
        "D19b web app: guesses between entries on the same host",
        "        if len(by_host) == 1:\n            return by_host.pop()\n",
        "        if by_host:\n            return sorted(by_host)[0]\n",
    ),
    (
        "D19b web app: www. not ignored",
        '    return host[4:] if host.startswith("www.") and len(host) > 4 else host\n',
        "    return host\n",
    ),
    (
        "D19b web app: the port is kept",
        '    host = host.lower().split(":", 1)[0]\n',
        "    host = host.lower()\n",
    ),
    (
        "D19b toolkit suffix: no desktop retry",
        "        if base != key:\n",
        "        if False:\n",
    ),
    # ---- audit C5: completed-day averages ----
    (
        "C5 the week's average counts today's partial day",
        "    done = [d for d in counted if d < today]\n",
        "    done = list(counted)\n",
    ),
    (
        "C5 the year's average counts today's partial day",
        "    if end == today and days > 1:\n",
        "    if False:\n",
    ),
    # ---- audit C4: when the comparison will exist ----
    # ---- D30: the theme's row colours ----
    (
        "D30 red included in the row colours",
        'HUES = ("yellow", "orange", "green", "cyan", "blue", "magenta", "brown", "purple")\n',
        'HUES = ("yellow", "orange", "green", "cyan", "blue", "magenta", "brown", "purple", "red")\n',
    ),
    (
        "D30 a colour under 3:1 accepted",
        "SWATCH_MIN_RATIO = 3.0  # WCAG 1.4.11\n",
        "SWATCH_MIN_RATIO = 2.0  # WCAG 1.4.11\n",
    ),
    (
        "D30 the order not optimized (picked set, unordered)",
        "    return list(order)\n",
        "    return list(best)\n",
    ),
    (
        "D30 fallback not taken: an unreadable file still counts as a theme",
        "    bg, text = popup_colours(colors)\n    other = readable_muted(text, bg)\n    if doc is not None:\n",
        "    bg, text = popup_colours(colors)\n    other = readable_muted(text, bg)\n    if True:\n",
    ),
    # ---- D33: the report header ----
    (
        "D33 the md header drops the rounding line",
        '    lines = [f"# Screen time — {title}", "", note, ROUNDED, ""]\n',
        '    lines = [f"# Screen time — {title}", "", note, ""]\n',
    ),
    # ---- D20: the archive ----
    (
        "D20 per-app detail dropped for archived days",
        '            if key not in hist["days"]:\n                hist["days"][key] = rec\n',
        '            if key not in hist["days"]:\n                hist["days"][key] = {"total": rec["total"], "apps": {}}\n',
    ),
    (
        "D20 a day in both history.json and the archive: the archived copy wins (counted from the wrong record)",
        '            if key not in hist["days"]:\n                hist["days"][key] = rec\n',
        '            hist["days"][key] = rec\n',
    ),
    (
        "D20 the archive is never read",
        '    merge_archive(hist, os.path.join(os.path.dirname(path), "archive"))\n',
        "",
    ),
    # ---- --by-day (V1 Part B) ----
    (
        "by-day: each day's rows are measured against the period total (rows do not add up per day)",
        '             "rows": audit_rows(hist, [x], names, totals.get(day_key(x), 0))}\n',
        '             "rows": audit_rows(hist, [x], names, total)}\n',
    ),
    (
        "by-day: the day before the period is included",
        '            for x in days\n        ]  # fmt: skip\n        out["undated_ms"]',
        '            for x in [start - datetime.timedelta(days=1)] + days\n        ]  # fmt: skip\n        out["undated_ms"]',
    ),
    (
        "by-day: days after today are included",
        '            for x in days\n        ]  # fmt: skip\n        out["undated_ms"]',
        '            for x in (start + datetime.timedelta(days=i) for i in range((end - start).days + 1))\n        ]  # fmt: skip\n        out["undated_ms"]',
    ),
    (
        "by-day: archived day totals are ignored (history.json days only)",
        '             "total_ms": totals.get(day_key(x), 0),\n',
        '             "total_ms": hist["days"].get(day_key(x), {"total": 0})["total"],\n',
    ),
    (
        "by-day: legacy month totals dropped (days no longer add up to the period)",
        '        out["undated_ms"] = lump_ms\n',
        '        out["undated_ms"] = 0\n',
    ),
    (
        "by-day: zero days dropped from the list",
        '            for x in days\n        ]  # fmt: skip\n        out["undated_ms"]',
        '            for x in days\n            if totals.get(day_key(x), 0)\n        ]  # fmt: skip\n        out["undated_ms"]',
    ),
    (
        "by-day --md: a day's table loses its Total row",
        """            out += [f"| **Total** | **{d['total_text']}** |", ""]\n""",
        """            out += [""]\n""",
    ),
    # ---- V1 Part C: pages one at a time, the Month page ----
    (
        "day page: yesterday is never called yesterday",
        '    label = "today" if d == today else "yesterday" if d == today - one else "day"\n',
        '    label = "today" if d == today else "yesterday" if d == today else "day"\n',
    ),
    (
        "day page: ‹ goes before the first tracked day",
        '        "prev": day_key(d - one) if first is not None and d - one >= first else None,\n',
        '        "prev": day_key(d - one) if first is not None else None,\n',
    ),
    (
        "day page: › goes past today",
        '        "next": day_key(d + one) if d < today else None,\n',
        '        "next": day_key(d + one),\n',
    ),
    (
        "week page: ‹ goes before the first tracked week",
        '        "prev": day_key(monday - week) if first is not None and first < monday else None,\n',
        '        "prev": day_key(monday - week) if first is not None else None,\n',
    ),
    (
        "week page: › on this week",
        '        "next": day_key(monday + week) if monday < current else None,\n',
        '        "next": day_key(monday + week) if monday <= current else None,\n',
    ),
    (
        "week page: every past week is 'last week'",
        '        else "last week" if monday == current - week\n',
        '        else "last week" if monday < current\n',
    ),
    (
        "average: today counted as a completed day (C5)",
        "    done = [d for d in counted if d < today]\n",
        "    done = [d for d in counted if d <= today]\n",
    ),
    (
        "month page: a legacy month lump is not counted",
        "    return sum(totals.get(day_key(d), 0) for d in days) + lumps.get(\n",
        "    return sum(totals.get(day_key(d), 0) for d in days) + 0 * lumps.get(\n",
    ),
    (
        "month page: every day number is labelled",
        '            "label": str(d.day) if d.day in MONTH_LABELS else "",\n',
        '            "label": str(d.day),\n',
    ),
    (
        "month page: today not marked on its bar",
        '        d = first_of + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n        elif first is None or d < first:\n            state = "before"\n        else:\n            state = "today" if d == today else "past"\n',
        '        d = first_of + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n        elif first is None or d < first:\n            state = "before"\n        else:\n            state = "past"\n',
    ),
    (
        "month page: days before the first tracked day get bars",
        '        d = first_of + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n        elif first is None or d < first:\n',
        '        d = first_of + datetime.timedelta(days=i)\n        if d > today:\n            state = "future"\n        elif first is None:\n',
    ),
    (
        "month page: › on this month",
        '        "next": month_after(first_of).strftime("%Y-%m") if first_of < current else None,\n',
        '        "next": month_after(first_of).strftime("%Y-%m"),\n',
    ),
    (
        "year page: ‹ goes before the first tracked year",
        '        "prev": str(y - 1) if first is not None and first.year < y else None,\n',
        '        "prev": str(y - 1),\n',
    ),
    (
        "--page: a day after today is a page",
        '    if kind == "day" and d is not None and first is not None and first <= d <= today:\n',
        '    if kind == "day" and d is not None and first is not None and first <= d:\n',
    ),
    (
        "--page: a month before the first tracked one is a page",
        "        if (first_all.year, first_all.month) <= (y, m) <= (today.year, today.month):\n",
        "        if (y, m) <= (today.year, today.month):\n",
    ),
    (
        "--page: a week key that is not a Monday is not normalised",
        "        monday = monday_of(d)\n        if monday_of(first) <= monday:\n",
        "        monday = d\n        if monday_of(first) <= monday:\n",
    ),
    (
        "card: the month page is last month's",
        '    card["month"] = month_page(\n        hist,\n        totals,\n        lumps,\n        today.replace(day=1),\n',
        '    card["month"] = month_page(\n        hist,\n        totals,\n        lumps,\n        month_before(today),\n',
    ),
    # ---- D38: every page against the all-time average ----
    (
        "D38 the shown period is in its own average",
        '    values = [span_value(p, span) for p in periods if p["key"] != shown_key]\n',
        "    values = [span_value(p, span) for p in periods]\n",
    ),
    (
        "D38 this week compared over the wrong span (Mon..yesterday)",
        '    mean, n = average(periods["week"], key, today.weekday() + 1 if now else None)\n',
        '    mean, n = average(periods["week"], key, today.weekday() if now else None)\n',
    ),
    (
        "D38 this month compared against whole months mid-month",
        '    mean, n_used = average(periods["month"], key, today.day if now else None)\n',
        '    mean, n_used = average(periods["month"], key, None)\n',
    ),
    (
        "D38 too few days accepted (2)",
        'AVERAGE_NEEDS = {"day": 3, "week": 2, "month": 2}\n',
        'AVERAGE_NEEDS = {"day": 2, "week": 2, "month": 2}\n',
    ),
    (
        "D38 too few weeks accepted (1)",
        'AVERAGE_NEEDS = {"day": 3, "week": 2, "month": 2}\n',
        'AVERAGE_NEEDS = {"day": 3, "week": 1, "month": 2}\n',
    ),
    (
        "D38 too few months accepted (1)",
        'AVERAGE_NEEDS = {"day": 3, "week": 2, "month": 2}\n',
        'AVERAGE_NEEDS = {"day": 3, "week": 2, "month": 1}\n',
    ),
    (
        "D38 untracked days counted (days under a minute)",
        "        if parse_day(k) < today and ms >= PERIOD_MIN_MS and k != first\n",
        "        if parse_day(k) < today and k != first\n",
    ),
    (
        "D38 untracked weeks counted",
        "        if sum(days) >= PERIOD_MIN_MS:\n            out.append(period(day_key(monday), days))\n",
        "        if True:\n            out.append(period(day_key(monday), days))\n",
    ),
    (
        "D38 a rolling 8-week window instead of all history (days)",
        "        if parse_day(k) < today and ms >= PERIOD_MIN_MS and k != first\n",
        "        if today - datetime.timedelta(days=56) <= parse_day(k) < today and ms >= PERIOD_MIN_MS and k != first\n",
    ),
    (
        "D38 a rolling 8-week window instead of all history (weeks)",
        "    while monday + datetime.timedelta(days=6) < today:\n",
        "    monday = max(monday, monday_of(today) - datetime.timedelta(weeks=8))\n    while monday + datetime.timedelta(days=6) < today:\n",
    ),
    (
        "D38 the day's warm-up date off by one",
        "        return today + datetime.timedelta(days=need + late)\n",
        "        return today + datetime.timedelta(days=need + late - 1)\n",
    ),
    (
        "D38 the week's warm-up date off by one",
        "        return start + datetime.timedelta(days=7 * need)\n",
        "        return start + datetime.timedelta(days=7 * need - 1)\n",
    ),
    (
        "D38 the month's warm-up date a month early",
        "    for _ in range(need):\n        start = month_after(start)\n",
        "    for _ in range(need - 1):\n        start = month_after(start)\n",
    ),
    (
        "D38 a week begun before tracking counts as whole",
        "    if monday < first:\n        monday += datetime.timedelta(days=7)\n",
        "    if False:\n        monday += datetime.timedelta(days=7)\n",
    ),
    (
        "D38 a month begun before tracking counts as whole",
        "    if first_of < first:\n        first_of = month_after(first_of)\n    while",
        "    if False:\n        first_of = month_after(first_of)\n    while",
    ),
    (
        "D38 a legacy month lump cut short (counted against part of a month)",
        '    if p["lump"]:\n        return None\n',
        "",
    ),
    (
        "D38 today, under its average, reads below (as if over)",
        '    elif current and kind == "day":\n',
        '    elif False and kind == "day":\n',
    ),
    (
        "D38 a row of today under its average reads below",
        '    return f"avg {fmt(mean)}" if current else f"▼ {fmt(mean - row[\'value_ms\'])}"\n',
        "    return f\"▼ {fmt(mean - row['value_ms'])}\"\n",
    ),
    (
        "D38 this week's line loses its by",
        '    by = f" by {WEEKDAYS[today.weekday()]}" if now else ""\n',
        '    by = ""\n',
    ),
    (
        "D38 report: an average from too few periods",
        "        enough = n >= AVERAGE_NEEDS[kind]\n",
        "        enough = n >= 1\n",
    ),
    (
        "D38 report: the week's start date ignores the first tracked day",
        '        "week": first_tracked(totals),\n        "month": first_day(totals, lumps),\n    }\n    averages = {}\n',
        '        "week": None,\n        "month": first_day(totals, lumps),\n    }\n    averages = {}\n',
    ),
    # ---- D41: "avg" only ever means the all-time average ----
    (
        "D41 the Week's dashed line from the average week, not the Day's average day",
        '    line = average_line("week", total, mean, n, now, by, today, first)\n    # the dashed line: the one average day, as the Day page\'s (D41)\n    day_mean, day_n = average(periods["day"], None)\n',
        '    line = average_line("week", total, mean, n, now, by, today, first)\n    # the dashed line: the one average day, as the Day page\'s (D41)\n    day_mean, day_n = average(periods["week"], None)\n',
    ),
    (
        "D41 the Month's dashed line from the average month",
        '    line = average_line("month", total, mean, n_used, now, by, today, first)\n    # the dashed line: the one average day, as the Day page\'s (D41)\n    day_mean, day_n = average(periods["day"], None)\n',
        '    line = average_line("month", total, mean, n_used, now, by, today, first)\n    # the dashed line: the one average day, as the Day page\'s (D41)\n    day_mean, day_n = average(periods["month"], None)\n',
    ),
    (
        "D41 the head back on the Week",
        '    line = average_line("week", total, mean, n, now, by, today, first)\n',
        '    line = average_line("week", total, mean, n, now, by, today, first)\n    line["text"] = f"avg {fmt(avg)} a day · " + line["text"]\n',
    ),
    (
        "D41 the head back on the Month",
        '    line = average_line("month", total, mean, n_used, now, by, today, first)\n',
        '    line = average_line("month", total, mean, n_used, now, by, today, first)\n    line["text"] = f"avg {fmt(avg)} a day · " + line["text"]\n',
    ),
    (
        "D41 the period count back in a chart label",
        '"mean_text": f"avg {fmt(mean)}{unit}"',
        '"mean_text": f"avg {fmt(mean)}{unit} · {n} {AVERAGE_UNITS[kind]}"',
    ),
    (
        "D41 the Week and Month labels without 'a day'",
        'PER_DAY = " a day"',
        'PER_DAY = ""',
    ),
    (
        "D41 the Year label without 'a month'",
        '"month", y_max, " a month")',
        '"month", y_max, "")',
    ),
    (
        "D41 this month's line loses its by",
        '    by = f" by {MONTHS[today.month - 1]} {today.day}" if now else ""\n',
        '    by = ""\n',
    ),
    (
        "D41 the Year line says avg",
        '"text": f"{fmt(avg)} a day{since}"',
        '"text": f"avg {fmt(avg)} a day{since}"',
    ),
    # ---- D42: the Day page's hourly chart ----
    (
        "D42 the current hour not marked",
        '            state = "current" if i == now_hour else "future"\n',
        '            state = "past" if i == now_hour else "future"\n',
    ),
    (
        "D42 future hours drawn as past",
        "        if d < today or i < now_hour:\n",
        "        if d < today or i != now_hour:\n",
    ),
    (
        "D42 a partial day drawn as whole",
        "    return hours if sum(hours) == total else None\n",
        "    return hours\n",
    ),
    (
        "D42 the wrong 'hourly from' date (today, not tomorrow)",
        "    return parse_day(min(whole)) if whole else today + datetime.timedelta(days=1)  # fmt: skip\n",
        "    return parse_day(min(whole)) if whole else today  # fmt: skip\n",
    ),
    (
        "D42 the wrong 'hourly from' date (the newest whole day)",
        "    return parse_day(min(whole)) if whole else today + datetime.timedelta(days=1)  # fmt: skip\n",
        "    return parse_day(max(whole)) if whole else today + datetime.timedelta(days=1)  # fmt: skip\n",
    ),
    (
        "D42 the repeated fall-back hour drawn past the top",
        '"y": min(1, ms / HOUR_MS), "state": state})  # fmt: skip\n',
        '"y": ms / HOUR_MS, "state": state})  # fmt: skip\n',
    ),
    # ---- D43: the time scale ----
    (
        "D43 no room kept below the top",
        "TICK_ROOM = 0.9",
        "TICK_ROOM = 1.0",
    ),
    (
        "D43 the hourly scale not fixed at 60m",
        '"ticks": [dict(t) for t in HOUR_TICKS]',
        '"ticks": scale_ticks(max([b["ms"] for b in bars] + [1]))',
    ),
    # ---- D44: two gridlines, clear of the dashed average ----
    (
        "D44 one line instead of two",
        '        return [{"ms": step, "text": fmt(step), "y": ys[0]},\n                {"ms": 2 * step, "text": fmt(2 * step), "y": ys[1]}]  # fmt: skip\n',
        '        return [{"ms": step, "text": fmt(step), "y": ys[0]}]  # fmt: skip\n',
    ),
    (
        "D44 uneven spacing (step and 3 x step)",
        "        ys = [step / y_max, 2 * step / y_max] if y_max > 0 else []\n",
        "        ys = [step / y_max, 3 * step / y_max] if y_max > 0 else []\n",
    ),
    (
        "D44 the top line above 90 %",
        "        if not ys or ys[1] > TICK_ROOM:\n",
        "        if not ys or ys[0] > TICK_ROOM:\n",
    ),
    (
        "D44 a line within 8 % of the average accepted",
        "        if mean_y is not None and any(abs(y - mean_y) <= TICK_CLEAR for y in ys):\n",
        "        if mean_y is not None and all(abs(y - mean_y) <= TICK_CLEAR for y in ys):\n",
    ),
    (
        "D44 the next-smaller-pair fallback skipped",
        "        if mean_y is not None and any(abs(y - mean_y) <= TICK_CLEAR for y in ys):\n            continue\n",
        "        if mean_y is not None and any(abs(y - mean_y) <= TICK_CLEAR for y in ys):\n            return []\n",
    ),
    (
        "D44 a label not matching its line",
        '        return [{"ms": step, "text": fmt(step), "y": ys[0]},\n',
        '        return [{"ms": step, "text": fmt(2 * step), "y": ys[0]},\n',
    ),
    # ---- D45: the install day in no day average ----
    (
        "D45 the first tracked day counted",
        "        if parse_day(k) < today and ms >= PERIOD_MIN_MS and k != first\n",
        "        if parse_day(k) < today and ms >= PERIOD_MIN_MS\n",
    ),
    (
        "D45 the install day's own warm-up counts it",
        "        late = 1 if first is not None and first == today else 0\n",
        "        late = 0\n",
    ),
    # ---- D48: a partial day draws the hours it has ----
    (
        "D48 the caption on a whole day",
        '    shown, caption = "ok", ""\n',
        '    shown, caption = "partial", "hours from 12 AM"\n',
    ),
    (
        "D48 a partial day hidden",
        "        hours = partial_hours(hist, totals, day_key(d))\n",
        "        hours = None\n",
    ),
    (
        "D48 the wrong first hour (one late)",
        "            first = next(i for i, ms in enumerate(hours) if ms > 0)\n",
        "            first = next(i for i, ms in enumerate(hours) if ms > 0) + 1\n",
    ),
    (
        "D48 the wrong first hour (the last with time)",
        "            first = next(i for i, ms in enumerate(hours) if ms > 0)\n",
        "            first = max(i for i, ms in enumerate(hours) if ms > 0)\n",
    ),
    (
        "D48 12 AM named 0 AM",
        """    return f"{(i + 11) % 12 + 1} {'AM' if i < 12 else 'PM'}"\n""",
        """    return f"{i % 12} {'AM' if i < 12 else 'PM'}"\n""",
    ),
    (
        "D48 hours with no time counted as partial",
        "    return hours if 0 < sum(hours) < totals.get(key, 0) else None\n",
        "    return hours if 0 <= sum(hours) < totals.get(key, 0) else None\n",
    ),
    # ---- D47: the install day in no Year line ----
    (
        "D47 the install day counted in the Year line",
        "    if install is not None and start <= install <= end and install < today and days > 1:\n",
        "    if False:\n",
    ),
    (
        "D47 the Year line still starts on the install day",
        "            start = install + datetime.timedelta(days=1)\n",
        "            start = install\n",
    ),
    (
        "D47 a later year's install day taken from a past year",
        "    if install is not None and start <= install <= end and install < today and days > 1:\n",
        "    if install is not None and start <= install and install < today and days > 1:\n",
    ),
]


def run_oracle(engine):
    """The oracle's failures for an engine, or None when the oracle itself broke."""
    p = subprocess.run(
        [sys.executable, "-I", ORACLE, engine],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    lines = p.stdout.strip().splitlines()
    try:
        fails = json.loads(lines[-1])
    except (IndexError, ValueError):
        return None
    return fails if isinstance(fails, list) else None


def verdict(source, name, old, new):
    """("unapplied", match count) or ("ran", the oracle's failures or None)."""
    if source.count(old) != 1 or old == new:
        return "unapplied", source.count(old)
    tmp = tempfile.mkdtemp(prefix="screen-time-mutant-")
    try:
        path = os.path.join(tmp, "screen_time.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source.replace(old, new))
        return "ran", run_oracle(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    base = run_oracle(ENGINE)
    if base is None or base:
        print("mutants: the unmutated engine does not pass the oracle; stopping")
        print(base)
        return 1
    print(f"mutants: unmutated engine passes the oracle; {len(MUTANTS)} mutants")
    with open(ENGINE, encoding="utf-8") as f:
        source = f.read()
    jobs = int(os.environ.get("SCREEN_TIME_MUTANT_JOBS", os.cpu_count() or 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        verdicts = list(pool.map(lambda m: verdict(source, *m), MUTANTS))
    killed, rc = 0, 0
    for (name, _, _), (kind, fails) in zip(MUTANTS, verdicts, strict=True):
        if kind == "unapplied":
            print(f"  ERROR  {name}: did not apply ({fails} matches)")
            rc = 1
        elif fails is None:
            print(f"  ERROR  {name}: the oracle gave no verdict")
            rc = 1
        elif fails:
            killed += 1
            print(f"  killed ({len(fails):3d} checks)  {name}")
        else:
            print(f"  SURVIVED  {name}")
            rc = 1
    print(f"mutants killed: {killed}/{len(MUTANTS)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
