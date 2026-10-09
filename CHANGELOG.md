# Changelog

What changes for the user, newest first. Kanso Screen Time started as a fork
of agx's Screen Time 1.6.2 (`2c7b75a`); its history up to then is in its
repository:
<https://github.com/ax1g/quickshell-screentime-plugin/blob/2c7b75a/CHANGELOG.md>.

## 1.0.0 (2026-10-08)

### Fixed

- After an update, the card no longer shows stale numbers in silence. Until
  the shell restarts, the old tracker keeps running beside the updated card;
  the glance and every page now say "Restart the shell to finish updating"
  (the pages name `omarchy restart shell`).
- A terminal window with several tabs or splits (Ghostty, kitty, Alacritty's
  extra windows, foot in server mode) is counted as the terminal instead of
  crediting whichever tab was opened first: the focused tab can't be told
  apart, so Kanso no longer guesses. One window per process (foot, Omarchy's
  default) still counts the program running in it.
- Time no longer counts while the screen is locked by another lock screen
  (hyprlock, swaylock): besides Omarchy's own lock, any session lock the
  compositor holds now pauses tracking.
- A background timer that woke the shell four times a second, forever, now
  stops after its first ten seconds.
- The engine, the report and the app resolver always run the system Python
  (`/usr/bin/python3`), so an older `python3` earlier on your PATH (mise,
  pyenv, conda) no longer leaves the card empty.

### Changed

- history.json now records its own format version (`"schema": 1`), and fields
  Kanso doesn't know are kept on every save instead of dropped. Kanso never
  writes a history.json from a newer version, or one with top-level fields it
  can't keep: the file stays untouched and the card says "Not saving history:
  update Kanso". agx 1.6.2 and Kanso 0.9.0 still read the new file, so going
  back keeps working.

### Docs

- The README states the requirements (Omarchy 4.0+, Python 3.11+) and, for
  agx users, that the ignore list and app aliases don't carry over and how to
  move them to names.json.
- The README says plainly that Kanso counts while the screen is on: with Stay
  awake on, time away from the computer counts, so lock the screen or close
  the lid when you leave.

### CI

- The checkout, Node and Python setup actions moved to their Node 24 releases
  (v7), and every job runs on Ubuntu 24.04.
- The long mutation run now runs only when the engine, the oracle or the tools
  change, on a release tag, or when started by hand. Every push and pull
  request still runs the JS, QML and Python checks and tests.

## 0.9.0 (pre-release)

The first release as Kanso Screen Time: plugin ID `io.github.nathankramm.kanso`.
It keeps its data in `~/.config/omarchy/screen-time/`, the same folder as
`agx.screen-time`, so existing history carries over. Disable `agx.screen-time`
before enabling Kanso: two trackers must never write one history.json.

### Added

- A hover card in your bar's style: today's total, how today compares with your
  average day, the day by hour (24 bars, 12 AM to 12 AM, this hour highlighted),
  and your top apps plus Other, adding up to the total. Hover the glyph for a short glance; click it (or
  "Details ›") for the full card, with Day · Week · Month · Year at its foot.
- A Week page: the week's total and dates, the week against your average week, a bar for each day with today highlighted, and the week's
  top apps plus Other.
- A Month page in the same layout: the month's total, the month against your
  average month, a bar for each day of the month with today
  highlighted, and the month's top apps plus Other. The switch reads
  Day · Week · Month · Year.
- Every page compares with your all-time average of its kind: every finished
  day, week or month in your history, the one shown left out (and never the
  install day, which is only partly tracked). A week or month
  under way is compared with the same span of the others ("▲ 40m above average
  by Wed"); today, so far, with a whole day ("average 3h 40m a day"); an earlier
  one whole ("▼ 25m below average"). Until there is enough history (3 days,
  2 weeks, 2 months) the line says when the average starts, in grey.
- "avg" always means your all-time average: the Week and Month charts draw your
  average day as a dashed line ("avg 2h 36m a day"), the Year chart your average
  month ("avg 22h 51m a month").
- Every chart has a light time scale: two evenly spaced round gridlines
  ("4h / 8h", "30h / 60h") on Week, Month and Year, kept clear of the dashed
  average, and 30m / 1h on the Day's hours.
- Each day now records the hour its time fell in. A day recorded only in part
  (the install day) draws the hours it has, captioned "hours from 12 PM"; days
  from before have no hourly chart ("hourly from <date>").
- ‹ › on every page step back to any earlier day (with its apps), week, month
  or year, as far as your history goes, and forward to today.
- The Year page in the same layout: the year's total, its time per day, a bar
  per month with this month highlighted, and that year's top apps plus Other.
- Right-click the glyph to **Copy report** or **Save report** (to
  `~/Documents/screen-time-YYYY-MM-DD.md`), from Omarchy's own menu; a
  notification confirms each. Also `omarchy-shell io.github.nathankramm.kanso copy|save`.
- The Markdown report says its times are rounded to the minute and where the
  exact values are.
- `screen-time report --day / --week / --month / --year`, as text, `--json` or
  `--md`: every app with hidden ones marked, adding up to the total exactly;
  `report --md` alone gives today, this week, this year and all-time, with your
  average day, week and month.
  `--by-day` with `--month` or `--year` lists every day with its own apps.
- Name your apps in `names.json`: rename, hide into Other, or stop counting.
- `screen-time report`, `keys` and `log` in the terminal.

### Changed

- The bar shows the hourglass only. The click panel, its settings menu, the
  goal badge and the right-click toggle are gone; the hover card replaces them.
- App names are display-only: stored history is never renamed.
- The Year's time per day counts completed days only, so a half-done today no
  longer pulls it down each morning.
- The glance no longer shows the date pill (it is always today), and its rows
  show times only; the Day page keeps each app's comparison.
- App colours come from your Omarchy theme: six of its own colours (never its
  red), chosen so they stay easy to tell apart for colour-blind eyes and
  adjusted until each reads clearly on the card. They follow a theme switch
  within a minute. Without a usable theme, a colour-blind-safe set is used.
- Colours stay readable on light and dark themes alike: secondary text, chart
  bars and the Day · Week · Month · Year switch.
- Apps get readable names without any setup: from your installed apps' own
  names (their desktop entries), a few built-ins (`claude` reads Claude Code,
  a shell prompt reads Terminal), then a tidy fallback (a web app's host, the
  last part of an id like `org.telegram.desktop`, `-wayland` dropped). Your
  names.json renames still win, and `screen-time keys` shows where each name
  came from.
- Per-app detail is kept forever. `history.json` holds the last 365 days and
  older days move, apps and all, to `archive/<year>.json` beside it (upstream
  kept only each old day's total).

### Fixed

- At midnight, with an app in focus, about half a minute around midnight was
  lost and an error was logged six times; the day now rolls over cleanly, each
  second on its own side of midnight.

### Removed

- The reset-today and wipe-everything commands. Nothing in the plugin
  deletes history any more.
- Every setting read from `shell.json`; leftover keys there are ignored.
