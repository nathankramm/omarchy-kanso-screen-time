# Kanso Screen Time

Kanso (簡素): simplicity by removing clutter. Screen time for Omarchy, kept quietly: per-app, terminal-aware, totals kept forever.

<p>
  <img src="docs/glance.png" alt="The glance: today's total, a bar of shares and the top three apps" width="460">
</p>
<table>
  <tr>
    <td><img src="docs/day.png" alt="The Day page: the day by hour and its apps" width="400"></td>
    <td><img src="docs/week.png" alt="The Week page: a bar per day against the average day" width="400"></td>
  </tr>
  <tr>
    <td><img src="docs/month.png" alt="The Month page: a bar per day of the month" width="400"></td>
    <td><img src="docs/year.png" alt="The Year page: a bar per month and the time per day" width="400"></td>
  </tr>
</table>

_Screenshots are rendered from made-up test data._

Per-app screen time for Omarchy, recorded in the
background into one local file and shown in a hover card on the bar.
Started as a fork of
[ax1g/quickshell-screentime-plugin](https://github.com/ax1g/quickshell-screentime-plugin)
(MIT).

## Features

- **Tracks** the focused app, folding time into one record per day. A focused
  terminal counts as the command running inside it (`claude`, not `foot`),
  re-resolved live; Chromium web apps fold by hostname; `steam_app_123456`
  becomes the game title.
- **Pauses** while the screen is locked or the screensaver is up, and across
  suspend. Time before the screensaver starts still counts (about 150 s per
  time away with the default idle settings), and so does time while an app holds an idle inhibitor
  or Stay awake is on.
  Kanso counts while the screen is on: if you keep it awake (Stay awake) and
  walk away, that time counts, so lock the screen or close the lid when you leave.
- **Shows** an hourglass glyph in the bar: no number, no settings. Hover it for
  a glance at today (total, a bar of shares, the top three apps). Click it for
  the full card, with **Day · Week · Month · Year** at its foot:
  - **Day**: the day against your average day, the day by hour (24 bars,
    12 AM to 12 AM, this hour highlighted), and its apps, each against its own
    average;
  - **Week**: a bar per day and the week against your average week;
  - **Month**: a bar per day of the month and the month against your average
    month;
  - **Year**: a bar per month and its time per day over its finished days,
    without the install day ("2h 37m a day since Oct 5").

  "Average" is all your history: every finished day, week or month (days with
  at least a minute, but never the install day, which is only partly tracked;
  weeks and months begun after tracking did), the one shown left out. While a week or month is under way it is compared with the same
  span of the others ("▲ 40m above average by Wed"); today, with a whole day
  ("average 3h 40m a day" while it is under). Until there are 3 days, 2 weeks
  or 2 months, the line says when the average starts. "avg" always means this
  all-time average: the Week and Month charts draw your average day as a dashed
  line ("avg 2h 36m a day"), the Year chart your average month ("avg 22h 51m a
  month"). Every chart has a light time scale: two evenly spaced round
  gridlines ("4h / 8h", "30h / 60h") on Week, Month and Year, kept clear of the
  dashed average, and 30m / 1h on the Day's hours. Hours are
  recorded from the moment this version is installed. A day recorded only in
  part (the install day) draws the hours it has, captioned "hours from
  12 PM"; a day from before hours existed shows "hourly from <date>".

  The card shows your top 6 apps; everything else is in Other. Right-click → Copy report for the full list.
  The rows always add up to the total. **‹ ›** step back to any earlier day,
  week, month or year, as far as your history goes. Row colours come from your Omarchy theme. The card never
  scrolls and never changes size.

- **Reports**: right-click the glyph for Omarchy's own menu.
  - **Ask agent**: the whole picture to
    `~/.local/state/kanso-screen-time/report-<time>.md`, then your default
    coding agent (`omarchy default agent`) opened on it to answer your
    questions. Claude starts in plan mode. With no default agent, or one
    that is not installed, a notification says so: use Copy report. Each ask
    leaves its file; nothing removes them.
  - **Copy report**: the whole picture as Markdown, to the clipboard.
  - **Save report**: `~/Documents/screen-time-YYYY-MM-DD.md`, replacing
    today's copy.

  A notification confirms a copy or a save, or carries the error; a failed
  save or ask writes nothing. The same from a terminal: `omarchy-shell io.github.nathankramm.kanso copy` and
  `omarchy-shell io.github.nathankramm.kanso save`.

- **Answers questions in the terminal**: `screen-time report` with `--day`,
  `--week`, `--month` or `--year` (add `--by-day` to a month or year for every
  day and its apps), as text, `--json` or `--md`. Every app is listed, hidden
  ones marked, and the rows add up to the total exactly. `report --md` alone
  gives today, this week, this year and all-time, with your average day, week
  and month. More in "How to audit screen time" below.
- **Names** come from `~/.config/omarchy/screen-time/names.json`: rename, hide
  (into Other) or ignore (stop counting) an app by its stored key.
  `screen-time keys` lists the keys, each with the label it shows now and
  where that label comes from. Labels are display-only; history is never rewritten.

  ```json
  {
    "rename": { "claude": "Claude Code" },
    "hide": ["bash"],
    "ignore": ["steam_app_123456"]
  }
  ```

  `rename` maps a stored key to the label to show (keys given the same label
  share one row); `hide` folds apps into Other without changing the total;
  `ignore` stops counting an app from then on (time already recorded stays).
  Every field is optional. Renames and hides show within a minute.

## How to audit screen time

`screen-time report` reads the history read-only and never writes. For an
audit, ask for one period and a format; every app is listed (no six-row cap,
sub-minute apps included), hidden apps are marked `(hidden)` instead of
dropped, and time with no app detail (archive days older than the 365-day
window, legacy month lumps, a day's untracked remainder) is its own
`(no app detail)` row, so **the rows always add up to the total exactly**
(in `--json`, to the millisecond; the `Xh Ym` texts are rounded).

```bash
screen-time report --day 2026-10-05            # one day, plain text
screen-time report --week 2026-10-07           # the Mon–Sun week holding that date
screen-time report --month 2026-10 --md        # a Markdown table (App | Time)
screen-time report --year 2026 --json          # machine-readable, ms exact
screen-time report --month 2026-10 --by-day    # every day of the month, each with
                                               # its total and every app
screen-time report --year 2026 --by-day --json # the same for a year, ms exact
screen-time report --md > screen-time.md       # the whole picture: today, this
                                               # week by day, this year by month,
                                               # all-time
```

`--by-day` (with `--month` or `--year` only; anything else exits 2) adds
`by_day`: every day up to today, each `{key, label, total_ms, total_text,
rows}` with its rows adding up to its own `total_ms`. Legacy month lumps have
no day: they are `undated_ms`, and `sum(by_day[].total_ms) + undated_ms ==
total_ms`. Days before the 365-day window keep their apps (in `archive/`).

The whole picture's `all_time.averages.{day,week,month}` are the card's
baselines: the mean of every completed day with at least a minute (never the install day),
whole Mon–Sun week and whole month, as `{ms, text, periods}`, or before
there are 3 / 2 / 2 of them `{starts_key, starts_text}`, the date the
average starts.

For an AI agent: prefer `--json` and check `sum(rows[].ms) == total_ms`
yourself (and, by day, each day's rows against its own total); use `rows[].keys` to see which stored keys a label covers and
`rows[].hidden` for what the card folds into Other. A wrong label is fixed
in names.json (see Names above), never in history.json. Periods in the
future are empty; an invalid period exits 2 with "not a valid period".

## Privacy

Everything stays on this computer. The plugin records which app is focused
and for how long, by name: a terminal's running command, a web app's site
(`x.com`, never the page), a Steam game's title. It does not record what you
type, what is on screen, or page addresses. It reads a window's title only to
name a game Steam launches under a shared name. It sends nothing anywhere:
there is no network code, no telemetry and no account. A report goes only
where you ask (the clipboard or `~/Documents`). The hour each minute fell in
is stored with the day, locally like everything else; nothing leaves the
machine. Delete `~/.config/omarchy/screen-time/` and it is gone.

## Data

Everything lives in `~/.config/omarchy/screen-time/`. The tracker writes
`history.json`:

```json
{
  "days": {
    "2026-08-16": {
      "total": 490875,
      "apps": { "zen": 313349, "claude": 148706 },
      "hours": [
        0, 0, 0, 0, 0, 0, 0, 0, 0, 120000, 370875, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0
      ]
    }
  },
  "months": {},
  "years": {
    "2026": { "2026-08-15": 582190 }
  }
}
```

- Per-app focus time in milliseconds, keyed by local day (`YYYY-MM-DD`). A
  session spanning midnight splits there.
- `hours`: the same time by local wall-clock hour, 24 totals (0 = 12–1 AM), split
  exactly at each hour, so a day recorded whole has hours adding up to its
  total. On a daylight-saving day the repeated 1 AM adds into hour 1 and the
  skipped 2 AM stays 0. Days from before hours were recorded have none and
  stay valid.
- `history.json` holds the last 365 days. Older days move, apps and all, to
  `archive/<year>.json` beside it, written once a day and kept forever. A day
  leaves `history.json` only after the archive has it, so nothing is lost
  along the way.
- `years` (per-day totals without apps) and `months` (monthly lumps) are what
  older versions kept; they are read, never written.
- Stored app keys are never renamed. Nothing in the plugin deletes history.
- Writes are atomic. A file that won't parse is moved aside as
  `history.json.corrupt-<epoch>`, never overwritten.

## Install

**Requirements:** Omarchy 4.0 or later (the version that added shell plugins,
`omarchy plugin`) and Python 3.11 or later at `/usr/bin/python3` (stdlib only;
nothing to install). A stock Omarchy has both. Kanso always runs the system
Python, so a different `python3` earlier on your PATH (mise, pyenv, conda)
doesn't matter.

```bash
omarchy plugin add https://github.com/nathankramm/omarchy-kanso-screen-time.git
omarchy plugin enable io.github.nathankramm.kanso
omarchy restart shell
```

`omarchy plugin add` asks a few questions first (trust this repo, clone it, enable it now, which bar section); if you enabled it there, skip the `enable` command.

It lands in `~/.config/omarchy/plugins/io.github.nathankramm.kanso/` and
starts recording at once. For the `screen-time` command in your terminal:

```bash
mkdir -p ~/.local/bin
ln -sfn ~/.config/omarchy/plugins/io.github.nathankramm.kanso/python/screen_time.py ~/.local/bin/screen-time
```

**Updating:** `omarchy plugin update io.github.nathankramm.kanso`, then
`omarchy restart shell`.

### Coming from agx.screen-time

Kanso keeps its data in the same folder, `~/.config/omarchy/screen-time/`,
so the history `agx.screen-time` recorded carries over and keeps counting.
Disable it first (two trackers must never write one history.json), and back up ~/.config/omarchy/screen-time/.

**What doesn't carry over:** agx's ignore list and app aliases (and its daily
goal, which Kanso doesn't have). agx kept them in its bar entry in
`~/.config/omarchy/shell.json`, and disabling agx removes that entry, so copy
them out first:

```bash
jq '.. | objects | select(.id? == "agx.screen-time") | {ignoredApps, appAliases}' ~/.config/omarchy/shell.json
```

Then redo them in `~/.config/omarchy/screen-time/names.json` (see Names
above): each ignored app goes in `"ignore"`, and each alias `"from": "to"`
becomes `"rename": { "from": "to" }`.

```bash
cp -a ~/.config/omarchy/screen-time ~/.config/omarchy/screen-time-backup-$(date +%Y%m%d-%H%M%S)
omarchy plugin disable agx.screen-time
omarchy plugin add https://github.com/nathankramm/omarchy-kanso-screen-time.git
omarchy plugin enable io.github.nathankramm.kanso
omarchy restart shell
```

To go back: `omarchy plugin disable io.github.nathankramm.kanso`, then
`omarchy plugin enable agx.screen-time` and restart the shell. agx reads every
day in history.json (the last 365) with its totals and apps; it doesn't read
`archive/` (days Kanso moved there after a year) or today's hourly data.

## Uninstall

```bash
omarchy plugin remove io.github.nathankramm.kanso
rm ~/.local/bin/screen-time
omarchy restart shell
```

`omarchy plugin remove` asks before it deletes the plugin's folder. Your
history in `~/.config/omarchy/screen-time/` stays until you delete it.

## How this was built

Kanso Screen Time started as a fork of ax1g/quickshell-screentime-plugin. I
chose what to change and wrote the spec. Claude Code wrote most of the new
code.

It is checked in the development repository (see Development):

- Unit tests for the engine and the JS model, and an independent oracle that
  works the totals out again with separate code.
- A mutation run: one-defect copies of the engine that must each fail the
  oracle.
- A CI job that runs the tracker through the real Quickshell.
- CI runs the tests on every push, and the mutation run by hand or on a
  release tag.
- Before 1.0.1 and before 1.0.2, the build ran on my own machine for a day,
  checked by a script, before it was released.

## Development

Tests, the oracle and its mutation run live in a separate development
repository. Each release is copied from there into this repository as one
commit.

## License

MIT (see `LICENSE`), as the original. Started as a fork of ax1g/quickshell-screentime-plugin (MIT).
