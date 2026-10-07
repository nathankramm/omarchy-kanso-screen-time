---
name: screen-time-contributor
description: Conventions for working on Kanso Screen Time (io.github.nathankramm.kanso), an Omarchy plugin. Read before changing code.
---

# AGENTS.md — Kanso Screen Time (`io.github.nathankramm.kanso`)

## Rules for agents (read these first)

- **`~/.config/omarchy/screen-time/names.json` is the one file you may edit.**
  Shape:
  `{"rename": {"<exact stored key>": "Label"}, "hide": ["<key>"], "ignore": ["<key>"]}`.
  `rename` changes the label shown (keys with the same label become one row); `hide` folds an app into Other (the total does not
  change); `ignore` stops the tracker counting an app from now on.
- **Use exact stored keys.** Find them with `screen-time keys --days 30` (or
  `python3 python/screen_time.py keys --days 30`): each key, its total, the
  label it shows and where the label comes from (`rename` | `builtin` |
  `desktop` | `fallback`, D17), plus `case` for a case-insensitive rename and
  `hidden`. A `desktop` or `fallback` label you don't like is what a rename
  fixes. A rename without the exact case only applies when it matches exactly
  one stored key.
- **Write names.json atomically** (a temp file in the same directory, then a
  rename) and keep it valid JSON. An invalid file keeps the last good ignore
  list, warns once, and the card's `names.state` reads `invalid`.
- **`history.json` is read-only. Never write, move, reformat or "fix" it.**
  The tracker owns it and rewrites it every few seconds; a second writer
  corrupts it. Read it through `screen-time report today|week|year|all` and
  `keys`, not by parsing it yourself.
- **When changes show:** renames and hides within a minute (the engine runs
  every 60 s); ignores apply to tracking at once, but never remove time
  already recorded.

Screen-time tracker for Omarchy: per-app focused time, recorded in the
background into one local JSON file, terminal-aware. Started as a fork of
[ax1g/quickshell-screentime-plugin](https://github.com/ax1g/quickshell-screentime-plugin)
1.6.2 (`2c7b75a`, MIT). The plugin id is `io.github.nathankramm.kanso`; the
data folder stays `~/.config/omarchy/screen-time/`, so history recorded by
`agx.screen-time` carries over. The two must never be enabled together: two
trackers writing one `history.json` corrupt it. The `D<n>` tags (and `audit
C<n>`/`P<n>`) in comments and tests name the design decision a piece of code
implements.

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
total_ms`. Days before the 365-day window keep their apps (archive, D20).

The whole picture's `all_time.averages.{day,week,month}` are the card's
baselines (D38): the mean of every completed day with at least a minute (never the install day, D45),
whole Mon–Sun week and whole month, as `{ms, text, periods}`, or before
there are 3 / 2 / 2 of them `{starts_key, starts_text}`, the date the
average starts.

For an AI agent: prefer `--json` and check `sum(rows[].ms) == total_ms`
yourself (and, by day, each day's rows against its own total); use `rows[].keys` to see which stored keys a label covers and
`rows[].hidden` for what the card folds into Other. A wrong label is fixed
in names.json (see the rules above), never in history.json. Periods in the
future are empty; an invalid period exits 2 with "not a valid period".

## Layout

```
.
├── qml/
│   ├── BarWidget.qml       # Bar button: the glyph; owns the hover machine and the page runs
│   ├── HoverCard.qml       # The card: glance, Day · Week · Month · Year (paints only)
│   └── Service.qml         # Side effects: timers, disk, the engine every 60 s, the archiver
├── js/                     # Pure logic, Node- and QML-importable
│   ├── Model.js            # Day keys, canonical names, ignore list, sanitize, retention, archive
│   ├── State.js            # Transitions (buckets, suspend, midnight)
│   ├── Pager.js            # The card's past pages: which run may paint (V1)
│   ├── Contrast.js         # Readable text and bar colours on the popup surface
│   └── browser_aliases.json
├── bin/screen-time-report  # Right-click Copy / Save report (D33)
├── python/
│   ├── resolve_app.py      # Terminal/Steam foreground resolver
│   ├── archive_days.py     # Writes days past 365 into archive/<year>.json (D20)
│   └── screen_time.py      # The engine: computes every number and string the card shows
├── tests/                  # node (bar, card, pager, model, state, service, archive...) + unittest (engine, oracle...)
├── tools/mutants.py        # mutation run against the oracle
├── lint/                   # qmllint import stubs (see lint/README.md)
└── manifest.json           # Plugin id, version, entry points
```

Never hand-edit vendored files under `lint/`. `Service.qml` owns side
effects; `State.js` owns transitions as pure functions; the bar widget is
a read-only mirror of the service.

## Commands

```bash
node --check js/Model.js && node --check js/State.js
npx -y prettier@3.9.6 --no-semi --check js/ tests/
node --test tests/*.test.js
uvx ruff@0.16.6 check python/ tests/ tools/ && uvx ruff@0.16.6 format --check python/ tests/ tools/
python3 -m unittest discover -s tests          # resolver + engine + the oracle
python3 tools/mutants.py                       # every engine mutant must fail the oracle
                                               # (one oracle run per mutant, in parallel: ~6 min on
                                               # 16 cores, ~11.5 min on 4; ~38 min on a CI runner)
python3 python/screen_time.py card --as-of 2026-10-05T21:30 --history FIXTURE.json
/usr/lib/qt6/bin/qmllint -I lint qml/*.qml   # MaxWarnings=0: any warning fails
for f in qml/*.qml; do /usr/lib/qt6/bin/qmlformat "$f" | cmp -s - "$f" || echo "needs formatting: $f"; done
omarchy plugin validate .
```

qmllint cannot see `Model.*` members, so also check that every
`Model.X(` called from `qml/` is a top-level declaration in `js/Model.js`.

## The engine and the card contract

The Iron Rule: Python computes, QML paints. `python/screen_time.py` reads
`history.json` and `names.json` **read-only** (a test asserts mtime, size and
sha256 are unchanged after every verb) and prints everything the card shows.
The QML never adds, averages, rounds or formats; it paints strings and draws
the 0..1 numbers as given. The authoritative field list is the engine's
docstring (`CARD CONTRACT`); in short:

| Field                          | What the card does with it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `schema`, `state`, `message`   | `state` is `ok`, `empty`, `missing`, `corrupt` or `error`; anything but `ok` shows `message` (one line) instead of the body. Every field is always present                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `today`                        | the glance's hero: `total_text`, `label`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `segments`                     | the glance's segmented bar: `{name, swatch, weight}`, weights sum to 1                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `rows`                         | the glance's rows (= `day.rows`), top 6 + Other: `name`, `swatch`, `vs_text` (against the app's own average day), `value_text`; they add up to the hero                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `footer`                       | `all_time_text`, `since_text` on every detail page ("all-time 41h since Oct 4"); `text` is that line, `week_*` are for `report` only (schema 3)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `day`, `week`, `month`, `year` | the four detail pages (V1; schema 11, D38/D41/D42/D43/D44/D48), this day, week, month and year only: each has `key`, `label`, `total_text`, `rows`/`segments` adding up to the total (archive days and month lumps land in Other), `prev`/`next` (the neighbour's key, or null at the first tracked period and at today's), and `line.text`, only the comparison with the all-time average of the kind (`state` `baseline` → "average starts <date>", painted muted and regular; `near`/`above`/`below` bold; the Year's line is its daily average over finished days, never the install day (D47), "2h 37m a day since Oct 5"). "avg" only ever means the all-time average (D41). Day: `pill`, `hours` (D42: `state` ok → 24 `bars[{label, x, y, state}]` 12 AM–12 AM on a fixed 60 min scale, this hour `current` in the accent, later ones `future` slots, no dashed line; `state` partial (D48: hours for only part of the day) → the same 24 bars over one muted caption `text` "hours from 12 PM", the first hour with time; `state` pending (no usable hours) → one muted `text` "hourly from <date>"). Week and Month: `range_text`, `bars[{label, x, y, state}]` (`state` past · today · future · before; future and before are empty slots), `mean_y`/`mean_text`: the same average day as the Day's ("avg 2h 36m a day"). Year: twelve bars (`state` past · current · future · before), `mean_*` the average month ("avg 22h 51m a month"). No chart paints `y_max_text` (D38); every chart paints its `ticks[{text, y}]` (D43): two faint round gridlines (step and 2 × step, clear of the dashed average by 8 %) with muted labels in a narrow left gutter on Week, Month and Year (D44), and the fixed 30m / 1h on the Day's hours |
| `card --page kind:key`         | any other page, as the card's (`day:2026-10-04`, `week:2026-09-28`, `month:2026-09`, `year:2025`): `{schema, state, message, as_of, kind, key, page}`; not a page from the first tracked period to today → `state` `error`, "Not a page: …". The bar runs it on ‹ ›, never Service and never a hover                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `names`                        | `state` of names.json for the status line                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `palette`                      | the rows' colours (schema 4, D30): `source` theme · okabe-ito, `reason`, `keys`, six `swatches` (≥ 3:1 on `surface`), `other` (the readable muted); the QML only paints the rows' `swatch` strings                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

Change the contract only with a schema bump, and change the docstring, this
table, the engine tests, the oracle (`tests/oracle_screen_time.py`) and the QML in
the same commit. The oracle recomputes the whole card with its own code and imports
nothing from the engine; `tools/mutants.py` proves it can catch one-defect
engines. Add a mutant for every new rule.

## Atomic commits (non-negotiable)

- One logical change per commit. Never mix a fix, a feature, a refactor,
  formatting, or docs in one commit.
- Conventional Commits: `<type>(<scope>): <short summary>` — imperative,
  lowercase, no period, max 72 chars. Types: `feat`, `fix`, `test`,
  `refactor`, `chore`, `docs`, `perf`, `style`.
- `style` commits are behavior-neutral by definition; `refactor` commits
  keep all tests green with no behavior change.
- Commit only what the change needs (`git status` + `git diff` first).
  Never commit secrets, caches (`.ruff_cache/`), or `__pycache__/`.
- A commit must leave every suite green and the tree installable. If it
  doesn't, split it until it does.

## Complexity budget

- Prefer small pure functions with early returns over nested branches.
  Past three levels of nesting, extract a helper.
- A file that is hard to skim is too big: extract a component (`qml/`),
  a helper (`js/`), or a test fixture — don't grow it.
- No clever one-liners. Boring, obvious code wins every review.

## Readability and comments

- Comments are WHY-only. One-line file headers; inline comments only where
  the reason isn't obvious from the code (sandbox constraints, framework
  typing lies, ordering hazards, one-way data loss).
- The opposite rule ("no comments, code is self-documenting") is banned:
  it was removed because silent rationale rots. Explain the trap, not the line.
- Name things after the domain (`activeDay`, `keepDays`), not the
  mechanism. No new abbreviations.
- QML: explicit `required` props + signals between components. Qualify
  outer access with the nearest id (`rowDelegate.index`). Delegate-context
  names (`index`, `modelData`) only work with a matching `required`
  declaration — a bare `row.index` is `undefined`, not an error.

## Language rules

- **QML**: 4 spaces, `qmlformat`-clean. `var` in JS-flavored logic only
  where the engine requires it. Scoped `// qmllint disable/enable` pairs
  for audited framework artifacts only, each with its WHY comment.
- **JavaScript**: `var`, no `let`/`const` in sources (tests may use them).
  Prettier (`--no-semi`, house style has no semicolons). `Model.js`/`State.js` stay
  importable by both Node and the QML engine (guarded `module`/`require`).
- **Python**: stdlib only, `ruff`-clean, run by the system `python3`.

## Data safety

- History is append-only in spirit: retention moves day detail into the
  archive with millisecond conservation (pinned by test), never deletes.
  Nothing in the plugin destroys history: there is no reset or wipe.
- A day record is `{total, apps, hours?}`. `hours` (D42) is 24 ms totals by local
  wall-clock hour, accrued only through `State.addSpan`, so a day recorded
  whole has `sum(hours) == total`; every path that copies a day (the restart
  copy `Model.copyDay`, the rollover and its unsaved-time flush, `sanitizeDay`,
  the archiver, the engine) must keep them. A day without hours is valid
  (partial for the chart), never backfilled. Every hours check goes through
  `Model.hoursArray` (D46): live, QML's JsonAdapter hands a loaded array over
  as a sequence, not an Array, so `Array.isArray` drops it. Node tests read
  JSON.parse arrays and cannot see that; `tests/test_runtime_hours.py` loads,
  saves and restarts through the real Quickshell runtime. Older releases read such a file
  and keep every total and app; they drop only the tracked day's hours.
- `history.json` keeps a fixed 365 days (`keepDays`). Days that roll out move,
  apps and all, to `archive/<year>.json` (D20, `python/archive_days.py`); the
  tracker drops a day from `history.json` only after the archiver confirms it
  wrote that day, so no per-app detail is ever lost. Lowering `keepDays` only
  moves more days into the archive.
- Stored app keys are never renamed. Names are display-only.
- Loads never mutate: `sanitize*` returns inputs by identity when clean,
  warns when discarding, and corrupt files move aside (without depending
  on python3) before tracking resumes.
- Writes are atomic (`FileView atomicWrites`), gated on the backup, and
  bounded under flapping (`start`, never `restart`); save failures back
  off and suspend after 6.
- The history format stays byte-compatible with upstream agx, so either
  can read the other's file. Schema sections are never renamed.

## Tests

- Put behavior in `Model.js`/`State.js` so `node --test` can reach it.
- `tests/service.test.js` / `tests/bar.test.js` assert QML wiring by
  source shape (props, signals, derivations) — extend them when adding
  either, and keep the regexes tight to the contract, not the layout.
  Renaming a function in `Service.qml` breaks them even when behavior is
  unchanged; update the test in the same commit.
- Python behavior gets `unittest` cases in `tests/test_resolve_app.py`.

## Browser aliases

`js/browser_aliases.json` is the single source of truth. `python/resolve_app.py`
reads it directly; Node reads it via `require`. QML cannot, so `js/Model.js`
mirrors it as a `qmlBrowserAliases()` literal. Adding a browser means updating
**both**; they must match exactly (`tests/model.test.js` fails otherwise).

## Changelog

- `CHANGELOG.md` records what changes for the user, newest first. Internal
  work (refactors, tests, tooling, file moves) never appears.

## QA traps

- Never put an `anchors.fill` MouseArea inside an implicit-height
  `Column`: it collapses to zero. Size the row explicitly (fixed
  height or `Math.max(...)`) and fill against that.
- Pure helpers must return safe defaults, never throw; history input is
  validated once at the `sanitize*` boundary with `isDayKey`/`isMonthKey`.
- Unreachable-in-theory is not untested-in-practice: clock jumps,
  suspends past midnight, corrupt files, and missing helpers are the
  paths that break. Cover the transition, not just the happy day.
- In a Repeater delegate that declares `required modelData`, never READ
  `index` in an expression — it resolves to 0 for every row (proven
  headlessly). Implicit same-named receipt
  (`required property int index`) still works. Derive position from model data or pass plain
  values down instead.
- After changing the installed checkout
  (`~/.config/omarchy/plugins/io.github.nathankramm.kanso`), run
  `omarchy restart shell` — never rely on the shell's hot reload.

## Definition of done

- [ ] One commit per logical change, Conventional Commits, suites green.
- [ ] `qmllint`, `qmlformat`, `prettier`, `ruff`, Node + Python suites,
      the `Model.X` reference check and `omarchy plugin validate` all pass.
- [ ] `CHANGELOG.md` entry for user-facing changes.
- [ ] No new warnings, no dead imports, no widened suppressions.
