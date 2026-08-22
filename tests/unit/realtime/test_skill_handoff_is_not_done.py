"""A ``run-skill`` load is a to-do list, not a deed — never report it as done.

Live forensic 2026-08-22 18:16:21 (session c845a2ce, gemini-live, hybrid tool
mode). The maintainer said "mach mal bitte schnell Musik an, entspannte".
What happened, from ``voice_events`` and ``jarvis_desktop.log``:

* 18:16:21.942 the live model called ``run_skill`` for ``plugin-spotify``; the
  tool succeeded in 19 ms and returned the skill's INSTRUCTIONS ("use the
  connected Spotify ... pass what the user said as query ... say what actually
  started") with the directive "follow these now, using your available tools".
* 18:16:21.985 Gemini Live closed the turn 0.02 s after the tool response —
  no audio, no transcript, no further function call. The ``spotify`` tool was
  never called (it was not even declared natively: dropped under the
  declaration budget, reachable only through ``jarvis_action``).
* 18:16:21.985 the empty-turn recovery found one SUCCESSFUL direct tool
  result, judged the turn finished, and sent the "just say it" prompt: *"the
  function call for the user's current request already finished ... use only
  the function result ... do not call any function and do not repeat the
  action"*.
* 18:16:25 the model did exactly that with a how-to as its "result":
  "Alles klar! Ich habe dir entspannte Musik angemacht." Nothing had played.

Two defects, one symptom, both pinned here:

1. The recovery treated a skill-instruction load as completed work, so its
   continuation prompt ordered a completion report and forbade the very call
   that would have done the work.
2. The deterministic fallback line for such a turn would have been the stock
   completion claim ("Erledigt.") — the same lie, spoken by us instead of the
   model.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.realtime.session import (
    RealtimeVoiceSession,
    _direct_tool_result_retry_prompt,
    _is_skill_handoff_result,
)
from jarvis.voice.action_phrases import action_phrase

SPOTIFY_INSTRUCTIONS = (
    "Use the connected Spotify to play and steer the user's music. Play by "
    "name, not by id: pass what the user said as `query`. Say what actually "
    "started."
)
INLINE_DIRECTIVE = (
    "These are the skill's instructions. Follow these skill instructions now, "
    "step by step, using your available tools."
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
        session_id="skill-handoff",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(),
        config=_cfg(),
        bus=None,
        brain=None,
    )
    sess._session = FakeTransport()
    sess._language = "de"
    return sess, jsons


def _spotify_skill_load():
    """The exact ``run-skill`` receipt of 18:16:21.962."""
    return (
        "run-skill",
        {
            "success": True,
            "output": {
                "skill_name": "plugin-spotify",
                "execution": "inline",
                "directive": INLINE_DIRECTIVE,
                "instructions": SPOTIFY_INSTRUCTIONS,
                "resources": {},
            },
        },
    )


def _spotify_played():
    return (
        "spotify",
        {
            "success": True,
            "spoken_reply": "Playing Weightless by Marconi Union.",
        },
    )


def _spotify_failed():
    return (
        "spotify",
        {
            "success": False,
            "output": None,
            "error": "Spotify is not connected — connect it in the Plugins view.",
        },
    )


# ---------------------------------------------------------------------------
# The classifier: what IS a hand-off
# ---------------------------------------------------------------------------


def test_a_run_skill_instruction_load_is_a_handoff() -> None:
    assert _is_skill_handoff_result(*_spotify_skill_load()) is True


def test_the_live_wire_spelling_is_recognised_too() -> None:
    """Gemini/Vertex call the tool ``run_skill`` / ``run_skill_<digest>``."""
    _name, result = _spotify_skill_load()
    assert _is_skill_handoff_result("run_skill", result) is True
    assert _is_skill_handoff_result("run_skill_982bc264fb", result) is True


def test_a_resource_read_is_a_real_answer_not_a_handoff() -> None:
    """L3: the model asked for a bundled file and got its content."""
    result = {
        "success": True,
        "output": {
            "skill_name": "plugin-spotify",
            "resource": "references/guide.md",
            "resource_content": "# Guide",
        },
    }
    assert _is_skill_handoff_result("run-skill", result) is False


def test_a_failed_skill_load_is_not_a_handoff() -> None:
    result = {"success": False, "output": None, "error": "Unknown skill: x"}
    assert _is_skill_handoff_result("run-skill", result) is False


def test_another_tool_carrying_instructions_is_not_a_handoff() -> None:
    _name, result = _spotify_skill_load()
    assert _is_skill_handoff_result("spotify", result) is False


# ---------------------------------------------------------------------------
# Pending hand-off: loaded and not carried out
# ---------------------------------------------------------------------------


def test_a_load_with_nothing_after_it_is_pending() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())
    assert sess._turn_has_pending_skill_handoff() is True


def test_a_load_followed_by_the_real_call_is_not_pending() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())
    sess._direct_tool_results.append(_spotify_played())
    assert sess._turn_has_pending_skill_handoff() is False


def test_a_load_followed_by_a_failed_call_is_not_pending_but_unfinished() -> None:
    """The later result is what the turn is judged on: failure → finish it."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())
    sess._direct_tool_results.append(_spotify_failed())
    assert sess._turn_has_pending_skill_handoff() is False
    assert sess._turn_has_unfinished_work() is True


def test_an_empty_turn_has_no_pending_handoff() -> None:
    sess, _jsons = _build()
    assert sess._turn_has_pending_skill_handoff() is False


# ---------------------------------------------------------------------------
# Defect 2 — the deterministic line never claims the deed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fallback_line_for_a_pending_load_says_nothing_ran() -> None:
    """18:16 verbatim: one successful run-skill receipt and nothing else."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert text == action_phrase("skill_loaded_not_run", "de")
    # The two lies this turn could have told — never again.
    assert text != action_phrase("cu_done", "de")
    assert "angemacht" not in text  # i18n-allow: quoted live hallucination
    # And the instructions themselves never leak into speech.
    assert "query" not in text.lower()
    assert "Spotify" not in text


@pytest.mark.asyncio
async def test_the_fallback_line_exists_in_every_supported_language() -> None:
    for language in ("de", "en", "es"):
        line = action_phrase("skill_loaded_not_run", language)
        assert line
        assert line != action_phrase("cu_done", language)


@pytest.mark.asyncio
async def test_a_load_followed_by_real_work_reports_the_work_only() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())
    sess._direct_tool_results.append(_spotify_played())

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is True
    assert text == "Playing Weightless by Marconi Union."


@pytest.mark.asyncio
async def test_a_load_followed_by_a_failure_names_the_failure() -> None:
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())
    sess._direct_tool_results.append(_spotify_failed())

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert "Spotify" in text
    assert text != action_phrase("cu_done", "de")


@pytest.mark.asyncio
async def test_a_load_followed_only_by_a_gated_call_is_not_broken() -> None:
    """Loaded, then the follow-up call was gated: nothing ran, nothing broke."""
    sess, _jsons = _build()
    sess._direct_tool_results.append(_spotify_skill_load())
    sess._direct_tool_results.append(
        (
            "spawn_worker",
            {"success": False, "blocked": True, "output": None, "error": "gated"},
        )
    )

    text, succeeded = await sess._direct_tool_fallback_text()

    assert succeeded is False
    assert text == action_phrase("actions_not_requested", "de")


# ---------------------------------------------------------------------------
# Defect 1 — the continuation prompt orders the work and forbids the claim
# ---------------------------------------------------------------------------


def test_the_pending_instructions_prompt_orders_the_work() -> None:
    prompt = _direct_tool_result_retry_prompt(language="de", pending_instructions=True)
    lowered = prompt.lower()

    # The instruction that produced "Ich habe dir Musik angemacht" must be gone.
    assert "already finished" not in lowered
    assert "do not call any function" not in lowered
    # ... and replaced by its opposite.
    assert "not over" in lowered
    assert "carry the instructions out now" in lowered
    # The bridge to tools the live model has no function for.
    assert "jarvis_action" in prompt
    # The claim is forbidden in so many words.
    assert "never say that something was played" in lowered
    assert "instructions themselves are not a result" in lowered


def test_the_pending_instructions_prompt_outranks_the_plain_readback() -> None:
    """Both flags false → plain readback; pending set → the work order."""
    plain = _direct_tool_result_retry_prompt(language="de")
    pending = _direct_tool_result_retry_prompt(language="de", pending_instructions=True)
    assert plain != pending
    assert "do not call any function" in plain.lower()


@pytest.mark.asyncio
async def test_the_empty_turn_recovery_sends_the_work_order_for_a_pending_load() -> None:
    """End to end through ``_recover_empty_provider_turn``: the 18:16 turn."""
    sess, _jsons = _build()
    transport = sess._session
    sess._turn_id = "turn-1816"
    sess._last_user_text = "Mach mal bitte schnell Musik an, entspannte."  # i18n-allow
    sess._direct_tool_results.append(_spotify_skill_load())

    handled = await sess._recover_empty_provider_turn()

    assert handled is True
    assert len(transport.text_inputs) == 1
    sent = transport.text_inputs[0].lower()
    assert "not over" in sent
    assert "already finished" not in sent
    assert "do not call any function" not in sent
    # The retained emergency line is the honest one, never a completion claim.
    state = sess._delegate_turns["turn-1816"]
    assert state.last_reply == action_phrase("skill_loaded_not_run", "de")
    assert state.result_success is False


@pytest.mark.asyncio
async def test_the_empty_turn_recovery_keeps_the_plain_readback_for_real_work() -> None:
    """Regression guard: a turn that DID the work still gets 'just say it'."""
    sess, _jsons = _build()
    transport = sess._session
    sess._turn_id = "turn-ok"
    sess._last_user_text = "Mach Musik an."  # i18n-allow
    sess._direct_tool_results.append(_spotify_skill_load())
    sess._direct_tool_results.append(_spotify_played())

    handled = await sess._recover_empty_provider_turn()

    assert handled is True
    sent = transport.text_inputs[0].lower()
    assert "do not call any function" in sent
    assert "already finished" in sent
    state = sess._delegate_turns["turn-ok"]
    assert state.last_reply == "Playing Weightless by Marconi Union."
    assert state.result_success is True
