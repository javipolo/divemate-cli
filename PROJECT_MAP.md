# PROJECT_MAP

Architectural index of `divemate-cli`. Read this first to locate logic before
scanning the tree.

## 1. Overview & stack

Query/stats CLI over a DiveMate `.ddb` (SQLite) dive logbook. Reads the DB
read-only, filters/aggregates dives, prints tables, stats and coloured terminal
charts. Also reads the Pandive extension tables (see memory
`pandive-extension-schema`).

- Python `>=3.10`, packaged with setuptools (`pyproject.toml`).
- Runtime deps: `lark>=1.2` (grammar for the `--query` language),
  `plotext>=5.2,<6` (terminal charts, pinned to 5.x API).
- Optional: `gdrive` extra (`google-auth`, `requests`) for `pull`; `dev` extra
  (`pytest`).
- Lint: `ruff` (config in `pyproject.toml`, line-length 100).

## 2. Entry points & execution

- CLI entry: `dmcli = divemate.cli:main` (console script) / `python -m divemate.cli`.
- `dmcli` (repo root): shell wrapper that sources `~/.config/divemate-cli/config`
  and runs the CLI (via container image or local).
- Subcommands: `list info count graph stats group years tanks tags divers
  query-help completion pull`.
- Make targets: `make test` (pytest), `make lint` (ruff), `make completion`
  (regenerate `completions/dmcli.bash`), `make hooks` (enable `.githooks`),
  `make build/install/config/clean` (container + install).
- Tests: `tests/` via `python -m pytest`.

## 3. Module & directory map

```
divemate/        Python package (all app logic)
completions/     generated bash completion (dmcli.bash) — do not hand-edit
tests/           pytest suite
.githooks/       pre-commit (completion+test), prepare-commit-msg, pre-push
Containerfile    container build; dmcli runs this image
Makefile         dev/build tasks
config.example   sample config sourced by the dmcli wrapper
```

Package `divemate/` (all `.py` relevant):

- `__init__.py` — package marker / library docstring. No logic.
- `db.py` — **data-access layer, no printing.** `class DiveMate` (open/query the
  SQLite DB) with `dives`, `dive_full`, `dive_profile`, `count`, `stats`,
  `group_count`, `group_buddies`, `dives_per_year`, `tags`, `boundary_number`,
  `divers`, `tank_sizes`, cached helpers `buddy_names` / `dive_tags` / `sac` /
  `owner_id`. `@dataclass DiveFilter` (structured filter). Module consts:
  `DIVE_COLUMNS`, `_GROUPABLE`, `TEMP_BIN_C`, `EMPTY_SENTINEL`, night-window
  consts. `_group_expr` maps a group key (incl. virtual year/month/week/
  weekend/night/watertemp) to SQL.
- `cli.py` — **argparse frontend + presentation.** `build_parser()`, `main()`,
  one `cmd_*` per subcommand, table/row formatting (`_print_table`, `_fmt_cell`,
  `_print_dive`), arg→filter glue (`_filter_from_args`, `_predicate_from_args`,
  `_query_text`/`_regroup` that reassemble shell-split query tokens), bash
  completion generator (`_bash_completion_script`).
- `query.py` — **`--query` filter language** (lark LALR grammar `_GRAMMAR`).
  AST nodes (`And/Or/Not/Compare/Flag/Query`), `FieldSpec` registry `_SPECS` +
  `_ALIASES`, field getters (`_tags` merges comment `#tags` + `db_tags`,
  `_buddies`, `_water`, `_is_night`…), `parse()`, `predicate()`, `sort_key()`,
  typed comparison (`_cmp_text/number/date/bool`), `help_text()`.
- `dates.py` — **pure date/period helpers**, no DB. `parse_date` (ISO + ES
  `dd-mm-yyyy`), `format_date_es`, `parse_period` ("this month", "2024",
  "last year"…) → ISO range.
- `chart.py` — **terminal charts via plotext.** `series_chart`, `profile_chart`
  (depth/temp dive profile), `term_width`, tick helpers.
- `gdrive.py` — **`pull` backend**: download the `.ddb` from a Google Drive
  folder with a service account. `GDriveConfig`, `find_file`, `download`.
  Imported lazily (optional `gdrive` extra).

## 4. Data flow & architecture

```
argv → cli.build_parser → cmd_* (cli.py)
         ↓ build filter
   DiveFilter (structured)  +  query.predicate(text)  ← lark grammar (query.py)
         ↓
   DiveMate (db.py): SQL WHERE from DiveFilter → fetch rows → enrich
     (buddy_names, tank_sizes, dive_tags) → apply query predicate in Python
         ↓ plain dict rows
   cli.py formatting (_print_table / _print_dive / _stats_lines)
     or chart.py (series/profile) → stdout
```

- Two-stage filtering: `DiveFilter` becomes SQL (cheap, in DB); the `--query`
  predicate runs in Python over enriched row dicts (expressive: tags, buddies,
  night/weekend, computed SAC).
- `db.py` never prints; `cli.py`/`chart.py` own all output. `dates.py` and
  `query.py` are pure (no DB). `gdrive.py` is the only network module.
- Pandive extension tables are optional: `DiveMate._has_table` guards access so a
  plain DiveMate DB still works.

## 4b. Pandive DB extension schema

Pandive = a separate (user-built) divelog tool compatible with the DiveMate
`.ddb` schema but extended with extra `Pandive*` tables. A plain DiveMate DB lacks
them — `DiveMate._has_table` guards all access. Sample DB: `pandive.ddb` (set via
`DIVEMATE_DB` in the config).

Extra tables: `PandiveTag`, `PandiveDiveTag`, `PandiveDiveType`,
`PandiveDiveTypeTank`, `PandiveGas`, `PandiveComputer`, `PandiveTankPreset`,
`PandiveSampleExtra`, `PandiveSync`, `PandivePending`.

Structured tags (vs DiveMate's comment `#hashtags`):

- `PandiveTag(Id INTEGER PK, Name TEXT)` — tag dictionary.
- `PandiveDiveTag(DiveId, TagId, PK(DiveId,TagId))` — link table.
  `DiveId` = `Logbook.ID` (**not** `Logbook.Number`).

The `tag=` filter and `tags` listing read both sources: comment `#tags` and these
structured tags (merged via the `db_tags` row key in `db.py`/`query.py`). `info`
shows a dive's combined tags via `DiveMate._combined_tags` (exposed as the `tags`
row key on `dive_full`, rendered by the `__tags__` section in `_print_dive`).

## 5. Project conventions

- **Layering:** data layer (`db.py`) frontend-agnostic and silent; presentation
  only in `cli.py`/`chart.py`; pure helpers isolated (`dates.py`, `query.py`).
- **Typing:** type hints throughout; `from __future__`-style `X | None` unions
  (py≥3.10); `@dataclass` for `DiveFilter`/`GDriveConfig`/AST nodes.
- **Config/env:** `DIVEMATE_DB` env var (default `DiveMate.ddb`); `dmcli` wrapper
  sources `~/.config/divemate-cli/config`; Google Drive via `DIVEMATE_GDRIVE_*`.
- **DB access:** read-only SQLite via `sqlite3` with `row_factory = Row`; sentinel
  `-300.0` for empty temps normalised to `None`; expensive maps cached on the
  instance (`_buddies`, `_sac`, `_dive_tags`).
- **Errors:** `ValueError` for bad user input (unknown group field, bad query),
  surfaced by `cli.main`.
- **Generated artefacts:** `completions/dmcli.bash` is generated from the parser —
  run `make completion` (or the git hook) after CLI changes; never hand-edit.
- **Tests:** pytest in `tests/`; `test_query.py` exercises the pure parser/
  evaluator against a representative record dict (no DB); `test_cli_query.py`
  covers CLI token reassembly.
- **Workflow:** commit per feature straight to `main`; commit/push only when asked.
