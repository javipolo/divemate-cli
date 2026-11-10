"""Tests for assembling the query from the --query flag and bare trailing words,
including repair of shell-split / shell-merged multi-word values (cli._query_text)."""

import argparse

from divemate import cli, query


def qt(flag=None, words=None):
    a = argparse.Namespace(query=flag, query_words=words or [])
    return cli._query_text(a)


def parses(s):
    query.parse(s)   # raises ValueError if not
    return True


def test_none_when_empty():
    assert qt() is None


def test_plain_word_passthrough():
    assert qt(words=["tag=sidemount"]) == "tag=sidemount"


def test_flag_and_words_and_combined():
    # both already valid -> AND-combined with parentheses (precedence preserved)
    assert qt(flag="depth>20", words=["night"]) == "(depth>20) and (night)"


def test_shell_merged_value_single_token():
    # period='last year' reaches us as one token with a space
    assert qt(words=["period=last year", "tag=sidemount"]) == \
        'period="last year" tag="sidemount"'
    assert parses(qt(words=["period=last year", "tag=sidemount"]))


def test_shell_split_value_across_tokens():
    # period=last year  (fully unquoted) -> two tokens, value split
    out = qt(words=["period=last", "year", "tag=sidemount"])
    assert out == 'period="last year" tag="sidemount"'
    assert parses(out)


def test_flag_holds_partial_value_continued_by_words():
    # --query period=last year tag=sidemount  -> flag='period=last', rest words
    out = qt(flag="period=last", words=["year", "tag=sidemount"])
    assert parses(out)
    assert 'period="last year"' in out


def test_text_value_with_space():
    assert qt(words=["region=costa", "brava"]) == 'region="costa brava"'


def test_ambiguous_but_valid_is_left_as_and():
    # region~mallorca night is valid (region AND flag night) -> not regrouped
    assert qt(words=["region~mallorca", "night"]) == "region~mallorca night"


def test_or_precedence_preserved_when_valid():
    assert qt(flag="depth>1 or number=2", words=["o2=3"]) == \
        "(depth>1 or number=2) and (o2=3)"
