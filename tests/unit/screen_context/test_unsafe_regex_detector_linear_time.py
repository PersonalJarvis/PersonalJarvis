"""The custom-pattern safety detector is not catastrophic itself.

CodeQL py/redos: a group body read a backslash either as an escape or as a
plain character, so ``(\\'\\'\\'…`` had exponentially many parses. The body now
reads a backslash only as the start of an escape pair.
"""

from __future__ import annotations

import time

import pytest

from jarvis.screen_context.redaction import _UNSAFE_CUSTOM_REGEX_RE, validate_pattern_source


@pytest.mark.parametrize(
    ("source", "unsafe"),
    [
        (r"(a+)+", True),
        (r"(a*b)*", True),
        (r"(ab{2,})+", True),
        (r"(a|b)+", True),
        (r"(a|b){3}", True),
        (r"(\d+)\1", True),
        (r"(?P<x>a)(?P=x)", True),
        (r"(\)+)+", True),
        (r"CUST-[0-9]+", False),
        (r"(ab)+", False),
        (r"(a{b)+", False),
        (r"(x+\)+y)", False),
        (r"\(a+\)+", False),
    ],
)
def test_unsafe_detector_normal(source: str, unsafe: bool) -> None:
    assert bool(_UNSAFE_CUSTOM_REGEX_RE.search(source)) is unsafe


@pytest.mark.parametrize(
    "prefix",
    ["(", "(*", "(|", "(a+"],
    ids=["open", "open-star", "open-bar", "open-quant"],
)
def test_unsafe_detector_pathological(prefix: str) -> None:
    source = prefix + "\\'" * 248
    start = time.perf_counter()
    _UNSAFE_CUSTOM_REGEX_RE.search(source)
    validate_pattern_source(source)
    assert time.perf_counter() - start < 1.0
