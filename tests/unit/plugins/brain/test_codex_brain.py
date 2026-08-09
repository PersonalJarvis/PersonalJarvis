"""CodexBrain: structured-prompt mode + API-key -> ChatGPT-CLI crossover.

Live 2026-07-18: the maintainer pays for the ChatGPT subscription, yet every
wiki extraction died on the SEPARATE, throttled OpenAI API key (RateLimitError
HTTP 429) — the subscription CLI was never tried because the API key existed.
And even when the CLI ran, the conversational prompt wrapper ("answer in one
to three short sentences, plain text only") made the wiki's JSON contract
unfulfillable by instruction. These tests pin both fixes.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import codex as codex_module
from jarvis.plugins.brain.codex import CodexBrain, _build_cli_command


def _wiki_request() -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content="Source content here."),),
        system="Return ONLY a single JSON array. No prose before or after.",
        max_tokens=900,
        temperature=0.2,
        stream=True,
    )


def test_structured_mode_forwards_the_json_contract_verbatim() -> None:
    brain = CodexBrain(structured_prompts=True)
    prompt = brain._render_prompt(_wiki_request())
    assert "Return ONLY a single JSON array" in prompt
    assert "Source content here." in prompt
    assert "one to three short sentences" not in prompt


def test_voice_mode_keeps_the_conversational_flattening() -> None:
    brain = CodexBrain()
    prompt = brain._render_prompt(_wiki_request())
    assert "one to three short sentences" in prompt
    # The heavy system contract stays out of conversational CLI turns.
    assert "Return ONLY a single JSON array" not in prompt


class _StatusError(RuntimeError):
    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status_code = status


async def _collect(stream: AsyncIterator[BrainDelta]) -> str:
    chunks: list[str] = []
    async for delta in stream:
        if delta.content:
            chunks.append(delta.content)
    return "".join(chunks)


def _arm_api_and_oauth(
    monkeypatch: pytest.MonkeyPatch,
    brain: CodexBrain,
    *,
    status: int,
) -> list[str]:
    """API path raises ``status``; OAuth is connected; CLI yields 'cli-answer'."""
    monkeypatch.setattr(CodexBrain, "_api_key", lambda self: "sk-test")
    monkeypatch.setattr(CodexBrain, "_ensure_client", lambda self, key: object())
    monkeypatch.setattr(codex_module, "_codex_oauth_connected", lambda: True)

    async def _failing_stream(client: Any, model: str, req: BrainRequest):
        raise _StatusError(status)
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(codex_module, "stream_complete", _failing_stream)

    calls: list[str] = []

    async def _fake_cli(self: CodexBrain, req: BrainRequest):
        calls.append("cli")
        yield BrainDelta(content="cli-answer")
        yield BrainDelta(finish_reason="stop")

    async def _fake_app_server(self: CodexBrain, req: BrainRequest):
        calls.append("app-server")
        yield BrainDelta(content="app-server-answer")
        yield BrainDelta(finish_reason="stop")

    monkeypatch.setattr(CodexBrain, "_complete_via_cli", _fake_cli)
    monkeypatch.setattr(CodexBrain, "_complete_via_app_server", _fake_app_server)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 403, 429])
async def test_throttled_api_key_crosses_over_to_the_subscription_cli(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=status)

    answer = await _collect(brain.complete(_wiki_request()))

    assert answer == "cli-answer"
    assert calls == ["cli"]


@pytest.mark.asyncio
async def test_explicit_subscription_choice_bypasses_a_stored_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = CodexBrain(prefer_subscription=True)
    calls = _arm_api_and_oauth(monkeypatch, brain, status=500)

    answer = await _collect(brain.complete(_wiki_request()))

    assert answer == "app-server-answer"
    assert calls == ["app-server"]


class _TextSubscription:
    def __init__(self, notifications: list[object]) -> None:
        self._notifications = list(notifications)
        self.closed = False
        self.waiting = asyncio.Event()

    async def get(self, timeout_s: float | None = None) -> object:
        del timeout_s
        if self._notifications:
            return self._notifications.pop(0)
        self.waiting.set()
        await asyncio.Future()
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.closed = True


class _TextClient:
    def __init__(self, notifications: list[object]) -> None:
        self.subscription = _TextSubscription(notifications)
        self.interrupts: list[tuple[str, str]] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def text_thread_start(self) -> dict[str, object]:
        return {"thread": {"id": "thread-voice"}}

    def subscribe(self, thread_id: str) -> _TextSubscription:
        assert thread_id == "thread-voice"
        return self.subscription

    async def turn_start(self, thread_id: str, prompt: str) -> dict[str, object]:
        assert thread_id == "thread-voice"
        assert prompt
        return {"turn": {"id": "turn-voice"}}

    async def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, object]:
        self.interrupts.append((thread_id, turn_id))
        return {}

    async def thread_unsubscribe(self, thread_id: str) -> dict[str, object]:
        self.unsubscribed.append(thread_id)
        return {}

    async def close(self) -> None:
        self.closed = True


def _notification(method: str, **params: object) -> object:
    return SimpleNamespace(method=method, params=params)


@pytest.mark.asyncio
async def test_app_server_subscription_streams_deltas_without_repeating_final(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _TextClient(
        [
            _notification(
                "item/agentMessage/delta",
                threadId="thread-voice",
                turnId="turn-voice",
                delta="Hello ",
            ),
            _notification(
                "item/agentMessage/delta",
                threadId="thread-voice",
                turnId="turn-voice",
                delta="there.",
            ),
            _notification(
                "item/completed",
                threadId="thread-voice",
                turnId="turn-voice",
                item={"type": "agentMessage", "text": "Hello there."},
            ),
            _notification(
                "turn/completed",
                threadId="thread-voice",
                turn={"id": "turn-voice", "status": "completed"},
            ),
        ]
    )
    monkeypatch.setattr(
        "jarvis.codex_app_server.CodexAppServerClient",
        lambda *_a, **_k: client,
    )
    brain = CodexBrain(prefer_subscription=True)

    answer = await _collect(brain._complete_via_app_server(_wiki_request()))

    assert answer == "Hello there."
    assert client.unsubscribed == ["thread-voice"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_app_server_subscription_interrupts_exact_turn_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _TextClient(
        [
            _notification(
                "item/agentMessage/delta",
                threadId="thread-voice",
                turnId="turn-voice",
                delta="Starting",
            )
        ]
    )
    monkeypatch.setattr(
        "jarvis.codex_app_server.get_shared_codex_app_server",
        lambda *_a, **_k: client,
    )
    stream = CodexBrain(
        prefer_subscription=True,
        persistent_subscription_transport=True,
    )._complete_via_app_server(_wiki_request())
    assert (await stream.__anext__()).content == "Starting"
    pending = asyncio.create_task(stream.__anext__())
    await asyncio.wait_for(client.subscription.waiting.wait(), timeout=1.0)

    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert client.interrupts == [("thread-voice", "turn-voice")]
    assert client.unsubscribed == ["thread-voice"]
    assert client.closed is False


def test_subscription_cli_command_carries_the_selected_model() -> None:
    argv = _build_cli_command("codex", "gpt-5.6-sol")

    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"


def test_subscription_cli_command_omits_an_unpinned_model() -> None:
    assert "--model" not in _build_cli_command("codex", "")


def test_forced_subscription_reports_only_its_actual_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CodexBrain, "_api_key", lambda self: "sk-test")

    brain = CodexBrain(prefer_subscription=True)

    assert brain.supports_vision is False
    assert brain.can_call_tools() is False


@pytest.mark.asyncio
async def test_forced_subscription_rejects_tool_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = CodexBrain(prefer_subscription=True)
    calls = _arm_api_and_oauth(monkeypatch, brain, status=500)
    request = BrainRequest(
        messages=(BrainMessage(role="user", content="Open the calculator."),),
        tools=({"name": "open_app"},),
    )

    with pytest.raises(RuntimeError, match="cannot execute brain tools"):
        await _collect(brain.complete(request))
    assert calls == []


@pytest.mark.asyncio
async def test_partial_tool_call_stream_never_crosses_over(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream that already emitted a tool_call delta must re-raise on a
    later 429 — crossing over would append CLI prose behind a half-consumed
    tool turn (double yield into the aggregator)."""
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=429)

    async def _tool_then_429(client: Any, model: str, req: BrainRequest):
        yield BrainDelta(tool_call={"name": "open_app", "arguments": "{}"})
        raise _StatusError(429)

    monkeypatch.setattr(codex_module, "stream_complete", _tool_then_429)

    with pytest.raises(_StatusError):
        await _collect(brain.complete(_wiki_request()))
    assert calls == []


@pytest.mark.asyncio
async def test_tool_turns_surface_the_error_instead_of_going_tool_blind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With tools requested, the tool-blind CLI must not answer in prose that
    looks like 'model chose no tool' — the error surfaces so BrainManager's
    fallback can pick a genuinely tool-capable provider."""
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=429)
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="Open the calculator."),),
        tools=({"name": "open_app"},),
    )

    with pytest.raises(_StatusError):
        await _collect(brain.complete(req))
    assert calls == []


@pytest.mark.asyncio
async def test_non_account_errors_still_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=500)

    with pytest.raises(_StatusError):
        await _collect(brain.complete(_wiki_request()))
    assert calls == []  # a server error is not an account problem — no crossover


@pytest.mark.asyncio
async def test_no_oauth_means_the_account_error_is_surfaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brain = CodexBrain()
    calls = _arm_api_and_oauth(monkeypatch, brain, status=429)
    monkeypatch.setattr(codex_module, "_codex_oauth_connected", lambda: False)

    with pytest.raises(_StatusError):
        await _collect(brain.complete(_wiki_request()))
    assert calls == []


def test_cli_timeout_defaults_to_the_voice_tier_cap() -> None:
    assert CodexBrain()._cli_timeout_s == codex_module._CLI_TIMEOUT_S


def test_cli_timeout_accepts_a_caller_budget() -> None:
    """Slow background callers (wiki Stage-2 judge) extend the internal cap.

    Live 2026-07-21: the fixed 90 s cap killed every consolidator run while
    the wiki tier's 180 s budget was half unused — 'Chain failure: codex
    RuntimeError' with a growing journal backlog.
    """
    assert CodexBrain(cli_timeout_s=180.0)._cli_timeout_s == 180.0
    # Garbage/zero budgets fall back to the voice-tier default.
    assert CodexBrain(cli_timeout_s=0)._cli_timeout_s == codex_module._CLI_TIMEOUT_S
    assert CodexBrain(cli_timeout_s="nope")._cli_timeout_s == codex_module._CLI_TIMEOUT_S


# ---------------------------------------------------------------------------
# The npm launcher's own interpreter lookup (live 2026-08-06 17:42: the shim
# resolved, the spawn returned rc=1 "node is not recognized", and the failure
# surfaced upstairs as "returned no answer").
# ---------------------------------------------------------------------------


def test_the_child_gets_a_path_the_npm_launcher_can_find_node_on(monkeypatch) -> None:
    import jarvis.core.path_augment as path_augment

    monkeypatch.setattr(path_augment, "resolve_node_executable", lambda: "/opt/node/bin/node")
    env = {"PATH": "/usr/local/npm-global"}
    codex_module._ensure_node_reachable(env, "/usr/local/npm-global/codex")

    assert "/opt/node/bin" in env["PATH"]
    # The existing entries survive: this repairs a PATH, never replaces one.
    assert "/usr/local/npm-global" in env["PATH"]


def test_a_path_that_already_has_node_is_left_alone(monkeypatch) -> None:
    import jarvis.core.path_augment as path_augment

    monkeypatch.setattr(path_augment, "resolve_node_executable", lambda: "/opt/node/bin/node")
    env = {"PATH": "/opt/node/bin"}
    codex_module._ensure_node_reachable(env, "/usr/local/npm-global/codex")

    assert env["PATH"] == "/opt/node/bin"


def test_a_launcher_that_cannot_run_without_node_fails_by_name(monkeypatch) -> None:
    """ "Returned no answer" hid the real cause; the error must name it."""
    import jarvis.core.path_augment as path_augment

    monkeypatch.setattr(path_augment, "resolve_node_executable", lambda: None)
    with pytest.raises(RuntimeError, match="Node.js was not found"):
        codex_module._ensure_node_reachable({"PATH": ""}, r"C:\npm\codex.cmd")


def test_a_native_binary_never_fails_for_a_missing_interpreter(monkeypatch) -> None:
    """Guessing wrong here would break a working install: only warn."""
    import jarvis.core.path_augment as path_augment

    monkeypatch.setattr(path_augment, "resolve_node_executable", lambda: None)
    codex_module._ensure_node_reachable({"PATH": ""}, "/usr/local/bin/codex")
