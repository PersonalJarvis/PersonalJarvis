"""app-command tool: enum/schema validation, in-process ASGI execution,
server-response readback (echo-verify), and honest degradation."""
from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException

from jarvis.plugins.tool.app_command import AppCommandTool, _validate_args


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

    return app


def _tool(calls: dict | None = None) -> AppCommandTool:
    app = _fake_app(calls if calls is not None else {})
    return AppCommandTool(
        transport=httpx.ASGITransport(app=app),
        control_key_resolver=lambda: None,
    )


async def test_unknown_command_id_rejected_nothing_sent() -> None:
    calls: dict = {}
    result = await _tool(calls).execute({"command_id": "wipe-disk"}, None)
    assert result.success is False
    assert "Unknown command id" in (result.error or "")
    assert "brain-switch" in (result.error or "")  # teaches valid ids
    assert calls == {}


async def test_enum_violation_rejected_nothing_sent() -> None:
    """The exact 'switches to something completely different' failure mode:
    a provider outside the catalog must be rejected BEFORE any request."""
    calls: dict = {}
    result = await _tool(calls).execute(
        {"command_id": "brain-switch", "args": {"provider": "skynet"}}, None
    )
    assert result.success is False
    assert "must be one of" in (result.error or "")
    assert calls == {}


async def test_missing_required_and_unknown_args_rejected() -> None:
    calls: dict = {}
    tool = _tool(calls)
    result = await tool.execute({"command_id": "brain-switch", "args": {}}, None)
    assert result.success is False
    assert "missing required argument" in (result.error or "")

    result = await tool.execute(
        {"command_id": "wake-word-get", "args": {"nonsense": 1}}, None
    )
    assert result.success is False
    assert "unknown argument" in (result.error or "")
    assert calls == {}


async def test_post_executes_and_readback_uses_server_response() -> None:
    calls: dict = {}
    result = await _tool(calls).execute(
        {"command_id": "brain-switch", "args": {"provider": "openrouter"}}, None
    )
    assert result.success is True
    assert calls["brain_switch"]["provider"] == "openrouter"
    # Echo-verify: the summary names the transition the SERVER reported.
    assert "openai -> openrouter" in result.output["summary"]
    assert result.output["response"]["active"] == "openrouter"


async def test_path_param_substitution() -> None:
    calls: dict = {}
    result = await _tool(calls).execute(
        {"command_id": "task-cancel", "args": {"task_id": "t-42"}}, None
    )
    assert result.success is True
    assert calls["cancel_task"] == "t-42"


async def test_get_command_works() -> None:
    result = await _tool().execute({"command_id": "wake-word-get"}, None)
    assert result.success is True
    assert result.output["response"]["phrase"] == "jarvis"


async def test_http_error_surfaces_detail() -> None:
    result = await _tool().execute(
        {"command_id": "tts-switch", "args": {"provider": "elevenlabs"}}, None
    )
    assert result.success is False
    assert "HTTP 409" in (result.error or "")
    assert "no key stored" in (result.error or "")


async def test_no_server_available_is_honest() -> None:
    tool = AppCommandTool(
        app_resolver=lambda: None, control_key_resolver=lambda: None
    )
    result = await tool.execute(
        {"command_id": "brain-switch", "args": {"provider": "openrouter"}}, None
    )
    assert result.success is False
    assert "not available" in (result.error or "")


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


async def test_dangerous_command_escalates_to_ask_tier() -> None:
    tool = _tool()
    assert tool.risk_tier == "monitor"
    assert tool.risk_tier_for_args({"command_id": "app-restart"}) == "ask"
    assert tool.risk_tier_for_args({"command_id": "mission-cancel"}) == "ask"
    assert tool.risk_tier_for_args({"command_id": "brain-switch"}) == "monitor"
    assert tool.risk_tier_for_args({"command_id": "nope"}) == "monitor"


async def test_end_to_end_against_real_webserver_app() -> None:
    """The full chain: registry command -> ASGI -> the REAL /api/brain/switch
    route (shared app_control validation) -> readback from the route's
    response. Uses a fake live brain on app.state."""
    from jarvis.core import config as cfg_mod
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
    server = WebServer(cfg, bus=EventBus())
    server.app.state.brain = _FakeBrain()
    server.app.state.cfg = cfg

    real_get_secret = cfg_mod.get_secret
    cfg_mod.get_secret = lambda key, env_fallback=None: (
        "sk-test" if key == "openrouter_api_key" else None
    )
    try:
        tool = AppCommandTool(
            transport=httpx.ASGITransport(app=server.app),
            control_key_resolver=lambda: None,
        )
        result = await tool.execute(
            {"command_id": "brain-switch",
             "args": {"provider": "openrouter", "persist": False}},
            None,
        )
    finally:
        cfg_mod.get_secret = real_get_secret

    assert result.success is True, result.error
    assert result.output["response"]["active"] == "openrouter"
    # old_provider comes from cfg.brain.primary (shared app_control logic),
    # so only pin the destination side of the transition here.
    assert "-> openrouter" in result.output["summary"]
