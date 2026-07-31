"""A terminal host must not pass on its launcher's "no colour" declaration.

The live case this pins (2026-07-31): the desktop app had been started once
from a coding agent's shell, inherited ``NO_COLOR=1`` and an empty
``COLORTERM``, and handed both to every terminal pane it opened — so every
coding CLI in the workspace rendered monochrome, and kept doing so across
restarts because the restart chain re-inherits the environment.
"""

from __future__ import annotations

import pytest

from jarvis.core.colour_env import sanitize_process_environment, stale_colour_claims


def test_an_environment_that_says_nothing_about_colour_is_left_alone() -> None:
    assert stale_colour_claims({"PATH": "/usr/bin", "TERM": "xterm-256color"}) == ()


def test_no_color_is_recognised_whatever_its_value() -> None:
    """The convention is presence, not value — "0" still means no colour."""
    assert stale_colour_claims({"NO_COLOR": "1"}) == ("NO_COLOR",)
    assert stale_colour_claims({"NO_COLOR": "0"}) == ("NO_COLOR",)


def test_an_empty_no_color_is_not_a_claim() -> None:
    assert stale_colour_claims({"NO_COLOR": ""}) == ()


def test_force_color_counts_only_when_it_disables_colour() -> None:
    assert stale_colour_claims({"FORCE_COLOR": "0"}) == ("FORCE_COLOR",)
    assert stale_colour_claims({"FORCE_COLOR": "false"}) == ("FORCE_COLOR",)
    assert stale_colour_claims({"FORCE_COLOR": "3"}) == ()
    assert stale_colour_claims({"FORCE_COLOR": "1"}) == ()


def test_colorterm_counts_only_when_empty() -> None:
    """Presence alone reads as "sixteen colours" and would downgrade a pane."""
    assert stale_colour_claims({"COLORTERM": ""}) == ("COLORTERM",)
    assert stale_colour_claims({"COLORTERM": "truecolor"}) == ()
    assert stale_colour_claims({"PATH": "/usr/bin"}) == ()


def test_the_measured_live_environment_is_reported_in_full() -> None:
    """Exactly what pid 72176 carried on 2026-07-31."""
    live = {"TERM": "dumb", "COLORTERM": "", "NO_COLOR": "1", "PATH": "/usr/bin"}

    assert stale_colour_claims(live) == ("NO_COLOR", "COLORTERM")


def test_sanitizing_the_process_environment_removes_them_and_reports_what_went(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLORTERM", "")
    monkeypatch.setenv("JARVIS_COLOUR_TEST_SENTINEL", "preserved")

    dropped = sanitize_process_environment()

    assert dropped == ("NO_COLOR", "COLORTERM")
    assert "NO_COLOR" not in os.environ
    assert "COLORTERM" not in os.environ
    assert os.environ["JARVIS_COLOUR_TEST_SENTINEL"] == "preserved"


def test_sanitizing_a_clean_environment_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("COLORTERM", "truecolor")

    assert sanitize_process_environment() == ()
