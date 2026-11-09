# divemate-cli — notes for Claude

Query/stats CLI for a DiveMate `.ddb` (SQLite) logbook. `db.py` is the
frontend-agnostic data layer (no printing); `cli.py` is the argparse frontend;
`dates.py` holds pure date/period helpers; `chart.py` renders terminal charts.

## Workflow

- **Work directly on `main`.** No feature branches — commit straight to `main`.
- **Commit per feature.** One logical change per commit — don't batch several
  unrelated features into one commit. Commit as each feature is finished, before
  starting the next.
- Only commit/push when asked.

## Sample database

A sample DiveMate `.ddb` for manual testing is pointed to by `DIVEMATE_DB` in
`~/.config/divemate-cli/config`. Source that file (`. ~/.config/divemate-cli/config`)
and export `DIVEMATE_DB` to run the CLI against it.
