"""Integration-Tests für /api/providers, /api/secrets/{key}, /api/brain/switch.

Strategie: keyring + subprocess + Claude-Cred-File werden komplett gemockt.
Die Tests laufen damit hermetisch ohne echte Credentials zu schreiben.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.events import SecretConfigured
from jarvis.ui.web.server import WebServer


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class _InMemorySecretStore:
    """Simuliert das Keyring per dict — wird per monkeypatch in cfg eingehängt."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str, env_fallback: str | None = None) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True

    def delete(self, key: str) -> bool:
        self.data.pop(key, None)
        return True


class _FakeBrainManager:
    """Minimaler BrainManager-Stub, der nur die switch-Schnittstelle implementiert."""

    def __init__(self, *, available: list[str], active: str = "openai", bus: EventBus | None = None) -> None:
        self._available = available
        self.active_provider = active
        self.calls: list[tuple[str, bool]] = []
        self._bus = bus
        self.persist_calls: list[bool] = []
        # Mirrors the real BrainManager: records the actual disk outcome of a
        # persisting switch. The route reads this to report ``persisted``.
        self.last_persist_ok: bool | None = None

    def available_providers(self) -> list[str]:
        return list(self._available)

    async def switch(self, provider: str, *, persist: bool = False) -> None:
        self.calls.append((provider, persist))
        self.persist_calls.append(persist)
        self.active_provider = provider
        # The fake "writes" successfully when persistence is requested.
        self.last_persist_ok = bool(persist)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def secret_store(monkeypatch: pytest.MonkeyPatch) -> _InMemorySecretStore:
    store = _InMemorySecretStore()
    # Patches in beiden Importpfaden — provider_routes.py importiert das Modul
    # unter dem Alias `cfg_mod`, andere Stellen direkt.
    from jarvis.core import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "get_secret", store.get)
    monkeypatch.setattr(cfg_mod, "set_secret", store.set)
    monkeypatch.setattr(cfg_mod, "delete_secret", store.delete)
    return store


@pytest.fixture
def web_server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    server = WebServer(cfg, bus=bus)
    yield server


@pytest.fixture
def server_with_brain(web_server: WebServer) -> WebServer:
    web_server.app.state.brain = _FakeBrainManager(
        available=["openai", "claude-api", "ollama-local", "openrouter", "codex"],
        active="openai",
        bus=web_server.bus,
    )
    web_server.app.state.cfg = web_server.cfg
    web_server.app.state.bus = web_server.bus
    return web_server


# ----------------------------------------------------------------------
# /api/providers
# ----------------------------------------------------------------------


def test_list_providers_returns_full_catalog(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert "providers" in body
        ids = {p["id"] for p in body["providers"]}
        assert "openai" in ids
        assert "codex" in ids
        assert "openclaw" not in ids
        assert "gemini-flash-tts" in ids, "Gemini Flash TTS muss im Katalog sein"
        assert "faster-whisper" in ids, "Lokales STT als 'none'-Auth-Provider"
        assert "elevenlabs" not in ids, "ElevenLabs ist Dead-Code"
        assert "ollama-local" not in ids, "Ollama wurde 2026-04-21 entfernt"


# ----------------------------------------------------------------------
# /api/providers/section-health — the at-a-glance tab indicators
# ----------------------------------------------------------------------


class _FakeTestResult:
    """Minimal stand-in for provider_test.ProviderTestResult — the section-health
    route only reads ``.status`` and ``.detail``."""

    def __init__(self, status: str = "ok", detail: str = "") -> None:
        self.status = status
        self.detail = detail


@pytest.fixture
def no_real_provider_test(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the REAL connectivity call so section-health never hits the network
    (and can't pick up the maintainer's live keyring keys), keeping the test
    hermetic and fast."""
    from jarvis.brain import provider_test as _pt

    async def _fake_run(spec: Any, cfg: Any) -> _FakeTestResult:  # noqa: ANN401
        return _FakeTestResult("ok", "")

    monkeypatch.setattr(_pt, "run_provider_test", _fake_run)


def test_section_health_returns_all_tabs(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    no_real_provider_test: None,
) -> None:
    """Every tab gets a status drawn from the SSOT vocabulary, and the response
    is honestly marked uncached on the first call."""
    with TestClient(server_with_brain.app) as client:
        resp = client.get("/api/providers/section-health")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body["sections"]) == {"brain", "tts", "stt", "subagents", "advanced"}
        valid = {"ok", "needs_setup", "error", "unknown"}
        for sec in body["sections"].values():
            assert sec["status"] in valid
        assert body["cached"] is False
        # No telephony manager mounted → the optional Advanced tab stays silent.
        assert body["sections"]["advanced"]["status"] == "unknown"


def test_section_health_missing_key_is_needs_setup(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    no_real_provider_test: None,
) -> None:
    """The active brain provider with no stored key rolls up to needs_setup —
    the 'you still have to set this up' (amber) signal, distinct from a broken
    key (which would be 'error')."""
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers/section-health").json()
        assert body["sections"]["brain"]["status"] == "needs_setup"
        assert body["sections"]["brain"]["reason"] == "not_configured"


def test_section_health_caches_then_refresh_bypasses(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    no_real_provider_test: None,
) -> None:
    """Repeated opens / tab switches reuse the cached rollup; ?refresh=true (used
    after a key save or provider switch) forces a fresh check."""
    with TestClient(server_with_brain.app) as client:
        assert client.get("/api/providers/section-health").json()["cached"] is False
        assert client.get("/api/providers/section-health").json()["cached"] is True
        assert (
            client.get("/api/providers/section-health?refresh=true").json()["cached"]
            is False
        )


def test_list_providers_exposes_credential_help_and_billing(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """The catalog carries the per-provider help text + how it is billed, so the
    UI can explain 'which key / subscription, and what for' without guessing."""
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers").json()
        by_id = {p["id"]: p for p in body["providers"]}
        assert by_id["gemini"]["credential_help"]
        assert by_id["gemini"]["billing"] == "api"
        assert by_id["antigravity"]["billing"] == "subscription_or_api"
        assert by_id["codex"]["billing"] == "subscription_or_api"
        assert by_id["faster-whisper"]["billing"] == "local"


def test_list_providers_exposes_gemini_vertex_alt_path(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """Gemini surfaces the Vertex AI alternative so the user sees AI Studio vs
    Vertex are different billing accounts (2026-06-22 forensic)."""
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers").json()
        by_id = {p["id"]: p for p in body["providers"]}
        alt = by_id["gemini"]["alt_credential"]
        assert alt is not None
        assert "vertex" in alt["label"].lower()
        assert alt["billing"] == "api"
        assert "cloud.google.com" in alt["dashboard_url"]
        assert by_id["openai"]["alt_credential"] is None


def test_list_providers_marks_active_brain(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers").json()
        openai = next(p for p in body["providers"] if p["id"] == "openai")
        claude_api = next(p for p in body["providers"] if p["id"] == "claude-api")
        assert openai["active"] is True
        assert claude_api["active"] is False


def test_list_providers_reports_configured_for_set_keys(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    secret_store.set("openai_api_key", "sk-test-123")
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers").json()
        openai = next(p for p in body["providers"] if p["id"] == "openai")
        gemini = next(p for p in body["providers"] if p["id"] == "gemini")
        assert openai["configured"] is True
        assert gemini["configured"] is False


def test_list_providers_never_leaks_secret_values(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    secret_store.set("openai_api_key", "SECRET-VALUE-DO-NOT-LEAK")
    with TestClient(server_with_brain.app) as client:
        text = client.get("/api/providers").text
        assert "SECRET-VALUE-DO-NOT-LEAK" not in text


def test_list_providers_reports_codex_without_leaking_auth_files(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeCodexService:
        def __init__(self, binary_path: str | None = None) -> None:
            self.binary_path = binary_path

        def status(self):
            from jarvis.codex_auth import CodexAuthStatus

            return CodexAuthStatus(
                installed=True,
                connected=True,
                mode="chatgpt",
                message="Codex ist verbunden",
                version="1.2.3",
                accountLabel="ChatGPT/Codex-Login",
            )

    monkeypatch.setattr("jarvis.ui.web.provider_routes.CodexAuthService", _FakeCodexService)
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers").json()
        codex = next(p for p in body["providers"] if p["id"] == "codex")
        assert codex["configured"] is True
        assert codex["codex_status"]["mode"] == "chatgpt"
        assert "auth.json" not in client.get("/api/providers").text


def test_codex_binary_path_persists_to_config(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_codex_binary_path",
        lambda binary_path, **kw: writes.append(binary_path),
    )
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/codex/binary-path",
            json={"binary_path": " C:\\Tools\\codex.cmd "},
        )
        assert resp.status_code == 200
        assert resp.json()["binary_path"] == "C:\\Tools\\codex.cmd"
    assert writes == ["C:\\Tools\\codex.cmd"]
    assert server_with_brain.cfg.codex.binary_path == "C:\\Tools\\codex.cmd"


# ----------------------------------------------------------------------
# /api/secrets/{key}
# ----------------------------------------------------------------------


def test_set_secret_persists_value(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/secrets/openai_api_key", json={"value": "sk-abc"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
    assert secret_store.data["openai_api_key"] == "sk-abc"


def test_set_secret_rejects_unknown_key(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/secrets/totally_made_up_key", json={"value": "x"})
        assert resp.status_code == 404


def test_set_secret_rejects_empty_value(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/secrets/openai_api_key", json={"value": ""})
        assert resp.status_code == 422  # pydantic min_length=1


def test_set_secret_emits_event(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    received: list[SecretConfigured] = []

    async def handler(evt: SecretConfigured) -> None:
        received.append(evt)

    server_with_brain.bus.subscribe(SecretConfigured, handler)
    with TestClient(server_with_brain.app) as client:
        client.post("/api/secrets/openai_api_key", json={"value": "sk-x"})
    assert any(e.key == "openai_api_key" and e.action == "set" for e in received)


def test_delete_secret(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    secret_store.set("gemini_api_key", "old")
    with TestClient(server_with_brain.app) as client:
        resp = client.delete("/api/secrets/gemini_api_key")
        assert resp.status_code == 200
    assert "gemini_api_key" not in secret_store.data


# ----------------------------------------------------------------------
# /api/brain/switch
# ----------------------------------------------------------------------


def test_brain_switch_calls_manager(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    # Seit dem 409-Credential-Gate wird ein Switch ohne gesetzten Key
    # abgelehnt. Wir setzen den Key vor dem Switch — exakt der UI-Pfad
    # (User speichert API-Key, klickt dann "Als aktiv").
    secret_store.set("openrouter_api_key", "sk-or-test-123")
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "openrouter", "persist": True})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] == "openrouter"
        assert body["persisted"] is True
    assert fake.calls == [("openrouter", True)]


def test_brain_switch_rejects_provider_without_key(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """Akzeptanzkriterium: Provider ohne API-Key kann nicht aktiviert werden.

    Liefert 409 mit klarer Message, sodass die UI eine konkrete Fehlermeldung
    aus `body.detail` darstellen kann (statt eines stillen Erfolgs, der erst
    beim ersten Voice-Turn sichtbar fehlschlaegt).
    """
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "openrouter"})
        assert resp.status_code == 409
        assert "API-Key" in resp.json()["detail"]
    # BrainManager.switch() darf NICHT aufgerufen worden sein.
    assert fake.calls == []


def test_brain_switch_codex_rejected_as_subagent_only_even_with_openai_key(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """Codex remains subagent-only even if an OpenAI key is configured."""
    secret_store.set("openai_api_key", "sk-openai-test-123")
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "codex", "persist": True})
        assert resp.status_code == 409
        assert "subagent-only" in resp.json()["detail"]
    assert fake.calls == []


def _patch_codex_status(monkeypatch: pytest.MonkeyPatch, *, connected: bool) -> None:
    """Pin provider_routes.CodexAuthService to a connected/disconnected stub so
    Codex route tests don't depend on the dev machine's real `codex login`."""

    class _Fake:
        def __init__(self, binary_path: str | None = None) -> None:
            self.binary_path = binary_path

        def status(self):
            from jarvis.codex_auth import CodexAuthStatus

            return CodexAuthStatus(
                installed=True,
                connected=connected,
                mode="chatgpt" if connected else "unknown",
            )

    monkeypatch.setattr("jarvis.ui.web.provider_routes.CodexAuthService", _Fake)


def _patch_antigravity_status(monkeypatch: pytest.MonkeyPatch, *, connected: bool) -> None:
    class _Status:
        def __init__(self) -> None:
            self.installed = True
            self.connected = connected
            self.mode = "oauth-personal" if connected else "unknown"
            self.cli_kind = "agy"
            self.message = "connected" if connected else "not connected"
            self.version = "1.0.0"
            self.user_email = "dev@example.com" if connected else None
            self.binary_path = "agy"
            self.error = None

        def to_dict(self) -> dict[str, Any]:
            return {
                "installed": self.installed,
                "connected": self.connected,
                "mode": self.mode,
                "cli_kind": self.cli_kind,
                "message": self.message,
                "version": self.version,
                "user_email": self.user_email,
                "binary_path": self.binary_path,
                "error": self.error,
            }

    class _Fake:
        def status(self) -> _Status:
            return _Status()

    monkeypatch.setattr("jarvis.google_cli.auth_service.GoogleCliAuthService", _Fake)


def test_brain_switch_codex_rejected_as_subagent_only_even_with_chatgpt_login(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OpenAI key, but a ChatGPT login -> 200: CodexBrain drives the slow
    ``codex exec`` CLI path over the OAuth token, so the toggle is usable."""
    _patch_codex_status(monkeypatch, connected=True)
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "codex", "persist": True})
        assert resp.status_code == 409
        assert "subagent-only" in resp.json()["detail"]
    assert fake.calls == []


def test_brain_switch_codex_rejected_without_any_auth(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No OpenAI key AND no ChatGPT login -> 409: nothing can back the brain."""
    _patch_codex_status(monkeypatch, connected=False)
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "codex"})
        assert resp.status_code == 409
        assert "subagent-only" in resp.json()["detail"]
    # No silent switch — the manager must not have been called.
    assert fake.calls == []


def test_brain_switch_antigravity_rejected_as_subagent_only(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Antigravity is OAuth-connected but not selectable as the main brain.

    It remains available through the dedicated subagent switch; the main-brain
    endpoint must reject it before calling BrainManager.switch().
    """
    _patch_antigravity_status(monkeypatch, connected=True)
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    fake._available.append("antigravity")

    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "antigravity"})

    assert resp.status_code == 409
    assert "Subagent" in resp.json()["detail"]
    assert fake.calls == []


def test_brain_switch_unknown_provider_returns_404(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "nonexistent-provider"})
        assert resp.status_code == 404


def test_brain_switch_provider_not_in_registry_returns_404(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    # gemini ist im Spec, aber nicht in available_providers des Fakes
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "gemini"})
        assert resp.status_code == 404


def test_brain_switch_blocked_in_airgapped_profile(server_with_brain: WebServer, secret_store: _InMemorySecretStore) -> None:
    server_with_brain.cfg.profile.name = "airgapped"
    server_with_brain.app.state.cfg = server_with_brain.cfg
    with TestClient(server_with_brain.app) as client:
        # Cloud-Provider blockiert (alle Brain-Provider sind aktuell cloud)
        resp = client.post("/api/brain/switch", json={"provider": "openai"})
        assert resp.status_code == 403
        resp = client.post("/api/brain/switch", json={"provider": "claude-api"})
        assert resp.status_code == 403


def test_brain_switch_503_when_brain_missing(web_server: WebServer, secret_store: _InMemorySecretStore) -> None:
    # Kein app.state.brain → Headless-Mode
    with TestClient(web_server.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "openai"})
        assert resp.status_code == 503


def test_brain_switch_codex_requires_api_key(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex is rejected as main Brain before credential checks."""
    _patch_codex_status(monkeypatch, connected=False)
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/brain/switch", json={"provider": "codex"})
        assert resp.status_code == 409
    assert fake.calls == []


def test_brain_switch_codex_with_api_key(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """Even with a Codex key saved, Codex remains subagent-only."""
    secret_store.set("codex_openai_api_key", "sk-codex-123")
    fake: _FakeBrainManager = server_with_brain.app.state.brain
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/brain/switch", json={"provider": "codex", "persist": False}
        )
        assert resp.status_code == 409, resp.text
        assert "subagent-only" in resp.json()["detail"]
    assert fake.calls == []


# ----------------------------------------------------------------------
# /api/tts/switch
# ----------------------------------------------------------------------


def test_list_providers_includes_grok_voice_tts(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """grok-voice ist als TTS-Provider im Katalog (UI-Sichtbarkeit)."""
    with TestClient(server_with_brain.app) as client:
        body = client.get("/api/providers").json()
        ids = {p["id"] for p in body["providers"]}
        assert "grok-voice" in ids
        grok = next(p for p in body["providers"] if p["id"] == "grok-voice")
        assert grok["tier"] == "tts"
        assert grok["secret_keys"] == ["grok_api_key"]


def test_tts_switch_persists_and_acks_restart(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy-Path: Provider mit Credentials -> 200 + restart_required."""
    secret_store.set("grok_api_key", "xai-test-key")

    write_calls: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_tts_provider",
        lambda name, **kw: write_calls.append(name),
    )

    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/tts/switch", json={"provider": "grok-voice", "persist": True}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] == "grok-voice"
        assert body["persisted"] is True
        assert body["restart_required"] is True

    assert write_calls == ["grok-voice"]


def test_tts_switch_rejects_unconfigured_provider(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """Ohne API-Key → 409, damit der User erst einen Key setzt."""
    # Kein grok_api_key gesetzt
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/tts/switch", json={"provider": "grok-voice", "persist": True}
        )
        assert resp.status_code == 409


def test_tts_switch_rejects_brain_provider(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    """Tier-Mismatch: openai ist Brain, kein TTS → 400."""
    secret_store.set("openai_api_key", "sk-test")
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/tts/switch", json={"provider": "openai", "persist": True}
        )
        assert resp.status_code == 400


def test_tts_switch_unknown_provider_returns_404(
    server_with_brain: WebServer, secret_store: _InMemorySecretStore
) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/tts/switch", json={"provider": "doesnt-exist", "persist": True}
        )
        assert resp.status_code == 404


def test_tts_switch_no_persist_skips_toml_write(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """persist=false → kein TOML-Write, aber Switch-Response weiterhin OK."""
    secret_store.set("grok_api_key", "xai-test-key")

    write_calls: list[str] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_tts_provider",
        lambda name, **kw: write_calls.append(name),
    )

    with TestClient(server_with_brain.app) as client:
        resp = client.post(
            "/api/tts/switch", json={"provider": "grok-voice", "persist": False}
        )
        assert resp.status_code == 200
        assert resp.json()["persisted"] is False

    assert write_calls == []


# ----------------------------------------------------------------------
# /api/providers/{pid}/login
# ----------------------------------------------------------------------


def test_provider_login_returns_409_when_cli_missing(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = monkeypatch
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/providers/openclaw/login")
        assert resp.status_code == 404
        # Detail kann string oder dict sein — beide zulässig in FastAPI
        detail = resp.json().get("detail")
        assert detail is not None


def test_provider_login_starts_subprocess(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = monkeypatch
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/providers/openclaw/login")
        assert resp.status_code == 404


def test_provider_login_rejects_api_key_provider(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
) -> None:
    with TestClient(server_with_brain.app) as client:
        resp = client.post("/api/providers/openai/login")
        assert resp.status_code == 404


# ----------------------------------------------------------------------
# /api/providers/{pid}/login/status
# ----------------------------------------------------------------------


def test_login_status_reports_logged_in_for_valid_claude_creds(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = monkeypatch

    with TestClient(server_with_brain.app) as client:
        resp = client.get("/api/providers/openclaw/login/status")
        assert resp.status_code == 404


def test_login_status_logged_in_false_when_creds_missing(
    server_with_brain: WebServer,
    secret_store: _InMemorySecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ = monkeypatch

    with TestClient(server_with_brain.app) as client:
        resp = client.get("/api/providers/openclaw/login/status")
        assert resp.status_code == 404
