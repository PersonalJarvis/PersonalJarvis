"""Which approval channel a tool call actually has (audit GT-12).

``resolve_approval_surface`` answers one question: if this ``ask``-tier call
needs a human, WHO can be reached? Getting that wrong is what made every
non-voice surface stall for the full approval timeout and then record the
silence as a refusal.
"""
from __future__ import annotations

from jarvis.safety.approval_surface import (
    CONVERSATIONAL,
    INTERACTIVE,
    UNATTENDED,
    resolve_approval_surface,
)


def test_nothing_declared_is_unattended() -> None:
    """The honest default: no declaration means no known channel.

    A workflow step, a cron run, and a one-shot CLI call all reach the
    executor with an empty snapshot. Assuming a human would be a lie.
    """
    assert resolve_approval_surface(None) == UNATTENDED
    assert resolve_approval_surface({}) == UNATTENDED
    assert resolve_approval_surface({"output_language": "de"}) == UNATTENDED


def test_voice_confirm_still_means_conversational() -> None:
    """The legacy boolean keeps voice and realtime byte-identical."""
    assert resolve_approval_surface({"voice_confirm": True}) == CONVERSATIONAL
    assert resolve_approval_surface({"voice_confirm": False}) == UNATTENDED


def test_a_mission_id_grants_the_deck_channel() -> None:
    """The mission deck's tool-approval panel can answer out of band."""
    assert resolve_approval_surface({"mission_id": "m-1"}) == INTERACTIVE
    # Blank strings are not a channel.
    assert resolve_approval_surface({"mission_id": "   "}) == UNATTENDED
    assert resolve_approval_surface({"mission_id": None}) == UNATTENDED


def test_explicit_declaration_wins_over_the_derived_channel() -> None:
    for value in (CONVERSATIONAL, INTERACTIVE, UNATTENDED):
        assert resolve_approval_surface({"approval_surface": value}) == value
    # Explicit beats both the legacy boolean and the mission-id inference.
    assert (
        resolve_approval_surface(
            {"approval_surface": UNATTENDED, "voice_confirm": True, "mission_id": "m"}
        )
        == UNATTENDED
    )


def test_unknown_declaration_never_invents_a_channel() -> None:
    """A typo must fall back, not crash and not grant a richer channel."""
    assert resolve_approval_surface({"approval_surface": "supervised"}) == UNATTENDED
    assert (
        resolve_approval_surface({"approval_surface": "", "mission_id": "m-1"})
        == INTERACTIVE
    )


def test_declaration_is_case_and_whitespace_tolerant() -> None:
    assert resolve_approval_surface({"approval_surface": " Interactive "}) == INTERACTIVE
