"""Chat-only subscriptions provide data; only Jarvis executes browser actions."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.browser_model import GrokBrowserModel, browser_model_for
from jarvis.core.protocols import BrainMessage, BrainRequest


class Pipe:
    def __init__(self, events=()):
        self.lines = [(json.dumps(event) + "\n").encode() for event in events]

    async def readline(self):
        return self.lines.pop(0) if self.lines else b""

    async def read(self, size):
        return b""


class Process:
    pid = 123
    returncode = None

    def __init__(self, events):
        self.stdout = Pipe(events)
        self.stderr = Pipe()
        self.killed = False

    async def wait(self):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


@pytest.fixture
def cli(monkeypatch):
    from jarvis import grok_build_auth
    from jarvis.agent_chat import browser_model, runner_cli

    state = SimpleNamespace(
        events=[], calls=[], closed=0, selected_home=Path("/selected-account").resolve()
    )
    monkeypatch.setattr(runner_cli, "grok_argv_prefix", lambda: ["grok-test"])
    monkeypatch.setattr(
        runner_cli,
        "_account_env",
        lambda _: {
            "PATH": "test-path",
            "XAI_API_KEY": "must-not-reach-child",
            "JARVIS_CONTROL_API_KEY": "must-not-reach-child",
            "GROK_HOME": "/selected-account",
        },
    )
    monkeypatch.setattr(
        grok_build_auth, "grok_build_login_in", lambda _: (True, "subscription", None)
    )

    class Tree:
        def assign(self, pid):
            assert pid == 123

        def close(self):
            state.closed += 1

    monkeypatch.setattr(browser_model, "make_process_tree", lambda _: Tree())

    async def spawn(*argv, **kwargs):
        prompt = Path(argv[argv.index("--prompt-file") + 1])
        text = await asyncio.to_thread(prompt.read_text, encoding="utf-8")
        state.calls.append((argv, kwargs, prompt, text))
        state.proc = Process(
            [
                {
                    "type": "system",
                    "subtype": "init",
                    "mcp_servers": [],
                    "permissionMode": "dontAsk",
                },
                *state.events,
            ]
        )
        return state.proc

    monkeypatch.setattr(browser_model.asyncio, "create_subprocess_exec", spawn)
    return state


async def test_inference_uses_isolated_subscription_and_denies_all_tools(cli):
    cli.events = [
        {
            "type": "assistant",
            "message": {
                "id": "one",
                "role": "assistant",
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": '{"ok": true}',
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    ]
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="DOM " + "x" * 50000),),
        system="Return JSON",
        tools=(),
    )
    cli.events[-1]["usage"].update(cache_read_input_tokens=5, cache_creation_input_tokens=2)
    deltas = [d async for d in GrokBrowserModel("selected-model").complete(req)]
    assert "".join(d.content or "" for d in deltas) == '{"ok": true}'
    args, kwargs, path, text = cli.calls[0]
    assert args[args.index("--deny") + 1] == "*"
    assert args[args.index("--tools") + 1] == "read_file"
    assert args[args.index("--disallowed-tools") + 1] == "read_file,web_search,x_search"
    assert args[args.index("--permission-mode") + 1] == "dontAsk"
    assert "--always-approve" not in args
    assert "--disable-web-search" in args and "--no-subagents" in args
    assert args[args.index("--model") + 1] == "selected-model"
    assert max(map(len, args)) < 5000 and len(text) > 50000
    assert "XAI_API_KEY" not in kwargs["env"] and "JARVIS_CONTROL_API_KEY" not in kwargs["env"]
    assert kwargs["env"]["GROK_HOME"].endswith(".grok")
    assert kwargs["env"]["HOME"] == kwargs["env"]["USERPROFILE"] == str(path.parent)
    assert kwargs["env"]["GROK_AUTH_PATH"] == str(cli.selected_home / "auth.json")
    assert not path.exists()
    assert cli.closed == 1
    assert deltas[-1].usage["output_tokens"] == 4
    assert deltas[-1].usage["input_tokens"] == 5
    assert deltas[-1].usage["cache_hit_tokens"] == 5


async def test_model_tool_attempt_fails_closed_and_reaps_its_process(cli):
    cli.events = [
        {
            "type": "assistant",
            "message": {
                "id": "bad",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "bad-tool",
                        "name": "write",
                        "input": {"path": "marker"},
                    }
                ],
            },
        }
    ]
    with pytest.raises(RuntimeError, match="forbidden tool"):
        _ = [d async for d in GrokBrowserModel().complete(BrainRequest(messages=()))]
    assert cli.proc.killed
    assert cli.closed == 1
    assert not cli.calls[0][2].exists()


async def test_exhausted_subscription_is_actionable_without_provider_body(cli):
    cli.events = [
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "errors": ["402 balance exhausted; private provider body"],
            "result": "",
        }
    ]
    with pytest.raises(RuntimeError, match="usage is exhausted") as caught:
        _ = [d async for d in GrokBrowserModel().complete(BrainRequest(messages=()))]
    assert "private provider body" not in str(caught.value)


def test_chat_only_transport_does_not_change_api_provider_selection(monkeypatch):
    from jarvis.brain import resolver

    selected = object()
    monkeypatch.setattr(resolver, "resolve_browser_brain", lambda *args, **kwargs: selected)
    assert browser_model_for(object(), "openai", "model") is selected

    def missing(*args, **kwargs):
        raise KeyError("missing")

    monkeypatch.setattr(resolver, "resolve_browser_brain", missing)
    result = browser_model_for(object(), "grok-build", "grok-model")
    assert result.supports_vision is False
    with pytest.raises(KeyError):
        browser_model_for(object(), "not-a-provider", "")


async def test_inherited_integrations_are_rejected_before_using_model_text(cli):
    cli.events = [
        {
            "type": "system",
            "subtype": "init",
            "permissionMode": "dontAsk",
            "mcp_servers": [{"name": "unexpected", "status": "connected"}],
        }
    ]
    with pytest.raises(RuntimeError, match="isolate"):
        _ = [d async for d in GrokBrowserModel().complete(BrainRequest(messages=()))]
    assert cli.proc.killed


async def test_api_key_login_cannot_silently_replace_subscription(cli, monkeypatch):
    monkeypatch.setattr(
        "jarvis.grok_build_auth.grok_build_login_in", lambda _: (True, "api_key", None)
    )
    with pytest.raises(RuntimeError, match="subscription"):
        _ = [d async for d in GrokBrowserModel().complete(BrainRequest(messages=()))]
    assert not cli.calls


async def test_cancelled_inference_reaps_process_and_removes_prompt(cli, monkeypatch):
    waiting = asyncio.Event()
    original = Pipe.readline

    async def pause_after_init(pipe):
        if pipe.lines:
            return await original(pipe)
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(Pipe, "readline", pause_after_init)

    async def consume():
        return [d async for d in GrokBrowserModel().complete(BrainRequest(messages=()))]

    job = asyncio.create_task(consume())
    await asyncio.wait_for(waiting.wait(), 3)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert cli.proc.killed and cli.closed == 1
    assert not cli.calls[0][2].exists()


def test_dual_cli_seat_never_falls_through_to_its_api_billed_twin(monkeypatch):
    from jarvis.brain import resolver

    calls = []

    class NativeBrain:
        def __init__(self, model="", structured_prompts=False):
            self.model = model

    class Registry:
        def available(self):
            return ["claude-cli", "claude-api"]

        def get_class(self, name):
            assert name == "claude-cli"
            return NativeBrain

        def instantiate(self, name, **kwargs):
            calls.append(name)
            return NativeBrain(**kwargs)

    monkeypatch.setattr(resolver, "_get_registry", lambda: Registry())
    config = SimpleNamespace(brain=SimpleNamespace(providers={}))
    result = resolver.resolve_browser_brain(config, "claude-api", "selected", runner="claude-cli")
    assert result.model == "selected"
    assert calls == ["claude-cli"]


def test_inherited_browser_selection_keeps_the_agents_tier_model(monkeypatch):
    from jarvis.agent_chat import browser_model

    seen = []
    monkeypatch.setattr(
        "jarvis.local_models.assistant_session.agents_tier",
        lambda _: SimpleNamespace(provider="openai", model="selected-tier-model"),
    )
    monkeypatch.setattr(
        browser_model,
        "browser_model_for",
        lambda config, provider, model: seen.append((provider, model)),
    )
    agent = SimpleNamespace(provider="", model="")
    browser_model.browser_model_for_agent(object(), agent)
    assert seen == [("openai", "selected-tier-model")]
    assert agent.provider == agent.model == ""
