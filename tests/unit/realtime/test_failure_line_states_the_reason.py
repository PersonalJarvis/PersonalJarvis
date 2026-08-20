"""A realtime failure the user hears must name its CAUSE, never a stock line.

Live forensic 2026-08-20 13:25:35 (session 5095df0a, vertex-live): the user
asked Jarvis to look into the mail inboxes. The live model called the ``gmail``
tool, which failed in 77 ms with the perfectly speakable
``"Gmail is not connected — connect it in the Plugins view."`` on
``ToolResult.error``. The provider rendered no speech, so the session fell back
to ``_direct_tool_fallback_text`` — and that path read only ``spoken_reply`` /
``message``, dropped ``error`` entirely, and spoke the 30-character generic
phrase. Fourteen seconds later a ``run_skill`` failure did the same thing.
Asked "what was the problem?", the live model then INVENTED a connection error,
because the only thing left in its context was the contentless generic line.

Contract (maintainer directive 2026-08-20, all supported languages):

* A failed realtime tool speaks the cause the tool reported.
* A failed delegated turn speaks its KNOWN internal cause from its own
  localized phrase — never the raw engineering string, never the stock line.
* The opaque-token gate still holds: a bare ``exit N`` / numeric / diagnostic
  token yields no reason and the generic phrase remains the honest floor.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.realtime.session as session_module
from jarvis.realtime.session import (
    _FAILURE_REASON_MAX_CHARS,
    RealtimeVoiceSession,
    _DelegateTurnState,
    _speakable_failure_reason,
)
from jarvis.voice.action_phrases import action_phrase

GMAIL_REASON = "Gmail is not connected — connect it in the Plugins view."


class FakeTransport:
    creates_responses_automatically = False

    def __init__(self):
        self.text_inputs = []
        self.tool_results = []

    async def send_text(self, text):
        self.text_inputs.append(text)

    async def send_tool_result(self, call_id, name, result):
        self.tool_results.append((call_id, name, result))

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


class DeadChainBrain:
    """Every configured model failed this turn (the outage shape)."""

    def __init__(self):
        self._last_turn_all_failed = True

    async def generate(self, text, **kwargs):
        del text, kwargs
        return ""


def _cfg():
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=False),
    )


def _build(brain=None):
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="failure-reason",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(),
        config=_cfg(),
        bus=None,
        brain=brain,
    )
    sess._session = FakeTransport()
    return sess, jsons


# ---------------------------------------------------------------------------
# _speakable_failure_reason — the realtime adapter over the shared gate
# ---------------------------------------------------------------------------


def test_reason_is_pulled_off_the_error_field() -> None:
    result = {"success": False, "output": None, "error": GMAIL_REASON}
    assert _speakable_failure_reason(result) == GMAIL_REASON


def test_reason_is_pulled_out_of_a_nested_output_field() -> None:
    result = {"success": False, "output": {"error": "The calendar is read-only."}}
    assert _speakable_failure_reason(result) == "The calendar is read-only."


def test_reason_prefers_a_harness_stderr_sentence() -> None:
    result = {
        "success": False,
        "output": {"stderr": "Permission denied", "exit_code": 1},
        "error": "exit 1",
    }
    assert _speakable_failure_reason(result) == "Permission denied"


def test_opaque_tokens_still_yield_no_reason() -> None:
    assert _speakable_failure_reason({"success": False, "error": "exit 1"}) == ""
    assert _speakable_failure_reason({"success": False, "error": "255"}) == ""
    assert _speakable_failure_reason({"success": False, "error": None}) == ""
    assert _speakable_failure_reason(None) == ""


def test_a_long_reason_is_bounded_for_speech() -> None:
    long_reason = (
        "Unknown skill: morning-routine. Installed skills: "
        + ", ".join(f"skill-number-{index}" for index in range(40))
    )
    spoken = _speakable_failure_reason({"success": False, "error": long_reason})
    assert spoken.startswith("Unknown skill: morning-routine.")
    assert len(spoken) <= _FAILURE_REASON_MAX_CHARS + 1  # +1 for the ellipsis


# ---------------------------------------------------------------------------
# The direct-tool fallback — the exact path of the 2026-08-20 transcript
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_tool_speaks_the_reason_not_the_stock_line() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        ("gmail", {"success": False, "output": None, "error": GMAIL_REASON})
    )

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert GMAIL_REASON in text, "the user must hear WHY the mail call failed"
    assert text != action_phrase("action_failed_generic", sess._language)


@pytest.mark.asyncio
async def test_failed_skill_call_names_the_skill_problem() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        (
            "run-skill",
            {
                "success": False,
                "output": None,
                "error": "Skill 'morning-routine' is in DRAFT state and not invocable.",
            },
        )
    )

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert "DRAFT" in text


@pytest.mark.asyncio
async def test_a_tool_without_any_reason_keeps_the_honest_generic_line() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(
        ("run-shell", {"success": False, "output": None, "error": "exit 1"})
    )

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert text == action_phrase("action_failed_generic", sess._language)
    assert "exit" not in text.lower()


@pytest.mark.asyncio
async def test_a_successful_tool_is_unaffected() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(("gmail", {"success": True, "output": {}}))

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text == action_phrase("cu_done", sess._language)


# ---------------------------------------------------------------------------
# Delegated turns — the internal cause is spoken, the jargon is not
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_brain_chain_says_why_instead_of_the_stock_line(monkeypatch):
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.05)
    sess, _jsons = _build(DeadChainBrain())
    sess._turn_id = "t1"
    state = _DelegateTurnState(
        deterministic=True,
        user_text="lies mir meine mails vor",  # i18n-allow: voice fixture
        provider_boundary_seen=True,
    )
    state.input_boundary_ready.set()
    state.provider_ready.set()
    sess._delegate_turns["t1"] = state

    await sess._run_deterministic_delegate("t1", state)

    spoken = str(state.last_reply or "")
    assert spoken == action_phrase("delegate_no_brain", sess._language)
    assert spoken != action_phrase("action_failed_generic", sess._language)
    # The engineering string stays on the wire for the model, out of speech.
    assert "Tool Model" not in spoken
    assert (
        state.result_payload["error"]
        == "No configured Tool Model completed the delegated turn."
    )


@pytest.mark.asyncio
async def test_every_delegate_cause_has_a_situation_and_a_phrase() -> None:
    """No cause may fall through to a KeyError or an empty spoken line."""
    for key, situation in session_module._DELEGATE_FAILURE_SITUATIONS.items():
        assert situation.strip(), f"{key} has no composer situation"
        for language in ("de", "en", "es"):
            assert action_phrase(key, language).strip(), (
                f"{key} has no spoken phrase in {language}"
            )
