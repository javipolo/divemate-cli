"""Command-line frontend for the DiveMate query library."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import query
from .db import DiveMate, DiveFilter
from .dates import format_date_es

DEFAULT_DB = os.environ.get("DIVEMATE_DB", "DiveMate.ddb")


def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-q", "--query", metavar="EXPR",
                   help="filter expression, e.g. '(region=Mallorca and place~toro) "
                        "or depth>=30'. Operators: text = ~ (contains) == (exact) "
                        "!= !~ !==; numbers/date = != < <= > >=; booleans "
                        "night/weekend; period=\"last year\". Also 'sort FIELD "
                        "[asc|desc]' and 'limit N' (used by `list`).")
    # the query can also be given as bare trailing words (no quotes needed), e.g.
    # `graph count tag=sidemount region=mallorca`. On `list`, a leading N-M dive
    # range is taken first. Joins with -q/--query (if both given) using AND.
    p.add_argument("query_words", nargs="*", metavar="QUERY",
                   help="query expression as bare words (no quotes needed); "
                        "joins with -q/--query if both are given")
    # scope flags: these pick which dives the query even sees, so they cannot be
    # expressed inside the query itself.
    p.add_argument("--include-deleted", action="store_true",
                   help="include dives DiveMate soft-deleted (Status=2)")
    p.add_argument("--diver", type=int, metavar="ID",
                   help="restrict to this DiverID (default: the owner / most dives)")
    p.add_argument("--all-divers", action="store_true",
                   help="include every diver profile, not just yours")


def parse_number_range(s: str) -> tuple[int | None, int | None]:
    """'100-130' -> (100, 130); '100' -> (100, 100); '100-' -> (100, None);
    '-130' -> (None, 130). Raises ValueError on anything else."""
    s = s.strip()
    try:
        if "-" in s:
            lo_s, hi_s = s.split("-", 1)
            lo = int(lo_s) if lo_s.strip() else None
            hi = int(hi_s) if hi_s.strip() else None
            if lo is None and hi is None:
                raise ValueError
            return lo, hi
        n = int(s)
        return n, n
    except ValueError:
        raise ValueError(
            f"invalid dive range {s!r} (use N, N-M, N-, or -M)") from None


def _filter_from_args(a: argparse.Namespace) -> DiveFilter:
    """Build the SQL-level DiveFilter: only the scope (diver / deleted) and the
    `list` dive-number range. Every dive attribute is filtered by the query."""
    number_from, number_to = None, None
    if getattr(a, "range", None):
        number_from, number_to = parse_number_range(a.range)
    return DiveFilter(
        number_from=number_from, number_to=number_to,
        include_deleted=a.include_deleted,
        diver=a.diver, all_divers=a.all_divers,
    )


_RANGE_RE = re.compile(r"^(\d+-?\d*|-\d+)$")


def _looks_like_range(tok: str) -> bool:
    """A dive-number range token: N, N-M, N-, or -M (used by `list`)."""
    return bool(_RANGE_RE.match(tok))


# one `field op value` chunk, used to re-quote a value the shell split or merged
# (e.g. `period='last year'` -> one token, or `period=last year` -> two tokens).
_CMP_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(!==|==|>=|<=|!=|!~|=|~|>|<)\s*(.+?)\s*$")
# words that must never be swallowed into a value during repair: boolean
# connectives and the sort/limit directive keywords
_KEYWORDS = {"and", "or", "not", "sort", "limit", "asc", "desc"}


def _parses(s: str) -> bool:
    try:
        query.parse(s)
        return True
    except ValueError:
        return False


def _absorbable(tok: str) -> bool:
    """A bare word that can be part of a preceding value: not a keyword, not a new
    comparison, no operators/parens/quotes of its own."""
    if tok.lower() in _KEYWORDS or _CMP_RE.match(tok):
        return False
    return not any(c in tok for c in "()=~<>!\"'")


def _regroup(toks: list[str]) -> str:
    """Rebuild a query from shell word-tokens, gathering a comparison's value from
    the following bare words and quoting it — so `period=last year` becomes
    `period="last year"`. Only used as a fallback when the plain join fails."""
    out: list[str] = []
    i, n = 0, len(toks)
    while i < n:
        m = _CMP_RE.match(toks[i])
        if not m:
            out.append(toks[i])
            i += 1
            continue
        field, op, val = m.group(1), m.group(2), m.group(3)
        if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
            out.append(toks[i])        # value already quoted
            i += 1
            continue
        parts, j = [val], i + 1
        while j < n and _absorbable(toks[j]):
            parts.append(toks[j])
            j += 1
        value = " ".join(parts)
        if '"' not in value:
            out.append(f'{field}{op}"{value}"')
        elif "'" not in value:
            out.append(f"{field}{op}'{value}'")
        else:
            out.append(f"{field}{op}{value}")
        i = j
    return " ".join(out)


def _query_text(a: argparse.Namespace) -> str | None:
    """The effective query string from the -q/--query flag and any bare trailing
    QUERY words. When both are already valid they combine with AND (parens keep
    precedence); otherwise the whole token stream is regrouped so shell-split or
    shell-merged multi-word values (`period=last year`, `region='costa brava'`)
    still work."""
    flag = getattr(a, "query", None)
    words = list(getattr(a, "query_words", None) or [])
    if not flag and not words:
        return None
    words_str = " ".join(words)
    # fast path: each piece already valid -> AND-combine, preserving precedence
    if (flag is None or _parses(flag)) and (not words or _parses(words_str)):
        pieces = ([flag] if flag else []) + ([words_str] if words else [])
        combined = pieces[0] if len(pieces) == 1 else " and ".join(f"({p})" for p in pieces)
        if _parses(combined):
            return combined
    # repair path: treat everything as one token stream
    toks = ([flag] if flag else []) + words
    joined = " ".join(toks)
    if _parses(joined):
        return joined
    regrouped = _regroup(toks)
    return regrouped if _parses(regrouped) else joined


def _parse_query(a: argparse.Namespace):
    """The parsed query.Query (expr + sort/limit) for this invocation, or None."""
    text = _query_text(a)
    return query.parse(text) if text else None


def _predicate_from_args(a: argparse.Namespace):
    """Compile the effective query into a `(dive_record) -> bool` callable,
    or None when no filter expression was given."""
    q = _parse_query(a)
    if q is None or q.expr is None:
        return None
    return lambda rec: query.match(q.expr, rec)


def _plural(word: str) -> str:
    if word.endswith("y"):
        return word[:-1] + "ies"
    return word + "s"


def _describe_filters(a: argparse.Namespace) -> str:
    """Human-readable summary of the active filters (query + scope)."""
    p = []
    if getattr(a, "range", None):
        lo, hi = parse_number_range(a.range)
        if lo is not None and hi is not None and lo == hi:
            p.append(f"#{lo}")
        else:
            p.append(f"#{lo if lo is not None else '…'}–{hi if hi is not None else '…'}")
    qtext = _query_text(a)
    if qtext:
        p.append(f"query '{qtext}'")
    if a.all_divers:
        p.append("all divers")
    elif a.diver is not None:
        p.append(f"diver {a.diver}")
    if a.include_deleted:
        p.append("incl. deleted")
    return ", ".join(p) if p else "all dives"


def _header(text: str) -> None:
    """Print a header line, dimmed when writing to a terminal."""
    if sys.stdout.isatty():
        print(f"\033[2m{text}\033[0m")
    else:
        print(text)


# Per-column unit suffix (shown in list output) and whether to round to a
# whole number. Keys are column names as returned by the DB.
COLUMN_UNITS = {
    "Depth": "m",
    "DepthAvg": "m",
    "Divetime": "min",
    "Tanksize": "L",
    "Watertemp": "°C",
    "Airtemp": "°C",
    "O2": "%",
    "SAC": "L/min",
}
COLUMN_ROUND_INT = {"Divetime"}
COLUMN_ROUND_1 = {"SAC"}  # round to 1 decimal for display
# columns where a stored 0 means "no data" and should render blank, not "0".
# (Temperatures are excluded: their missing value is the -300 sentinel, so a
# stored 0 is a genuine 0 °C.)
COLUMN_ZERO_IS_EMPTY = {"Tanksize", "DepthAvg", "O2", "Weight"}


def _print_table(rows: list[dict], cols: list[str],
                 units: dict[str, str] | None = None) -> None:
    if not rows:
        print("(no results)")
        return
    units = units or {}
    cells = {
        c: [_fmt_cell(c, r.get(c), units) for r in rows]
        for c in cols
    }
    widths = {c: max([len(c)] + [len(v) for v in cells[c]]) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for i in range(len(rows)):
        print("  ".join(cells[c][i].ljust(widths[c]) for c in cols))


def _fmt_cell(col: str, v, units: dict[str, str]) -> str:
    if v is None or v == "" or (col in COLUMN_ZERO_IS_EMPTY and v == 0):
        return "—"
    # show entry time as HH:MM, dropping stored seconds
    if col == "Entrytime" and isinstance(v, str) and len(v) >= 5:
        return v[:5]
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        if col in COLUMN_ROUND_INT:
            num = round(v)
        elif col in COLUMN_ROUND_1:
            num = round(v, 1)
        else:
            num = v
        s = f"{num:g}" if isinstance(num, float) else str(num)
        unit = units.get(col)
        return f"{s} {unit}" if unit else s
    return _fmt(v)


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, str):
        return format_date_es(v)  # ISO dates -> DD-MM-YYYY, else unchanged
    return str(v)


def cmd_list(db: DiveMate, a: argparse.Namespace) -> None:
    # a leading N-M token is the dive-number range; the rest is the query
    words = list(a.query_words or [])
    a.range = words.pop(0) if words and _looks_like_range(words[0]) else None
    a.query_words = words
    f = _filter_from_args(a)
    q = _parse_query(a)
    pred = (lambda rec: query.match(q.expr, rec)) if (q and q.expr is not None) else None
    rows = db.dives(f, predicate=pred)
    # sort: query `sort FIELD [asc|desc]`, default newest-first by date
    sort_field = q.sort if (q and q.sort) else "date"
    desc = q.desc if (q and q.sort) else True
    key = query.sort_key(sort_field)
    present = [r for r in rows if key(r) is not None]
    missing = [r for r in rows if key(r) is None]
    present.sort(key=key, reverse=desc)
    rows = present + missing          # missing values always last
    if q and q.limit:
        rows = rows[:q.limit]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    cols = a.columns.split(",") if a.columns else \
        ["Number", "Divedate", "Entrytime", "Place", "Depth", "Divetime",
         "Tanksize", "Watertemp", "SAC"]
    _header(f"Dives — {_describe_filters(a)}")
    _print_table(rows, cols, units=COLUMN_UNITS)
    print(f"\n{len(rows)} dive(s)")


# the `info` detail view, grouped into sections (a rule is drawn between them).
# Each entry is (label, column, unit); "Buddies" and "Tanks" are rendered
# specially from the dive's resolved buddy list and tanks.
_INFO_SECTIONS = [
    [("Date", "Divedate", ""),
     ("Entry time", "Entrytime", "")],
    [("Place", "Place", ""),
     ("Region", "Region", ""),
     ("Country", "Country", ""),
     ("Buddies", "__buddies__", "")],
    [("Dive time", "Divetime", "min"),
     ("Max depth", "Depth", "m"),
     ("Avg depth", "DepthAvg", "m"),
     ("Water temp", "Watertemp", "°C")],
    [("Tanks", "__tanks__", ""),
     ("SAC (RMV)", "SAC", "L/min")],
]

_INFO_LABEL_W = 13


def _info_scalar(label: str, v, unit: str) -> str | None:
    """Render one 'label value' line, or None when there is no value."""
    if v in (None, "", 0):
        return None
    if isinstance(v, str):
        v = format_date_es(v)  # ISO date -> DD-MM-YYYY, else unchanged
    elif isinstance(v, float):
        v = f"{round(v, 1):g}"
    txt = f"{v} {unit}".strip() if unit else str(v)
    return f"{label:<{_INFO_LABEL_W}} {txt}"


def _info_tanks(r: dict) -> list[str]:
    out = []
    for i, t in enumerate(r.get("tanks") or [], 1):
        parts = []
        if t.get("Tanksize"):
            parts.append(f"{t['Tanksize']:g} L")
        if t.get("O2"):
            parts.append(f"O₂ {t['O2']:g}%")
        if t.get("He"):
            parts.append(f"He {t['He']:g}%")
        if t.get("PresS") and t.get("PresE"):
            parts.append(f"{t['PresS']:g}→{t['PresE']:g} bar")
        if t.get("Name"):
            parts.append(str(t["Name"]))
        if parts:
            out.append(f"{'Tank ' + str(i):<{_INFO_LABEL_W}} {'  '.join(parts)}")
    return out


def _info_rule() -> str:
    rule = "─" * (_INFO_LABEL_W + 14)
    return f"\033[2m{rule}\033[0m" if sys.stdout.isatty() else rule


def _print_dive(r: dict) -> None:
    rendered: list[list[str]] = []
    for section in _INFO_SECTIONS:
        lines: list[str] = []
        for label, col, unit in section:
            if col == "__buddies__":
                buddies = r.get("buddies") or ([r["Buddy"]] if r.get("Buddy") else [])
                if buddies:
                    lines.append(f"{label:<{_INFO_LABEL_W}} {', '.join(buddies)}")
            elif col == "__tanks__":
                lines.extend(_info_tanks(r))
            else:
                line = _info_scalar(label, r.get(col), unit)
                if line:
                    lines.append(line)
        if lines:
            rendered.append(lines)

    print(_dive_title(r))
    for lines in rendered:
        print(_info_rule())
        for line in lines:
            print(line)
    if r.get("Comments"):
        print(_info_rule())
        print(r["Comments"])


def _dive_title(r: dict) -> str:
    """'<number> - <place>' header for a dive (number alone if no place)."""
    num = str(r.get("Number") or "").strip()
    place = (r.get("Place") or "").strip()
    return f"{num} - {place}" if (num and place) else (num or place)


def cmd_info(db: DiveMate, a: argparse.Namespace) -> None:
    tok = str(a.number).strip().lower()
    if tok in ("last", "latest"):
        number = db.boundary_number(True, diver=a.diver, all_divers=a.all_divers,
                                    include_deleted=a.include_deleted)
    elif tok in ("first", "oldest"):
        number = db.boundary_number(False, diver=a.diver, all_divers=a.all_divers,
                                    include_deleted=a.include_deleted)
    else:
        try:
            number = int(a.number)
        except ValueError:
            raise ValueError(f"invalid dive {a.number!r} (a number, 'last' or 'first')")
    if number is None:
        print("no dives")
        return
    rows = db.dive_full(number, diver=a.diver, all_divers=a.all_divers,
                        include_deleted=a.include_deleted)
    prof = db.dive_profile(number, diver=a.diver, all_divers=a.all_divers)
    if a.json:
        for r in rows:
            r["profile"] = prof
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    if not rows:
        print(f"no dive #{number}")
        return
    for i, r in enumerate(rows):
        if i:
            print("\n" + "-" * 40)
        _print_dive(r)
    from .chart import profile_chart
    if prof:
        title = _dive_title(rows[0])
        print()   # blank line to separate the chart from the dive details
        for line in profile_chart(prof["times"], prof["depths"], prof["temps"],
                                  title=title):
            print(line)
    else:
        print("\n(no depth profile recorded for this dive)")
    if len(rows) > 1:
        print(f"\n({len(rows)} dives share #{number} — "
              f"use --diver ID to pick one)")


# numeric fields that `graph` can plot: user alias -> (row key, unit)
GRAPH_FIELDS = {
    "depth": ("Depth", "m"),
    "maxdepth": ("Depth", "m"),
    "avgdepth": ("DepthAvg", "m"),
    "depthavg": ("DepthAvg", "m"),
    "divetime": ("Divetime", "min"),
    "time": ("Divetime", "min"),
    "watertemp": ("Watertemp", "°C"),
    "temp": ("Watertemp", "°C"),
    "sac": ("SAC", "L/min"),
    "rmv": ("SAC", "L/min"),
}


def _fill_periods(labels: list[str], by: str) -> list[str]:
    """Contiguous period labels from min..max so empty periods show as 0."""
    if not labels:
        return []
    if by == "year":
        lo, hi = int(labels[0]), int(labels[-1])
        return [str(y) for y in range(lo, hi + 1)]
    if by == "week":
        # labels are ISO Monday dates; step a week at a time
        import datetime
        lo = datetime.date.fromisoformat(labels[0])
        hi = datetime.date.fromisoformat(labels[-1])
        out, d = [], lo
        while d <= hi:
            out.append(d.isoformat())
            d += datetime.timedelta(days=7)
        return out
    # month: 'YYYY-MM'
    first, last = labels[0], labels[-1]
    y0, m0 = int(first[:4]), int(first[5:7])
    y1, m1 = int(last[:4]), int(last[5:7])
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def cmd_graph(db: DiveMate, a: argparse.Namespace) -> None:
    key = a.field.strip().lower()
    if key in ("count", "dives"):
        _graph_count(db, a)
        return
    if key not in GRAPH_FIELDS:
        raise ValueError(f"cannot graph {a.field!r}. Fields: "
                         + ", ".join(sorted(set(f for f, _ in GRAPH_FIELDS.values()))))
    col, unit = GRAPH_FIELDS[key]
    rows = db.dives(_filter_from_args(a), order="Divedate", desc=a.desc,
                    predicate=_predicate_from_args(a))
    # value per dive; a 0 that means "no data" becomes a gap
    ys: list[float | None] = []
    for r in rows:
        v = r.get(col)
        if v in (None, "") or (col in COLUMN_ZERO_IS_EMPTY and v == 0):
            ys.append(None)
        else:
            ys.append(v)
    pairs = [{"date": r["Divedate"], "number": r["Number"], "value": y}
             for r, y in zip(rows, ys)]
    if a.json:
        print(json.dumps(pairs, ensure_ascii=False, indent=2))
        return
    if not any(y is not None for y in ys):
        print("(no data to plot)")
        return
    xs = list(range(len(rows)))
    xticks = _date_xticks(rows)
    from .chart import series_chart
    title = f"{col} — {_describe_filters(a)}"
    for line in series_chart(xs, ys, title=title, ylabel=f"{col} ({unit})" if unit
                             else col, xticks=xticks):
        print(line)


def _graph_count(db: DiveMate, a: argparse.Namespace) -> None:
    by = (a.by or "month").strip().lower()
    if by not in ("month", "year", "week"):
        raise ValueError("--by must be 'week', 'month' or 'year'")
    filt = _filter_from_args(a)
    rows = db.group_count(by, filt, order="value", predicate=_predicate_from_args(a))
    counts = {r["value"]: r["dives"] for r in rows}
    labels = _fill_periods(sorted(counts), by)
    ys: list[float | None] = [counts.get(lbl, 0) for lbl in labels]
    if a.json:
        print(json.dumps([{"period": lbl, "dives": counts.get(lbl, 0)}
                          for lbl in labels], ensure_ascii=False, indent=2))
        return
    if not labels:
        print("(no data to plot)")
        return
    xs = list(range(len(labels)))
    step = max(1, len(labels) // 8)
    xticks = [(float(i), labels[i]) for i in range(0, len(labels), step)]
    from .chart import series_chart
    # title shows the plotted span (first..last period) plus the active filters
    rest = _describe_filters(a)
    span = f"{labels[0]} to {labels[-1]}"
    title = f"dives per {by} — {span}" + (f", {rest}" if rest != "all dives" else "")
    for line in series_chart(xs, ys, title=title, ylabel="dives",
                             xticks=xticks, color="green+", int_y=True):
        print(line)


def _date_xticks(rows: list[dict], n: int = 6) -> list[tuple[float, str]]:
    """Pick ~n evenly spaced row indices, labelled with their dive date."""
    if not rows:
        return []
    idx = sorted({round(i * (len(rows) - 1) / max(1, n - 1)) for i in range(n)})
    out = []
    for i in idx:
        iso = rows[i].get("Divedate") or ""
        label = iso
        if len(iso) == 10:  # YYYY-MM-DD -> DD-MM-YY
            y, m, d = iso.split("-")
            label = f"{d}-{m}-{y[2:]}"
        out.append((float(i), label))
    return out


def cmd_count(db: DiveMate, a: argparse.Namespace) -> None:
    print(db.count(_filter_from_args(a), predicate=_predicate_from_args(a)))


def cmd_stats(db: DiveMate, a: argparse.Namespace) -> None:
    s = db.stats(_filter_from_args(a), predicate=_predicate_from_args(a))
    if a.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    _header(f"Stats — {_describe_filters(a)}")
    if s.get("count", 0) == 0:
        print("No dives match.")
        return
    for label, value in _stats_lines(s):
        print(f"{label:<20} {value}")


def _hm(minutes: float) -> str:
    """Minutes -> 'Hh Mm' (e.g. 788 h 41 min)."""
    total = round(minutes)
    return f"{total // 60} h {total % 60:02d} min"


def _stats_lines(s: dict) -> list[tuple[str, str]]:
    """Human-readable (label, value-with-units) pairs, in display order."""
    def num(x, unit, nd=1):
        return f"{x:.{nd}f} {unit}" if x is not None else "—"

    lines = [("Dives", str(s["count"]))]
    if s.get("total_divetime_min") is not None:
        lines.append(("Total dive time",
                      f"{_hm(s['total_divetime_min'])}  ({s['total_divetime_h']:.1f} h)"))
    lines += [
        ("Avg. dive time", num(s.get("avg_divetime_min"), "min")),
        ("Max depth", num(s.get("max_depth_m"), "m")),
        ("Avg. depth", num(s.get("avg_depth_m"), "m")),
        ("Avg. water temp", num(s.get("avg_watertemp_c"), "°C")),
    ]
    return lines


def cmd_group(db: DiveMate, a: argparse.Namespace) -> None:
    rows = db.group_count(a.by, _filter_from_args(a), limit=a.limit, order=a.sort,
                          predicate=_predicate_from_args(a))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    # virtual split dimensions have no natural plural noun — call them "groups"
    key = a.by.strip().lower()
    field = "groups" if key in ("weekend", "weekday", "night", "day") \
        else _plural(key)
    scope = f"Top {a.limit} {field}" if a.limit else field.capitalize()
    _header(f"{scope} by number of dives — {_describe_filters(a)}")
    if a.sort == "count":
        # ranked -> number the list
        numbered = [{"#": i, **r} for i, r in enumerate(rows, 1)]
        _print_table(numbered, ["#", "value", "dives"])
    else:
        _print_table(rows, ["value", "dives"])
    total = sum(r["dives"] for r in rows)
    ng = len(rows)
    name = field if ng != 1 else a.by.lower()
    print(f"\n{total} dive{'s' if total != 1 else ''} in {ng} {name}")


def cmd_years(db: DiveMate, a: argparse.Namespace) -> None:
    rows = db.dives_per_year(_filter_from_args(a), predicate=_predicate_from_args(a))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    _print_table(rows, ["year", "n"])


def cmd_tanks(db: DiveMate, a: argparse.Namespace) -> None:
    sizes = db.tank_sizes()
    if a.json:
        print(json.dumps(sizes))
        return
    print(" ".join(_fmt(s) for s in sizes))


def cmd_tags(db: DiveMate, a: argparse.Namespace) -> None:
    rows = db.tags(_filter_from_args(a), predicate=_predicate_from_args(a))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    _print_table([{"tag": "#" + r["tag"], "dives": r["dives"]} for r in rows],
                 ["tag", "dives"])


def cmd_divers(db: DiveMate, a: argparse.Namespace) -> None:
    rows = db.divers()
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for r in rows:
        mark = " *" if r["owner"] else ""
        print(f"{r['id']:>3}  {r['dives']:>4}  {r['name']}{mark}")
    print("\n* = owner (used by default)")


def _iter_subparsers(parser: argparse.ArgumentParser) -> dict:
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            return act.choices
    return {}


def _option_strings(p: argparse.ArgumentParser) -> list[str]:
    out: list[str] = []
    for act in p._actions:
        out.extend(act.option_strings)
    return out


def _bash_completion_script() -> str:
    """Generate a static bash completion from the live argparse parser, so it
    always matches the current commands and options."""
    parser = build_parser()
    subs = _iter_subparsers(parser)
    names = " ".join(subs)
    global_opts = " ".join(_option_strings(parser))
    # value suggestions for positionals that take a known set of words
    from .db import _GROUPABLE
    positional = {
        "group": sorted(_GROUPABLE) + ["year", "month", "week", "weekend", "night", "buddy", "region"],
        "graph": sorted(GRAPH_FIELDS) + ["count"],
        "info": ["last", "first"],
        "completion": ["bash"],
    }
    cases = []
    for name, sp in subs.items():
        words = _option_strings(sp) + positional.get(name, [])
        cases.append(f"    {name}) opts=\"{' '.join(words)}\" ;;")
    cases_block = "\n".join(cases)
    return f"""\
# bash completion for dmcli — generated by `dmcli completion bash`.
# Regenerate after changing the CLI; it is derived from the argparse parser.
_dmcli() {{
    local cur cmd i
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    local global="{global_opts}"
    cmd=""
    for ((i=1; i<COMP_CWORD; i++)); do
        case "${{COMP_WORDS[i]}}" in
            -*) ;;
            *) cmd="${{COMP_WORDS[i]}}"; break ;;
        esac
    done
    if [[ -z "$cmd" ]]; then
        COMPREPLY=( $(compgen -W "{names} $global" -- "$cur") )
        return
    fi
    local opts=""
    case "$cmd" in
{cases_block}
    esac
    COMPREPLY=( $(compgen -W "$opts $global" -- "$cur") )
}}
complete -o default -F _dmcli dmcli
"""


def cmd_completion(db: None, a: argparse.Namespace) -> None:
    print(_bash_completion_script(), end="")


def cmd_pull(db: None, a: argparse.Namespace) -> None:
    # imported lazily so the rest of the CLI works without google-auth installed
    from .gdrive import GDriveConfig, download
    cfg = GDriveConfig.from_env(key_file=a.key, folder_id=a.folder, file_name=a.name)
    out = a.out or DEFAULT_DB
    meta = download(cfg, out)
    print(f"downloaded {meta['name']} ({meta['bytes']:,} bytes) -> {meta['path']}")
    print(f"  modified on Drive: {meta.get('modifiedTime', '?')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dmcli", description="Query a DiveMate .ddb logbook.")
    p.add_argument("--db", default=DEFAULT_DB, help=f"path to .ddb (default: {DEFAULT_DB})")
    p.add_argument("--json", action="store_true", help="output JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list dives matching a query "
                        "(sort/limit go in the query, e.g. '... sort depth desc limit 10')")
    _add_filter_args(pl)
    pl.add_argument("--columns", help="comma-separated column list")
    pl.set_defaults(func=cmd_list)

    pi = sub.add_parser("info", help="show every detail of one dive by its number")
    pi.add_argument("number", metavar="N",
                    help="dive number (e.g. 501), or 'last' / 'first'")
    pi.add_argument("--diver", type=int, metavar="ID",
                    help="pick a diver if a number is shared (default: the owner)")
    pi.add_argument("--all-divers", action="store_true",
                    help="search every diver profile")
    pi.add_argument("--include-deleted", action="store_true",
                    help="include dives DiveMate soft-deleted (Status=2)")
    pi.set_defaults(func=cmd_info)

    pc = sub.add_parser("count", help="count matching dives")
    _add_filter_args(pc)
    pc.set_defaults(func=cmd_count)

    pgr = sub.add_parser("graph", help="plot a numeric field over time (+ filters)")
    pgr.add_argument("field", help="field to plot: depth (maxdepth), avgdepth, "
                     "divetime, watertemp, sac; or 'count' for dives per period")
    pgr.add_argument("--by", choices=("week", "month", "year"),
                     help="for 'count': bucket size (default month)")
    pgr.add_argument("--desc", action="store_true", help="newest first")
    _add_filter_args(pgr)
    pgr.set_defaults(func=cmd_graph)

    ps = sub.add_parser("stats", help="aggregate stats over matching dives")
    _add_filter_args(ps)
    ps.set_defaults(func=cmd_stats)

    pg = sub.add_parser("group", help="count dives grouped by a field (+ filters)")
    pg.add_argument("by", help="field to group by: Place, Country, Region, Buddy, "
                    "Computer, Divesuit, Water, Tanksize, Boat, Rating, ... or year / "
                    "month / week / weekend / night")
    pg.add_argument("--limit", type=int, help="show only the top N groups")
    pg.add_argument("--sort", choices=("count", "value"), default="count",
                    help="order by dive count (default) or by the group value")
    _add_filter_args(pg)
    pg.set_defaults(func=cmd_group)

    py = sub.add_parser("years", help="dive count per year")
    _add_filter_args(py)
    py.set_defaults(func=cmd_years)

    pt = sub.add_parser("tanks", help="list distinct tank sizes")
    pt.set_defaults(func=cmd_tanks)

    ptag = sub.add_parser("tags", help="list #tags found in comments, with counts")
    _add_filter_args(ptag)
    ptag.set_defaults(func=cmd_tags)

    pd = sub.add_parser("divers", help="list diver profiles and dive counts")
    pd.set_defaults(func=cmd_divers)

    pcomp = sub.add_parser("completion",
                           help="print a bash completion script for dmcli")
    pcomp.add_argument("shell", nargs="?", choices=["bash"], default="bash")
    pcomp.set_defaults(func=cmd_completion, no_db=True)

    pp = sub.add_parser("pull", help="download the logbook from Google Drive")
    pp.add_argument("--key", help="service-account JSON key (env DIVEMATE_GDRIVE_KEY)")
    pp.add_argument("--folder", help="Drive folder ID (env DIVEMATE_GDRIVE_FOLDER)")
    pp.add_argument("--name", help="file name in the folder (default DiveMate.ddb)")
    pp.add_argument("--out", help=f"where to save it (default {DEFAULT_DB})")
    pp.set_defaults(func=cmd_pull, no_db=True)

    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if getattr(a, "no_db", False):
            # commands like 'pull' don't need (or may create) the DB
            a.func(None, a)
        else:
            with DiveMate(a.db) as db:
                a.func(db, a)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
