# divemate-cli

Query and extract statistics from a [DiveMate](https://www.dive-mate.eu/) dive
logbook. A DiveMate `.ddb` export is a plain SQLite database; this tool gives you
a clean CLI and a small reusable Python library on top of it, so you can slice
your dive log without writing SQL by hand.

```console
$ dmcli list tank=24 period='last year'
Dives — query 'tank="24" period="last year"'
Number  Divedate    Place                             Depth   Divetime  Tanksize  Watertemp
------  ----------  --------------------------------  ------  --------  --------  ---------
487     07-02-2025  Tossa. Perduts                    29.9 m  104 min   24 L      13 °C
513     16-06-2025  Barcelona. Port del Forum         3.5 m   198 min   24 L      24 °C
514     17-06-2025  Tossa. Ruta clàsica dreta         20.4 m  69 min    24 L      22 °C
517     01-07-2025  Sant Feliu. Túnels de Port Salvi  27.9 m  96 min    24 L      19 °C
541     22-08-2025  Cova De Cala Sa Nau               9.2 m   118 min   24 L      27 °C
544     07-09-2025  Tarragona. SES. Dragonera         22.3 m  69 min    24 L      22 °C
549     20-09-2025  Tossa. Ruta larga                 27.5 m  98 min    24 L      17 °C

7 dive(s)
```

## Features

- A small [query language](#query-language--query) for filtering — AND/OR,
  grouping, negation, numeric/date comparisons, plus `sort`/`limit` — over any
  dive attribute (date, site, region, buddy, depth, dive time, SAC, tags, …).
- Aggregate statistics (totals, averages, maxima) over any filtered set.
- Group and rank dives by site, country, region, buddy, computer, suit, week, …
- Terminal charts: a field over time, or dives per week/month/year.
- Machine-readable `--json` output on every command, for scripts or a future UI.
- Python 3.10+; runtime dependencies are `lark` (the query parser) and
  `plotext` (the terminal charts).
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
dmcli --db ~/dives/DiveMate.ddb stats year=2026
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
| `list`  | List dives matching the filters (optional `N-M` number range) |
| `info`  | Show every detail of one dive by its number |
| `count` | Count matching dives |
| `graph` | Plot a numeric field over time |
| `stats` | Aggregate statistics over matching dives |
| `group` | Count dives grouped by a column, ranked |
| `years` | Dive count per year |
| `tanks` | List the distinct tank sizes present in the database |
| `tags` | List `#tags` found in comments, with dive counts |
| `divers` | List diver profiles in the DB with their dive counts |
| `query-help` (`qhelp`) | Print a quick reference for the query language |
| `pull` | Download the logbook from Google Drive (see below) |

`--json` is a global flag — put it before the command:
`dmcli --json list year=2026`.

### Filters

All dive-attribute filtering is done with the **[query language](#query-language--query)**,
accepted by `list`, `count`, `stats`, `graph`, `group`, `years` and `tags` — as
`-q/--query EXPR` or simply as bare trailing words. Besides it, only three
**scope** flags remain (they decide which dives the query even sees, so they
can't live inside the query):

| Flag | Meaning |
|------|---------|
| `-q EXPR` / `--query EXPR` | Filter expression — see [Query language](#query-language--query) |
| `--include-deleted` | Include dives DiveMate soft-deleted (`Status=2`) |
| `--diver ID` | Restrict to a given `DiverID` (default: the owner) |
| `--all-divers` | Include every diver profile, not just yours |

**SAC** (queried as `sac`, though it is strictly RMV — the popular name) is
computed per dive as `gas used / (dive time × mean ATA)`, where gas used is
summed over the dive's tanks as `(start − end pressure) × tank size` and mean ATA
is `avg depth / 10 + 1`. It is in L/min, surface-equivalent. Only dives with a
usable tank pressure drop, a dive time and a recorded average depth get a value;
others show `—` and never match a `sac` comparison.

> Dates display/parse as `DD-MM-YYYY`, but `--json` output stays ISO. In the
> query use ISO dates (`date>=2024-06-01`) or the `period=` shortcut.

### Query language (`--query`)

The query language expresses every dive-attribute filter — AND/OR, grouping,
negation, numeric and date comparisons — and (for `list`) sorting and limiting.
Use `-q` / `--query` with an expression, or just type it as bare trailing words.

```sh
dmcli list  '(region=Mallorca and place~toro) or region=Catalunya'
dmcli count 'maxdepth>=10'
dmcli list  'sac<18 or tank=15'
dmcli group region --query 'night and divetime>50'
dmcli list  region~mallorca sort depth desc limit 10     # no quotes needed here
```

You can also drop the `--query` and the quotes and just append the expression as
trailing words — they are joined into the query. These are equivalent:

```sh
dmcli graph count tag=sidemount region=mallorca
dmcli graph count 'tag=sidemount region=mallorca'
dmcli graph count --query 'tag=sidemount and region=mallorca'
```

If you pass both `--query` and trailing words, they are combined with AND. (On
`list`, a leading `N-M` dive-number range is taken first; the rest is the query.)

Multi-word values need no quotes at all: `period=last year` or `region=costa
brava` work whether or not the shell groups them (`period='last year'` is also
fine). The following bare words are folded into the value — but only when that is
needed to parse, so an already-valid query like `region~mallorca night` (region
AND the `night` flag) is left alone. The shell still eats bare `>`, `<` and `(`
`)`, so quote those (`'depth>30'`, `'(a or b)'`).

Grammar: boolean `and` / `or` / `not` (case-insensitive) with parentheses;
leaves are `field op value` comparisons (or a bare boolean field like `night`).
`and` is also implicit between adjacent terms — `region~mallorca period=2025` is
the same as `region~mallorca and period=2025`.
Operators by field type:

| Field type | Operators | `=` means |
|------------|-----------|-----------|
| text (`place`, `country`, `region`, `comment`, `tag`, `buddy`, `water`) | `=` `~` (contains), `==` (exact), `!=` `!~` (not contains), `!==` (not exact) | contains (case-insensitive) |
| number (`depth`/`maxdepth`, `avgdepth`, `divetime`/`time`, `watertemp`/`temp`, `airtemp`, `o2`, `tanksize`/`tank`, `sac`/`rmv`, `number`, `year`) | `=` `==` `!=` `!==` `<` `<=` `>` `>=` | equal |
| date (`date`, ISO `YYYY-MM-DD`) | `=` `==` `!=` `!==` `<` `<=` `>` `>=` | equal |
| boolean (`night`, `weekend`) | bare flag, or `=true` / `=false` | — |

For text, `=` and `~` are synonyms (substring match); use `==` for an exact,
whole-value match — e.g. `place=tossa` matches any Tossa site, `place=="Tossa de
Mar"` only that exact place. On numbers/dates `=` and `==` both mean equal.

Text matching is accent-insensitive as well as case-insensitive, so `buddy~tomas`
matches `Tomás` and `Tomàs`, and `place~palamos` matches `Palamós`.

`tag=cave` matches a `#cave` tag in the comments (give it without the `#`), the
same tags `dmcli tags` lists.

`period` is a date-range shortcut, expanded before evaluation into a `date`
range — `period="last year"` becomes `date>=2025-01-01 and date<=2025-12-31`. It
accepts `period=today`, `period=yesterday`, `period="this week"` / `"last week"`,
`period="this month"` / `"last month"`, `period="this year"` / `"last year"`,
`period=2024`, `period="month 2024-07"`, `period="last 30 days"`, … and supports
only `=` / `!=` (`!=` meaning outside the range). Quote any value with spaces.

Values are barewords (`region=Mallorca`, accents are fine); quote anything with
spaces or punctuation: `place="el toro"`. Multi-valued fields (`buddy`,
`tanksize`) match when **any** of the dive's values satisfies the comparison.
Matching is done in Python over the selected dives (the dataset is one
logbook), so the query is powerful without a SQL round-trip; unknown fields,
bad operators and syntax errors are reported clearly.

**Sorting & limiting** (used by `list`; ignored by the other commands): append
`sort FIELD [asc|desc]` and/or `limit N` to the query. `FIELD` is any query
field; default direction is `desc`; missing values sort last. A directives-only
query (no filter) is allowed, e.g. `sort depth limit 5`.

```sh
dmcli list region~mallorca sort depth desc limit 10   # ten deepest in Mallorca
dmcli list sort sac asc limit 5                        # five most air-efficient
dmcli list 'divetime>=60' sort divetime desc          # longest dives first
```

`sort`, `limit`, `asc`, `desc` are reserved words (still usable as values after
an operator, e.g. `place=sort`).

### `list`

Lists dives matching the query. A leading `N-M` token is still taken as a
dive-number range sugar (`N`, `N-M`, `N-`, `-M`); sorting/limiting and all
filtering go in the query. Results default to **newest first** (`sort date desc`).
`--columns a,b,c` chooses the displayed columns.

```bash
dmcli list 100-130                              # dives numbered 100 through 130
dmcli list 500                                  # just dive 500
dmcli list 700-                                 # dive 700 to the latest
dmcli list 'sac>=18'                            # air-hog dives (high SAC)
dmcli list 'sac<=12' --columns Number,Place,SAC # your most efficient dives
dmcli list year=2026 tank=24.0
dmcli list sort depth desc limit 10             # ten deepest dives
dmcli list sort divetime desc                   # longest dives first
dmcli list sort sac asc limit 5                 # five most air-efficient dives
dmcli list tag=sidemount year=2026              # dives tagged #sidemount
dmcli list 'comment~ejercicio'                  # free-text comment search
dmcli tags                                      # see every #tag you use
dmcli list weekend night                        # night dives on weekends
dmcli stats weekend=false night=false           # weekday daytime dives
dmcli list period="this month"
dmcli stats period="year 2024"
dmcli list 'date>=2025-06-01 and date<=2025-08-31' --columns Number,Divedate,Place,Depth
```

### `info`

Shows every detail of a single dive — date, site, times, depths, temperatures,
gear, each tank (size, gas mix, start→end pressure) and the comment — looked up
by its dive number.

```console
$ dmcli info 262
262 - Begur. Furió Fitó
───────────────────────────
Date          15-08-2021
Entry time    09:30:00
───────────────────────────
Place         Begur. Furió Fitó
Region        Catalunya
Country       Spain
Buddies       Angel Caldo
───────────────────────────
Dive time     67.3 min
Max depth     52.5 m
Avg depth     20.4 m
Water temp    14 °C
───────────────────────────
Tank 1        24 L  O₂ 21%  220→120 bar
Tank 2        11 L  O₂ 50%  180→100 bar
SAC (RMV)     16 L/min
```

`info` always draws the dive's **depth profile** below the details, with
**temperature** overlaid when the computer logged it: depth in blue on the left
axis (metres, surface at the top) and temperature in red on its own right axis
(°C), with a legend below. `--json` includes the raw samples under a `profile`
key.

```console
$ dmcli info 709
...
                     709 - Shark and Yolanda Reef
 0┤▀▀▀▀▚▖                                                        ▐▀├30
  │     ▜▖                                                      ▗▌ │
 ...
30┤       ▚▟▀▝█▄▌ ▝▟  ▐▟▜                                          ├24
  └┬───────────────┬───────────────┬───────────────┬────────────┘
   0              14              28              42           56
                            time (min)
▬ depth (m) [left]    ▬ temp (°C) [right]
```

Instead of a number you can pass `last` (the highest-numbered dive) or `first`,
e.g. `dmcli info last`.

A dive number is only unique per diver; if several profiles share it, all are
shown — pass `--diver ID` (see `dmcli divers`) to pick one.

### `stats`

```console
$ dmcli stats year=2026 tank=24.0
count                  6
total_divetime_min     598.5
total_divetime_h       10
avg_divetime_min       99.8
max_depth_m            29.1
avg_depth_m            25
avg_watertemp_c        17.7
```

### `graph`

Plots a numeric field across the matching dives in date order (x axis labelled
with dive dates, y axis the field). Accepts the same query as `list`.
Fields: `depth` (a.k.a. `maxdepth`), `avgdepth`, `divetime`, `watertemp`, `sac`.

Use the special field **`count`** to plot the **number of dives per period**
instead of a per-dive value: `--by week`, `--by month` (default) or `--by year`,
with empty periods shown as 0 (weeks are labelled by their ISO Monday). Combine
with any query — e.g. dives with a buddy over time. The x axis spans the first to
the last dive that matches (constrain it with a `date`/`period` query).

It is a coloured line chart (via `plotext`). `--json` prints the `{date, number,
value}` points instead.
Dives missing the field (or with a 0 that means "no data") leave a gap.

```bash
dmcli graph sac year=2024                # how your air consumption trends
dmcli graph depth place~Tossa            # max depth per dive at a site
dmcli graph watertemp                    # water temperature over your whole log
dmcli graph count buddy~Lushka           # dives with a buddy, per month
dmcli graph count --by week period=2025  # dives per week in 2025
dmcli graph count --by year              # dives per year across the whole log
```

### `group`

A filtered `list` collapsed into a count per field — "how many dives per site /
country / tank size / …". It accepts the **same query** as `list`, so you can
scope the breakdown any way you want.

Group by (case-insensitive): `Place`, `Country`, `Buddy`, `Computer`, `Divesuit`,
`Water`, `Tanksize`, `Tanktype`, `Divemaster`, `Boat`, `Weather`, `Waves`,
`UWCurrent`, `Rating`, `Entry`, `Deco`, `Divetype`, `O2`, `Region`, or the
virtual `year`, `month`, `week` (by ISO Monday), `weekend` (splits into
`weekday` / `weekend`) and `night` (splits into `night` / `day`, plus `unknown`
for dives with no entry time — same 18:00–06:00 rule as the `night` query field).

Options: `--limit N` (top N groups), `--sort count|value` (default `count`) —
these are `group`'s own flags, distinct from the query's `sort`/`limit`.

`list`, `group` and `stats` print a header describing exactly what was queried,
e.g. `Top 10 places by number of dives — query 'year=2026'`. It's dimmed in a
terminal and omitted from `--json` output.

```bash
dmcli group place --limit 10                # ten most-dived sites
dmcli group country                         # dives per country
dmcli group tanksize                        # dives per tank size
dmcli group year --sort value               # dives per year, chronological
dmcli group month year=2026 tag=sidemount   # sidemount per month in 2026
dmcli group weekend                         # weekday vs weekend dives
dmcli group night year=2026                 # night vs day dives in 2026
dmcli group buddy place~Tossa --limit 5     # top buddies at Tossa
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

## Shell completion (bash)

`dmcli` ships a bash completion that completes subcommands, their options and the
value lists for `group`/`graph`. A ready-made script lives at
[`completions/dmcli.bash`](completions/dmcli.bash).

Install it by sourcing it from your `~/.bashrc`:

```bash
echo 'source /path/to/divemate-cli/completions/dmcli.bash' >> ~/.bashrc
# or drop it where bash-completion looks:
cp completions/dmcli.bash ~/.local/share/bash-completion/completions/dmcli
```

The script is **generated from the CLI's own argument parser**, so it never
drifts from the code. After adding commands or flags, regenerate it with:

```bash
make completion            # writes completions/dmcli.bash
# equivalently:
dmcli completion bash > completions/dmcli.bash
```

Or let it happen automatically: `make hooks` enables the project git hooks
(`.githooks`), whose pre-commit regenerates and stages `completions/dmcli.bash`
whenever `divemate/cli.py` changes (it chains to any existing `.git/hooks`).

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
dmcli list year=2026 tank=24.0

# or point at a logbook anywhere
DIVEMATE_DB=~/dives/DiveMate.ddb dmcli stats year=2026

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
dmcli stats period="this year"
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

`DiveFilter` carries SQL-level criteria: `year`, `date_from`, `date_to`,
`number_from`, `number_to`, `tanksize`, `place`, `country`, `region`, `buddy`,
`comment`, the `exclude_*` lists, `min_depth`, `max_depth`, `min_divetime`,
`min_sac`, `max_sac`, `water`, `weekend`, `night`, and the scope fields
`include_deleted` / `diver` / `all_divers`. All optional; unset fields are
ignored (`weekend`/`night` are tri-state: `True`, `False`, or `None`).

For the CLI's full expression language, parse a query and pass its predicate:

```python
from divemate import query
pred = query.predicate("region~mallorca and (depth>=30 or night)")
deep = db.dives(DiveFilter(), predicate=pred)
```

`db.dives()` also takes `order=` (any returned column, default `Divedate`),
`desc=` (default `False`), and `predicate=` (a `(record) -> bool` post-filter,
also accepted by `count`/`stats`/`group_count`/`dives_per_year`/`tags`).

## Project layout

```
divemate/
  __init__.py   exports DiveMate, DiveFilter
  db.py         library — data access, filtering, stats (no I/O)
  dates.py      date/period parsing helpers (pure)
  cli.py        argparse command-line frontend
pyproject.toml
```

A web frontend would import `divemate.db` directly; the dicts it returns are
already JSON-serialisable.

## Notes on the data

- Dates are stored as `YYYY-MM-DD` text; the `date`/`year`/`period` query fields
  compare on that.
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
  into the `Buddy` table. The `buddy` query field and `group buddy` resolve those
  names for you.
  Since a dive can have several buddies, `group buddy` totals can exceed the dive
  count (each buddy on a dive is counted).
- `Water`: `1` = salt, `0` = fresh, following DiveMate's own convention.
- The database is opened read-only; running this tool cannot modify your logbook.
```

## License

Copyright (C) 2026 Javi Polo

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version. See [LICENSE](LICENSE) for the full text.
