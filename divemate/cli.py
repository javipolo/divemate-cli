"""Command-line frontend for the DiveMate query library."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import query
from .db import DiveMate, DiveFilter
from .dates import parse_date, parse_period, format_date_es

DEFAULT_DB = os.environ.get("DIVEMATE_DB", "DiveMate.ddb")


def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("-q", "--query", metavar="EXPR",
                   help="filter expression, e.g. '(region=Mallorca and place~toro) "
                        "or depth>=30'. Operators: text = (exact) ~ (contains) "
                        "!= !~; numbers = != < <= > >=; booleans night/weekend. "
                        "Combines with the other filters using AND.")
    p.add_argument("--year", type=int, help="dive year, e.g. 2026")
    p.add_argument("--period", metavar="PERIOD",
                   help="quick range: 'this month', 'this year', 'year 2024', "
                        "'last month', 'last 30 days', today, ...")
    p.add_argument("--from", dest="date_from", help="date from (DD-MM-YYYY)")
    p.add_argument("--to", dest="date_to", help="date to (DD-MM-YYYY)")
    p.add_argument("--tank", "--tanksize", dest="tanksize", type=float,
                   help="tank size in litres (matches any tank on the dive)")
    p.add_argument("--exclude-tank", dest="exclude_tanksize", type=float, metavar="LITRES",
                   help="exclude dives that have a tank of this size (e.g. --tag sidemount "
                        "--exclude-tank 11.1 = sidemount dives missing an 11.1)")
    p.add_argument("--place", help="place substring")
    p.add_argument("--country", help="country substring")
    p.add_argument("--region", help="region substring (dive-site region)")
    p.add_argument("--buddy", help="buddy substring")
    p.add_argument("--comment", help="text to find in the comments")
    # negative filters (repeatable): exclude dives matching any given substring
    p.add_argument("--exclude-place", action="append", metavar="SUBSTR",
                   help="exclude dives whose place matches (repeatable)")
    p.add_argument("--exclude-country", action="append", metavar="SUBSTR",
                   help="exclude dives whose country matches (repeatable)")
    p.add_argument("--exclude-region", action="append", metavar="SUBSTR",
                   help="exclude dives whose region matches (repeatable)")
    p.add_argument("--exclude-buddy", action="append", metavar="SUBSTR",
                   help="exclude dives with a matching buddy (repeatable)")
    p.add_argument("--exclude-comment", action="append", metavar="SUBSTR",
                   help="exclude dives whose comments match (repeatable)")
    p.add_argument("--tag", help="find a #tag in the comments (give it without the #)")
    p.add_argument("--min-depth", type=float, help="minimum max-depth (m)")
    p.add_argument("--max-depth", type=float, help="maximum max-depth (m)")
    p.add_argument("--min-divetime", type=float, help="minimum dive time (min)")
    p.add_argument("--min-sac", type=float, metavar="LMIN",
                   help="minimum SAC / RMV (L/min)")
    p.add_argument("--max-sac", type=float, metavar="LMIN",
                   help="maximum SAC / RMV (L/min)")
    p.add_argument("--water", type=int, choices=(0, 1), help="1=salt, 0=fresh")
    wk = p.add_mutually_exclusive_group()
    wk.add_argument("--weekend", dest="weekend", action="store_true", default=None,
                    help="only dives on Sat/Sun")
    wk.add_argument("--weekday", dest="weekend", action="store_false", default=None,
                    help="only dives on Mon–Fri")
    nt = p.add_mutually_exclusive_group()
    nt.add_argument("--night", dest="night", action="store_true", default=None,
                    help="only night dives (entry 18:00–06:00)")
    nt.add_argument("--day", dest="night", action="store_false", default=None,
                    help="only day dives (entry 06:00–18:00)")
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
    # --tag foo is sugar for --comment "#foo"; explicit --comment wins
    comment = a.comment
    if comment is None and getattr(a, "tag", None):
        comment = "#" + a.tag.lstrip("#")

    # resolve dates: --period sets both bounds; explicit --from/--to override
    date_from, date_to = None, None
    if a.period:
        date_from, date_to = parse_period(a.period)
    if a.date_from:
        date_from = parse_date(a.date_from)
    if a.date_to:
        date_to = parse_date(a.date_to)

    # optional positional dive-number range, e.g. `list 100-130` or `list 100`
    number_from, number_to = None, None
    if getattr(a, "range", None):
        number_from, number_to = parse_number_range(a.range)

    return DiveFilter(
        year=a.year, date_from=date_from, date_to=date_to,
        number_from=number_from, number_to=number_to,
        tanksize=a.tanksize, exclude_tanksize=a.exclude_tanksize,
        place=a.place, country=a.country, region=a.region, buddy=a.buddy,
        comment=comment,
        exclude_place=a.exclude_place or [],
        exclude_country=a.exclude_country or [],
        exclude_region=a.exclude_region or [],
        exclude_buddy=a.exclude_buddy or [],
        exclude_comment=a.exclude_comment or [],
        min_depth=a.min_depth, max_depth=a.max_depth,
        min_divetime=a.min_divetime, water=a.water,
        min_sac=a.min_sac, max_sac=a.max_sac,
        weekend=a.weekend, night=a.night,
        include_deleted=a.include_deleted,
        diver=a.diver, all_divers=a.all_divers,
    )


def _predicate_from_args(a: argparse.Namespace):
    """Compile the --query expression into a `(dive_record) -> bool` callable,
    or None when no --query was given."""
    text = getattr(a, "query", None)
    return query.predicate(text) if text else None


def _describe_period(period: str) -> str:
    """Resolve a period to a concrete label: 'year 2026', 'month 2026-02',
    a single date, or a 'DD-MM-YYYY to DD-MM-YYYY' range."""
    try:
        lo, hi = parse_period(period)
    except ValueError:
        return period
    if lo == hi:
        return format_date_es(lo)
    if lo.endswith("-01-01") and hi.endswith("-12-31") and lo[:4] == hi[:4]:
        return f"year {lo[:4]}"
    if lo[:7] == hi[:7] and lo.endswith("-01"):
        return f"month {lo[:7]}"
    return f"{format_date_es(lo)} to {format_date_es(hi)}"


def _plural(word: str) -> str:
    if word.endswith("y"):
        return word[:-1] + "ies"
    return word + "s"


def _describe_filters(a: argparse.Namespace) -> str:
    """Human-readable summary of the active filters, as the user typed them."""
    p = []
    if getattr(a, "range", None):
        lo, hi = parse_number_range(a.range)
        if lo is not None and hi is not None and lo == hi:
            p.append(f"#{lo}")
        else:
            p.append(f"#{lo if lo is not None else '…'}–{hi if hi is not None else '…'}")
    if getattr(a, "period", None):
        p.append(_describe_period(a.period))
    if a.year:
        p.append(f"year {a.year}")
    if a.date_from or a.date_to:
        lo = format_date_es(parse_date(a.date_from)) if a.date_from else "…"
        hi = format_date_es(parse_date(a.date_to)) if a.date_to else "…"
        p.append(f"{lo} to {hi}")
    if a.tanksize is not None:
        p.append(f"tank {a.tanksize:g}L")
    if getattr(a, "exclude_tanksize", None) is not None:
        p.append(f"without {a.exclude_tanksize:g}L tank")
    if a.place:
        p.append(f"place '{a.place}'")
    if a.country:
        p.append(f"country '{a.country}'")
    if getattr(a, "region", None):
        p.append(f"region '{a.region}'")
    if a.buddy:
        p.append(f"buddy '{a.buddy}'")
    if getattr(a, "tag", None):
        p.append(f"#{a.tag.lstrip('#')}")
    if a.comment:
        p.append(f"comment '{a.comment}'")
    for label, terms in (("place", getattr(a, "exclude_place", None)),
                         ("country", getattr(a, "exclude_country", None)),
                         ("region", getattr(a, "exclude_region", None)),
                         ("buddy", getattr(a, "exclude_buddy", None)),
                         ("comment", getattr(a, "exclude_comment", None))):
        if terms:
            p.append(f"not {label} " + ", ".join(f"'{t}'" for t in terms))
    if a.min_depth is not None:
        p.append(f"depth ≥{a.min_depth:g}m")
    if a.max_depth is not None:
        p.append(f"depth ≤{a.max_depth:g}m")
    if a.min_divetime is not None:
        p.append(f"≥{a.min_divetime:g} min")
    if getattr(a, "min_sac", None) is not None:
        p.append(f"SAC ≥{a.min_sac:g} L/min")
    if getattr(a, "max_sac", None) is not None:
        p.append(f"SAC ≤{a.max_sac:g} L/min")
    if a.water is not None:
        p.append("salt water" if a.water == 1 else "fresh water")
    if getattr(a, "weekend", None) is not None:
        p.append("weekend" if a.weekend else "weekday")
    if getattr(a, "night", None) is not None:
        p.append("night dives" if a.night else "day dives")
    if a.all_divers:
        p.append("all divers")
    elif a.diver is not None:
        p.append(f"diver {a.diver}")
    if a.include_deleted:
        p.append("incl. deleted")
    if getattr(a, "query", None):
        p.append(f"query '{a.query}'")
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


# user alias -> dive column for `list --sort` (SAC is sorted in Python)
SORT_FIELDS = {
    "number": "Number", "date": "Divedate", "divedate": "Divedate",
    "entrytime": "Entrytime", "time": "Divetime", "divetime": "Divetime",
    "depth": "Depth", "maxdepth": "Depth", "avgdepth": "DepthAvg",
    "depthavg": "DepthAvg", "watertemp": "Watertemp", "temp": "Watertemp",
    "airtemp": "Airtemp", "o2": "O2", "tanksize": "Tanksize",
    "place": "Place", "country": "Country", "region": "Region", "sac": "SAC",
}


def cmd_list(db: DiveMate, a: argparse.Namespace) -> None:
    f = _filter_from_args(a)
    key = (a.sort or "date").strip().lower()
    if key not in SORT_FIELDS:
        raise ValueError(f"cannot sort by {a.sort!r}. Fields: "
                         + ", ".join(sorted(set(SORT_FIELDS.values()))))
    col = SORT_FIELDS[key]
    pred = _predicate_from_args(a)
    desc = not a.asc  # default: highest first
    if col == "SAC":
        rows = db.dives(f, predicate=pred)
        # None sorts last regardless of direction
        rows.sort(key=lambda r: (r.get("SAC") is None,
                                 -(r.get("SAC") or 0) if desc else (r.get("SAC") or 0)))
    else:
        rows = db.dives(f, order=col, desc=desc, predicate=pred)
    if a.limit:
        rows = rows[:a.limit]
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
    [("Dive #", "Number", "")],
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

    for i, lines in enumerate(rendered):
        if i:
            print(_info_rule())
        for line in lines:
            print(line)
    if r.get("Comments"):
        print(_info_rule())
        print(r["Comments"])


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
        place = rows[0].get("Place")
        title = f"Dive #{number}" + (f" - {place}" if place else "")
        for line in profile_chart(prof["times"], prof["depths"], prof["temps"],
                                  show_temp=a.temp, title=title):
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
    """Contiguous period labels from min..max so empty months/years show as 0."""
    if not labels:
        return []
    if by == "year":
        lo, hi = int(labels[0]), int(labels[-1])
        return [str(y) for y in range(lo, hi + 1)]
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
    if by not in ("month", "year"):
        raise ValueError("--by must be 'month' or 'year'")
    filt = _filter_from_args(a)
    rows = db.group_count(by, filt, order="value", predicate=_predicate_from_args(a))
    counts = {r["value"]: r["dives"] for r in rows}
    # --from / --to also fix the axis extent (zero-filled up to those dates)
    keys = set(counts)
    period = (lambda iso: iso[:4]) if by == "year" else (lambda iso: iso[:7])
    if filt.date_from:
        keys.add(period(filt.date_from))
    if filt.date_to:
        keys.add(period(filt.date_to))
    labels = _fill_periods(sorted(keys), by)
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
    # title shows the actual plotted span (first..last period) plus any non-date
    # filters; the start is the first dive when no --from was given
    a2 = argparse.Namespace(**vars(a))
    a2.period = a2.date_from = a2.date_to = a2.year = None
    rest = _describe_filters(a2)
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
        "group": sorted(_GROUPABLE) + ["year", "month", "weekend", "night", "buddy", "region"],
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

    pl = sub.add_parser("list", help="list dives matching filters")
    pl.add_argument("range", nargs="?", metavar="N-M",
                    help="dive number range, e.g. 100-130 (or a single number, "
                         "or open-ended 100- / -130)")
    _add_filter_args(pl)
    pl.add_argument("--columns", help="comma-separated column list")
    pl.add_argument("--sort", metavar="FIELD",
                    help="sort by: date (default), number, depth, avgdepth, "
                         "divetime, watertemp, tanksize, sac, place, country, region")
    pl.add_argument("--asc", action="store_true",
                    help="ascending order (default: descending, highest first)")
    pl.add_argument("--limit", type=int, help="show only the first N dives")
    pl.set_defaults(func=cmd_list)

    pi = sub.add_parser("info", help="show every detail of one dive by its number")
    pi.add_argument("number", metavar="N",
                    help="dive number (e.g. 501), or 'last' / 'first'")
    pi.add_argument("-t", "--temp", action="store_true",
                    help="also plot temperature on the depth profile")
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
    pgr.add_argument("--by", choices=("month", "year"),
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
                    "month / weekend / night")
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
