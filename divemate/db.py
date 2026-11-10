"""Core data-access layer for a DiveMate .ddb (SQLite) logbook.

Frontend-agnostic: no printing, no argparse. Returns plain dicts so a CLI,
a web view or a notebook can all consume the same results.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class DiveFilter:
    """Filter criteria for selecting dives.

    All fields are optional and combine with AND. `None`/empty means "ignore".
    """

    year: int | None = None                 # Divedate year, e.g. 2026
    number_from: int | None = None          # inclusive, dive Number >= this
    number_to: int | None = None            # inclusive, dive Number <= this
    date_from: str | None = None             # inclusive, 'YYYY-MM-DD'
    date_to: str | None = None               # inclusive, 'YYYY-MM-DD'
    tanksize: float | None = None            # litres, matches any tank on the dive
    exclude_tanksize: float | None = None    # litres, exclude dives having this tank size
    place: str | None = None                 # substring match on Place (case-insensitive)
    country: str | None = None               # substring match on Country
    region: str | None = None                # substring match on Place.Region
    buddy: str | None = None                 # substring match on Buddy
    comment: str | None = None               # substring match on Comments (case-insensitive)
    # negative substring filters: drop a dive if it matches ANY listed term
    exclude_place: list[str] = field(default_factory=list)
    exclude_country: list[str] = field(default_factory=list)
    exclude_region: list[str] = field(default_factory=list)
    exclude_buddy: list[str] = field(default_factory=list)
    exclude_comment: list[str] = field(default_factory=list)
    min_depth: float | None = None           # metres
    max_depth: float | None = None           # metres
    min_divetime: float | None = None        # minutes
    min_sac: float | None = None             # L/min, SAC (RMV) >= this
    max_sac: float | None = None             # L/min, SAC (RMV) <= this
    water: int | None = None                 # 1=salt, 0=fresh (DiveMate convention)
    weekend: bool | None = None              # True=Sat/Sun only, False=Mon–Fri only, None=any
    night: bool | None = None                # True=night entry only, False=day only, None=any
    include_deleted: bool = False            # include dives DiveMate soft-deleted (Status=2)
    diver: int | None = None                 # restrict to this DiverID; None = the owner
    all_divers: bool = False                 # include every diver profile in the DB


# DiveMate stores "no value" for temperature as this sentinel; the app shows the
# field blank. Normalize it to None so it never leaks into output or averages.
EMPTY_SENTINEL = -300.0
SENTINEL_COLUMNS = ("Watertemp", "Airtemp")

# A dive is "night" when its entry hour is at/after NIGHT_START or before
# NIGHT_END. Diving after 18:00 in daylight is rare enough that a fixed clock
# cutoff beats any seasonal estimate given DiveMate's unreliable coordinates.
NIGHT_START = 18    # 18:00
NIGHT_END = 6       # 06:00

# Entry hour as an int in SQL; empty/NULL Entrytime -> NULL (neither day nor night).
_ENTRY_HOUR = (
    "CASE WHEN L.Entrytime IS NULL OR L.Entrytime = '' THEN NULL "
    "ELSE CAST(substr(L.Entrytime, 1, 2) AS INTEGER) END"
)

# Bulky/binary columns dropped from `dive_full` output (raw depth-time profiles).
_PROFILE_COLUMNS = (
    "Profile", "Profile2", "Profile3", "Profile4", "Profile10",
    "tpDescent", "tpAscent",
)

# Columns returned for each dive. Keep stable: frontends depend on these keys.
DIVE_COLUMNS = [
    "ID", "Number", "Divedate", "Entrytime", "Country", "Place",
    "Divetime", "Depth", "DepthAvg", "Watertemp", "Airtemp",
    "O2", "Tanksize", "Water", "Buddy", "Comments",
]


class DiveMate:
    """Read-only handle to a DiveMate logbook database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        # read-only, so a web backend can open it safely alongside the app
        uri = f"file:{self.path}?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True)
        self.conn.row_factory = sqlite3.Row
        self._owner: int | None = None
        self._buddies: dict[int, str] | None = None
        self._sac: dict[int, float] | None = None

    def buddy_names(self) -> dict[int, str]:
        """Map Buddy.ID -> display name (cached). Buddies live in their own table;
        dives reference them via the CSV in Logbook.BuddyIDs."""
        if self._buddies is None:
            self._buddies = {}
            for r in self.conn.execute("SELECT ID, FirstName, LastName FROM Buddy"):
                name = " ".join(x.strip() for x in (r["FirstName"], r["LastName"])
                                if x and x.strip())
                self._buddies[r["ID"]] = name or f"#{r['ID']}"
        return self._buddies

    def sac(self) -> dict[int, float]:
        """Map dive ID -> SAC (a.k.a. RMV) in L/min, surface-equivalent.

        SAC = gas used / (dive time x mean ATA), where gas used (litres) is
        summed over the dive's tanks as (start - end pressure) x tank size, and
        mean ATA = DepthAvg / 10 + 1. Only dives with a usable pressure drop,
        a positive dive time and a recorded average depth get a value. Cached.

        Strictly this is RMV (volume-based); we call it SAC because that's the
        name divers commonly use.
        """
        if self._sac is None:
            gas: dict[int, float] = {}
            for r in self.conn.execute(
                    "SELECT LogID, PresS, PresE, Tanksize FROM Tank"):
                ps, pe, ts = r["PresS"], r["PresE"], r["Tanksize"]
                if ts and ps is not None and pe is not None and ps > pe:
                    gas[r["LogID"]] = gas.get(r["LogID"], 0.0) + (ps - pe) * ts
            out: dict[int, float] = {}
            for r in self.conn.execute(
                    "SELECT ID, Divetime, DepthAvg FROM Logbook"):
                g = gas.get(r["ID"])
                dt, da = r["Divetime"], r["DepthAvg"]
                if g and dt and dt > 0 and da and da > 0:
                    out[r["ID"]] = g / (dt * (da / 10.0 + 1))
            self._sac = out
        return self._sac

    @staticmethod
    def _parse_ids(csv: str | None) -> list[int]:
        if not csv:
            return []
        return [int(x) for x in re.split(r"[,\s]+", csv.strip()) if x.isdigit()]

    def owner_id(self) -> int | None:
        """DiverID treated as "you": the profile with the most logged dives.

        A DiveMate DB can hold several diver profiles (imports, demo data). The
        one with the most dives is almost always the owner. Cached per handle.
        """
        if self._owner is None:
            row = self.conn.execute(
                "SELECT DiverID FROM Logbook "
                "GROUP BY DiverID ORDER BY COUNT(*) DESC LIMIT 1"
            ).fetchone()
            self._owner = row[0] if row else None
        return self._owner

    def divers(self) -> list[dict[str, Any]]:
        """All diver profiles with their dive counts and name, owner first."""
        sql = (
            "SELECT L.DiverID AS id, COUNT(*) AS dives, "
            "       P.FirstName AS first, P.LastName AS last "
            "FROM Logbook L LEFT JOIN Personal P ON P.ID = L.DiverID "
            "GROUP BY L.DiverID ORDER BY dives DESC"
        )
        rows = []
        owner = self.owner_id()
        for r in self.conn.execute(sql):
            d = dict(r)
            name = " ".join(x for x in (d.pop("first"), d.pop("last")) if x)
            d["name"] = name
            d["owner"] = d["id"] == owner
            rows.append(d)
        return rows

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DiveMate":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---- internal: build WHERE from a DiveFilter -------------------------

    @staticmethod
    def _joins(needs_tank: bool, needs_place: bool) -> str:
        """Compose the FROM-clause joins a query needs. Place is LEFT-joined so
        dives with no linked site (PlaceID 0/NULL) are not dropped."""
        join = ""
        if needs_tank:
            join += " JOIN Tank T ON T.LogID = L.ID"
        if needs_place:
            join += " LEFT JOIN Place P ON P.ID = L.PlaceID"
        return join

    def _where(self, f: DiveFilter) -> tuple[str, list[Any], bool, bool]:
        """Return (where_sql, params, needs_tank_join, needs_place_join)."""
        clauses: list[str] = []
        params: list[Any] = []
        needs_tank = False
        needs_place = False

        # DiveMate soft-deletes dives by setting Status=2 (they keep the row but
        # blank out Place/tank). Exclude them by default.
        if not f.include_deleted:
            clauses.append("(L.Status IS NULL OR L.Status != 2)")

        # Restrict to one diver profile. Default: the owner (most dives).
        if not f.all_divers:
            diver = f.diver if f.diver is not None else self.owner_id()
            if diver is not None:
                clauses.append("L.DiverID = ?")
                params.append(diver)

        if f.year is not None:
            clauses.append("L.Divedate LIKE ?")
            params.append(f"{f.year}%")
        if f.number_from is not None:
            clauses.append("L.Number >= ?")
            params.append(f.number_from)
        if f.number_to is not None:
            clauses.append("L.Number <= ?")
            params.append(f.number_to)
        if f.date_from is not None:
            clauses.append("L.Divedate >= ?")
            params.append(f.date_from)
        if f.date_to is not None:
            clauses.append("L.Divedate <= ?")
            params.append(f.date_to)
        if f.tanksize is not None:
            # match any tank on the dive, not only the primary Logbook.Tanksize
            needs_tank = True
            clauses.append("T.Tanksize = ?")
            params.append(f.tanksize)
        if f.exclude_tanksize is not None:
            # dives that do NOT have any tank of this size (subquery, no join needed)
            clauses.append("L.ID NOT IN (SELECT LogID FROM Tank WHERE Tanksize = ?)")
            params.append(f.exclude_tanksize)
        if f.place:
            clauses.append("L.Place LIKE ?")
            params.append(f"%{f.place}%")
        if f.country:
            clauses.append("L.Country LIKE ?")
            params.append(f"%{f.country}%")
        if f.region:
            needs_place = True
            clauses.append("P.Region LIKE ?")
            params.append(f"%{f.region}%")
        if f.buddy:
            # buddies are referenced by ID in Logbook.BuddyIDs -> resolve names first
            ids = [bid for bid, name in self.buddy_names().items()
                   if f.buddy.lower() in name.lower()]
            subs = ["L.Buddy LIKE ?"]  # also match any legacy free-text buddy
            params.append(f"%{f.buddy}%")
            for bid in ids:
                # token-safe match inside the CSV, e.g. ',63,'
                subs.append("(',' || replace(L.BuddyIDs,' ','') || ',') LIKE ?")
                params.append(f"%,{bid},%")
            clauses.append("(" + " OR ".join(subs) + ")")
        if f.comment:
            clauses.append("L.Comments LIKE ?")
            params.append(f"%{f.comment}%")

        # --- negative substring filters (exclude on match of any term) --------
        # NULL-safe: a NULL column counts as "does not match", so the dive stays.
        for col, terms in (("Place", f.exclude_place),
                           ("Country", f.exclude_country),
                           ("Comments", f.exclude_comment)):
            for term in terms:
                clauses.append(f"(L.{col} IS NULL OR L.{col} NOT LIKE ?)")
                params.append(f"%{term}%")
        for term in f.exclude_region:
            needs_place = True
            clauses.append("(P.Region IS NULL OR P.Region NOT LIKE ?)")
            params.append(f"%{term}%")
        for term in f.exclude_buddy:
            # mirror the positive buddy predicate, then negate it; COALESCE keeps
            # NULL Buddy/BuddyIDs from turning the NOT(...) into NULL.
            ids = [bid for bid, name in self.buddy_names().items()
                   if term.lower() in name.lower()]
            subs = ["COALESCE(L.Buddy, '') LIKE ?"]
            params.append(f"%{term}%")
            for bid in ids:
                subs.append("(',' || replace(COALESCE(L.BuddyIDs, ''),' ','') || ',') LIKE ?")
                params.append(f"%,{bid},%")
            clauses.append("NOT (" + " OR ".join(subs) + ")")

        if f.min_depth is not None:
            clauses.append("L.Depth >= ?")
            params.append(f.min_depth)
        if f.max_depth is not None:
            clauses.append("L.Depth <= ?")
            params.append(f.max_depth)
        if f.min_divetime is not None:
            clauses.append("L.Divetime >= ?")
            params.append(f.min_divetime)
        if f.min_sac is not None or f.max_sac is not None:
            # SAC is computed in Python, not a column — resolve matching dive IDs
            # and constrain on them. Empty set -> a valid (always-false) clause.
            sac = self.sac()
            ids = [i for i, v in sac.items()
                   if (f.min_sac is None or v >= f.min_sac)
                   and (f.max_sac is None or v <= f.max_sac)]
            placeholders = ", ".join("?" for _ in ids) if ids else "NULL"
            clauses.append(f"L.ID IN ({placeholders})")
            params.extend(ids)
        if f.water is not None:
            clauses.append("L.Water = ?")
            params.append(f.water)
        if f.weekend is not None:
            # strftime %w: 0=Sunday .. 6=Saturday. Weekend = Sat/Sun.
            days = ("0", "6") if f.weekend else ("1", "2", "3", "4", "5")
            placeholders = ", ".join("?" for _ in days)
            clauses.append(f"strftime('%w', L.Divedate) IN ({placeholders})")
            params.extend(days)
        if f.night is not None:
            if f.night:
                clauses.append(
                    f"({_ENTRY_HOUR} IS NOT NULL AND "
                    f"({_ENTRY_HOUR} >= ? OR {_ENTRY_HOUR} < ?))"
                )
            else:
                clauses.append(
                    f"({_ENTRY_HOUR} IS NOT NULL AND "
                    f"{_ENTRY_HOUR} < ? AND {_ENTRY_HOUR} >= ?)"
                )
            params.extend([NIGHT_START, NIGHT_END])

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params, needs_tank, needs_place

    # ---- queries ---------------------------------------------------------

    def dives(self, f: DiveFilter | None = None, order: str = "Divedate",
              desc: bool = False) -> list[dict[str, Any]]:
        """Return dives matching the filter as a list of dicts."""
        f = f or DiveFilter()
        where, params, needs_tank, _ = self._where(f)
        # always LEFT JOIN Place so Region is available as a column / sort key
        join = self._joins(needs_tank, needs_place=True)
        cols = ", ".join(f"L.{c}" for c in DIVE_COLUMNS) + ", P.Region AS Region"
        # DISTINCT so a dive with several matching tanks is not duplicated
        direction = "DESC" if desc else "ASC"
        # Region is an alias (Place column), not L.*; everything else is on L
        order_expr = "Region" if order == "Region" else f"L.{order}"
        sql = (
            f"SELECT DISTINCT {cols} FROM Logbook L{join}{where} "
            f"ORDER BY {order_expr} {direction}"
        )
        rows = [dict(r) for r in self.conn.execute(sql, params)]
        sac = self.sac()
        for r in rows:
            for c in SENTINEL_COLUMNS:
                if r.get(c) == EMPTY_SENTINEL:
                    r[c] = None
            r["SAC"] = sac.get(r["ID"])
        return rows

    def dive_full(self, number: int, diver: int | None = None,
                  all_divers: bool = False,
                  include_deleted: bool = False) -> list[dict[str, Any]]:
        """Return every column of the dive(s) with this Number, plus resolved
        tanks and buddy names. Normally one dive; a list because Number is only
        unique per diver. Defaults to the owner's dive."""
        clauses = ["L.Number = ?"]
        params: list[Any] = [number]
        if not include_deleted:
            clauses.append("(L.Status IS NULL OR L.Status != 2)")
        if not all_divers:
            d = diver if diver is not None else self.owner_id()
            if d is not None:
                clauses.append("L.DiverID = ?")
                params.append(d)
        where = " WHERE " + " AND ".join(clauses)
        rows = [dict(r) for r in self.conn.execute(
            "SELECT L.*, P.Region AS Region FROM Logbook L "
            f"LEFT JOIN Place P ON P.ID = L.PlaceID{where}", params)]
        for r in rows:
            for c in SENTINEL_COLUMNS:
                if r.get(c) == EMPTY_SENTINEL:
                    r[c] = None
            # drop bulky/binary dive-profile columns — not useful for `info`
            for c in _PROFILE_COLUMNS:
                r.pop(c, None)
            r["SAC"] = self.sac().get(r["ID"])
            r["tanks"] = self._tanks(r["ID"])
            r["buddies"] = [self.buddy_names().get(b, f"#{b}")
                            for b in self._parse_ids(r.get("BuddyIDs"))]
        return rows

    def dive_profile(self, number: int, diver: int | None = None,
                     all_divers: bool = False) -> dict[str, Any] | None:
        """Sampled depth (and temperature) profile for a dive, or None.

        DiveMate stores samples as fixed-width digit runs: `Profile` holds depth
        in 12-char records (first 4 digits = depth in decimetres), `Profile2`
        holds temperature in 11-char records (first 4 digits = centi-°C).
        `ProfileInt` is the sample interval in seconds. Returns a dict with
        `times` (minutes), `depths` (m) and `temps` (°C or None per sample)."""
        clauses = ["L.Number = ?"]
        params: list[Any] = [number]
        if not all_divers:
            d = diver if diver is not None else self.owner_id()
            if d is not None:
                clauses.append("L.DiverID = ?")
                params.append(d)
        row = self.conn.execute(
            "SELECT ProfileInt, Profile, Profile2 FROM Logbook L WHERE "
            + " AND ".join(clauses), params).fetchone()
        if row is None:
            return None
        interval = row["ProfileInt"] or 0
        prof = row["Profile"] or ""
        if interval <= 0 or len(prof) < 12 or len(prof) % 12 != 0:
            return None
        depths = [int(prof[i:i + 4]) / 10.0 for i in range(0, len(prof), 12)]
        times = [i * interval / 60.0 for i in range(len(depths))]

        temps: list[float | None] | None = None
        p2 = row["Profile2"] or ""
        if p2 and len(p2) % 11 == 0 and len(p2) // 11 == len(depths):
            raw = [int(p2[i:i + 4]) for i in range(0, len(p2), 11)]
            # 0 is DiveMate's "no sample"; keep only plausible temperatures
            t = [v / 100.0 if v and -500 < v < 6000 else None for v in raw]
            if any(x is not None for x in t):
                temps = t
        return {"interval": interval, "times": times,
                "depths": depths, "temps": temps}

    def boundary_number(self, last: bool = True, diver: int | None = None,
                        all_divers: bool = False,
                        include_deleted: bool = False) -> int | None:
        """Highest (``last``) or lowest dive Number, for the owner by default."""
        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("(L.Status IS NULL OR L.Status != 2)")
        if not all_divers:
            d = diver if diver is not None else self.owner_id()
            if d is not None:
                clauses.append("L.DiverID = ?")
                params.append(d)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        agg = "MAX" if last else "MIN"
        row = self.conn.execute(
            f"SELECT {agg}(L.Number) FROM Logbook L{where}", params).fetchone()
        return row[0] if row else None

    def _tanks(self, log_id: int) -> list[dict[str, Any]]:
        """Per-dive tanks (size, gas, pressures, name), in display order."""
        sql = ("SELECT Tanksize, O2, He, PresS, PresE, Name "
               "FROM Tank WHERE LogID = ? ORDER BY SortOrd")
        return [dict(r) for r in self.conn.execute(sql, (log_id,))]

    def count(self, f: DiveFilter | None = None) -> int:
        f = f or DiveFilter()
        where, params, needs_tank, needs_place = self._where(f)
        join = self._joins(needs_tank, needs_place)
        sql = f"SELECT COUNT(DISTINCT L.ID) FROM Logbook L{join}{where}"
        return self.conn.execute(sql, params).fetchone()[0]

    def stats(self, f: DiveFilter | None = None) -> dict[str, Any]:
        """Aggregate stats over matching dives."""
        rows = self.dives(f)
        n = len(rows)
        if n == 0:
            return {"count": 0}
        divetimes = [r["Divetime"] for r in rows if r["Divetime"] is not None]
        depths = [r["Depth"] for r in rows if r["Depth"] is not None]
        # average real dive depth from DepthAvg (per-dive mean); 0 = not recorded
        avg_depths = [r["DepthAvg"] for r in rows if r["DepthAvg"] not in (None, 0)]
        temps = [r["Watertemp"] for r in rows if r["Watertemp"] is not None]
        return {
            "count": n,
            "total_divetime_min": round(sum(divetimes), 1),
            "total_divetime_h": round(sum(divetimes) / 60, 1),
            "avg_divetime_min": round(sum(divetimes) / len(divetimes), 1) if divetimes else None,
            "max_depth_m": max(depths) if depths else None,
            "avg_depth_m": round(sum(avg_depths) / len(avg_depths), 1) if avg_depths else None,
            "avg_watertemp_c": round(sum(temps) / len(temps), 1) if temps else None,
        }

    def group_count(self, column: str, f: DiveFilter | None = None,
                    limit: int | None = None, order: str = "count") -> list[dict[str, Any]]:
        """Count dives grouped by a field, ranked.

        `column` is case-insensitive and may be any field in GROUPABLE, or the
        virtual 'year' / 'month' (derived from the dive date).
        `order`: 'count' (default, most dives first) or 'value' (by group value).
        """
        if column.strip().lower() in ("buddy", "buddies"):
            return self.group_buddies(f, limit=limit, order=order)
        expr = self._group_expr(column)
        f = f or DiveFilter()
        where, params, needs_tank, needs_place = self._where(f)
        # grouping by region (P.Region) also needs the Place join
        join = self._joins(needs_tank, needs_place or "P." in expr)
        order_by = "dives DESC" if order == "count" else "value ASC"
        sql = (
            f"SELECT {expr} AS value, COUNT(DISTINCT L.ID) AS dives "
            f"FROM Logbook L{join}{where} "
            f"GROUP BY value ORDER BY {order_by}"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql, params)]

    def group_buddies(self, f: DiveFilter | None = None, limit: int | None = None,
                      order: str = "count") -> list[dict[str, Any]]:
        """Count dives per buddy, expanding Logbook.BuddyIDs. A dive with two
        buddies counts once for each, so totals can exceed the dive count."""
        f = f or DiveFilter()
        where, params, needs_tank, needs_place = self._where(f)
        join = self._joins(needs_tank, needs_place)
        sql = f"SELECT DISTINCT L.ID, L.BuddyIDs FROM Logbook L{join}{where}"
        names = self.buddy_names()
        counts: dict[int, int] = {}
        for r in self.conn.execute(sql, params):
            for bid in self._parse_ids(r["BuddyIDs"]):
                counts[bid] = counts.get(bid, 0) + 1
        rows = [{"value": names.get(bid, f"#{bid}"), "dives": n}
                for bid, n in counts.items()]
        if order == "value":
            rows.sort(key=lambda d: d["value"].lower())
        else:
            rows.sort(key=lambda d: (-d["dives"], d["value"].lower()))
        return rows[:limit] if limit else rows

    @staticmethod
    def _group_expr(column: str) -> str:
        """Validate a group-by field and return its SQL expression."""
        key = column.strip().lower()
        if key in ("year", "año", "ano"):
            return "substr(L.Divedate,1,4)"
        if key in ("region", "región", "regions"):
            # treat an empty region the same as an unlinked site (one blank group)
            return "NULLIF(P.Region, '')"
        if key in ("night", "day", "nightday", "daynight"):
            return (f"CASE WHEN {_ENTRY_HOUR} IS NULL THEN 'unknown' "
                    f"WHEN {_ENTRY_HOUR} >= {NIGHT_START} OR {_ENTRY_HOUR} < {NIGHT_END} "
                    f"THEN 'night' ELSE 'day' END")
        if key in ("month", "mes"):
            return "substr(L.Divedate,1,7)"
        if key in ("weekend", "weekday", "weekends", "weekdays"):
            return ("CASE WHEN strftime('%w',L.Divedate) IN ('0','6') "
                    "THEN 'weekend' ELSE 'weekday' END")
        if key in _GROUPABLE_LC:
            return f"L.{_GROUPABLE_LC[key]}"
        allowed = ", ".join(sorted(_GROUPABLE) +
                            ["year", "month", "weekend", "night", "buddy", "region"])
        raise ValueError(f"cannot group by {column!r}. Allowed: {allowed}")

    def dives_per_year(self, f: DiveFilter | None = None) -> list[dict[str, Any]]:
        f = f or DiveFilter()
        where, params, needs_tank, needs_place = self._where(f)
        join = self._joins(needs_tank, needs_place)
        sql = (
            f"SELECT substr(L.Divedate,1,4) AS year, COUNT(DISTINCT L.ID) AS n "
            f"FROM Logbook L{join}{where} "
            f"GROUP BY year ORDER BY year"
        )
        return [dict(r) for r in self.conn.execute(sql, params)]

    def tags(self, f: DiveFilter | None = None) -> list[dict[str, Any]]:
        """Discover #tags used in comments, with dive counts, most-used first.

        Case-insensitive: #Sidemount and #sidemount count as one tag (lowercased).
        """
        rows = self.dives(f)
        counts: dict[str, int] = {}
        for r in rows:
            c = r.get("Comments") or ""
            seen = {m.lower() for m in _TAG_RE.findall(c)}
            for t in seen:
                counts[t] = counts.get(t, 0) + 1
        return [
            {"tag": t, "dives": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    def tank_sizes(self) -> list[float]:
        """Distinct tank sizes present in the DB (handy for the UI)."""
        rows = self.conn.execute(
            "SELECT DISTINCT Tanksize FROM Tank WHERE Tanksize > 0 ORDER BY Tanksize"
        )
        return [r[0] for r in rows]


_GROUPABLE = {
    "Place", "Country", "Buddy", "Computer", "Divesuit", "Water",
    "Tanksize", "Tanktype", "Divemaster", "Boat", "Weather", "Waves",
    "UWCurrent", "Rating", "Entry", "Deco", "Divetype", "O2",
}
# case-insensitive lookup: lowercased name -> real column name
_GROUPABLE_LC = {c.lower(): c for c in _GROUPABLE}

# a #tag: hash + word chars (letters/digits/underscore), Unicode-aware
_TAG_RE = re.compile(r"#(\w+)", re.UNICODE)
