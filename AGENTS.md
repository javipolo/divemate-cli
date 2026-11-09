# divemate-cli — notes for Claude

Query/stats CLI for a DiveMate `.ddb` (SQLite) logbook. `db.py` is the
frontend-agnostic data layer (no printing); `cli.py` is the argparse frontend;
`dates.py` holds pure date/period helpers.

## Workflow

- **Commit per feature.** One logical change per commit — don't batch several
  unrelated features into one commit. Commit (and branch off `main`) as each
  feature is finished, before starting the next.
- Only commit/push when asked.
