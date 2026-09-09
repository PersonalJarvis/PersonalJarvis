"""RUB-14: internal addressees, accepted voice replies, and duplicate tool calls."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from jarvis.realtime.session import RealtimeVoiceSession
from jarvis.realtime.tools import RealtimeToolBridge
from jarvis.society.message_routing import is_internal_message_request
from tests.unit.realtime.test_tools import FakeExecutor, FakeTool, _bridge


@pytest.mark.parametrize(
    "text",
    [
        "Ja, kannst du bitte den Gmail Agenten eine Nachricht schreiben? "  # i18n-allow
        "Eine Testnachricht.",  # i18n-allow  # i18n-allow: German input regression
        "Send the Gmail agent a test message.",
        "Escribe un mensaje al Gmail agente.",
    ],
)
def test_internal_addressee_is_recognized(text):
    assert is_internal_message_request(text, ["Gmail agent"])


@pytest.mark.parametrize(
    "text",
    [
        "Send an email to Alice using Gmail.",
        "Read the Gmail messages.",
        "Which agents do you have?",
    ],
)
def test_external_email_and_read_requests_are_not_rewritten(text):
    assert not is_internal_message_request(text, ["Gmail agent"])


async def test_original_request_cannot_start_gmail_approval(monkeypatch):
    from jarvis.society import lead_card

    monkeypatch.setattr(lead_card, "society_agent_names", lambda: ("Gmail agent",))
    executor = FakeExecutor(confirmation_required=True)
    bridge = RealtimeToolBridge(
        tools={"gmail": FakeTool("gmail")}, executor=executor, language="de"
    )
    await bridge.handle_user_transcript(
        "Kannst du dem Gmail Agenten eine Testnachricht schreiben?"  # i18n-allow
    )  # i18n-allow: German input regression
    _, result = await bridge.execute(wire_name="gmail", arguments={"app_name": "mail"})
    assert result["blocked"] and "message_agent" in result["error"]
    assert executor.execute_calls == []
    assert not bridge.has_pending_confirmation


async def test_concurrent_retries_of_a_confirmed_action_execute_once():
    bridge, _, executor = _bridge(confirmation_required=True)
    await bridge.handle_user_transcript("Open Calculator")
    args = {"app_name": "Calculator"}
    await bridge.execute(wire_name="open_app", arguments=args)
    await bridge.handle_user_transcript("Yes")
    results = await asyncio.gather(
        *(bridge.execute(wire_name="open_app", arguments=args) for _ in range(4))
    )
    await bridge.handle_user_transcript("Yes")
    duplicate = await bridge.execute(wire_name="open_app", arguments=args)
    assert all(result == duplicate for result in results)
    assert len(executor.execute_calls) == len(executor.confirmed_calls) == 1


async def test_new_request_after_confirmation_can_ask_again():
    bridge, _, executor = _bridge(confirmation_required=True)
    await bridge.handle_user_transcript("Open Calculator")
    await bridge.execute(wire_name="open_app", arguments={"app_name": "Calculator"})
    await bridge.handle_user_transcript("Yes")
    await bridge.execute(wire_name="open_app", arguments={"app_name": "Calculator"})
    await bridge.handle_user_transcript("Yes, now open Firefox instead")
    _, result = await bridge.execute(wire_name="open_app", arguments={"app_name": "Firefox"})
    assert result["confirmation_required"]
    assert len(executor.execute_calls) == 2


@pytest.mark.parametrize(
    "language,text",
    [
        ("de", "Ja, sende sie."),
        ("en", "Yes, send it."),
        ("es", "Sí, envíalo."),
        ("de", "Nein, nicht senden."),  # i18n-allow  # i18n-allow: German cancellation input
    ],
)
def test_acoustically_grounded_answer_is_not_discarded_as_text_echo(language, text):
    session = object.__new__(RealtimeVoiceSession)
    now = time.monotonic()
    session._tool_bridge = SimpleNamespace(has_pending_confirmation=True)
    session._language = language
    session._echo_playback_horizon = now - 3
    session._last_voiced_input_monotonic = now - 1
    assert session._grounded_confirmation(text)
    # Audio from during playback is not affirmative user evidence.
    session._last_voiced_input_monotonic = now - 4
    assert not session._grounded_confirmation(text)
    session._last_voiced_input_monotonic = 0
    assert not session._grounded_confirmation(text)
