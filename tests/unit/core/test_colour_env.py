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
    """Presence is the trigger, not the value — "0" still means no colour."""
    assert stale_colour_claims({"NO_COLOR": "1"}) == ("NO_COLOR",)
    assert stale_colour_claims({"NO_COLOR": "0"}) == ("NO_COLOR",)


def test_an_empty_no_color_is_dropped_too() -> None:
    """Consumers disagree on whether ``NO_COLOR=`` suppresses colour.

    The published convention says a non-empty value is required; several
    libraries check presence alone. An empty value therefore cannot express an
    intent either way — and nobody who wants colour off writes one — so a
    terminal host drops it rather than betting on the reader.
    """
    assert stale_colour_claims({"NO_COLOR": ""}) == ("NO_COLOR",)


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


# --------------------------------------------------------------------------
# The wiring, not just the helper. Both defects this module was corrected for
# lived in the seam between the two: a helper that is never called, or called
# too late, passes every test above.
# --------------------------------------------------------------------------


def _main_stopped_at_arg_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run ``launcher.main`` up to the first thing that follows the drop.

    Stopping at ``_parse_args`` is the assertion: everything the app does with
    its environment happens after it, so a drop that still ran is a drop that
    beat every spawn path.
    """
    import jarvis.core.path_augment as path_augment
    import jarvis.ui.web.launcher as launcher

    monkeypatch.setattr(path_augment, "ensure_cli_paths", lambda: None)

    def _stop(_argv: object) -> None:
        raise RuntimeError("stop: argument parsing reached")

    monkeypatch.setattr(launcher, "_parse_args", _stop)
    with pytest.raises(RuntimeError, match="argument parsing reached"):
        launcher.main([])


def test_start_up_drops_the_claims_before_anything_else_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLORTERM", "")

    _main_stopped_at_arg_parsing(monkeypatch)

    assert "NO_COLOR" not in os.environ
    assert "COLORTERM" not in os.environ


def test_the_drop_is_announced_somewhere_a_person_can_read_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An app that silently rewrites its own environment cannot be debugged.

    Pinned against a live sink because the first version of this logged through
    the stdlib root logger, which at that point in ``main`` discards INFO
    outright — the line existed in the source and reached nobody.
    """
    from loguru import logger

    monkeypatch.setenv("NO_COLOR", "1")
    written: list[str] = []
    sink = logger.add(written.append, level="INFO", format="{message}")
    try:
        _main_stopped_at_arg_parsing(monkeypatch)
    finally:
        logger.remove(sink)

    assert any("NO_COLOR" in line for line in written), written


def test_nothing_is_announced_when_there_was_nothing_to_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from loguru import logger

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    written: list[str] = []
    sink = logger.add(written.append, level="INFO", format="{message}")
    try:
        _main_stopped_at_arg_parsing(monkeypatch)
    finally:
        logger.remove(sink)

    assert not [line for line in written if "colour-suppressing" in line]
