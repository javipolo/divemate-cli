"""Command-line frontend for the DiveMate query library."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .db import DiveMate, DiveFilter
from .dates import parse_date, parse_period, format_date_es

DEFAULT_DB = os.environ.get("DIVEMATE_DB", "DiveMate.ddb")


def _add_filter_args(p: argparse.ArgumentParser) -> None:
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
    p.add_argument("--buddy", help="buddy substring")
    p.add_argument("--comment", help="text to find in the comments")
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
        place=a.place, country=a.country, buddy=a.buddy,
        comment=comment,
        min_depth=a.min_depth, max_depth=a.max_depth,
        min_divetime=a.min_divetime, water=a.water,
        min_sac=a.min_sac, max_sac=a.max_sac,
        weekend=a.weekend, night=a.night,
        include_deleted=a.include_deleted,
        diver=a.diver, all_divers=a.all_divers,
    )


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
    if a.buddy:
        p.append(f"buddy '{a.buddy}'")
    if getattr(a, "tag", None):
        p.append(f"#{a.tag.lstrip('#')}")
    if a.comment:
        p.append(f"comment '{a.comment}'")
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
    f = _filter_from_args(a)
    rows = db.dives(f, desc=a.desc)
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    cols = a.columns.split(",") if a.columns else \
        ["Number", "Divedate", "Entrytime", "Place", "Depth", "Divetime",
         "Tanksize", "Watertemp", "SAC"]
    _header(f"Dives — {_describe_filters(a)}")
    _print_table(rows, cols, units=COLUMN_UNITS)
    print(f"\n{len(rows)} dive(s)")


# (label, column, unit) for the `info` detail view, in display order.
_INFO_FIELDS = [
    ("Dive #", "Number", ""),
    ("Date", "Divedate", ""),
    ("Entry time", "Entrytime", ""),
    ("Country", "Country", ""),
    ("Place", "Place", ""),
    ("Dive time", "Divetime", "min"),
    ("Max depth", "Depth", "m"),
    ("Avg depth", "DepthAvg", "m"),
    ("SAC (RMV)", "SAC", "L/min"),
    ("Water temp", "Watertemp", "°C"),
    ("Air temp", "Airtemp", "°C"),
    ("Surface int.", "Surfint", ""),
    ("Computer", "Computer", ""),
    ("Dive suit", "Divesuit", ""),
    ("Weight", "Weight", "kg"),
    ("Dive type", "Divetype", ""),
    ("Rating", "Rating", ""),
    ("Boat", "Boat", ""),
    ("Weather", "Weather", ""),
    ("Waves", "Waves", ""),
    ("Current", "UWCurrent", ""),
    ("Divemaster", "Divemaster", ""),
]


def _print_dive(r: dict) -> None:
    water = {1: "salt", 0: "fresh"}.get(r.get("Water"))
    for label, col, unit in _INFO_FIELDS:
        v = r.get(col)
        if v in (None, "", 0) and col not in ("Number",):
            continue
        if col == "Divedate":
            v = format_date_es(v)
        elif isinstance(v, float):
            v = f"{round(v, 1):g}"
        txt = f"{v} {unit}".strip() if unit else str(v)
        print(f"{label:<14} {txt}")
    if water:
        print(f"{'Water':<14} {water}")
    buddies = r.get("buddies") or ([r["Buddy"]] if r.get("Buddy") else [])
    if buddies:
        print(f"{'Buddies':<14} {', '.join(buddies)}")
    tanks = r.get("tanks") or []
    for i, t in enumerate(tanks, 1):
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
            print(f"{'Tank ' + str(i):<14} {'  '.join(parts)}")
    if r.get("Comments"):
        print(f"\n{r['Comments']}")


def cmd_info(db: DiveMate, a: argparse.Namespace) -> None:
    rows = db.dive_full(a.number, diver=a.diver, all_divers=a.all_divers,
                        include_deleted=a.include_deleted)
    prof = db.dive_profile(a.number, diver=a.diver, all_divers=a.all_divers)
    if a.json:
        for r in rows:
            r["profile"] = prof
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    if not rows:
        print(f"no dive #{a.number}")
        return
    for i, r in enumerate(rows):
        if i:
            print("\n" + "-" * 40)
        _print_dive(r)
    from .chart import profile_chart
    if prof:
        place = rows[0].get("Place")
        title = f"Dive #{a.number}" + (f" - {place}" if place else "")
        for line in profile_chart(prof["times"], prof["depths"], prof["temps"],
                                  show_temp=a.temp, title=title):
            print(line)
    else:
        print("\n(no depth profile recorded for this dive)")
    if len(rows) > 1:
        print(f"\n({len(rows)} dives share #{a.number} — "
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
    "airtemp": ("Airtemp", "°C"),
    "o2": ("O2", "%"),
    "tanksize": ("Tanksize", "L"),
    "sac": ("SAC", "L/min"),
    "rmv": ("SAC", "L/min"),
    "number": ("Number", ""),
}


def cmd_graph(db: DiveMate, a: argparse.Namespace) -> None:
    key = a.field.strip().lower()
    if key not in GRAPH_FIELDS:
        raise ValueError(f"cannot graph {a.field!r}. Fields: "
                         + ", ".join(sorted(set(f for f, _ in GRAPH_FIELDS.values()))))
    col, unit = GRAPH_FIELDS[key]
    rows = db.dives(_filter_from_args(a), order="Divedate", desc=a.desc)
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
    print(db.count(_filter_from_args(a)))


def cmd_stats(db: DiveMate, a: argparse.Namespace) -> None:
    s = db.stats(_filter_from_args(a))
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
    rows = db.group_count(a.by, _filter_from_args(a), limit=a.limit, order=a.sort)
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
    rows = db.dives_per_year(_filter_from_args(a))
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
    rows = db.tags(_filter_from_args(a))
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
    pl.add_argument("--desc", action="store_true", help="newest first")
    pl.set_defaults(func=cmd_list)

    pi = sub.add_parser("info", help="show every detail of one dive by its number")
    pi.add_argument("number", type=int, help="dive number, e.g. 501")
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
    pgr.add_argument("field", help="field to plot: depth, avgdepth, divetime, "
                     "watertemp, airtemp, o2, tanksize, sac, number")
    pgr.add_argument("--desc", action="store_true", help="newest first")
    _add_filter_args(pgr)
    pgr.set_defaults(func=cmd_graph)

    ps = sub.add_parser("stats", help="aggregate stats over matching dives")
    _add_filter_args(ps)
    ps.set_defaults(func=cmd_stats)

    pg = sub.add_parser("group", help="count dives grouped by a field (+ filters)")
    pg.add_argument("by", help="field to group by: Place, Country, Buddy, Computer, "
                    "Divesuit, Water, Tanksize, Boat, Rating, ... or year / month / "
                    "weekend / night")
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
