"""Unit tests for OopsProtocol (per-session v2 API).

TDD — written first, run RED, then implemented, run GREEN.
Uses FakeBus from CONTRACTS.md and asyncio.run() — no pytest-asyncio.
"""
from __future__ import annotations

import asyncio

from optimistic.events import (
    CorrectionReason,
    WorkerCorrectionNeeded,
)
from optimistic.oops import OopsProtocol

# ---------------------------------------------------------------------------
# FakeBus — verbatim from CONTRACTS.md
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

def _run(coro):
    return asyncio.run(coro)


def _make_ev(
    *,
    reason: CorrectionReason = CorrectionReason.MISSING_INFO,
    detail: str = "no email address on file for Max",
    command: str = "Schreib Max eine Mail",
    mission_id: str = "m1",
    session_id: str = "s1",
) -> WorkerCorrectionNeeded:
    return WorkerCorrectionNeeded(
        mission_id=mission_id,
        reason=reason,
        detail=detail,
        command=command,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Core per-session routing tests
# ---------------------------------------------------------------------------

def test_correction_lands_in_correct_session_not_other():
    """A WorkerCorrectionNeeded for 's1' must appear in pending('s1') but NOT in pending('s2')."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = _make_ev(session_id="s1")
        await bus.publish(ev)

        assert len(oops.pending("s1")) == 1
        assert oops.pending("s1")[0] is ev
        assert oops.pending("s2") == [], "event for s1 must not appear in s2"

    _run(scenario())


def test_pending_default_session():
    """Events without an explicit session_id land in the 'default' session."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = WorkerCorrectionNeeded(
            mission_id="m-default",
            reason=CorrectionReason.NETWORK_ERROR,
            detail="timeout",
            command="do something",
            # session_id defaults to "default"
        )
        await bus.publish(ev)

        assert len(oops.pending("default")) == 1
        assert oops.pending("s1") == []

    _run(scenario())


def test_pending_returns_list_copy_not_reference():
    """pending() must not return a mutable reference to internal state."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = _make_ev(session_id="s1")
        await bus.publish(ev)

        lst = oops.pending("s1")
        assert len(lst) == 1
        # Mutating the returned list must not affect the internal buffer.
        lst.clear()
        assert len(oops.pending("s1")) == 1

    _run(scenario())


# ---------------------------------------------------------------------------
# flush() tests
# ---------------------------------------------------------------------------

def test_flush_returns_phrase_and_clears_session_buffer():
    """flush('s1') returns one German phrase and clears s1's buffer; s2 untouched."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev_s1 = _make_ev(session_id="s1")
        ev_s2 = _make_ev(
            detail="no email address on file for Anna",
            command="Mail an Anna",
            session_id="s2",
        )
        await bus.publish(ev_s1)
        await bus.publish(ev_s2)

        phrases = oops.flush("s1")

        assert len(phrases) == 1, "exactly one phrase for one pending correction"
        # Buffer for s1 cleared
        assert oops.pending("s1") == []
        # s2 untouched
        assert len(oops.pending("s2")) == 1

    _run(scenario())


def test_flush_phrase_contains_recipient_name():
    """flush returns a phrase whose lowercase contains the recipient name 'max'."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = _make_ev(
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Max",
            session_id="s1",
        )
        await bus.publish(ev)

        phrases = oops.flush("s1")
        assert len(phrases) == 1
        assert "max" in phrases[0].lower(), (
            f"phrase must contain 'max'; got: {phrases[0]!r}"
        )

    _run(scenario())


def test_flush_phrase_contains_no_tool_names():
    """flush phrases must not contain tool-name tokens like 'gmail'."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = _make_ev(
            detail="no email address on file for Max via gmail",
            command="send via gmail",
            session_id="s1",
        )
        await bus.publish(ev)

        phrases = oops.flush("s1")
        assert "gmail" not in phrases[0].lower(), (
            f"tool name 'gmail' must be scrubbed; got: {phrases[0]!r}"
        )

    _run(scenario())


def test_flush_phrase_contains_no_backticks():
    """flush phrases must not contain backticks."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = _make_ev(
            detail="no email address on file for Max via `gmail`",
            command="send via `gmail` now",
            session_id="s1",
        )
        await bus.publish(ev)

        phrases = oops.flush("s1")
        assert "`" not in phrases[0], (
            f"backtick must be scrubbed; got: {phrases[0]!r}"
        )

    _run(scenario())


def test_flush_empty_session_returns_empty_list():
    """flush on a session with no pending events returns []."""

    bus = FakeBus()
    oops = OopsProtocol(bus)

    result = oops.flush("nonexistent-session")
    assert result == []


def test_flush_clears_only_target_session():
    """flush('s1') must NOT clear s2's buffer."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        await bus.publish(_make_ev(session_id="s1"))
        await bus.publish(_make_ev(
            detail="no email address on file for Anna",
            command="Mail Anna",
            session_id="s2",
        ))

        oops.flush("s1")

        assert oops.pending("s1") == []
        assert len(oops.pending("s2")) == 1

    _run(scenario())


def test_flush_twice_second_call_returns_empty():
    """Calling flush twice on the same session: second call returns []."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        await bus.publish(_make_ev(session_id="s1"))

        first = oops.flush("s1")
        second = oops.flush("s1")

        assert len(first) == 1
        assert second == []

    _run(scenario())


# ---------------------------------------------------------------------------
# injected_context() tests
# ---------------------------------------------------------------------------

def test_injected_context_reflects_pending_before_flush():
    """injected_context returns one machine-readable line per pending correction."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = _make_ev(session_id="s1")
        await bus.publish(ev)

        ctx = oops.injected_context("s1")
        assert len(ctx) == 1
        assert CorrectionReason.MISSING_INFO.value in ctx[0]
        assert "no email address on file for Max" in ctx[0]
        # injected_context for a different session is empty
        assert oops.injected_context("s2") == []

    _run(scenario())


def test_injected_context_empty_after_flush():
    """injected_context returns [] for a session after flush clears its buffer."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        await bus.publish(_make_ev(session_id="s1"))

        # Confirm it's present before flush
        assert len(oops.injected_context("s1")) == 1

        oops.flush("s1")

        assert oops.injected_context("s1") == []

    _run(scenario())


def test_injected_context_multiple_events():
    """injected_context returns one line per event, in insertion order."""

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev1 = _make_ev(reason=CorrectionReason.MISSING_INFO, session_id="s1")
        ev2 = _make_ev(
            reason=CorrectionReason.NETWORK_ERROR,
            detail="timeout",
            command="do X",
            session_id="s1",
        )
        await bus.publish(ev1)
        await bus.publish(ev2)

        ctx = oops.injected_context("s1")
        assert len(ctx) == 2
        assert CorrectionReason.MISSING_INFO.value in ctx[0]
        assert CorrectionReason.NETWORK_ERROR.value in ctx[1]

    _run(scenario())


# ---------------------------------------------------------------------------
# phrase() / _scrub() tests  (carried over from v1, still valid)
# ---------------------------------------------------------------------------

def test_phrase_missing_info_names_recipient():
    """MISSING_INFO phrase organically names the recipient extracted from detail."""

    bus = FakeBus()
    oops = OopsProtocol(bus)

    ev = WorkerCorrectionNeeded(
        mission_id="m1",
        reason=CorrectionReason.MISSING_INFO,
        detail="no email address on file for Sophie",
        command="Schreib Sophie eine Mail",
    )
    result = oops.phrase(ev)
    assert "sophie" in result.lower(), f"'Sophie' must appear; got: {result!r}"
    assert "`" not in result
    assert "gmail" not in result.lower()


def test_phrase_auth_required_polite_german():
    """AUTH_REQUIRED phrase is non-empty and scrubbed."""

    bus = FakeBus()
    oops = OopsProtocol(bus)

    ev = WorkerCorrectionNeeded(
        mission_id="auth",
        reason=CorrectionReason.AUTH_REQUIRED,
        detail="account not authorised",
        command="Buche Termin",
    )
    result = oops.phrase(ev)
    assert result.strip()
    assert "`" not in result
    assert "auth_required" not in result.lower()


def test_phrase_fatal_brief_apology():
    """FATAL phrase is non-empty, apologetic, and scrubbed."""

    bus = FakeBus()
    oops = OopsProtocol(bus)

    ev = WorkerCorrectionNeeded(
        mission_id="fatal",
        reason=CorrectionReason.FATAL,
        detail="division by zero",
        command="Erstell Dokument",
    )
    result = oops.phrase(ev)
    assert result.strip()
    assert "`" not in result
    assert "**" not in result


def test_scrub_removes_tool_names_and_markdown():
    """_scrub removes gmail/calendar/drive/mcp tokens, backticks, asterisks, collapses spaces."""

    bus = FakeBus()
    oops = OopsProtocol(bus)

    raw = "send via `gmail` now with **MCP** and Calendar   and drive"
    scrubbed = oops._scrub(raw)

    assert "`" not in scrubbed
    assert "**" not in scrubbed
    assert "gmail" not in scrubbed.lower()
    assert "mcp" not in scrubbed.lower()
    assert "calendar" not in scrubbed.lower()
    assert "drive" not in scrubbed.lower()
    assert "  " not in scrubbed


# ---------------------------------------------------------------------------
# Contract test from the prompt spec (exact scenario)
# ---------------------------------------------------------------------------

def test_spec_scenario_missing_info_max():
    """
    Exact scenario from the sub-agent spec:
    publish WorkerCorrectionNeeded(reason=MISSING_INFO,
                                   detail='no email address on file for Max',
                                   mission_id='m1', session_id='s1')
    -> lands in pending('s1'), not pending('s2')
    -> flush('s1') returns exactly one phrase whose lowercase contains 'max'
       and contains neither 'gmail' nor a backtick
    -> afterwards pending('s1') is empty
    -> injected_context('s1') had reflected the pending before flush
    """

    async def scenario():
        bus = FakeBus()
        oops = OopsProtocol(bus)

        ev = WorkerCorrectionNeeded(
            reason=CorrectionReason.MISSING_INFO,
            detail="no email address on file for Max",
            mission_id="m1",
            session_id="s1",
        )
        await bus.publish(ev)

        # Lands in s1, not s2
        assert len(oops.pending("s1")) == 1
        assert oops.pending("s2") == []

        # injected_context reflects the pending
        ctx = oops.injected_context("s1")
        assert len(ctx) == 1
        assert "missing_info" in ctx[0]

        # flush returns exactly one phrase
        phrases = oops.flush("s1")
        assert len(phrases) == 1

        phrase_low = phrases[0].lower()
        assert "max" in phrase_low, f"phrase must contain 'max'; got: {phrases[0]!r}"
        assert "gmail" not in phrase_low, f"'gmail' must be scrubbed; got: {phrases[0]!r}"
        assert "`" not in phrases[0], f"backtick must be scrubbed; got: {phrases[0]!r}"

        # Buffer cleared
        assert oops.pending("s1") == []

    _run(scenario())
