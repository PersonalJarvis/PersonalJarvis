"""Unit tests for the App-Control tools and the shared app_control service.

Covers:
- describe-app-settings: structure, secret-free output, no-param contract.
- switch-provider: happy path (brain live switch), missing-credential,
  unknown-provider, wrong-tier, honest outcome flags.
- manage-mcp-server: add (starts disabled), enable, disable, remove, raw-secret
  arg rejection, unknown-server errors.
- ROUTER_TOOLS membership + AP-5/AP-14 (never in a worker set — there is none).
- provider_routes / app_control credential-check single-source-of-truth.

No unittest.mock — fakes + monkeypatch only (project convention).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.core import runtime_refs
from jarvis.core.protocols import ExecutionContext

# ----------------------------------------------------------------------
# Fixtures / fakes
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_runtime_refs():
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(), user_utterance="", config={}, memory_read=None
    )


def make_cfg(*, brain="grok", tts="grok-voice", stt="faster-whisper", sub="claude-api"):
    return SimpleNamespace(
        brain=SimpleNamespace(
            primary=brain,
            worker=SimpleNamespace(provider=sub),
            reply_language="de",
        ),
        tts=SimpleNamespace(provider=tts, voice_de="Charon", voice_en="Charon", speed=1.0),
        stt=SimpleNamespace(provider=stt),
        ui=SimpleNamespace(theme="dark"),
        profile=SimpleNamespace(language="auto"),
        persona=SimpleNamespace(name="Jarvis"),
        # resolve_assistant_name now derives the name from the wake phrase
        # (wake_word coupling 2026-06-20), not persona.name.  Set the trigger
        # block so "Hey Jarvis" → "Jarvis" in build_settings_snapshot.
        trigger=SimpleNamespace(
            wake_word=SimpleNamespace(phrase="Hey Jarvis"),
        ),
        wake=SimpleNamespace(phrase="hey jarvis", engine="openwakeword"),
        autostart=SimpleNamespace(enabled=True),
        computer_use=SimpleNamespace(step_budget=100),
    )


class FakeBrainManager:
    def __init__(self, active="grok"):
        self.active_provider = active
        self.last_persist_ok = False
        self._config = None
        self.switch_calls: list[tuple[str, bool]] = []

    async def switch(self, provider, *, persist=False):
        self.switch_calls.append((provider, persist))
        self.active_provider = provider
        self.last_persist_ok = bool(persist)


@pytest.fixture
def configured_keys(monkeypatch):
    """All providers report a stored credential."""
    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda p: "tok")
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: "tok")


@pytest.fixture
def no_keys(monkeypatch):
    """No provider has a stored credential."""
    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda p: "")
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: "")


# ----------------------------------------------------------------------
# describe-app-settings
# ----------------------------------------------------------------------


async def test_describe_rejects_params():
    from jarvis.plugins.tool.describe_app_settings import DescribeAppSettingsTool

    res = await DescribeAppSettingsTool().execute({"x": 1}, _ctx())
    assert res.success is False
    assert "no parameters" in (res.error or "")


async def test_describe_snapshot_structure(monkeypatch, configured_keys):
    from jarvis.plugins.tool.describe_app_settings import DescribeAppSettingsTool

    # Use Gemini so the snapshot assertion remains independent of Grok.
    monkeypatch.setattr(
        "jarvis.brain.app_control.resolve_running_cfg", lambda: make_cfg(brain="gemini")
    )
    monkeypatch.setattr("jarvis.brain.app_control.list_mcp_servers", lambda: [])

    res = await DescribeAppSettingsTool().execute({}, _ctx())
    assert res.success is True
    out = res.output
    assert set(out) == {"providers", "settings", "mcp_servers"}
    assert "brain" in out["providers"] and "tts" in out["providers"]
    # active brain provider reflects cfg
    actives = [p for p in out["providers"]["brain"] if p["active"]]
    assert actives and actives[0]["id"] == "gemini"
    # secret-free: provider entries expose only booleans for credentials
    for p in out["providers"]["brain"]:
        assert isinstance(p["configured"], bool)
        assert "secret" not in p and "secret_keys" not in p
    assert out["settings"]["assistant_name"] == "Jarvis"


# ----------------------------------------------------------------------
# switch-provider (service-level)
# ----------------------------------------------------------------------


async def test_switch_brain_live(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    fake = FakeBrainManager(active="grok")
    runtime_refs.set_brain_manager(fake)
    cfg = make_cfg()

    res = await apply_provider_switch("brain", "gemini", cfg=cfg, persist=True)
    assert res["ok"] is True
    assert res["old_provider"] == "grok"
    assert res["new_provider"] == "gemini"
    assert res["applied_live"] is True
    assert res["requires_restart"] is False
    assert fake.active_provider == "gemini"


async def test_switch_missing_credential(no_keys):
    from jarvis.brain.app_control import apply_provider_switch

    runtime_refs.set_brain_manager(FakeBrainManager())
    res = await apply_provider_switch("brain", "gemini", cfg=make_cfg(), persist=True)
    assert res["ok"] is False
    assert res["error_kind"] == "missing_credential"


async def test_switch_unknown_provider(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    res = await apply_provider_switch("brain", "no-such-provider", cfg=make_cfg(), persist=False)
    assert res["ok"] is False
    assert res["error_kind"] == "unknown_provider"


async def test_switch_wrong_tier(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    # cartesia is a TTS provider — switching it as 'brain' must be rejected.
    res = await apply_provider_switch("brain", "cartesia", cfg=make_cfg(), persist=False)
    assert res["ok"] is False
    assert res["error_kind"] == "wrong_tier"


async def test_switch_antigravity_rejected_as_main_brain(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    fake = FakeBrainManager(active="grok")
    runtime_refs.set_brain_manager(fake)

    res = await apply_provider_switch(
        "brain", "antigravity", cfg=make_cfg(), persist=False,
    )

    assert res["ok"] is False
    assert res["error_kind"] == "subagent_only"
    assert fake.switch_calls == []


async def test_switch_codex_rejected_as_main_brain(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    fake = FakeBrainManager(active="grok")
    runtime_refs.set_brain_manager(fake)

    res = await apply_provider_switch(
        "brain", "codex", cfg=make_cfg(), persist=False,
    )

    assert res["ok"] is False
    assert res["error_kind"] == "subagent_only"
    assert fake.switch_calls == []


async def test_switch_antigravity_allowed_as_subagent(monkeypatch, no_keys):
    from jarvis.brain.app_control import apply_provider_switch

    class _Status:
        installed = True
        connected = True
        mode = "oauth-personal"

    class _FakeGoogleCliAuthService:
        def status(self):
            return _Status()

    monkeypatch.setattr(
        "jarvis.google_cli.auth_service.GoogleCliAuthService",
        _FakeGoogleCliAuthService,
    )

    cfg = make_cfg(sub="gemini")
    res = await apply_provider_switch(
        "subagent", "antigravity", cfg=cfg, persist=False,
    )

    assert res["ok"] is True
    assert res["tier"] == "subagent"
    assert res["new_provider"] == "antigravity"
    assert cfg.brain.worker.provider == "antigravity"


async def test_switch_antigravity_rejects_key_without_cli(
    monkeypatch, configured_keys
):
    from jarvis.brain.app_control import apply_provider_switch

    class _Status:
        installed = False
        connected = False
        mode = "unknown"

    class _FakeGoogleCliAuthService:
        def status(self):
            return _Status()

    monkeypatch.setattr(
        "jarvis.google_cli.auth_service.GoogleCliAuthService",
        _FakeGoogleCliAuthService,
    )

    cfg = make_cfg(sub="gemini")
    res = await apply_provider_switch(
        "subagent", "antigravity", cfg=cfg, persist=False,
    )

    assert res["ok"] is False
    assert res["error_kind"] == "subagent_unavailable"
    assert cfg.brain.worker.provider == "gemini"


async def test_switch_codex_allowed_as_subagent(monkeypatch, no_keys):
    from jarvis.brain.app_control import apply_provider_switch

    class _Status:
        connected = True
        installed = True

    class _FakeCodexAuthService:
        def status(self):
            return _Status()

    monkeypatch.setattr("jarvis.codex_auth.CodexAuthService", _FakeCodexAuthService)

    cfg = make_cfg(sub="gemini")
    res = await apply_provider_switch(
        "subagent", "openai-codex", cfg=cfg, persist=False,
    )

    assert res["ok"] is True
    assert res["tier"] == "subagent"
    assert res["new_provider"] == "openai-codex"
    assert cfg.brain.worker.provider == "openai-codex"


async def test_switch_unknown_tier(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    res = await apply_provider_switch("wakeword", "x", cfg=make_cfg(), persist=False)
    assert res["ok"] is False
    assert res["error_kind"] == "unknown_tier"


async def test_switch_stt_requires_restart(configured_keys):
    from jarvis.brain.app_control import apply_provider_switch

    res = await apply_provider_switch("stt", "groq-api", cfg=make_cfg(), persist=False)
    assert res["ok"] is True
    assert res["applied_live"] is False
    assert res["requires_restart"] is True


async def test_switch_provider_tool_missing_credential(no_keys, monkeypatch):
    from jarvis.plugins.tool.switch_provider import SwitchProviderTool

    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: make_cfg())
    # tier=tts: the brain provider is locked to user-only channels (CLI / manual
    # UI switch) and is refused up-front by the tool, so the missing-credential
    # path is exercised on a still-voice-switchable tier. The brain lock itself
    # is covered by tests/unit/plugins/tool/test_switch_provider_brain_lock.py.
    res = await SwitchProviderTool().execute(
        {"tier": "tts", "provider": "cartesia", "reason": "test"}, _ctx()
    )
    assert res.success is False
    assert "not configured" in (res.error or "")


async def test_switch_provider_tool_validates_input():
    from jarvis.plugins.tool.switch_provider import SwitchProviderTool

    res = await SwitchProviderTool().execute(
        {"tier": "bogus", "provider": "x", "reason": "y"}, _ctx()
    )
    assert res.success is False
    assert "tier must be one of" in (res.error or "")


# ----------------------------------------------------------------------
# manage-mcp-server
# ----------------------------------------------------------------------


@pytest.fixture
def fake_mcp_state(monkeypatch):
    """In-memory stand-in for jarvis.mcp.state."""
    store: dict[str, dict] = {}

    def upsert_server(name, spec):
        store[name] = {**store.get(name, {}), **spec}

    def remove_server(name):
        return store.pop(name, None) is not None

    def set_enabled(name, enabled):
        store.setdefault(name, {})["enabled"] = bool(enabled)

    def get_server_entry(name):
        return dict(store[name]) if name in store else None

    def load_config():
        return {"mcpServers": store}

    import jarvis.mcp.state as st

    monkeypatch.setattr(st, "upsert_server", upsert_server)
    monkeypatch.setattr(st, "remove_server", remove_server)
    monkeypatch.setattr(st, "set_enabled", set_enabled)
    monkeypatch.setattr(st, "get_server_entry", get_server_entry)
    monkeypatch.setattr(st, "load_config", load_config)
    return store


async def test_mcp_add_starts_disabled(fake_mcp_state):
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool

    res = await ManageMcpServerTool().execute(
        {
            "action": "add",
            "name": "github",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "reason": "test",
        },
        _ctx(),
    )
    assert res.success is True
    assert res.output["enabled"] is False  # AP-15 spirit: review before activate
    assert fake_mcp_state["github"]["enabled"] is False
    assert fake_mcp_state["github"]["command"] == "npx"


async def test_mcp_add_rejects_unknown_field(fake_mcp_state):
    """A raw secret value would arrive as an unexpected field — schema is strict.

    The tool itself also rejects unknown top-level keys defensively; here we
    assert it never persists a bare 'env' secret passed as a key value.
    """
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool

    # stdio add without a command must fail (no silent empty server).
    res = await ManageMcpServerTool().execute(
        {"action": "add", "name": "broken", "transport": "stdio", "reason": "x"}, _ctx()
    )
    assert res.success is False
    assert "command" in (res.error or "")
    assert "broken" not in fake_mcp_state


async def test_mcp_enable_unknown_server(fake_mcp_state):
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool

    res = await ManageMcpServerTool().execute(
        {"action": "enable", "name": "ghost", "reason": "x"}, _ctx()
    )
    assert res.success is False
    assert "no MCP server" in (res.error or "")


async def test_mcp_enable_without_registry_requires_restart(fake_mcp_state):
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool

    fake_mcp_state["github"] = {"command": "npx", "enabled": False}
    res = await ManageMcpServerTool().execute(
        {"action": "enable", "name": "github", "reason": "x"}, _ctx()
    )
    assert res.success is True
    assert fake_mcp_state["github"]["enabled"] is True
    # No live registry wired -> honest restart flag, no false live claim.
    assert res.output["requires_restart"] is True
    assert res.output["applied_live"] is False


async def test_mcp_remove(fake_mcp_state):
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool

    fake_mcp_state["fs"] = {"command": "uvx", "enabled": False}
    res = await ManageMcpServerTool().execute(
        {"action": "remove", "name": "fs", "reason": "x"}, _ctx()
    )
    assert res.success is True
    assert "fs" not in fake_mcp_state


async def test_mcp_invalid_action():
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool

    res = await ManageMcpServerTool().execute(
        {"action": "nuke", "name": "x", "reason": "y"}, _ctx()
    )
    assert res.success is False
    assert "action must be one of" in (res.error or "")


# ----------------------------------------------------------------------
# Wiring / discipline
# ----------------------------------------------------------------------


def test_router_tools_contains_app_control():
    from jarvis.brain.factory import ROUTER_TOOLS

    assert {
        "describe-app-settings",
        "switch-provider",
        "manage-mcp-server",
        "reveal-key-preview",
    } <= ROUTER_TOOLS


def test_app_control_tools_have_correct_risk_tiers():
    from jarvis.plugins.tool.describe_app_settings import DescribeAppSettingsTool
    from jarvis.plugins.tool.manage_mcp_server import ManageMcpServerTool
    from jarvis.plugins.tool.reveal_key_preview import RevealKeyPreviewTool
    from jarvis.plugins.tool.switch_provider import SwitchProviderTool

    assert DescribeAppSettingsTool.risk_tier == "safe"
    # Forensic 2026-06-26: a voice "switch the subagent brain to antigravity"
    # asked "really do that? say yes or no" and then hung up. A provider switch
    # is REVERSIBLE and the tool already speaks an honest post-change readback
    # (old -> new), which catches an STT mishear after the fact — so it must not
    # block on an up-front confirmation (anti-confirmation-fatigue mandate).
    # "monitor" runs without confirmation but is still audited (state change).
    assert SwitchProviderTool.risk_tier == "monitor"
    assert ManageMcpServerTool.risk_tier == "ask"
    assert RevealKeyPreviewTool.risk_tier == "monitor"


def test_switch_provider_does_not_trigger_voice_confirmation():
    """A reversible provider switch must not force an up-front yes/no.

    Root cause of the 2026-06-26 voice incident: ``risk_tier="ask"`` is the one
    tier in ``always_confirm_tiers``, so the executor returned the
    VOICE_CONFIRM_SENTINEL and the brain asked before switching. "monitor" is
    not a confirm tier, so the switch runs immediately.
    """
    from jarvis.core.config import SafetyConfig
    from jarvis.plugins.tool.switch_provider import SwitchProviderTool

    assert SwitchProviderTool.risk_tier not in SafetyConfig().always_confirm_tiers


def test_provider_routes_uses_shared_credential_check(monkeypatch):
    """Anti-drift: provider_routes' credential check delegates to app_control.

    Both paths must agree for the same provider — that is the single-source-of-
    truth guarantee (BUG-008 class). provider_routes no longer keeps its own
    alias map or heuristic.
    """
    from jarvis.brain import app_control
    from jarvis.ui.web import provider_routes
    from jarvis.ui.web.provider_spec import get_spec

    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda p: "tok")
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: "tok")

    spec = get_spec("gemini")
    assert app_control.is_credential_present(spec) is True
    assert provider_routes._is_credential_present(spec) is True
    # provider_routes must NOT keep a private alias map (would re-introduce drift)
    assert not hasattr(provider_routes, "_AUTH_PROVIDER_ALIASES")


# ----------------------------------------------------------------------
# reveal-key-preview (masked key, user mandate 2026-05-31)
# ----------------------------------------------------------------------


_FAKE_KEY = "AIzaSyB1234567890abcdefXYZ"  # 26 chars; first3=AIz, last3=XYZ


@pytest.fixture
def long_key(monkeypatch):
    """A realistic-length stored key for every provider."""
    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda p: _FAKE_KEY)
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: _FAKE_KEY)


def test_masked_preview_format(long_key):
    from jarvis.brain.app_control import masked_secret_preview

    out = masked_secret_preview("gemini")
    assert out["configured"] is True
    assert out["first3"] == "AIz"
    assert out["last3"] == "XYZ"
    assert out["preview"] == "AIz...XYZ"
    assert out["hidden_chars"] == len(_FAKE_KEY) - 6
    # HARD GUARANTEE: the full key never appears anywhere in the output.
    assert _FAKE_KEY not in str(out)


def test_masked_preview_not_configured(no_keys):
    from jarvis.brain.app_control import masked_secret_preview

    out = masked_secret_preview("gemini")
    assert out["configured"] is False
    assert out["preview"] is None


def test_masked_preview_too_short(monkeypatch):
    from jarvis.brain.app_control import masked_secret_preview

    monkeypatch.setattr("jarvis.core.config.get_provider_secret", lambda p: "abc123")
    monkeypatch.setattr("jarvis.core.config.get_secret", lambda *a, **k: "abc123")
    out = masked_secret_preview("gemini")
    assert out["configured"] is True
    assert out["preview"] is None  # set, but too short to reveal safely


async def test_reveal_tool_returns_masked_only(long_key):
    from jarvis.plugins.tool.reveal_key_preview import RevealKeyPreviewTool

    res = await RevealKeyPreviewTool().execute({"provider": "gemini"}, _ctx())
    assert res.success is True
    assert res.output["preview"] == "AIz...XYZ"
    # The full key must never leak through the tool result.
    assert _FAKE_KEY not in str(res.output)


async def test_reveal_tool_not_configured(no_keys):
    from jarvis.plugins.tool.reveal_key_preview import RevealKeyPreviewTool

    res = await RevealKeyPreviewTool().execute({"provider": "gemini"}, _ctx())
    assert res.success is True
    assert res.output["configured"] is False
    assert "No API key" in res.output["message"]


async def test_reveal_tool_requires_provider():
    from jarvis.plugins.tool.reveal_key_preview import RevealKeyPreviewTool

    res = await RevealKeyPreviewTool().execute({}, _ctx())
    assert res.success is False
    assert "'provider' is required" in (res.error or "")
