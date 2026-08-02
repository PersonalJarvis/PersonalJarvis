"""Registry command tools: virtual-loader expansion, flat per-command schema
validation, in-process ASGI execution, server-response readback (echo-verify),
and honest degradation.

The flat-tools design is load-bearing (forensic 2026-07-11): the earlier
umbrella tool (one `app-command` with nested command_id+args) failed live —
the router LLM called `provider-test` AS a tool name and got "not in the
router tool set". Each registry command is therefore its own tool.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
from fastapi import FastAPI, HTTPException

from jarvis.commands.registry import get_registry
from jarvis.plugins.tool.app_command import (
    AppCommandTool,
    _validate_args,
    _with_defaults,
)


def _fake_app(calls: dict) -> FastAPI:
    app = FastAPI()

    @app.post("/api/brain/switch")
    async def brain_switch(body: dict) -> dict:
        calls["brain_switch"] = body
        return {
            "ok": True, "active": body["provider"], "old_provider": "openai",
            "persisted": True, "requires_restart": False,
        }

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str) -> dict:
        calls["cancel_task"] = task_id
        return {"ok": True}

    @app.get("/api/settings/wake-word")
    async def get_wake_word() -> dict:
        return {"phrase": "jarvis", "enabled": True}

    @app.post("/api/tts/switch")
    async def tts_switch(body: dict) -> dict:
        raise HTTPException(status_code=409, detail="no key stored")

    @app.post("/api/agentic-ide/terminals/{name}/prompt")
    async def terminal_prompt(name: str, body: dict) -> dict:
        calls["terminal_prompt"] = {"name": name, **body}
        return {"ok": True, "terminal": name, "submitted": True}

    return app


def _tools(calls: dict | None = None) -> dict[str, object]:
    app = _fake_app(calls if calls is not None else {})
    loader = AppCommandTool(
        transport=httpx.ASGITransport(app=app),
        control_key_resolver=lambda: None,
    )
    return {t.name: t for t in loader.expand()}


def test_loader_expands_one_flat_tool_per_registry_command() -> None:
    loader = AppCommandTool(control_key_resolver=lambda: None)
    assert loader.is_virtual_loader is True
    tools = {t.name: t for t in loader.expand()}
    assert set(tools) == {c.id for c in get_registry()}
    # Flat schemas: the tool schema IS the command's params schema — no
    # nested command_id/args indirection for the LLM to fumble.
    brain = tools["brain-switch"]
    assert "provider" in brain.schema["properties"]
    assert "command_id" not in brain.schema["properties"]


def test_dangerous_commands_carry_ask_tier() -> None:
    tools = _tools()
    assert tools["app-restart"].risk_tier == "ask"
    assert tools["mission-cancel"].risk_tier == "ask"
    assert tools["task-cancel"].risk_tier == "ask"
    assert tools["brain-switch"].risk_tier == "monitor"
    assert tools["wake-word-get"].risk_tier == "monitor"


async def test_enum_violation_rejected_nothing_sent() -> None:
    """The exact 'switches to something completely different' failure mode:
    a provider outside the catalog must be rejected BEFORE any request."""
    calls: dict = {}
    result = await _tools(calls)["brain-switch"].execute({"provider": "skynet"}, None)
    assert result.success is False
    assert "must be one of" in (result.error or "")
    assert calls == {}


async def test_missing_required_and_unknown_args_rejected() -> None:
    calls: dict = {}
    tools = _tools(calls)
    result = await tools["brain-switch"].execute({}, None)
    assert result.success is False
    assert "missing required argument" in (result.error or "")

    result = await tools["wake-word-get"].execute({"nonsense": 1}, None)
    assert result.success is False
    assert "unknown argument" in (result.error or "")
    assert calls == {}


async def test_post_executes_and_readback_uses_server_response() -> None:
    calls: dict = {}
    result = await _tools(calls)["brain-switch"].execute(
        {"provider": "openrouter"}, None
    )
    assert result.success is True
    assert calls["brain_switch"]["provider"] == "openrouter"
    # Echo-verify: the summary names the transition the SERVER reported.
    assert "openai -> openrouter" in result.output["summary"]
    assert result.output["response"]["active"] == "openrouter"


async def test_path_param_substitution() -> None:
    calls: dict = {}
    result = await _tools(calls)["task-cancel"].execute({"task_id": "t-42"}, None)
    assert result.success is True
    assert calls["cancel_task"] == "t-42"


async def test_get_command_works() -> None:
    result = await _tools()["wake-word-get"].execute({}, None)
    assert result.success is True
    assert result.output["response"]["phrase"] == "jarvis"


async def test_http_error_surfaces_detail() -> None:
    result = await _tools()["tts-switch"].execute({"provider": "elevenlabs"}, None)
    assert result.success is False
    assert "HTTP 409" in (result.error or "")
    assert "no key stored" in (result.error or "")


async def test_no_server_available_is_honest() -> None:
    loader = AppCommandTool(
        app_resolver=lambda: None, control_key_resolver=lambda: None
    )
    tools = {t.name: t for t in loader.expand()}
    result = await tools["brain-switch"].execute({"provider": "openrouter"}, None)
    assert result.success is False
    assert "not available" in (result.error or "")


async def test_schema_default_is_sent_when_the_model_leaves_it_out() -> None:
    """The live 2026-07-27 18:47 failure, pinned.

    ``agentic-ide-prompt`` declares ``compose: true``; the endpoint behind it
    defaults to False because its other caller is the typed prompt bar, which
    must send a hand-written prompt untouched. A model that reads the default
    and omits the argument therefore got the OPPOSITE of what the schema
    promised: its one-line paraphrase was typed into the coding agent as the
    whole assignment, instead of being composed into a briefed task with this
    workspace's files attached.
    """
    calls: dict = {}
    result = await _tools(calls)["agentic-ide-prompt"].execute(
        {"name": "Casey", "prompt": "have a look at the macOS paths"}, None
    )
    assert result.success is True
    assert calls["terminal_prompt"]["compose"] is True


async def test_an_explicit_argument_still_beats_the_default() -> None:
    """A default is a fallback, never an override — "continue" stays verbatim."""
    calls: dict = {}
    await _tools(calls)["agentic-ide-prompt"].execute(
        {"name": "Casey", "prompt": "continue", "compose": False}, None
    )
    assert calls["terminal_prompt"]["compose"] is False


async def test_direct_realtime_tool_uses_the_terminal_visible_in_chat(
    monkeypatch,
) -> None:
    """A stale model argument cannot overrule the exact deictic utterance."""
    from jarvis.agentic_ide import session as session_mod

    t1 = SimpleNamespace(name="T1")
    t4 = SimpleNamespace(name="T4")
    session = SimpleNamespace(
        terminals=[t1, t4],
        contextual_terminal=lambda: t4,
    )
    monkeypatch.setattr(
        session_mod,
        "get_registry",
        lambda: SimpleNamespace(session=session),
    )
    calls: dict = {}
    context = SimpleNamespace(
        user_utterance=(
            "Kannst du bitte das Terminal prompten "  # i18n-allow: spoken input
            "hier und den Subscription-Pfad prüfen?"  # i18n-allow: spoken input
        )
    )

    result = await _tools(calls)["agentic-ide-prompt"].execute(
        {"name": "T1", "prompt": "Review the subscription path"}, context
    )

    assert result.success is True
    assert calls["terminal_prompt"]["name"] == "T4"


async def test_direct_realtime_tool_keeps_an_explicit_terminal(
    monkeypatch,
) -> None:
    from jarvis.agentic_ide import session as session_mod

    t1 = SimpleNamespace(name="T1")
    t4 = SimpleNamespace(name="T4")
    session = SimpleNamespace(
        terminals=[t1, t4],
        contextual_terminal=lambda: t4,
    )
    monkeypatch.setattr(
        session_mod,
        "get_registry",
        lambda: SimpleNamespace(session=session),
    )
    calls: dict = {}
    context = SimpleNamespace(
        user_utterance="Prompt T1, not this terminal, to run the tests"
    )

    await _tools(calls)["agentic-ide-prompt"].execute(
        {"name": "T1", "prompt": "Run the tests"}, context
    )

    assert calls["terminal_prompt"]["name"] == "T1"


def test_every_registry_default_survives_into_the_payload() -> None:
    """Whatever the registry promises the model is what the endpoint receives.

    Generic on purpose: the compose bug was one instance of a class, and the
    next default added to the registry gets the same guarantee without anyone
    having to remember this test exists.
    """
    for cmd in get_registry():
        props = (cmd.params or {}).get("properties", {})
        promised = {
            key: spec["default"]
            for key, spec in props.items()
            if isinstance(spec, dict) and "default" in spec
        }
        if not promised:
            continue
        filled = _with_defaults(cmd.params, {})
        for key, value in promised.items():
            assert filled[key] == value, f"{cmd.id}: default for {key!r} was dropped"


def test_validator_covers_types_and_ranges() -> None:
    schema = {
        "type": "object",
        "properties": {
            "volume": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "phrase": {"type": "string", "minLength": 1, "maxLength": 4},
            "persist": {"type": "boolean"},
        },
        "required": ["volume"],
    }
    assert _validate_args(schema, {"volume": 0.5}) == []
    assert _validate_args(schema, {"volume": 2.0})  # above maximum
    assert _validate_args(schema, {"volume": True})  # bool is not a number
    assert _validate_args(schema, {"volume": 0.1, "phrase": ""})  # too short
    assert _validate_args(schema, {"volume": 0.1, "phrase": "abcde"})  # too long
    assert _validate_args(schema, {"volume": 0.1, "persist": "yes"})  # not bool
    assert _validate_args(schema, {})  # missing required


async def test_end_to_end_against_real_webserver_app(monkeypatch) -> None:
    """The full chain: flat tool -> ASGI -> the REAL /api/brain/switch route
    (shared app_control validation) -> readback from the route's response."""
    from jarvis.core import config as cfg_mod
    from jarvis.core import control_key as control_key_mod
    from jarvis.core.bus import EventBus
    from jarvis.core.config import JarvisConfig
    from jarvis.ui.web.server import WebServer

    class _FakeBrain:
        active_provider = "openai"
        last_persist_ok = False

        def available_providers(self):
            return ["openai", "openrouter"]

        async def switch(self, provider: str, *, persist: bool = False) -> None:
            self.active_provider = provider

    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    test_control_key = "jctl_test_app_command"
    monkeypatch.setattr(
        control_key_mod,
        "get_control_key",
        lambda: test_control_key,
    )
    server = WebServer(cfg, bus=EventBus())
    server.app.state.brain = _FakeBrain()
    server.app.state.cfg = cfg

    real_get_secret = cfg_mod.get_secret
    cfg_mod.get_secret = lambda key, env_fallback=None: (
        "sk-test" if key == "openrouter_api_key" else None
    )
    try:
        loader = AppCommandTool(
            transport=httpx.ASGITransport(app=server.app),
            control_key_resolver=lambda: test_control_key,
        )
        tools = {t.name: t for t in loader.expand()}
        result = await tools["brain-switch"].execute(
            {"provider": "openrouter", "persist": False}, None
        )
    finally:
        cfg_mod.get_secret = real_get_secret

    assert result.success is True, result.error
    assert result.output["response"]["active"] == "openrouter"
    # old_provider comes from cfg.brain.primary (shared app_control logic),
    # so only pin the destination side of the transition here.
    assert "-> openrouter" in result.output["summary"]


# --------------------------------------------------------------------------- #
# Response trimming: what CHANGED, not a snapshot of everything                #
# --------------------------------------------------------------------------- #


def test_a_full_state_snapshot_is_kept_out_of_the_readback() -> None:
    """Mutating routes echo the whole app state back; the model must not read it.

    One Agentic-IDE spawn answers with roughly 25 000 characters of workspace
    state — every pane, its transcript, the project's skills and subagents —
    purely so an open UI can re-render without a second fetch. Three such calls
    in one turn buried the result the model then had to act on: a live session
    on 2026-07-27 spent 58 000 input tokens on a single turn and lost track of
    the pane it had just opened, spawning three more.
    """
    from jarvis.plugins.tool.app_command import _without_snapshots

    trimmed = _without_snapshots(
        {"ok": True, "terminals": [{"name": "Kate"}], "state": {"huge": "x" * 5000}}
    )

    assert trimmed == {"ok": True, "terminals": [{"name": "Kate"}]}


def test_a_response_that_is_only_a_snapshot_is_still_returned() -> None:
    """Trimming must never answer with nothing at all."""
    from jarvis.plugins.tool.app_command import _without_snapshots

    payload = {"ok": True, "state": {"active": True}}

    assert _without_snapshots(payload) == payload
    assert _without_snapshots("plain text") == "plain text"
