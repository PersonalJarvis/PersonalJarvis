"""One failed step must never end a multi-step spoken turn.

Live forensic 2026-08-20 13:41:20-29 (session c5863fe0, vertex-live). The user
asked for four things in one breath: what is due today, research it, look at
the plugins, and check everything. What happened:

* 13:41:22 ``google_calendar`` failed with the perfectly speakable
  ``"Google Calendar is not connected — connect it in the Plugins view."``
* 13:41:23 the model called ``spawn_worker``; the delegation gate blocked it
  (the transcript had mangled "spawn a deep-dive sub-agent" beyond recognition)
  and handed back model-directed text: *"Answer the user's turn directly
  yourself, right now, inline."*
* 13:41:24 the model produced nothing; the recovery read only
  ``_direct_tool_results[-1]`` — the BLOCK — found nothing speakable in it and
  fell through to the stock line.
* 13:41:27 the user heard "Das hat gerade nicht geklappt." and hung up.

Three defects, one symptom, all pinned here:

1. Only the LAST tool result was read, so the one usable reason of the whole
   turn (result 0) was thrown away.
2. A policy BLOCK was treated as a tool failure, so a decision the guard made
   became the spoken outcome of the turn.
3. The recovery prompt forbade every remaining step ("do not call any
   function"), so a four-part request died after part one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.realtime.session import (
    RealtimeVoiceSession,
    _direct_tool_result_retry_prompt,
)
from jarvis.voice.action_phrases import action_phrase

CALENDAR_REASON = "Google Calendar is not connected — connect it in the Plugins view."
SPAWN_BLOCK_TEXT = (
    "spawn_worker was not executed: the user did not explicitly ask to "
    "delegate this to a background agent. Answer the user's turn directly "
    "yourself, right now, inline."
)


class FakeTransport:
    creates_responses_automatically = False

    def __init__(self):
        self.text_inputs = []

    async def send_text(self, text):
        self.text_inputs.append(text)

    async def send_tool_result(self, call_id, name, result):
        pass

    async def request_response(self, *, required_tool=None):
        del required_tool

    async def interrupt(self):
        pass

    async def close(self):
        pass


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        del cfg
        return FakeTransport()


def _cfg():
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=False),
    )


def _build():
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="multi-step",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(),
        config=_cfg(),
        bus=None,
        brain=None,
    )
    sess._session = FakeTransport()
    return sess, jsons


def _calendar_failure():
    return ("google_calendar", {"success": False, "output": None, "error": CALENDAR_REASON})


def _spawn_block():
    return (
        "spawn_worker",
        {"success": False, "blocked": True, "output": None, "error": SPAWN_BLOCK_TEXT},
    )


# ---------------------------------------------------------------------------
# Defect 1 + 2 — the exact transcript of 13:41
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_reason_survives_a_block_that_ran_after_it() -> None:
    """The 13:41 turn verbatim: a failure, then a gated call."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(_calendar_failure())
    sess._direct_tool_results.append(_spawn_block())

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    # What the user actually heard on 2026-08-20 — never again.
    assert text != action_phrase("action_failed_generic", sess._language)
    assert "Google Calendar" in text


@pytest.mark.asyncio
async def test_a_block_never_leaks_its_model_instructions_into_speech() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_calendar_failure())
    sess._direct_tool_results.append(_spawn_block())

    text, _succeeded = await sess._direct_tool_fallback_text()

    assert "spawn_worker" not in text
    assert "inline" not in text.lower()
    assert "background agent" not in text.lower()


@pytest.mark.asyncio
async def test_a_turn_that_only_got_blocked_is_not_reported_as_broken() -> None:
    """Nothing ran and nothing broke — that is not "that didn't work"."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spawn_block())

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert text == action_phrase("actions_unavailable", sess._language)
    assert "spawn_worker" not in text


# ---------------------------------------------------------------------------
# Partial success — a result is a result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_success_reports_the_work_and_the_shortfall() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        ("wiki", {"success": True, "spoken_reply": "Three things are due today."})
    )
    sess._direct_tool_results.append(_calendar_failure())

    text, succeeded = await sess._direct_tool_fallback_text()

    # Work happened, so the turn is NOT a failure — but the gap is still named.
    assert succeeded is True
    assert "Three things are due today." in text
    assert "Google Calendar" in text


@pytest.mark.asyncio
async def test_several_successes_are_all_spoken() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        ("wiki", {"success": True, "spoken_reply": "Three things are due today."})
    )
    sess._direct_tool_results.append(
        ("plugins", {"success": True, "spoken_reply": "Two plugins need attention."})
    )

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert "Three things are due today." in text
    assert "Two plugins need attention." in text


@pytest.mark.asyncio
async def test_every_failure_cause_reaches_the_line() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_calendar_failure())
    sess._direct_tool_results.append(
        ("gmail", {"success": False, "output": None, "error": "Gmail is not connected."})
    )

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert "Google Calendar" in text
    assert "Gmail" in text


@pytest.mark.asyncio
async def test_a_pending_confirmation_still_owns_the_turn() -> None:
    """The one line that needs an answer outranks any status summary."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        ("wiki", {"success": True, "spoken_reply": "Three things are due today."})
    )
    sess._direct_tool_results.append(
        (
            "send_email",
            {
                "success": False,
                "confirmation_required": True,
                "message": "Should I really send this email?",
            },
        )
    )

    text, _succeeded = await sess._direct_tool_fallback_text()

    assert text == "Should I really send this email?"


# ---------------------------------------------------------------------------
# Defect 3 — the recovery prompt must not forbid the remaining steps
# ---------------------------------------------------------------------------


def test_unfinished_work_is_detected_from_a_block_alone() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spawn_block())
    assert sess._turn_has_unfinished_work() is True


def test_unfinished_work_is_detected_from_a_failure() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_calendar_failure())
    assert sess._turn_has_unfinished_work() is True


def test_a_fully_successful_turn_has_no_unfinished_work() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(("wiki", {"success": True, "output": {}}))
    assert sess._turn_has_unfinished_work() is False


def test_a_pending_confirmation_is_not_unfinished_work() -> None:
    """The ball is in the user's court; nothing for the model to finish."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        ("send_email", {"success": False, "confirmation_required": True})
    )
    assert sess._turn_has_unfinished_work() is False


def test_the_continuation_prompt_orders_the_remaining_work() -> None:
    prompt = _direct_tool_result_retry_prompt(language="de", unfinished=True)

    # The instruction that ended the 13:41 turn must be gone.
    assert "do not call any function" not in prompt.lower()
    # ... and replaced by its opposite.
    assert "remaining part" in prompt.lower()
    assert "not over" in prompt.lower()
    # The side-effect guard survives: a succeeded call is never repeated.
    assert "already succeeded" in prompt.lower()


def test_the_plain_readback_prompt_still_forbids_new_calls() -> None:
    """Nothing is unfinished: the model must only speak what already ran."""
    prompt = _direct_tool_result_retry_prompt(language="de", unfinished=False)

    assert "do not call any function" in prompt.lower()
    assert "do not repeat the action" in prompt.lower()
