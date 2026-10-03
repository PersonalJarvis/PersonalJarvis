"""One output language per turn for every honesty phrase (audit OF-17).

The evidence-gate fallback and the action-honesty replacement sit two lines
apart in ``BrainManager.generate`` and used to resolve the turn's language
separately with DIFFERENT defaults — ``"de"`` here, ``DEFAULT_LOCALE`` there.
On a turn whose language cannot be detected (a bare product name, "42", a
grunt) the user could get a German refusal followed by an English honesty
phrase in the same breath.

AGENTS.md §1: ONE resolver decides the turn for ALL layers, and every locale is
equal — no layer may pin its own default.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.core.turn_language import DEFAULT_LOCALE

_MANAGER = Path(__file__).resolve().parents[3] / "jarvis" / "brain" / "manager.py"
_RESOLVE_CALL_RE = re.compile(
    r"resolve_output_language\((?:[^()]|\([^()]*\))*\)", re.DOTALL
)


def test_default_locale_is_not_german() -> None:
    # The whole point of the shared constant: nobody's mother tongue is the
    # silent winner of an undetectable turn.
    assert DEFAULT_LOCALE == "en"


def test_honesty_resolution_uses_the_shared_default_locale() -> None:
    source = _MANAGER.read_text(encoding="utf-8")
    marker = "honesty_lang = resolve_output_language("
    start = source.index(marker) + len("honesty_lang = ")
    calls = _RESOLVE_CALL_RE.findall(source[start:])
    assert calls, "missing honesty resolve_output_language call"
    assert "default=DEFAULT_LOCALE" in calls[0], calls[0]


def test_the_two_honesty_guards_share_one_resolution() -> None:
    """The evidence fallback and the action-honesty guard read one variable."""
    source = _MANAGER.read_text(encoding="utf-8")
    assert "honesty_lang = resolve_output_language(" in source
    assert source.count("lang=honesty_lang") == 1
    assert source.count("language=honesty_lang") == 1
