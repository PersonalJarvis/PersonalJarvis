"""agy requires --effort next to --model, and silently "answers" without it.

Live 2026-07-26: a current agy aborted with

    invalid model selection (--model "gemini-3.5-flash" --effort ""): ...

and wrote that to stdout, where the caller read it as the model's answer. The
pairing is therefore not cosmetic — without it the provider is unusable while
looking like it works.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.protocols import BrainRequest
from jarvis.plugins.brain import antigravity


def _agy() -> SimpleNamespace:
    return SimpleNamespace(kind="agy", argv_prefix=["agy"])


def _gemini() -> SimpleNamespace:
    return SimpleNamespace(kind="gemini", argv_prefix=["gemini"])


def test_agy_always_gets_an_effort_beside_its_model() -> None:
    argv = antigravity._build_argv(_agy(), "prompt", "gemini-3.5-flash")

    assert "--effort" in argv
    effort = argv[argv.index("--effort") + 1]
    assert effort in {"low", "medium", "high"}
    assert effort, "an empty effort is exactly what agy rejects"


def test_the_gemini_cli_is_left_alone() -> None:
    """Only agy demands the pair; adding it elsewhere would break that path."""
    argv = antigravity._build_argv(_gemini(), "prompt", "gemini-3.5-flash")

    assert "--effort" not in argv


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        # agy has no "off"; the caller's intent maps to the lowest it offers.
        ("none", "low"),
        # Anything unknown or unset falls back rather than passing debris on.
        (None, "medium"),
        ("nonsense", "medium"),
    ],
)
def test_effort_follows_the_caller_and_never_goes_empty(
    requested: str | None, expected: str
) -> None:
    req = BrainRequest(messages=(), reasoning_effort=requested)  # type: ignore[arg-type]

    assert antigravity._agy_effort(req) == expected


def test_a_request_without_the_field_still_yields_a_valid_effort() -> None:
    assert antigravity._agy_effort(None) == "medium"
    assert antigravity._agy_effort(SimpleNamespace()) == "medium"
