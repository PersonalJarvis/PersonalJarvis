"""BUG-089: repeated provider-down / recovery notices cool down.

While the brain chain's rate-limit cooldown is active, EVERY delegate turn
returns the same canned apology; re-speaking it each turn is the self-talk
loop's fuel (each spoken apology can echo back as the next "user" turn).
The session speaks ONE outage notice per window, completes repeat turns
silently, and never writes a suppressed phrase into the audible transcript
record. German fixture strings quote the runtime voice product surface.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import jarvis.realtime.session as session_module
from jarvis.realtime.session import RealtimeVoiceSession, _DelegateTurnState

APOLOGY = (
    "Tut mir leid, mein Sprachmodell ist im Moment nicht erreichbar. "  # i18n-allow: voice fixture
    "Ich versuche es gleich erneut."  # i18n-allow: voice fixture
)


class OutageBrain:
    """Delegate brain whose whole chain is down: apology + all-failed flag."""

    def __init__(self):
        self._last_turn_all_failed = True
        self.calls = 0

    async def generate(self, text, **kwargs):
        del text, kwargs
        self.calls += 1
        return APOLOGY


class FakeTransport:
    creates_responses_automatically = False

    def __init__(self):
        self.text_inputs = []
        self.tool_results = []
        self.interrupts = 0

    async def send_text(self, text):
        self.text_inputs.append(text)

    async def send_tool_result(self, call_id, name, result):
        self.tool_results.append((call_id, name, result))

    async def request_response(self, *, required_tool=None):
        del required_tool

    async def interrupt(self):
        self.interrupts += 1

    async def close(self):
        pass


class FakeProvider:
    name = "fake"
    supports_realtime = True
    input_sample_rate = 16000
    output_sample_rate = 24000

    def __init__(self):
        self.opened_with = None

    async def can_open_duplex_session(self):
        return True

    async def open_session(self, cfg):
        self.opened_with = cfg
        return FakeTransport()


def _cfg():
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="auto", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime"),
        latency=SimpleNamespace(enabled=False),
    )


def _build(brain):
    jsons = []
    sess = RealtimeVoiceSession(
        session_id="cooldown",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=FakeProvider(),
        config=_cfg(),
        bus=None,
        brain=brain,
    )
    sess._session = FakeTransport()
    return sess, jsons


async def _delegate_turn(sess, turn_id):
    """Run one deterministic delegate turn end to end and return its state."""
    sess._turn_id = turn_id
    state = _DelegateTurnState(
        deterministic=True,
        user_text="wie wird das wetter morgen in berlin",  # i18n-allow: voice fixture
        provider_boundary_seen=True,
    )
    state.input_boundary_ready.set()
    state.provider_ready.set()
    sess._delegate_turns[turn_id] = state
    await sess._run_deterministic_delegate(turn_id, state)
    return state


def _spoken_notices(jsons):
    return [m for m in jsons if m.get("type") == "error_spoken"]


@pytest.mark.asyncio
async def test_repeat_provider_down_apology_is_suppressed(monkeypatch):
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.05)
    brain = OutageBrain()
    sess, jsons = _build(brain)
    transport = sess._session

    await _delegate_turn(sess, "t1")
    first_text_inputs = len(transport.text_inputs)
    first_notices = len(_spoken_notices(jsons))
    assert first_text_inputs == 1, "first outage turn must be delivered"

    state2 = await _delegate_turn(sess, "t2")
    assert len(transport.text_inputs) == first_text_inputs, (
        "repeat apology within the window must not be re-delivered"
    )
    assert len(_spoken_notices(jsons)) == first_notices, (
        "repeat apology must not be re-spoken through the surface"
    )
    assert state2.delivery_started is True, (
        "the suppressed turn counts as delivered (no late-result replay)"
    )
    # The suppressed manual-provider turn is closed locally.
    assert any(m.get("type") == "turn_complete" for m in jsons)


@pytest.mark.asyncio
async def test_apology_speaks_again_after_the_window(monkeypatch):
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.05)
    monkeypatch.setattr(session_module, "_OUTAGE_NOTICE_COOLDOWN_S", 0.2)
    brain = OutageBrain()
    sess, _jsons = _build(brain)
    transport = sess._session

    await _delegate_turn(sess, "t1")
    await asyncio.sleep(0.25)
    await _delegate_turn(sess, "t2")
    assert len(transport.text_inputs) == 2, (
        "after the window a fresh outage notice is honest again"
    )


@pytest.mark.asyncio
async def test_healthy_brain_reply_is_never_suppressed(monkeypatch):
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.05)
    brain = OutageBrain()
    brain._last_turn_all_failed = False
    sess, _jsons = _build(brain)
    transport = sess._session

    await _delegate_turn(sess, "t1")
    await _delegate_turn(sess, "t2")
    assert len(transport.text_inputs) == 2


@pytest.mark.asyncio
async def test_pending_native_tool_call_is_always_answered(monkeypatch):
    """Provider protocol wins: a native function call must get its result."""
    monkeypatch.setattr(session_module, "_DELEGATE_READBACK_WAIT_S", 0.05)
    brain = OutageBrain()
    sess, _jsons = _build(brain)
    transport = sess._session

    await _delegate_turn(sess, "t1")

    sess._turn_id = "t2"
    state = _DelegateTurnState(
        deterministic=True,
        user_text="wie wird das wetter",  # i18n-allow: voice fixture
        provider_boundary_seen=True,
    )
    state.pending_tool_calls.append(("call-1", "delegate"))
    state.input_boundary_ready.set()
    state.provider_ready.set()
    sess._delegate_turns["t2"] = state
    await sess._run_deterministic_delegate("t2", state)
    assert transport.tool_results, (
        "a pending native call is answered even inside the cooldown window"
    )


@pytest.mark.asyncio
async def test_empty_turn_recovery_notice_cools_down():
    brain = OutageBrain()
    sess, jsons = _build(brain)
    sess._input_turn_observed = True
    sess._last_user_text = ""

    sess._turn_id = "t1"
    await sess._recover_empty_provider_turn()
    assert len(_spoken_notices(jsons)) == 1
    transcript_after_first = list(sess._output_transcript)

    sess._turn_id = "t2"
    sess._output_transcript.clear()
    await sess._recover_empty_provider_turn()
    assert len(_spoken_notices(jsons)) == 1, (
        "the recovery phrase must not repeat within the window"
    )
    assert sess._output_transcript == [], (
        "a suppressed phrase must not be written into the audible record"
    )
    assert transcript_after_first, "the first notice IS recorded as spoken"
