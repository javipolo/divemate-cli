"""Tests for the --query filter language (pure parser + evaluator, no DB)."""

import pytest

from divemate import query as q
from divemate.query import And, Or, Not, Compare, Flag


# a representative enriched dive record, as DiveMate.dives() produces it
REC = {
    "Number": 500,
    "Place": "El Toro. Boya 3",
    "Country": "Spain",
    "Region": "Mallorca",
    "Comments": "great #cave dive",
    "Depth": 28.0,
    "DepthAvg": 18.0,
    "Divetime": 55.0,
    "Watertemp": 14.0,
    "Airtemp": 25.0,
    "O2": 32.0,
    "SAC": 15.2,
    "Water": 1,                 # salt
    "Entrytime": "19:30:00",    # night
    "Divedate": "2025-08-10",   # a Sunday -> weekend
    "Buddy": None,
    "buddies": ["Isa Rey"],
    "tank_sizes": [15.0],
}


def ev(expr, rec=REC):
    return q.predicate(expr)(rec)


# --- parsing / structure ---------------------------------------------------

def test_precedence_or_binds_looser_than_and():
    ast = q.parse("depth=1 and number=2 or o2=3")
    assert isinstance(ast, Or)
    assert isinstance(ast.items[0], And)


def test_parentheses_override_precedence():
    ast = q.parse("depth=1 and (number=2 or o2=3)")
    assert isinstance(ast, And)
    assert isinstance(ast.items[1], Or)


def test_and_or_flatten():
    assert len(q.parse("depth=1 and number=2 and o2=3").items) == 3
    assert len(q.parse("depth=1 or number=2 or o2=3").items) == 3


def test_not_and_flag():
    assert q.parse("not night") == Not(Flag("night"))


def test_keywords_case_insensitive():
    assert isinstance(q.parse("depth=1 AND number=2"), And)
    assert isinstance(q.parse("depth=1 Or number=2"), Or)


def test_quoted_value_keeps_spaces():
    assert q.parse('place="el toro"') == Compare("place", "=", "el toro")
    assert q.parse("place='el toro'") == Compare("place", "=", "el toro")


def test_bareword_accents():
    assert q.parse("region=Palamós") == Compare("region", "=", "Palamós")


# --- text operators --------------------------------------------------------

def test_eq_and_tilde_both_mean_contains():
    # = and ~ are synonyms: substring, case-insensitive
    assert ev("region=mall") is True
    assert ev("region~mall") is True
    assert ev("region=MALL") is True
    assert ev("place=toro") is True
    assert ev("place~toro") is True
    assert ev("place=shark") is False


def test_double_eq_is_exact():
    assert ev("region==Mallorca") is True
    assert ev("region==mallorca") is True          # case-insensitive
    assert ev("region==mall") is False             # not a substring match
    assert ev("place==toro") is False              # full value is "El Toro. Boya 3"


def test_text_negations():
    assert ev("region!=mall") is False             # !=/!~ = does not contain
    assert ev("region!~mall") is False
    assert ev("region!=catalunya") is True
    assert ev("region!==Mallorca") is False        # !== = not exact
    assert ev("region!==mall") is True


# --- number / date operators ----------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ("maxdepth>=10", True), ("maxdepth>30", False), ("depth=28", True),
    ("depth!=28", False), ("watertemp<16", True), ("sac<18", True),
    ("divetime>=55", True),
])
def test_number_ops(expr, expected):
    assert ev(expr) is expected


def test_date_ops():
    assert ev("date>=2025-01-01") is True
    assert ev("date<2025-01-01") is False
    assert ev("date=2025-08-10") is True


def test_year_derived():
    assert ev("year=2025") is True
    assert ev("year<2025") is False


def test_period_expands_to_date_range():
    # REC date is 2025-08-10
    assert ev('period=2025') is True
    assert ev('period=2024') is False
    assert ev('period!=2024') is True
    assert ev('period="month 2025-08"') is True
    # expands to a date-range AND node, not a 'period' field
    assert q.parse("period=2025") == And(
        [Compare("date", ">=", "2025-01-01"), Compare("date", "<=", "2025-12-31")])


def test_period_errors():
    with pytest.raises(ValueError, match="only supports = and !="):
        q.parse("period~2024")
    with pytest.raises(ValueError, match="invalid period"):
        q.parse("period=nonsense")


# --- multi-valued + derived fields ----------------------------------------

def test_multivalued_tank_and_buddy():
    assert ev("tank=15") is True
    assert ev("tank=12") is False
    assert ev("buddy~isa") is True


def test_tag_field():
    assert ev("tag=cave") is True        # record has "#cave" in comments
    assert ev("tag~cav") is True
    assert ev("tag=sidemount") is False
    assert ev("not tag=cave") is False


def test_bool_flags_and_equality():
    assert ev("night") is True
    assert ev("weekend") is True
    assert ev("not night") is False
    assert ev("night=true") is True
    assert ev("night=false") is False


def test_water_mapping():
    assert ev("water=salt") is True
    assert ev("water=fresh") is False


# --- composition -----------------------------------------------------------

def test_implicit_and_by_juxtaposition():
    # adjacent terms with no operator mean AND
    assert q.parse("region~mall period=2025") == q.parse("region~mall and period=2025")
    assert q.parse("night weekend depth>30") == And(
        [Flag("night"), Flag("weekend"), Compare("depth", ">", "30")])
    assert ev("place~toro region=Mallorca") is True
    assert ev("place~toro region=Catalunya") is False
    # mixes with explicit or/parentheses
    assert ev("region=Mallorca (place~toro or place~cala)") is True


def test_full_expression():
    assert ev("(region=Mallorca and place~toro) or region=Catalunya") is True
    assert ev("region=Catalunya or (depth>30 and night)") is False


# --- missing values --------------------------------------------------------

def test_missing_values_are_safe():
    empty = {"Depth": None, "Region": None, "buddies": [], "tank_sizes": []}
    assert q.predicate("region=mallorca")(empty) is False
    assert q.predicate("region!=mallorca")(empty) is True   # NULL != x -> kept
    assert q.predicate("depth>10")(empty) is False
    assert q.predicate("tank=15")(empty) is False


# --- errors ----------------------------------------------------------------

def test_unknown_field():
    with pytest.raises(ValueError, match="unknown query field"):
        q.parse("bogus=1")


def test_bad_operator_for_kind():
    with pytest.raises(ValueError, match="not valid for text field"):
        q.parse("region<5")
    with pytest.raises(ValueError, match="not valid for field"):
        q.parse("depth~foo")


def test_syntax_error():
    with pytest.raises(ValueError, match="invalid query"):
        q.parse("region=")


def test_empty_query():
    with pytest.raises(ValueError, match="empty query"):
        q.parse("   ")


def test_number_type_error_at_eval():
    with pytest.raises(ValueError, match="expected a number"):
        ev("maxdepth>=abc")
