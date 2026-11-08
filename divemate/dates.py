"""Date parsing/formatting helpers.

The DB stores dates as ISO ``YYYY-MM-DD`` text. Users prefer Spanish
``DD-MM-YYYY`` for in/out, plus quick relative periods like "this month".
This module is pure (no DB, no I/O) so any frontend can reuse it.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import re

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ES_RE = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")


def parse_date(s: str, today: _dt.date | None = None) -> str:
    """Accept ``DD-MM-YYYY``, ISO ``YYYY-MM-DD`` or the relative keywords
    ``today``/``now`` and ``yesterday`` (Spanish ``hoy``/``ayer``); return ISO.

    Raises ValueError on anything else.
    """
    s = s.strip()
    k = s.lower()
    if k in ("today", "now", "hoy"):
        return (today or _dt.date.today()).isoformat()
    if k in ("yesterday", "ayer"):
        return ((today or _dt.date.today()) - _dt.timedelta(days=1)).isoformat()
    if ISO_RE.match(s):
        _dt.date.fromisoformat(s)  # validate
        return s
    m = ES_RE.match(s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        return _dt.date(y, mo, d).isoformat()  # validates ranges
    raise ValueError(
        f"invalid date {s!r} (use DD-MM-YYYY, YYYY-MM-DD, today or yesterday)")


def format_date_es(iso: str) -> str:
    """ISO ``YYYY-MM-DD`` -> ``DD-MM-YYYY``. Pass through anything else."""
    if isinstance(iso, str) and ISO_RE.match(iso):
        y, m, d = iso.split("-")
        return f"{d}-{m}-{y}"
    return iso


def _month_range(y: int, m: int) -> tuple[str, str]:
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def parse_period(s: str, today: _dt.date | None = None) -> tuple[str, str]:
    """Resolve a human period to an inclusive ``(from_iso, to_iso)`` pair.

    Supported (case-insensitive):
      today, yesterday
      this week, last week
      this month, last month
      this year, last year
      year YYYY         (or a bare YYYY)
      month YYYY-MM
      last N days
    """
    today = today or _dt.date.today()
    k = s.strip().lower()

    # bare words are shorthand for the current period
    k = {
        "day": "today", "dia": "today", "día": "today",
        "week": "this week", "semana": "this week",
        "month": "this month", "mes": "this month",
        "year": "this year", "ano": "this year", "año": "this year",
    }.get(k, k)

    if k == "today":
        return today.isoformat(), today.isoformat()
    if k in ("yesterday", "ayer"):
        d = today - _dt.timedelta(days=1)
        return d.isoformat(), d.isoformat()

    if k in ("this week", "esta semana"):
        start = today - _dt.timedelta(days=today.weekday())
        return start.isoformat(), (start + _dt.timedelta(days=6)).isoformat()
    if k in ("last week", "semana pasada"):
        start = today - _dt.timedelta(days=today.weekday() + 7)
        return start.isoformat(), (start + _dt.timedelta(days=6)).isoformat()

    if k in ("this month", "este mes"):
        return _month_range(today.year, today.month)
    if k in ("last month", "mes pasado"):
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return _month_range(y, m)

    if k in ("this year", "este ano", "este año"):
        return f"{today.year}-01-01", f"{today.year}-12-31"
    if k in ("last year", "ano pasado", "año pasado"):
        y = today.year - 1
        return f"{y}-01-01", f"{y}-12-31"

    m = re.match(r"^(?:year|ano|año)\s+(\d{4})$", k) or re.match(r"^(\d{4})$", k)
    if m:
        y = int(m.group(1))
        return f"{y}-01-01", f"{y}-12-31"

    m = re.match(r"^(?:month|mes)\s+(\d{4})-(\d{1,2})$", k)
    if m:
        return _month_range(int(m.group(1)), int(m.group(2)))

    m = re.match(r"^last\s+(\d+)\s+days?$", k) or re.match(r"^(?:ultimos|últimos)\s+(\d+)\s+d[ií]as?$", k)
    if m:
        n = int(m.group(1))
        start = today - _dt.timedelta(days=n - 1)
        return start.isoformat(), today.isoformat()

    raise ValueError(
        f"unknown period {s!r}. Try: today, this week, this month, "
        f"this year, last month, last year, 'year 2024', 'last 30 days'"
    )
