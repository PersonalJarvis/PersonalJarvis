"""One spoken request chopped in two by the provider stays ONE order.

Live 2026-08-12 16:09 (gemini-live): the provider's VAD read a thinking pause
as end-of-turn, so "…the skill system doesn't work properly. It doesn't
really — you know, recognize the skills" arrived as TWO final turns. The
first dispatched its deterministic delegate; the 5-word tail then planned as
an orchestrator turn of its own (the word "skills" is planner evidence),
opened a SECOND executor, and pane T4 was briefed with the same deep-dive
twice, three seconds apart. ``_order_already_executing`` never fired: that
guard covers a turn that asked for NOTHING of its own, and the tail carried
a planner reason.

Pinned here: ``_continues_executing_order`` refuses the second executor for
a short fragment whose evidence the running order already covers, while every
shape that IS a new order — a command verb, new evidence, an addressed pane,
sheer length, or no running order at all — keeps its dispatch. The live
transcripts are used verbatim against the real planner.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from jarvis.realtime.session import (
    _DELEGATE_DECLARATION,
    RealtimeVoiceSession,
    _DelegateTurnState,
)

RATE = 24_000

# The live 2026-08-12 16:09 transcripts, verbatim (session
# 5f6eb2eb-2966-49ce-bc64-b85273c25266, turns 0 and 1).
LIVE_ORDER = (
    "Can you please prompt Gemini Pro that he looks at our skills and makes "
    "a deep dive and how we can improve the skills drastically? I have the "
    "feeling that the skill system doesn't work properly. It never uses the "
    "skills I've built. It doesn't really"
)
LIVE_FRAGMENT = "You know, recognize the skills."


class _IdleWire:
    """Wire shape for tests that never start the receive pump."""

    session_id = "split-utterance-wire"
    supports_tool_updates = True
    creates_responses_automatically = True
    isolates_response_generations = True

    def __init__(self) -> None:
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []

    async def send_tool_result(self, call_id: str, name: str, payload: dict[str, Any]) -> None:
        self.tool_results.append((call_id, name, payload))

    async def send_json(self, *_args: Any) -> None:
        return None


class _IdleProvider:
    name = "split-utterance"
    supports_realtime = True
    supports_direct_tools = True
    input_sample_rate = RATE
    output_sample_rate = RATE

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, _config: Any) -> _IdleWire:
        return _IdleWire()


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        brain=SimpleNamespace(reply_language="en", providers={}),
        stt=SimpleNamespace(language="auto"),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="delegate"),
        latency=SimpleNamespace(enabled=False),
    )


def _session(messages: list[dict[str, Any]] | None = None) -> RealtimeVoiceSession:
    sink = messages if messages is not None else []
    session = RealtimeVoiceSession(
        session_id="split-utterance-single-order",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: sink.append(message) or asyncio.sleep(0),
        providers=[_IdleProvider()],
        config=_config(),
        bus=None,
        surface="desktop",
        half_duplex=True,
        browser_sample_rate=RATE,
        # No ``plan_turn`` attribute: ``_plan_turn`` falls back to the REAL
        # shared planner, so these tests exercise the production vocabulary
        # on the live transcripts instead of a scripted plan.
        brain=SimpleNamespace(conversation_language="en"),
    )
    session._language = "en"
    return session


def _pending_order(
    session: RealtimeVoiceSession, text: str, turn_id: str = "turn-a"
) -> asyncio.Task[None]:
    """Model an earlier turn's delegate that is still executing."""
    state = _DelegateTurnState(deterministic=True, user_text=text)
    session._delegate_turns[turn_id] = state
    task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
    session._delegate_tasks_by_turn[turn_id] = {task}
    return task


def _guard(session: RealtimeVoiceSession, fragment: str) -> bool:
    session._turn_id = "turn-b"
    session._last_user_text = fragment
    return session._continues_executing_order(session._plan_turn(fragment))


async def test_the_live_split_utterance_opens_no_second_executor() -> None:
    session = _session()
    task = _pending_order(session, LIVE_ORDER)
    try:
        assert _guard(session, LIVE_FRAGMENT), (
            "the live tail fragment must be refused as a continuation — "
            "dispatching it briefed pane T4 twice on 2026-08-12"
        )
    finally:
        task.cancel()


async def test_a_command_verb_follow_up_keeps_its_dispatch() -> None:
    # The running order ALSO plans as a plain action, so only the
    # self-standing-order probe (a command verb of its own) protects the
    # follow-up here — the reason-subset probe alone would refuse it.
    session = _session()
    task = _pending_order(session, "Please open the garage and switch the heating to eco mode")
    try:
        assert not _guard(session, "And turn on the lights."), (
            "a short follow-up carrying its own command verb is a second "
            "order, never a continuation"
        )
    finally:
        task.cancel()


async def test_new_evidence_keeps_its_dispatch() -> None:
    session = _session()
    task = _pending_order(session, LIVE_ORDER)
    try:
        assert not _guard(session, "What is on my calendar today?"), (
            "a question bringing evidence the running order never carried "
            "(calendar/current) is a new request and keeps its dispatch"
        )
    finally:
        task.cancel()


async def test_an_addressed_pane_keeps_its_dispatch(monkeypatch: Any) -> None:
    session = _session()
    monkeypatch.setattr(
        RealtimeVoiceSession,
        "_workspace_call_signs",
        staticmethod(lambda: ("Dana",)),
    )
    task = _pending_order(session, LIVE_ORDER)
    try:
        assert not _guard(session, "Dana should look at the skills too."), (
            "naming an open pane is as strong as order evidence gets — the "
            "workspace turn must dispatch even while another order runs"
        )
    finally:
        task.cancel()


async def test_a_long_same_topic_follow_up_keeps_its_dispatch() -> None:
    session = _session()
    task = _pending_order(session, LIVE_ORDER)
    long_follow_up = (
        "You know I want you to really recognize every single skill I have built for this system"
    )
    try:
        assert not _guard(session, long_follow_up), (
            "a follow-up past the fragment length bound carries new content "
            "by sheer length and keeps its dispatch"
        )
    finally:
        task.cancel()


async def test_a_completed_order_never_captures_the_next_turn() -> None:
    # The clarify-answer and voice-confirm flows depend on this: once the
    # earlier delegate has its result, a short reply belongs to THOSE paths,
    # not to the continuation refusal.
    session = _session()
    task = _pending_order(session, LIVE_ORDER)
    session._delegate_turns["turn-a"].result_complete = True
    try:
        assert not _guard(session, LIVE_FRAGMENT)
    finally:
        task.cancel()

    done: asyncio.Task[None] = asyncio.get_running_loop().create_task(asyncio.sleep(0))
    await done
    session._delegate_turns["turn-a"].result_complete = False
    session._delegate_tasks_by_turn["turn-a"] = {done}
    assert not _guard(session, LIVE_FRAGMENT)


async def test_no_running_order_means_no_refusal() -> None:
    session = _session()
    assert not _guard(session, LIVE_FRAGMENT)


async def test_a_clarify_answer_dispatches_while_an_unrelated_order_runs() -> None:
    # A bare "Yes." plans with EMPTY reasons, and an empty set is a subset
    # of every running order's reasons — without the clarify/confirm bypass
    # the vacuous subset would swallow the answer (not delay it: drop it)
    # whenever any unrelated order is still in flight.
    session = _session()
    task = _pending_order(session, "Please check my email for anything urgent")
    session._delegate_reply_awaits_answer = True
    try:
        assert not _guard(session, "Yes."), (
            "a short answer to an open clarify question belongs to the "
            "clarify flow — an unrelated executing order must never refuse it"
        )
    finally:
        task.cancel()


async def test_a_voice_confirmation_dispatches_while_an_unrelated_order_runs() -> None:
    session = _session()
    session._brain = SimpleNamespace(
        conversation_language="en",
        has_pending_voice_confirm=lambda: True,
    )
    task = _pending_order(session, "Please check my email for anything urgent")
    try:
        assert not _guard(session, "Yes, do it."), (
            "a pending ask-tier confirmation owns the yes — refusing it "
            "would silently drop a confirmed action"
        )
    finally:
        task.cancel()


async def test_an_empty_reason_fragment_is_never_refused_on_vacuous_evidence() -> None:
    # Defense in depth past the clarify/confirm bypass: refusal must require
    # the fragment to carry at least one planner reason of its own.
    session = _session()
    task = _pending_order(session, LIVE_ORDER)
    try:
        assert not _guard(session, "Nossa."), (
            "an evidence-free fragment must not be refused on the vacuous "
            "empty-subset — it never planned as an order in the first place"
        )
    finally:
        task.cancel()


async def test_the_provider_tool_call_path_refuses_the_fragment_too() -> None:
    """The same fragment arriving as a provider ``jarvis_action`` call.

    It plans as requiring the orchestrator, so ``_order_already_executing``
    (built for a turn that asked nothing of its own) lets it through — the
    continuation guard must cover this door as well.
    """
    messages: list[dict[str, Any]] = []
    session = _session(messages)
    wire = _IdleWire()
    session._session = wire
    session._delegate_enabled = True
    task = _pending_order(session, LIVE_ORDER)
    session._turn_id = "turn-b"
    session._last_user_text = LIVE_FRAGMENT
    event = SimpleNamespace(
        call_id="call-1",
        tool_name=str(_DELEGATE_DECLARATION["name"]),
        tool_args={"request": LIVE_FRAGMENT},
    )
    try:
        await session._handle_tool_call(event)
    finally:
        task.cancel()

    assert len(wire.tool_results) == 1
    _call_id, _name, payload = wire.tool_results[0]
    assert payload["success"] is False
    assert "Do not start it again" in payload["error"]
    assert "turn-b" not in session._delegate_tasks_by_turn, (
        "the refused call must not have started a second executor"
    )
    assert any(m.get("type") for m in messages), (
        "the refusal must not leave a silent turn — the deterministic progress line answers it"
    )
