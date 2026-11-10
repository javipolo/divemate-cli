"""A small filter query language for dives.

Frontend-agnostic by design, so the execution backend can be swapped later
without touching the grammar or the CLI:

    --query "…"  ──lark──►  AST (And/Or/Not/Compare/Flag)  ──►  PythonEvaluator
                               ▲
                          FIELDS registry (alias -> FieldSpec)

Today the only backend is `PythonEvaluator`, which evaluates the AST against an
in-memory dive record (a dict as returned by `DiveMate.dives`). A future
`SqlCompiler` would consume the very same AST + FIELDS registry and emit a SQL
predicate instead; the `FieldSpec.sql` / `.needs` slots are reserved for it.

Grammar (infix, precedence `or < and < not`, parentheses):

    region=Mallorca and (place~toro or depth>=30)
    sac<18 or tank=15
    night and divetime>50
    region!=Mallorca

Operators:
  text fields  : `=` exact (case-insensitive), `~` contains, `!=` / `!~` negate
  number/date  : `=` `!=` `<` `<=` `>` `>=`
  bool fields  : bare `night` / `weekend` (truthy), or `night=true` / `=false`

Values are barewords (`region=Mallorca`, accents allowed) or quoted when they
contain spaces/punctuation (`place="el toro"`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as _dc_field
from typing import Any

from lark import Lark, Transformer, v_args
from lark.exceptions import LarkError

from .dates import parse_period

# --- AST (backend-agnostic) -----------------------------------------------


@dataclass
class And:
    items: list[Node]


@dataclass
class Or:
    items: list[Node]


@dataclass
class Not:
    item: Node


@dataclass
class Compare:
    field: str
    op: str          # one of = != ~ !~ < <= > >=
    value: str


@dataclass
class Flag:
    field: str       # bare field, truthy test


Node = Any  # And | Or | Not | Compare | Flag


@dataclass
class Query:
    """A parsed query: a filter expression plus optional presentation directives
    (sort / limit). `expr` is None for a directives-only query like
    `sort depth limit 5`."""
    expr: Node | None = None
    sort: str | None = None
    desc: bool = True            # default newest/highest first, like list used to
    limit: int | None = None


# --- field registry (single source of truth) -------------------------------

TEXT, NUMBER, DATE, BOOL = "text", "number", "date", "bool"


@dataclass
class FieldSpec:
    key: str                                   # canonical name
    kind: str                                  # TEXT | NUMBER | DATE | BOOL
    get: Callable[[dict], Any]                 # value from a dive record (Python backend)
    multi: bool = False                        # get() returns a list; "any value satisfies"
    # reserved for a future SQL backend; unused by the Python evaluator:
    sql: str | None = None
    needs: tuple[str, ...] = _dc_field(default_factory=tuple)


_ENTRY_NIGHT_START, _ENTRY_NIGHT_END = 18, 6


def _entry_hour(rec: dict) -> int | None:
    t = rec.get("Entrytime")
    if not t or not isinstance(t, str) or len(t) < 2 or not t[:2].isdigit():
        return None
    return int(t[:2])


def _is_night(rec: dict) -> bool | None:
    h = _entry_hour(rec)
    if h is None:
        return None
    return h >= _ENTRY_NIGHT_START or h < _ENTRY_NIGHT_END


def _is_weekend(rec: dict) -> bool | None:
    d = rec.get("Divedate")
    if not d or not isinstance(d, str) or len(d) < 10:
        return None
    import datetime
    try:
        wd = datetime.date.fromisoformat(d[:10]).weekday()  # Mon=0..Sun=6
    except ValueError:
        return None
    return wd >= 5


def _water(rec: dict) -> str | None:
    w = rec.get("Water")
    if w == 1:
        return "salt"
    if w == 0:
        return "fresh"
    return None


def _year(rec: dict) -> int | None:
    d = rec.get("Divedate")
    if isinstance(d, str) and len(d) >= 4 and d[:4].isdigit():
        return int(d[:4])
    return None


def _buddies(rec: dict) -> list[str]:
    out = list(rec.get("buddies") or [])
    b = rec.get("Buddy")
    if b:
        out.append(b)
    return out


# a #tag in the comments: hash + word chars (unicode-aware), matches db.tags()
_TAG_RE = re.compile(r"#(\w+)", re.UNICODE)


def _tags(rec: dict) -> list[str]:
    return _TAG_RE.findall(rec.get("Comments") or "")


def _col(name: str) -> Callable[[dict], Any]:
    return lambda rec: rec.get(name)


# canonical spec objects, then an alias -> spec map
_SPECS = [
    FieldSpec("place", TEXT, _col("Place"), sql="L.Place"),
    FieldSpec("country", TEXT, _col("Country"), sql="L.Country"),
    FieldSpec("region", TEXT, _col("Region"), sql="P.Region", needs=("place",)),
    FieldSpec("comment", TEXT, _col("Comments"), sql="L.Comments"),
    FieldSpec("tag", TEXT, _tags, multi=True),
    FieldSpec("buddy", TEXT, _buddies, multi=True),
    FieldSpec("depth", NUMBER, _col("Depth"), sql="L.Depth"),
    FieldSpec("avgdepth", NUMBER, _col("DepthAvg"), sql="L.DepthAvg"),
    FieldSpec("divetime", NUMBER, _col("Divetime"), sql="L.Divetime"),
    FieldSpec("watertemp", NUMBER, _col("Watertemp"), sql="L.Watertemp"),
    FieldSpec("airtemp", NUMBER, _col("Airtemp"), sql="L.Airtemp"),
    FieldSpec("o2", NUMBER, _col("O2"), sql="L.O2"),
    FieldSpec("tanksize", NUMBER, lambda r: r.get("tank_sizes") or [], multi=True),
    FieldSpec("sac", NUMBER, _col("SAC")),
    FieldSpec("number", NUMBER, _col("Number"), sql="L.Number"),
    FieldSpec("year", NUMBER, _year, sql="substr(L.Divedate,1,4)"),
    FieldSpec("date", DATE, _col("Divedate"), sql="L.Divedate"),
    FieldSpec("water", TEXT, _water, sql="L.Water"),
    FieldSpec("night", BOOL, _is_night),
    FieldSpec("weekend", BOOL, _is_weekend),
]

# user-facing aliases -> canonical spec
_ALIASES = {
    "comments": "comment",
    "maxdepth": "depth",
    "depthavg": "avgdepth",
    "time": "divetime",
    "temp": "watertemp",
    "tank": "tanksize",
    "rmv": "sac",
}

FIELDS: dict[str, FieldSpec] = {s.key: s for s in _SPECS}
for _alias, _target in _ALIASES.items():
    FIELDS[_alias] = FIELDS[_target]


def _resolve(name: str) -> FieldSpec:
    spec = FIELDS.get(name.lower())
    if spec is None:
        allowed = ", ".join(sorted({s.key for s in _SPECS}))
        raise ValueError(f"unknown query field {name!r}. Fields: {allowed}")
    return spec


def help_text() -> str:
    """A short reference for the query language: fields, operators, examples.
    Built from the field registry so it stays in sync."""
    aliases: dict[str, list[str]] = {}
    for alias, target in _ALIASES.items():
        aliases.setdefault(target, []).append(alias)

    def names(kind: str) -> str:
        out = []
        for s in _SPECS:
            if s.kind == kind:
                al = aliases.get(s.key)
                out.append(f"{s.key} ({'/'.join(al)})" if al else s.key)
        return ", ".join(out)

    return "\n".join([
        "divemate query language",
        "",
        "A filter is: field OP value, combined with and / or / not and ( ).",
        "Adjacent terms imply 'and'. Trailing: sort FIELD [asc|desc], limit N.",
        "",
        "text fields:    " + names(TEXT),
        "  = ~ (contains)  == (exact)  != !~ (not contains)  !== (not exact)",
        "number fields:  " + names(NUMBER),
        "  = == (equal)  != !== (not equal)  < <= > >=",
        "date field:     date   (ISO YYYY-MM-DD)   = == != !== < <= > >=",
        "bool fields:    " + names(BOOL) + "   (bare flag, or =true / =false)",
        "",
        "shortcuts:",
        "  tag=<name>      a #tag in the comments (without the #)",
        '  period=<range>  2024, "this month", "last year", "month 2024-07",',
        '                  "last 30 days", …   (only = / !=)',
        "",
        "notes: text is case- and accent-insensitive; multi-valued fields",
        "  (buddy, tanksize) match when any value does; water is salt/fresh.",
        "",
        "examples:",
        "  dmcli list region~mallorca sort depth desc limit 10",
        "  dmcli count 'depth>=30 and (night or watertemp<16)'",
        "  dmcli list period=2025 tag=sidemount",
        '  dmcli list \'place=="Tossa de Mar"\'      # exact, not substring',
        "  dmcli group region 'country=spain and depth>20'",
        "",
        "tip: quote anything with > < ( ) so the shell doesn't eat it.",
    ])


# --- grammar + transformer -------------------------------------------------

_GRAMMAR = r"""
start: or_expr directive*       -> q_full
     | directive+               -> q_dirs

directive: SORT FIELD SORTDIR?  -> d_sort
         | LIMIT INT            -> d_limit

?or_expr: or_expr OR and_expr   -> or_op
        | and_expr

?and_expr: and_expr AND not_expr -> and_op
         | and_expr not_expr     -> and_juxta
         | not_expr

?not_expr: NOT not_expr          -> not_op
         | atom

?atom: "(" or_expr ")"
     | FIELD OP value            -> comparison
     | FIELD                     -> flag

?value: STRING                   -> string
      | RAW                      -> raw

OR: "or"i
AND: "and"i
NOT: "not"i
SORT.3: "sort"i
LIMIT.3: "limit"i
SORTDIR.3: "asc"i | "desc"i
OP.2: "!==" | "==" | ">=" | "<=" | "!=" | "!~" | "=" | "~" | ">" | "<"
INT: /[0-9]+/
FIELD: /[a-zA-Z_][a-zA-Z0-9_]*/
STRING: /"[^"]*"/ | /'[^']*'/
RAW: /[^\s()=~<>!'"]+/

%import common.WS
%ignore WS
"""


@v_args(inline=True)
class _ToAst(Transformer):
    def or_op(self, a, _tok, b):
        items = (a.items if isinstance(a, Or) else [a]) + [b]
        return Or(items)

    def and_op(self, a, _tok, b):
        items = (a.items if isinstance(a, And) else [a]) + [b]
        return And(items)

    def and_juxta(self, a, b):
        # two adjacent terms with no operator mean AND
        items = (a.items if isinstance(a, And) else [a]) + [b]
        return And(items)

    def not_op(self, _tok, item):
        return Not(item)

    def comparison(self, field_tok, op_tok, value):
        return Compare(str(field_tok), str(op_tok), value)

    def flag(self, field_tok):
        return Flag(str(field_tok))

    def string(self, tok):
        return str(tok)[1:-1]

    def raw(self, tok):
        return str(tok)

    # directives and the top-level Query
    def d_sort(self, _kw, field, direction=None):
        desc = True if direction is None else str(direction).lower() == "desc"
        return ("sort", str(field), desc)

    def d_limit(self, _kw, n):
        return ("limit", int(n))

    def q_full(self, expr, *dirs):
        return _build_query(expr, dirs)

    def q_dirs(self, *dirs):
        return _build_query(None, dirs)


def _build_query(expr, dirs) -> Query:
    q = Query(expr=expr)
    for d in dirs:
        if d[0] == "sort":
            q.sort, q.desc = d[1], d[2]
        else:  # limit
            if d[1] < 1:
                raise ValueError(f"limit must be >= 1, got {d[1]}")
            q.limit = d[1]
    return q


_PARSER = Lark(_GRAMMAR, parser="lalr", transformer=_ToAst())


# text: `=` and `~` both mean "contains"; `==` is exact (and their `!` negations)
_TEXT_OPS = {"=", "==", "~", "!=", "!==", "!~"}
_NUM_OPS = {"=", "==", "!=", "!==", "<", "<=", ">", ">="}


def _expand(node: Node) -> Node:
    """Rewrite `period=<range>` leaves into date comparisons before validation,
    so e.g. `period="last year"` becomes `(date>=2025-01-01 and date<=2025-12-31)`.
    `period` is a date-range shortcut, not a per-record field."""
    if isinstance(node, (And, Or)):
        return type(node)([_expand(it) for it in node.items])
    if isinstance(node, Not):
        return Not(_expand(node.item))
    if isinstance(node, Flag) and node.field.lower() == "period":
        raise ValueError("period needs a value, e.g. period=\"last year\"")
    if isinstance(node, Compare) and node.field.lower() == "period":
        if node.op not in ("=", "==", "!=", "!=="):
            raise ValueError("period only supports = and != (it is a date range)")
        try:
            lo, hi = parse_period(node.value)
        except ValueError as e:
            raise ValueError(f"invalid period {node.value!r}: {e}") from None
        rng = And([Compare("date", ">=", lo), Compare("date", "<=", hi)])
        return Not(rng) if node.op in ("!=", "!==") else rng
    return node


def _validate(node: Node) -> None:
    """Walk the AST, resolving fields and checking op/kind compatibility."""
    if isinstance(node, (And, Or)):
        for it in node.items:
            _validate(it)
    elif isinstance(node, Not):
        _validate(node.item)
    elif isinstance(node, Flag):
        _resolve(node.field)
    elif isinstance(node, Compare):
        spec = _resolve(node.field)
        if spec.kind == TEXT and node.op not in _TEXT_OPS:
            raise ValueError(
                f"operator {node.op!r} is not valid for text field "
                f"{node.field!r} (use = != ~ !~)")
        if spec.kind in (NUMBER, DATE, BOOL) and node.op not in _NUM_OPS:
            raise ValueError(
                f"operator {node.op!r} is not valid for field {node.field!r} "
                f"(use = != < <= > >=)")


def parse(text: str) -> Query:
    """Parse a query string into a Query (expr + sort/limit), raising ValueError
    on any problem."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty query")
    try:
        q = _PARSER.parse(text)
    except LarkError as e:
        raise ValueError(f"invalid query: {e}") from None
    if q.expr is not None:
        q.expr = _expand(q.expr)
        _validate(q.expr)
    if q.sort is not None:
        _resolve(q.sort)   # validate the sort field name
    return q


# --- Python evaluation backend --------------------------------------------


def _as_list(spec: FieldSpec, rec: dict) -> list[Any]:
    v = spec.get(rec)
    if spec.multi:
        return list(v or [])
    return [] if v is None else [v]


def _fold(s: Any) -> str:
    """Case- and accent-insensitive key: NFD-decompose, drop combining marks,
    casefold. So 'tomas' matches 'Tomás' / 'Tomàs'."""
    s = unicodedata.normalize("NFD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.casefold()


def _cmp_text(op: str, values: list[Any], target: str) -> bool:
    t = _fold(target)
    svals = [_fold(v) for v in values if v not in (None, "")]
    if op in ("=", "~"):           # contains
        return any(t in v for v in svals)
    if op in ("!=", "!~"):         # does not contain
        return all(t not in v for v in svals)
    if op == "==":                 # exact
        return any(v == t for v in svals)
    if op == "!==":                # not exact
        return all(v != t for v in svals)
    return False


def _cmp_number(op: str, values: list[Any], target: str) -> bool:
    try:
        n = float(target)
    except ValueError:
        raise ValueError(f"query: expected a number, got {target!r}") from None
    nums = []
    for v in values:
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    op = {"==": "=", "!==": "!="}.get(op, op)   # == / !== are equality for numbers
    ops = {"=": lambda x: x == n, "!=": lambda x: x != n,
           "<": lambda x: x < n, "<=": lambda x: x <= n,
           ">": lambda x: x > n, ">=": lambda x: x >= n}
    f = ops[op]
    if op == "!=":
        return all(f(x) for x in nums) if nums else True
    return any(f(x) for x in nums)


def _cmp_date(op: str, values: list[Any], target: str) -> bool:
    # ISO dates compare correctly as strings
    svals = [str(v)[:10] for v in values if v]
    op = {"==": "=", "!==": "!="}.get(op, op)
    ops = {"=": lambda x: x == target, "!=": lambda x: x != target,
           "<": lambda x: x < target, "<=": lambda x: x <= target,
           ">": lambda x: x > target, ">=": lambda x: x >= target}
    f = ops[op]
    if op == "!=":
        return all(f(x) for x in svals) if svals else True
    return any(f(x) for x in svals)


_TRUE = {"true", "1", "yes", "y", "salt", "night", "weekend"}
_FALSE = {"false", "0", "no", "n", "fresh", "day", "weekday"}


def _cmp_bool(op: str, value: Any, target: str) -> bool:
    t = target.casefold()
    if t in _TRUE:
        want = True
    elif t in _FALSE:
        want = False
    else:
        raise ValueError(f"query: expected true/false, got {target!r}")
    got = bool(value)
    return got == want if op in ("=", "==") else got != want


def _truthy(spec: FieldSpec, rec: dict) -> bool:
    if spec.kind == BOOL:
        return bool(spec.get(rec))
    vals = _as_list(spec, rec)
    return any(v not in (None, "", 0) for v in vals)


def match(node: Node, rec: dict) -> bool:
    """Evaluate an AST node against one dive record."""
    if isinstance(node, And):
        return all(match(it, rec) for it in node.items)
    if isinstance(node, Or):
        return any(match(it, rec) for it in node.items)
    if isinstance(node, Not):
        return not match(node.item, rec)
    if isinstance(node, Flag):
        return _truthy(_resolve(node.field), rec)
    if isinstance(node, Compare):
        spec = _resolve(node.field)
        if spec.kind == BOOL:
            return _cmp_bool(node.op, spec.get(rec), node.value)
        values = _as_list(spec, rec)
        if spec.kind == TEXT:
            return _cmp_text(node.op, values, node.value)
        if spec.kind == DATE:
            return _cmp_date(node.op, values, node.value)
        return _cmp_number(node.op, values, node.value)
    raise TypeError(f"unknown AST node: {node!r}")


def predicate(text: str) -> Callable[[dict], bool]:
    """Parse `text` and return a callable `(dive_record) -> bool`. A query with
    no filter expression (only sort/limit directives) matches every dive."""
    node = parse(text).expr
    if node is None:
        return lambda rec: True
    return lambda rec: match(node, rec)


def sort_key(field: str) -> Callable[[dict], Any]:
    """A key function for sorting dive records by a query field. Multi-valued
    fields sort by their smallest value; missing values sort as None."""
    spec = _resolve(field)
    def key(rec: dict) -> Any:
        v = spec.get(rec)
        if spec.multi:
            vals = [x for x in (v or []) if x is not None]
            return min(vals) if vals else None
        return v
    return key
