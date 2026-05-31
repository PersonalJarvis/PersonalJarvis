"""Unit tests for the OopsProtocol ("Oops" error-handling / turn-boundary protocol).

TDD: tests are written first, run RED against a missing module, then go GREEN
once optimistic/oops.py is implemented.

Uses FakeBus (from CONTRACTS.md) and asyncio.run() — no pytest-asyncio needed.
"""
from __future__ import annotations

import asyncio

from optimistic.events import (
    CorrectionReason,
    WorkerCorrectionNeeded,
)
from optimistic.oops import OopsProtocol

# ---------------------------------------------------------------------------
# FakeBus — verbatim from CONTRACTS.md "SUB-AGENT 3" section
# ---------------------------------------------------------------------------

class FakeBus:
    def __init__(self):
        self.published = []
        self._subs = {}
        self._all = []

    def subscribe(self, event_type, handler):
        self._subs.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler):
        self._all.append(handler)

    async def publish(self, event):
        self.published.append(event)
        for et, hs in self._subs.items():
            if isinstance(event, et):
                for h in hs:
                    await h(event)
        for h in self._all:
            await h(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_correction(
    *,
    reason: CorrectionReason = CorrectionReason.MISSING_INFO,
    detail: str = "no email address on file for Max",
    command: str = "Schreib Max eine Mail",
) -> WorkerCorrectionNeeded:
    return WorkerCorrectionNeeded(
        mission_id="test-mission",
        reason=reason,
        detail=detail,
        command=command,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_published_correction_lands_in_pending_not_auto_spoken() -> None:
    """After bus.publish(WorkerCorrectionNeeded), it must be in pending — nothing spoken."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        assert oops.pending == []

        ev = _make_correction()
        await bus.publish(ev)

        # Must appear in pending (context injection)
        assert len(oops.pending) == 1
        assert oops.pending[0] is ev

        # Nothing must have been auto-spoken: bus.published contains only our
        # original event, not any additional "speak" event from OopsProtocol.
        correction_events = [e for e in bus.published if isinstance(e, WorkerCorrectionNeeded)]
        assert len(correction_events) == 1  # only the one we published

    _run(scenario())


def test_correction_stays_pending_while_user_is_speaking() -> None:
    """While is_user_speaking() is True, corrections accumulate but are not surfaced."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)
        oops.set_user_speaking(True)

        assert oops.is_user_speaking() is True

        ev = _make_correction()
        await bus.publish(ev)

        # Still pending, still speaking
        assert len(oops.pending) == 1
        assert oops.is_user_speaking() is True

    _run(scenario())


def test_vad_turn_boundary_returns_phrase_and_clears_pending() -> None:
    """vad_turn_boundary() returns one scrubbed phrase per pending correction,
    clears the buffer, and sets is_user_speaking to False."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)
        oops.set_user_speaking(True)

        ev = _make_correction(
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Max",
            command="Schreib Max eine Mail",
        )
        await bus.publish(ev)

        assert len(oops.pending) == 1
        assert oops.is_user_speaking() is True

        spoken = oops.vad_turn_boundary()

        # One phrase returned
        assert len(spoken) == 1

        phrase = spoken[0].lower()

        # Must name the recipient
        assert "max" in phrase, f"phrase must contain 'max'; got: {spoken[0]!r}"

        # Must be scrubbed — no tool names, no backticks
        assert "gmail" not in phrase, f"tool name 'gmail' must be scrubbed; got: {spoken[0]!r}"
        assert "`" not in phrase, f"backtick must be scrubbed; got: {spoken[0]!r}"

        # Buffer cleared, speaking flag off
        assert oops.pending == [], "pending buffer must be empty after vad_turn_boundary"
        assert oops.is_user_speaking() is False

    _run(scenario())


def test_injected_context_reflects_pending_count_and_reason() -> None:
    """injected_context() returns one internal line per pending correction,
    containing the reason value string (unspoken, unprocessed)."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        # No pending yet
        assert oops.injected_context() == []

        ev1 = _make_correction(
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Max",
        )
        ev2 = _make_correction(
            reason=CorrectionReason.NETWORK_ERROR,
            detail="connection timed out",
            command="Buche einen Termin",
        )
        await bus.publish(ev1)
        await bus.publish(ev2)

        ctx = oops.injected_context()
        assert len(ctx) == 2, "one context line per pending correction"

        # The context lines carry the reason value
        assert CorrectionReason.MISSING_INFO.value in ctx[0]
        assert CorrectionReason.NETWORK_ERROR.value in ctx[1]

    _run(scenario())


def test_scrub_removes_tool_names_and_backticks() -> None:
    """_scrub() is exposed via the outcome of phrase(); verify it directly via a
    crafted correction whose detail or command embeds scrub targets."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        # Build a correction whose raw phrase would contain a tool name and backtick
        # by using a detail that smuggles in the target.
        # We access _scrub indirectly: inject a correction and call phrase().
        ev = _make_correction(
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Max via `gmail`",
            command="send via `gmail` now",
        )
        result = oops.phrase(ev)

        low = result.lower()
        assert "gmail" not in low, "tool name 'gmail' must be scrubbed from phrase output"
        assert "`" not in result, "backtick must be scrubbed from phrase output"

    _run(scenario())


def test_scrub_standalone_sample() -> None:
    """Directly call _scrub on a known sample and verify all three scrub rules."""

    bus = FakeBus()
    oops = OopsProtocol(bus)

    raw = "send via `gmail` now with **MCP** and Calendar"
    scrubbed = oops._scrub(raw)

    assert "`" not in scrubbed, "backticks must be removed"
    assert "**" not in scrubbed, "asterisks must be removed"
    assert "gmail" not in scrubbed.lower(), "tool name 'gmail' must be removed"
    assert "mcp" not in scrubbed.lower(), "tool name 'mcp' must be removed"
    assert "calendar" not in scrubbed.lower(), "tool name 'calendar' must be removed"
    # No double spaces
    assert "  " not in scrubbed, "whitespace must be collapsed"


def test_phrase_auth_required_is_polite_and_german() -> None:
    """AUTH_REQUIRED phrase must be a non-empty, non-jargon German sentence."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = WorkerCorrectionNeeded(
            mission_id="auth-test",
            reason=CorrectionReason.AUTH_REQUIRED,
            detail="Google account not authorised",
            command="Buche Termin",
        )
        result = oops.phrase(ev)

        assert result.strip(), "phrase must be non-empty"
        # Must not expose raw enum or tool jargon
        assert "auth_required" not in result.lower()
        # Must not contain backticks or asterisks (scrubbed)
        assert "`" not in result
        assert "**" not in result

    _run(scenario())


def test_phrase_fatal_is_brief_apology() -> None:
    """FATAL phrase must be a non-empty apologetic German sentence."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = WorkerCorrectionNeeded(
            mission_id="fatal-test",
            reason=CorrectionReason.FATAL,
            detail="unexpected exception: division by zero",
            command="Erstell ein Dokument",
        )
        result = oops.phrase(ev)

        assert result.strip(), "phrase must be non-empty"
        assert "`" not in result
        assert "**" not in result

    _run(scenario())


def test_multiple_corrections_all_returned_by_vad() -> None:
    """When two corrections accumulate, vad_turn_boundary returns both."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)
        oops.set_user_speaking(True)

        ev1 = _make_correction(
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Max",
        )
        ev2 = _make_correction(
            reason=CorrectionReason.NETWORK_ERROR,
            detail="connection timeout",
            command="Buche Termin",
        )
        await bus.publish(ev1)
        await bus.publish(ev2)

        assert len(oops.pending) == 2

        spoken = oops.vad_turn_boundary()
        assert len(spoken) == 2, "both pending corrections must be spoken at turn boundary"
        assert oops.pending == []
        assert oops.is_user_speaking() is False

    _run(scenario())


def test_vad_turn_boundary_when_not_speaking_still_works() -> None:
    """vad_turn_boundary() works even if is_user_speaking was already False."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)
        # Default: not speaking
        assert oops.is_user_speaking() is False

        ev = _make_correction()
        await bus.publish(ev)

        spoken = oops.vad_turn_boundary()
        assert len(spoken) == 1
        assert oops.pending == []

    _run(scenario())


def test_missing_info_phrase_contains_recipient_name() -> None:
    """MISSING_INFO phrase must organically surface the recipient name extracted from detail."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = WorkerCorrectionNeeded(
            mission_id="m1",
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Sophie",
            command="Schreib Sophie eine Mail",
        )
        result = oops.phrase(ev)
        assert "sophie" in result.lower(), f"name 'Sophie' must appear in phrase; got: {result!r}"

    _run(scenario())
