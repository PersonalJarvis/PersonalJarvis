"""A blocked answer is retried WITH its evidence, never from memory alone.

Live forensic 2026-08-24 10:57 (session 95b85f31). The user asked what was on
their calendar. ``google_calendar`` ran, succeeded in 1.2 s and returned real
events. The model began a grounded reply, the output-language gate blocked it
at the speech boundary, and the retry that followed said: "use the function
result that is already present in this conversation" — to a provider whose
generation had just been superseded, while "do not call any function" closed
every other route to the facts. What the user heard was:

    "I tried to fetch your recent Vercel deployments, but it looks like
    there's an issue with the Vercel integration."

No Vercel tool was ever called that turn; ``vercel`` was simply a neighbour in
the declared tool set. The same recovery drifted on 2 of 3 blocked turns that
morning (10:25:21 answered a calendar + mail briefing with a report about
background missions).

The rule these tests pin: whenever Jarvis still holds the turn's evidence, it
travels WITH the retry request. A prompt may not point at provider state it
cannot guarantee.
"""

import asyncio

import pytest

from jarvis.realtime.session import (
    RealtimeVoiceSession,
    _direct_tool_result_retry_prompt,
    _output_language_retry_prompt,
)

from .test_session import FakeProvider, FakeSession, _cfg


def _events_payload() -> dict:
    """One realistic ``google_calendar`` success, shaped like the live one."""
    return {
        "success": True,
        "output": {
            "events": [
                {
                    "id": "evt-1",
                    "summary": "Busy",
                    "start": "2026-08-24T05:15:00Z",
                    "end": "2026-08-24T06:00:00Z",
                }
            ]
        },
        "error": None,
    }


async def _started_session(session_id: str) -> tuple[RealtimeVoiceSession, FakeSession]:
    """A live session whose transport takes prompted retries via send_text."""

    class _PromptedSession(FakeSession):
        supports_prompted_response_retry = True

        async def receive(self):
            # Hold the pump open; these tests drive the recovery directly.
            while True:  # pragma: no cover - cancelled at end()
                await asyncio.sleep(0.05)
                yield  # type: ignore[misc]

    class _PromptedProvider(FakeProvider):
        async def open_session(self, cfg):
            self.opened_with = cfg
            self.session = _PromptedSession([])
            return self.session

    provider = _PromptedProvider([])
    sess = RealtimeVoiceSession(
        session_id=session_id,
        send_binary=lambda data: asyncio.sleep(0),
        send_json=lambda message: asyncio.sleep(0),
        provider=provider,
        config=_cfg(reply_language="en"),
        bus=None,
    )
    await sess.handle_control({"type": "audio_start", "sample_rate": 16_000})
    for _ in range(200):
        if sess._session is not None:  # noqa: SLF001
            break
        await asyncio.sleep(0.01)
    assert sess._session is not None  # noqa: SLF001
    return sess, provider.session


@pytest.mark.asyncio
async def test_language_retry_carries_the_turns_real_tool_result():
    """The Vercel case. The retry must SHOW the calendar data, not point at it.

    Without the evidence in the request, the model answers a question about a
    calendar with an invented failure of an unrelated integration.
    """
    sess, transport = await _started_session("grounded-retry")
    try:
        sess._last_user_text = (  # noqa: SLF001
            "can you please look now in my calendar and tell me if I have to "
            "do anything or I have to watch out for anything?"
        )
        sess._executed_tool_names.add("google_calendar")  # noqa: SLF001
        sess._direct_tool_results.append(  # noqa: SLF001
            ("google_calendar", _events_payload())
        )
        sess._turn_id = "turn-1"  # noqa: SLF001

        await sess._request_output_language_retry()  # noqa: SLF001

        assert len(transport.text_inputs) == 1
        prompt = transport.text_inputs[0]
        # The evidence itself travelled — the tool that ran and what it said.
        assert "google_calendar" in prompt
        assert "Busy" in prompt
        # And the question it belongs to, so the answer cannot drift to
        # another turn's topic.
        assert "calendar" in prompt.lower()
        # The closed-world rule that forbids substituting a neighbour tool.
        assert "Name no service" in prompt
        # The retry still pins the resolved output language.
        assert "English" in prompt
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_retry_is_refused_when_no_tool_result_survived():
    """Tools ran but nothing was retained: do not ask for an ungrounded answer.

    "Answer from the function result" with no result in reach is precisely the
    prompt that invents one. The turn takes its deterministic local line and
    the session books a language failure instead of a retry.
    """
    sess, transport = await _started_session("ungrounded-retry")
    try:
        sess._executed_tool_names.add("google_calendar")  # noqa: SLF001
        sess._direct_tool_results.clear()  # noqa: SLF001
        sess._last_user_text = ""  # noqa: SLF001
        sess._turn_id = "turn-1"  # noqa: SLF001

        await sess._request_output_language_retry()  # noqa: SLF001

        assert transport.text_inputs == []
        assert sess._output_language_failures == 1  # noqa: SLF001
    finally:
        await sess.end(reason="test")


@pytest.mark.asyncio
async def test_language_retry_without_tools_hands_back_the_blocked_wording():
    """A tool-less turn gets its own blocked answer back, word for word.

    "Repeat the same answer" is unanswerable once the response is retired and
    the transcript cleared; the words themselves make it a translation job.
    """
    sess, transport = await _started_session("verbatim-retry")
    try:
        sess._last_user_text = "What is kindness?"  # noqa: SLF001
        sess._output_language_blocked_reply = (  # noqa: SLF001
            "Freundlichkeit ist der achtsame Umgang mit anderen."
        )
        sess._turn_id = "turn-1"  # noqa: SLF001

        await sess._request_output_language_retry()  # noqa: SLF001

        assert len(transport.text_inputs) == 1
        prompt = transport.text_inputs[0]
        assert "Freundlichkeit ist der achtsame Umgang mit anderen." in prompt
        assert "What is kindness?" in prompt
        assert "English" in prompt
    finally:
        await sess.end(reason="test")


def test_blocked_reply_is_captured_before_the_transcript_is_cleared():
    """The prompt builder refuses to invent when it has nothing to repeat."""
    prompt = _output_language_retry_prompt(
        language="en",
        user_text="What is on my calendar?",
        blocked_reply="",
    )
    assert "Its wording was not kept" in prompt
    assert "Introduce no service" in prompt
    assert "What is on my calendar?" in prompt


def test_grounded_prompt_states_the_results_are_complete():
    """The block is presented as the whole truth of the turn, not a hint."""
    grounded = _direct_tool_result_retry_prompt(
        language="en",
        grounding="RESULTS OF THIS TURN (complete):\n- google_calendar returned: {}",
    )
    assert "COMPLETE record" in grounded
    assert "google_calendar" in grounded
    # An empty grounding leaves the historical prompt untouched, so transports
    # that genuinely retain the result are unaffected.
    bare = _direct_tool_result_retry_prompt(language="en")
    assert "COMPLETE record" not in bare
    assert "already present in this conversation" in bare
