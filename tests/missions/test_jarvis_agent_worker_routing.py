"""Defense-in-depth: a configured subagent provider can NEVER be silently
diverted to the Gemini API key by a per-step model string.

User mandate: heavy tasks run on the configured provider (claude-api ->
Claude Max OAuth subscription). Gemini must never be a silent fallback.

These tests pin the pure routing decision in
``jarvis.missions.init._select_subagent_worker_kind`` so the worker that runs
never drifts from the configured ``[brain.sub_jarvis].provider``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.missions.init import (
    _live_subagent_provider,
    _select_subagent_worker_kind,
)

# --- HARD LOCK: claude-api wins over ANY step model ----------------------


@pytest.mark.parametrize(
    "step_model",
    ["", "gemini-3.1-pro-preview", "gemini", "GEMINI-X", "sonnet", "grok-4.3"],
)
def test_claude_api_is_a_hard_lock(step_model: str) -> None:
    """With claude-api configured, NO step model can route elsewhere —
    especially not to the Gemini worker (which uses the Gemini API key)."""
    assert _select_subagent_worker_kind("claude-api", step_model) == "claude_direct"


def test_openclaw_claude_routes_subjarvis() -> None:
    assert _select_subagent_worker_kind("openclaw-claude", "gemini-x") == "subjarvis"


@pytest.mark.parametrize("provider", ["chatgpt", "openai-codex"])
def test_codex_providers_route_codex(provider: str) -> None:
    assert _select_subagent_worker_kind(provider, "gemini-x") == "codex_direct"


@pytest.mark.parametrize("provider", ["openai", "openrouter", "grok"])
@pytest.mark.parametrize("step_model", ["", "gemini-3.1-pro", "sonnet", "claude-opus-4-8"])
def test_api_agent_providers_route_to_api_agent(provider: str, step_model: str) -> None:
    """grok/openai/openrouter run ON their own provider via the in-process
    ApiAgentWorker (2026-06-22). A HARD LOCK like claude-api/antigravity: no step
    model can divert them — and they must NOT silently route to Claude (subjarvis)
    or to the Gemini API worker. The credential gate (no key -> Claude) lives in
    the worker factory, not in this pure routing decision."""
    assert _select_subagent_worker_kind(provider, step_model) == "api_agent"


def test_api_agent_providers_are_not_a_claude_fallback() -> None:
    """The UI badge must stop pretending openai/openrouter run on Claude."""
    from jarvis.missions.init import subagent_runs_on_claude_fallback

    for provider in ("openai", "openrouter", "grok"):
        assert subagent_runs_on_claude_fallback(provider) is False
    # the genuine always-Claude case stays True
    assert subagent_runs_on_claude_fallback("openclaw-claude") is True


@pytest.mark.parametrize("step_model", ["", "claude-opus-4-8", "gemini-3.1-pro"])
def test_antigravity_routes_to_oauth_cli_worker(step_model: str) -> None:
    """Choosing 'antigravity' (Google subscription) routes to the dedicated
    OAuth-CLI worker kind — never the API-key Gemini path. Like claude-api, it
    is a hard lock that no step model can divert."""
    assert _select_subagent_worker_kind("antigravity", step_model) == "antigravity"


def test_gemini_as_subagent_provider_uses_direct_gemini_worker() -> None:
    """Post-Welle-4: explicitly choosing 'gemini' routes to the direct
    GeminiWorker so the sub-agent actually RUNS on Gemini. The OpenClaw path it
    used to take was removed, so without this it silently ran on Claude. This is
    an EXPLICIT selection, NOT the anti-silent-Gemini fallback case."""
    assert _select_subagent_worker_kind("gemini", "") == "gemini"
    assert _select_subagent_worker_kind("gemini", "claude-opus-4-8") == "gemini"


# --- Legacy fallback: gemini worker ONLY when NOTHING is configured ------


def test_gemini_worker_only_when_no_provider_configured() -> None:
    assert _select_subagent_worker_kind(None, "gemini-3.1-pro") == "gemini"
    assert _select_subagent_worker_kind("", "gemini-3.1-pro") == "gemini"


def test_unconfigured_non_gemini_defaults_to_subjarvis() -> None:
    assert _select_subagent_worker_kind(None, "sonnet") == "subjarvis"
    assert _select_subagent_worker_kind(None, "") == "subjarvis"


# --- Live re-resolution: a subagent switch takes effect WITHOUT a restart ---
#
# The worker factory used to freeze ``[brain.sub_jarvis].provider`` at the
# boot-time snapshot, so switching the heavy-mission subagent to Codex only
# took effect after an app restart — every mission ran on the hard-coded
# ClaudeDirectWorker (Opus) fallback in the meantime. ``_live_subagent_provider``
# re-resolves the persisted choice per mission (healing a stale inherited
# process-env first, then re-reading the uncached config) so the running app
# follows the current selection.


def _fake_cfg(provider: object) -> SimpleNamespace:
    return SimpleNamespace(brain=SimpleNamespace(worker=SimpleNamespace(provider=provider)))


def test_live_provider_overrides_stale_boot_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live config of openai-codex wins over a stale boot snapshot of claude-api
    — the exact incident: a process booted before the codex pin kept running Claude."""
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _fake_cfg("openai-codex"))
    monkeypatch.setattr(cfg_mod, "refresh_persisted_env_from_user_registry", lambda *a, **k: {})

    assert _live_subagent_provider("claude-api") == "openai-codex"


def test_live_provider_heals_inherited_env_before_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """The persisted JARVIS__* override is refreshed from the registry into
    os.environ BEFORE the (uncached, env>toml) re-read, or a frozen inherited
    env would keep winning over the user's persisted choice."""
    import jarvis.core.config as cfg_mod

    calls: list[str] = []
    monkeypatch.setattr(
        cfg_mod,
        "refresh_persisted_env_from_user_registry",
        lambda *a, **k: calls.append("refreshed") or {},
    )
    monkeypatch.setattr(cfg_mod, "load_config", lambda: _fake_cfg("openai-codex"))

    assert _live_subagent_provider("claude-api") == "openai-codex"
    assert calls == ["refreshed"]


def test_live_provider_normalizes_case_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _fake_cfg("  OpenAI-Codex  "))
    monkeypatch.setattr(cfg_mod, "refresh_persisted_env_from_user_registry", lambda *a, **k: {})

    assert _live_subagent_provider("claude-api") == "openai-codex"


@pytest.mark.parametrize("live_value", [None, "", "   "])
def test_live_provider_falls_back_to_boot_when_unset(
    monkeypatch: pytest.MonkeyPatch, live_value: object
) -> None:
    import jarvis.core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _fake_cfg(live_value))
    monkeypatch.setattr(cfg_mod, "refresh_persisted_env_from_user_registry", lambda *a, **k: {})

    assert _live_subagent_provider("claude-api") == "claude-api"


def test_live_provider_falls_back_to_boot_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A config read failure must never break dispatch — fall back to the boot
    snapshot, exactly as the boot path already does."""
    import jarvis.core.config as cfg_mod

    def _boom() -> object:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(cfg_mod, "load_config", _boom)
    monkeypatch.setattr(cfg_mod, "refresh_persisted_env_from_user_registry", lambda *a, **k: {})

    assert _live_subagent_provider("openai-codex") == "openai-codex"


def test_live_provider_survives_registry_refresh_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A registry-refresh blip is best-effort — the live read still proceeds."""
    import jarvis.core.config as cfg_mod

    def _boom(*a: object, **k: object) -> dict[str, str]:
        raise OSError("registry unavailable")

    monkeypatch.setattr(cfg_mod, "refresh_persisted_env_from_user_registry", _boom)
    monkeypatch.setattr(cfg_mod, "load_config", lambda: _fake_cfg("openai-codex"))

    assert _live_subagent_provider("claude-api") == "openai-codex"


# --- Honesty surface: which selections silently fall back to Claude ----------
#
# Grok/OpenAI/OpenRouter run on their OWN provider via the
# in-process ApiAgentWorker, so they are NO LONGER routing-level Claude
# fallbacks. Only the legacy ``"subjarvis"`` kind (openclaw-claude / unknown)
# still always runs Claude. The UI reads ``subagent_runs_on_claude_fallback``
# (derived from the SAME routing function) so the badge never lies.


@pytest.mark.parametrize("provider", ["openclaw-claude"])
def test_subjarvis_kind_flagged_as_claude(provider: str) -> None:
    from jarvis.missions.init import subagent_runs_on_claude_fallback

    assert subagent_runs_on_claude_fallback(provider) is True


@pytest.mark.parametrize("provider", ["openai", "openrouter", "grok"])
def test_api_agent_providers_no_longer_flagged_as_claude(provider: str) -> None:
    from jarvis.missions.init import subagent_runs_on_claude_fallback

    assert subagent_runs_on_claude_fallback(provider) is False


@pytest.mark.parametrize(
    "provider", ["openai-codex", "chatgpt", "gemini", "claude-api", "antigravity"]
)
def test_native_providers_not_flagged(provider: str) -> None:
    """A provider with its own dedicated worker (incl. claude-api itself, which
    genuinely IS Claude) is not a misleading fallback."""
    from jarvis.missions.init import subagent_runs_on_claude_fallback

    assert subagent_runs_on_claude_fallback(provider) is False
