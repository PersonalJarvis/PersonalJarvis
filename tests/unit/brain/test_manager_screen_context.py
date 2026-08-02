"""Production BrainManager wiring for explicit one-shot Screen Context."""
from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainDelta, BrainRequest, ImageBlock
from jarvis.screen_context.turn import TurnScreenContext


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True

    def __init__(self, *, vision: bool) -> None:
        self.supports_vision = vision

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _RecordingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tool_overrides: list[dict[str, Any] | None] = []

    async def dispatch(
        self,
        user_text: str,
        *,
        images=(),
        turn_context: str = "",
        **_kwargs: Any,
    ) -> StreamingAggregate:
        self.calls.append(
            {"user_text": user_text, "images": images, "turn_context": turn_context}
        )
        result = StreamingAggregate()
        result.text = "grounded answer"
        result.finish_reason = "stop"
        return result


def _manager(
    *, chain: list[tuple[str, str]] | None = None
) -> tuple[BrainManager, _RecordingDispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    recorder = _RecordingDispatcher()
    manager._build_fallback_chain = lambda _level: (  # type: ignore[method-assign]
        chain or [("fake", "model")]
    )
    manager._get_brain = lambda _name, _model: _FakeBrain(vision=True)  # type: ignore[method-assign]
    def _dispatcher(_brain, *, tools_override=None, **_kwargs):
        recorder.tool_overrides.append(tools_override)
        return recorder

    manager._build_dispatcher = _dispatcher  # type: ignore[method-assign]
    return manager, recorder


def _patch_screen(
    monkeypatch: pytest.MonkeyPatch, result: TurnScreenContext
) -> None:
    async def _fake(*_args: Any, **_kwargs: Any) -> TurnScreenContext:
        return result

    monkeypatch.setattr(
        "jarvis.screen_context.turn.screen_context_for_turn", _fake
    )


@pytest.mark.asyncio
async def test_captured_screen_reaches_the_production_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(
            status="captured",
            image=b"jpeg",
            mime="image/jpeg",
            note=(
                "SECURITY BOUNDARY: untrusted visual evidence.\n"
                "<SCREEN_EVIDENCE>Screen capture of monitor 2.</SCREEN_EVIDENCE>"
            ),
            source_hash="abc",
        ),
    )
    manager, recorder = _manager()

    result = await manager.generate("look at this", use_history=False)

    assert result == "grounded answer"
    call = recorder.calls[0]
    assert base64.b64decode(call["images"][0].data_b64) == b"jpeg"
    assert call["images"][0].source_hash == "abc"
    assert "Screen capture of monitor 2." in call["turn_context"]
    assert "untrusted visual evidence" in call["turn_context"]
    assert recorder.tool_overrides == [{}]


@pytest.mark.asyncio
async def test_ambiguous_request_is_confirmed_on_the_same_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake(_text: str, **kwargs: Any) -> TurnScreenContext:
        calls.append(kwargs)
        if kwargs.get("force"):
            return TurnScreenContext(
                status="captured", image=b"jpeg", mime="image/jpeg"
            )
        return TurnScreenContext(status="clarify", question="Should I look?")

    monkeypatch.setattr(
        "jarvis.screen_context.turn.screen_context_for_turn", _fake
    )
    manager, recorder = _manager()

    first = await manager.generate(
        "what is that?", use_history=False, source_layer="ui.chat"
    )
    second = await manager.generate(
        "yes", use_history=False, source_layer="ui.chat"
    )

    assert first == "Should I look?"
    assert second == "grounded answer"
    assert recorder.calls and calls[-1]["force"] is True


@pytest.mark.asyncio
async def test_screen_consent_is_isolated_by_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake(text: str, **kwargs: Any) -> TurnScreenContext:
        calls.append((text, kwargs))
        if kwargs.get("force"):
            return TurnScreenContext(
                status="captured", image=b"jpeg", mime="image/jpeg"
            )
        if text == "what is that?":
            return TurnScreenContext(status="clarify", question="Should I look?")
        return TurnScreenContext(status="none")

    monkeypatch.setattr(
        "jarvis.screen_context.turn.screen_context_for_turn", _fake
    )
    manager, recorder = _manager()

    await manager.generate(
        "what is that?",
        use_history=False,
        source_layer="ui.web.ws",
        conversation_id="thread-a",
    )
    other_reply = await manager.generate(
        "yes",
        use_history=False,
        source_layer="ui.web.ws",
        conversation_id="thread-b",
    )
    owner_reply = await manager.generate(
        "yes",
        use_history=False,
        source_layer="ui.web.ws",
        conversation_id="thread-a",
    )

    assert other_reply == "grounded answer"
    assert owner_reply == "grounded answer"
    assert [kwargs.get("force", False) for _text, kwargs in calls] == [
        False,
        False,
        True,
    ]
    assert len(recorder.calls) == 2


@pytest.mark.asyncio
async def test_web_screen_confirmation_does_not_hold_voice_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="clarify", question="Should I look?"),
    )
    manager, _recorder = _manager()

    await manager.generate(
        "what is that?",
        use_history=False,
        source_layer="ui.web.ws",
        conversation_id="thread-a",
    )

    assert manager.has_pending_voice_confirm() is False


@pytest.mark.asyncio
async def test_screen_capture_receives_the_turn_trace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []

    async def _fake(_text: str, **kwargs: Any) -> TurnScreenContext:
        seen.append(kwargs.get("trace_id"))
        return TurnScreenContext(status="none")

    monkeypatch.setattr(
        "jarvis.screen_context.turn.screen_context_for_turn", _fake
    )
    manager, _recorder = _manager()
    trace_id = uuid4()

    await manager.generate("hello", trace_id=trace_id, use_history=False)

    assert seen == [trace_id]


@pytest.mark.asyncio
async def test_screen_veto_ends_without_capture_or_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="clarify", question="Should I look?"),
    )
    manager, recorder = _manager()

    await manager.generate("what is that?", use_history=False, source_layer="ui.chat")
    reply = await manager.generate("no", use_history=False, source_layer="ui.chat")

    assert "won't look" in reply
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_stop_vetoes_pending_screen_consent_before_global_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _screen(text: str, **_kwargs: Any) -> TurnScreenContext:
        if text == "was ist das?":  # i18n-allow: German input fixture
            return TurnScreenContext(
                status="clarify",
                question="Soll ich schauen?",  # i18n-allow: German output fixture
            )
        return TurnScreenContext(status="none")

    monkeypatch.setattr(
        "jarvis.screen_context.turn.screen_context_for_turn", _screen
    )
    manager, recorder = _manager()
    cancel_calls: list[bool] = []
    manager._cancel_all_background_tasks = lambda: cancel_calls.append(True) or []  # type: ignore[method-assign]

    await manager.generate(
        "was ist das?",  # i18n-allow: German input fixture
        use_history=False,
        source_layer="ui.chat",
    )
    reply = await manager.generate(
        "stopp", use_history=False, source_layer="ui.chat"
    )
    after = await manager.generate(
        "ja", use_history=False, source_layer="ui.chat"
    )

    assert reply
    assert cancel_calls == []
    assert manager._pending_screen_confirms == {}
    assert after == "grounded answer"
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_privacy_refusal_blocks_every_other_screen_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="refused", message="Privacy rule blocked it."),
    )
    manager, recorder = _manager()

    reply = await manager.generate("look at this", use_history=False)

    assert reply == "Privacy rule blocked it."
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_blind_provider_is_skipped_for_a_screen_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="captured", image=b"jpeg", mime="image/jpeg"),
    )
    manager, recorder = _manager(chain=[("blind", "a"), ("seeing", "b")])
    created: list[str] = []

    def _brain(name: str, _model: str) -> _FakeBrain:
        created.append(name)
        return _FakeBrain(vision=name == "seeing")

    manager._get_brain = _brain  # type: ignore[method-assign]

    result = await manager.generate("take a screenshot", use_history=False)

    assert result == "grounded answer"
    assert created == ["blind", "seeing"]
    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_no_vision_provider_returns_an_honest_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="captured", image=b"jpeg", mime="image/jpeg"),
    )
    manager, recorder = _manager()
    manager._get_brain = lambda _name, _model: _FakeBrain(vision=False)  # type: ignore[method-assign]

    reply = await manager.generate("take a screenshot", use_history=False)

    assert "inspect images" in reply
    assert recorder.calls == []


class _ScriptedDispatcher:
    """Dispatcher returning a different aggregate per attempt."""

    def __init__(self, *results: StreamingAggregate) -> None:
        self._results = list(results)
        self.calls: list[dict[str, Any]] = []
        self.tool_overrides: list[dict[str, Any] | None] = []

    async def dispatch(self, user_text: str, **kwargs: Any) -> StreamingAggregate:
        self.calls.append({"user_text": user_text, **kwargs})
        return self._results[min(len(self.calls) - 1, len(self._results) - 1)]


def _scripted_manager(
    *results: StreamingAggregate, chain: list[tuple[str, str]]
) -> tuple[BrainManager, _ScriptedDispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={})
    dispatcher = _ScriptedDispatcher(*results)
    manager._build_fallback_chain = lambda _level: chain  # type: ignore[method-assign]
    manager._get_brain = lambda _name, _model: _FakeBrain(vision=True)  # type: ignore[method-assign]

    def _build(_brain: Any, *, tools_override: Any = None, **_kwargs: Any) -> Any:
        dispatcher.tool_overrides.append(tools_override)
        return dispatcher

    manager._build_dispatcher = _build  # type: ignore[method-assign]
    return manager, dispatcher


@pytest.mark.asyncio
async def test_toolless_turn_falls_through_when_the_model_answers_with_a_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A requested tool call cannot excuse empty text on a turn with NO tools.

    Live 2026-08-02 09:58 (voice session fa9d4279): the screen was captured
    correctly, the one vision-capable provider replied with 1170 tokens of
    reasoning, zero text and ``finish_reason="tool_calls"`` — on a turn where
    Screen Context had stripped every tool, so nothing could have executed. The
    empty-response guard read the requested call as a legitimate silence, no
    other provider was tried, and the user heard the generic failure phrase
    while a fresh screenshot sat unused.
    """
    _patch_screen(
        monkeypatch,
        TurnScreenContext(status="captured", image=b"jpeg", mime="image/jpeg"),
    )
    silent = StreamingAggregate()
    silent.text = ""
    silent.tool_calls = [{"id": "1", "name": "computer_use", "input": {}}]
    silent.finish_reason = "tool_calls"
    grounded = StreamingAggregate()
    grounded.text = "Your editor shows a failing test."
    grounded.finish_reason = "stop"
    manager, dispatcher = _scripted_manager(
        silent, grounded, chain=[("first", "a"), ("second", "b")]
    )

    reply = await manager.generate("what is on my screen?", use_history=False)

    assert reply == "Your editor shows a failing test."
    assert len(dispatcher.calls) == 2, "the second vision provider must be tried"
    assert dispatcher.tool_overrides == [{}, {}]


@pytest.mark.asyncio
async def test_executed_tool_call_still_excuses_empty_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tools-present path is untouched — a side effect is never re-run.

    Fire-and-forget tools (``spawn_worker``) legitimately end a turn with empty
    text. Falling through to another provider there would run the side effect a
    second time, which is the regression the 2026-04-29 fix was written for.
    """
    _patch_screen(monkeypatch, TurnScreenContext(status="none"))
    silent = StreamingAggregate()
    silent.text = ""
    silent.tool_calls = [{"id": "1", "name": "spawn_worker", "input": {}}]
    silent.executed_tool_names = {"spawn_worker"}
    silent.finish_reason = "suppress_response"
    second = StreamingAggregate()
    second.text = "should never be reached"
    second.finish_reason = "stop"
    manager, dispatcher = _scripted_manager(
        silent, second, chain=[("first", "a"), ("second", "b")]
    )

    await manager.generate("research this in the background", use_history=False)

    assert len(dispatcher.calls) == 1, "an executed side effect must not re-run"


@pytest.mark.asyncio
async def test_dropped_image_never_uses_the_screen_capture_failure_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_screen(monkeypatch, TurnScreenContext(status="none"))
    manager, recorder = _manager()
    manager._get_brain = lambda _name, _model: _FakeBrain(vision=False)  # type: ignore[method-assign]
    trace_id = uuid4()
    manager.inject_images_for_turn(
        trace_id,
        (ImageBlock(mime="image/png", data_b64="ZmFrZQ=="),),
    )

    reply = await manager.generate(
        "describe this attachment", trace_id=trace_id, use_history=False
    )

    assert "captured the screen" not in reply.lower()
    assert recorder.calls == []
