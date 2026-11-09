# divemate-cli

Query and extract statistics from a [DiveMate](https://www.dive-mate.eu/) dive
logbook. A DiveMate `.ddb` export is a plain SQLite database; this tool gives you
a clean CLI and a small reusable Python library on top of it, so you can slice
your dive log without writing SQL by hand.

```console
$ dmcli list --year 2026 --tank 24.0
Number  Divedate    Place                             Depth  Divetime  Tanksize  Watertemp
------  ----------  --------------------------------  -----  --------  --------  ---------
641     2026-04-10  Tossa. Ruta clàsica completa      22.3   80.0167   24        13
646     2026-05-08  Tossa. Ruta larga                 20.3   96.7167   24        16
647     2026-05-15  Tossa. Perduts                    27.9   87.55     24        17
653     2026-06-05  Tossa. Ruta larga                 29.1   102.217   24        19
688     2026-08-04  Tossa. Ruta clàsica completa      22.6   132.85    24        22
728     2026-10-16  Sant Feliu. Túnels de Port Salvi  27.9   99.1667   24        19

6 dive(s)
```

## Features

- Filter dives by year, date range, tank size, place, country, buddy, depth,
  dive time, and water type — any combination.
- Aggregate statistics (totals, averages, maxima) over any filtered set.
- Group and rank dives by site, country, buddy, computer, suit, or water type.
- Dive counts per year.
- Machine-readable `--json` output on every command, for scripts or a future UI.
- Zero dependencies — standard library only, Python 3.10+.
- Read-only access: the tool never writes to your logbook.

## Installation

```bash
pip install -e .
```

This adds a `divemate` command. You can also run it without installing:

```bash
python -m divemate.cli ...
```

## Pointing at your database

The tool needs the path to your `.ddb` file. In order of precedence:

1. `--db PATH` on the command line.
2. The `DIVEMATE_DB` environment variable (which you can set once in the
   [config file](#configuration-file)).
3. Default: `DiveMate.ddb` in the current directory.

```bash
dmcli --db ~/dives/DiveMate.ddb stats --year 2026
export DIVEMATE_DB=~/dives/DiveMate.ddb   # then omit --db
```

## Configuration file

Instead of exporting variables every time, put them in a config file that the
`dmcli` wrapper loads automatically:

```
~/.config/divemate-cli/config
```

(Respects `$XDG_CONFIG_HOME`; override the whole path with `$DIVEMATE_CONFIG`.)

Install the documented example and edit it:

```bash
make config           # copies config.example, won't overwrite an existing one
$EDITOR ~/.config/divemate-cli/config
```

It is sourced as shell, so it is plain `KEY="value"` lines (you may use `$HOME`):

```sh
DIVEMATE_DB="$HOME/dives/DiveMate.ddb"
DIVEMATE_GDRIVE_KEY="$HOME/.config/divemate-cli/service-account.json"
DIVEMATE_GDRIVE_FOLDER="xxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

See [`config.example`](config.example) for every supported variable. A variable
already set in your environment **overrides** the file, so one-off overrides like
`DIVEMATE_DB=/tmp/other.ddb dmcli list` still work.

> The config file is read by the wrapper script. When you run the CLI natively
> (`python -m divemate.cli`), set the environment variables yourself.

## Commands

Run `divemate <command> --help` for the full option list of any command.

| Command | What it does |
|---------|--------------|
| `list`  | List dives matching the filters |
| `count` | Count matching dives |
| `stats` | Aggregate statistics over matching dives |
| `group` | Count dives grouped by a column, ranked |
| `years` | Dive count per year |
| `tanks` | List the distinct tank sizes present in the database |
| `tags` | List `#tags` found in comments, with dive counts |
| `divers` | List diver profiles in the DB with their dive counts |
| `pull` | Download the logbook from Google Drive (see below) |

`--json` is a global flag — put it before the command:
`dmcli --json list --year 2026`.

### Filters

Accepted by `list`, `count`, `stats`, `group`, and `years`:

| Flag | Meaning |
|------|---------|
| `--year N` | Dives in calendar year N |
| `--period PERIOD` | Quick range — see below |
| `--from DD-MM-YYYY` | On or after this date (ISO also accepted) |
| `--to DD-MM-YYYY` | On or before this date (ISO also accepted) |
| `--tank L` / `--tanksize L` | Dives using a tank of L litres |
| `--exclude-tank L` | Exclude dives that have a tank of L litres |
| `--place TEXT` | Place name contains TEXT (case-insensitive) |
| `--country TEXT` | Country contains TEXT |
| `--buddy TEXT` | Buddy field contains TEXT |
| `--comment TEXT` | Comments contain TEXT |
| `--tag NAME` | Comments contain `#NAME` (give it without the `#`) |
| `--min-depth M` | Max depth ≥ M metres |
| `--max-depth M` | Max depth ≤ M metres |
| `--min-divetime MIN` | Dive time ≥ MIN minutes |
| `--water {0,1}` | 1 = salt water, 0 = fresh |
| `--include-deleted` | Include dives DiveMate soft-deleted (`Status=2`) |
| `--diver ID` | Restrict to a given `DiverID` (default: the owner) |
| `--all-divers` | Include every diver profile, not just yours |

**Dates** are shown and accepted in Spanish `DD-MM-YYYY` format (ISO input still
works; `--json` output stays ISO for machines).

`--period` accepts quick relative ranges (case-insensitive):

| Period | Meaning |
|--------|---------|
| `today` (`day`), `yesterday` | That day |
| `this week` (`week`), `last week` | Mon–Sun of the current / previous week |
| `this month` (`month`), `last month` | The current / previous calendar month |
| `this year` (`year`), `last year` | The current / previous calendar year |
| `year 2024` or `2024` | A specific year |
| `month 2024-07` | A specific month |
| `last 30 days` | The last N days up to today |

`--period` sets both bounds; an explicit `--from`/`--to` overrides that side.

Filters combine with AND. `--tank` matches **any** tank recorded on a dive (it
joins the per-dive `Tank` table), not only the primary tank stored on the dive
record — so a dive where the 24 L was a stage or deco tank still matches.

### `list`

Extra options: `--columns a,b,c` to choose columns, `--desc` for newest first.

```bash
dmcli list --year 2026 --tank 24.0
dmcli list --place Tossa --min-depth 25 --desc
dmcli list --tag sidemount --year 2026        # dives tagged #sidemount
dmcli list --comment "ejercicio"               # free-text comment search
dmcli tags                                     # see every #tag you use
dmcli list --period "this month"
dmcli stats --period "year 2024"
dmcli list --from 01-06-2025 --to 31-08-2025   # Spanish DD-MM-YYYY
dmcli list --from 2025-06-01 --to 2025-08-31 --columns Number,Divedate,Place,Depth
```

### `stats`

```console
$ dmcli stats --year 2026 --tank 24.0
count                  6
total_divetime_min     598.5
total_divetime_h       10
avg_divetime_min       99.8
max_depth_m            29.1
avg_depth_m            25
avg_watertemp_c        17.7
```

### `group`

A filtered `list` collapsed into a count per field — "how many dives per site /
country / tank size / …". It accepts **all the same filters** as `list`, so you
can scope the breakdown any way you want.

Group by (case-insensitive): `Place`, `Country`, `Buddy`, `Computer`, `Divesuit`,
`Water`, `Tanksize`, `Tanktype`, `Divemaster`, `Boat`, `Weather`, `Waves`,
`UWCurrent`, `Rating`, `Entry`, `Deco`, `Divetype`, `O2`, or the virtual `year` /
`month`.

Options: `--limit N` (top N groups), `--sort count|value` (default `count`).

`list`, `group` and `stats` print a header describing exactly what was queried,
e.g. `Top 10 places by number of dives — year 2026`. It's dimmed in a terminal
and omitted from `--json` output.

```bash
dmcli group place --limit 10               # ten most-dived sites
dmcli group country                         # dives per country
dmcli group tanksize                        # dives per tank size
dmcli group year --sort value               # dives per year, chronological
dmcli group month --year 2026 --tag sidemount   # sidemount per month in 2026
dmcli group buddy --place Tossa --limit 5   # top buddies at Tossa
```

### `years`

```console
$ dmcli years
year  n
----  ---
...
2024  55
2025  142
2026  160
```

### `tanks`

```console
$ divemate tanks
7 11 11.1 12 12.2 13.2 15 24
```

## Running in a container (podman)

No local Python or dependency install needed — build an image once and drive it
through the `dmcli` wrapper, which runs podman and auto-mounts your
logbook read-only.

```bash
# build the image and install the wrapper on your PATH in one step
make install

# or do it by hand:
podman build -t divemate-cli .                 # = make build
cp dmcli ~/.local/bin/ && chmod +x ~/.local/bin/dmcli
# ...or just alias it
alias dmcli='/path/to/divemate-cli/dmcli'
```

Then use it exactly like the CLI, minus `--db` (the wrapper handles the mount):

```bash
# uses ./DiveMate.ddb in the current directory
dmcli list --year 2026 --tank 24.0

# or point at a logbook anywhere
DIVEMATE_DB=~/dives/DiveMate.ddb dmcli stats --year 2026

# JSON out, piped like any command
DIVEMATE_DB=~/dives/DiveMate.ddb dmcli --json years | jq
```

How the wrapper finds the database (first that exists wins):

1. `$DIVEMATE_DB` — path to a `.ddb` file
2. `./DiveMate.ddb` in the current directory

The file is bind-mounted read-only with an SELinux relabel (`:ro,Z`), so it
works out of the box on Fedora/RHEL. The logbook is **never** copied into the
image (excluded via `.containerignore`). Override the image name with
`DIVEMATE_IMAGE` if you tagged it differently.

## Syncing from Google Drive (`pull`)

The DiveMate phone app uploads the `.ddb` to a Google Drive folder. `divemate
pull` downloads it so you always query the latest log.

Access uses a **service account**: you share only the target folder with its
email, so its reach is limited to that one folder, and it authenticates with a
key file — no browser prompt, ever. (Drive's OAuth scopes can't be narrowed to a
single folder, which is why a service account is used instead.)

### One-time setup

1. In the [Google Cloud Console](https://console.cloud.google.com/): create (or
   pick) a project and **enable the Google Drive API**.
2. Create a **service account**, then add a **JSON key** and download it.
3. Copy the service account's email (looks like
   `name@project.iam.gserviceaccount.com`).
4. In Google Drive, open the folder where the app uploads the log, **Share** it
   with that email as **Viewer**.
5. Get the folder's ID — it's the last part of the folder URL
   `https://drive.google.com/drive/folders/<FOLDER_ID>`.

### Configure and run

Set these once in the [config file](#configuration-file) (recommended), or export
them:

```bash
export DIVEMATE_GDRIVE_KEY=~/secrets/divemate-sa.json   # the JSON key
export DIVEMATE_GDRIVE_FOLDER=<FOLDER_ID>
export DIVEMATE_DB=~/dives/DiveMate.ddb                  # where to save it

dmcli pull            # downloads DiveMate.ddb into DIVEMATE_DB
dmcli stats --period "this year"
```

Flags override the env vars: `--key`, `--folder`, `--name`, `--out`. The default
remote file name is `DiveMate.ddb` (change with `--name`/`DIVEMATE_GDRIVE_FILE`).
The download is atomic (temp file then rename), so a half-finished transfer never
clobbers your existing log.

Running natively (not in the container) needs the extra dependency:

```bash
pip install -e '.[gdrive]'
```

With the container wrapper, `dmcli pull` mounts the DB directory
read-write and the key file read-only automatically.

## Library use

The CLI is a thin wrapper over `divemate.db`, which is UI-agnostic: it does no
printing, returns plain dicts, and opens the database read-only. Reuse it from a
script, a notebook, or a web backend.

```python
from divemate import DiveMate, DiveFilter

with DiveMate("DiveMate.ddb") as db:
    f = DiveFilter(year=2026, tanksize=24.0)

    dives = db.dives(f)                 # list[dict]
    n     = db.count(f)                 # int
    s     = db.stats(f)                 # dict of aggregates
    top   = db.group_count("Place", limit=10)
    years = db.dives_per_year()
    sizes = db.tank_sizes()            # list[float]
```

`DiveFilter` fields mirror the CLI filters: `year`, `date_from`, `date_to`,
`tanksize`, `place`, `country`, `buddy`, `min_depth`, `max_depth`,
`min_divetime`, `water`. All optional; unset fields are ignored.

`db.dives()` also takes `order=` (any returned column, default `Divedate`) and
`desc=` (default `False`).

## Project layout

```
divemate/
  __init__.py   exports DiveMate, DiveFilter
  db.py         library — data access, filtering, stats (no I/O)
  cli.py        argparse command-line frontend
pyproject.toml
```

A web frontend would import `divemate.db` directly; the dicts it returns are
already JSON-serialisable.

## Notes on the data

- Dates are stored as `YYYY-MM-DD` text; `--year`/`--from`/`--to` compare on that.
- `Divetime` is in minutes, stored as a fractional number (e.g. `80.0167`).
- A DiveMate DB can hold **several diver profiles** (dive-computer imports, demo
  data, buddies). Every command shows only *your* dives by default — the profile
  with the most dives, treated as the owner. Run `dmcli divers` to see them
  all; use `--diver ID` or `--all-divers` to change the selection.
- DiveMate **soft-deletes** dives: it keeps the row but sets `Status = 2` and
  blanks the place/tank. These are excluded from every command by default; pass
  `--include-deleted` to count them too.
- Some remaining dives still have an empty `Place` (`PlaceID = 0`) — a gap in the
  source log, not a tool bug. They show as blank rows in `group Place`.
- An empty temperature is stored as the sentinel `-300`; the tool normalizes it
  to blank (and leaves it out of averages), matching what the app shows.
- Buddies aren't stored on the dive row — `Logbook.BuddyIDs` holds a CSV of IDs
  into the `Buddy` table. `--buddy` and `group buddy` resolve those names for you.
  Since a dive can have several buddies, `group buddy` totals can exceed the dive
  count (each buddy on a dive is counted).
- `Water`: `1` = salt, `0` = fresh, following DiveMate's own convention.
- The database is opened read-only; running this tool cannot modify your logbook.
```
