"""Self-hosted realtime: the local option this tier used to lack entirely.

Every other realtime card bills a hosted account, so an install running its
brain, its recognizer and its voice on its own hardware still had to leave the
machine for low-latency voice — or give that mode up. What these tests pin is
the part that makes a self-hosted card trustworthy rather than merely present:

 - it never joins a call it was not chosen for (an unconfigured endpoint must
   not swallow a turn from the provider that would have worked);
 - it sends the SERVER's own model and no hosted OpenAI model ids, because a
   field the user never chose must not be the reason a session is rejected;
 - a credential, if any, comes from the environment — never from jarvis.toml
   (AP-12).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.realtime.openai_realtime import (
    LocalRealtimeProvider,
    _normalize_local_root,
    _session_payload,
)


def _cfg(base_url: str = "", model: str = "") -> SimpleNamespace:
    provider = SimpleNamespace(base_url=base_url, model=model)
    return SimpleNamespace(brain=SimpleNamespace(providers={"local-realtime": provider}))


# ── Address handling ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:8080", "http://localhost:8080/v1"),
        ("http://localhost:8080/", "http://localhost:8080/v1"),
        # An already-complete API root must not double up to /v1/v1.
        ("http://localhost:8080/v1", "http://localhost:8080/v1"),
        ("localhost:8080", "http://localhost:8080/v1"),
        # What a realtime server actually PRINTS on startup is its websocket
        # endpoint, and that is what a user pastes. The SDK wants the HTTP API
        # root and derives the socket itself, so the paste has to survive.
        ("ws://localhost:8765/v1/realtime", "http://localhost:8765/v1"),
        ("ws://localhost:8765", "http://localhost:8765/v1"),
        ("wss://gpu.lan:8443/v1/realtime", "https://gpu.lan:8443/v1"),
        ("http://localhost:8765/v1/realtime", "http://localhost:8765/v1"),
        # 0.0.0.0 is a server BIND address; as a client target it fails.
        ("0.0.0.0:8080", "http://localhost:8080/v1"),
        ("https://gpu.lan:8443", "https://gpu.lan:8443/v1"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_server_address_normalization(raw: str, expected: str) -> None:
    assert _normalize_local_root(raw) == expected


# ── Never an uninvited candidate ─────────────────────────────────────────
def test_unconfigured_card_is_not_ready() -> None:
    """No address means nothing to try — and the factory must not build it,
    or a call would route into an endpoint the user never set up."""
    assert LocalRealtimeProvider.external_login_ready(_cfg()) is False
    assert LocalRealtimeProvider.external_login_ready(None) is False


def test_configured_card_is_ready_without_touching_the_network() -> None:
    """The factory calls this on an audio loop: it answers "is there an
    endpoint at all", never "does it respond"."""
    assert LocalRealtimeProvider.external_login_ready(_cfg("http://localhost:8080")) is True


def test_never_an_implicit_fallback() -> None:
    """A self-hosted endpoint is a deliberate choice; quietly routing a call
    into one the user did not pick is the opposite of what this card is for."""
    assert LocalRealtimeProvider.implicit_usage_fallback_allowed is False


def test_the_class_satisfies_the_provider_protocol() -> None:
    """Live failure 2026-08-06: leaving ``credential_candidates`` off the class
    (it is empty for a keyless card, so it felt redundant) made the runtime
    protocol check fail. The loader then rejected the plugin, the factory
    produced no candidate, and a call sat on "connecting" forever while nothing
    ever reached the server. Declaring the attribute empty is what selects the
    keyless path; omitting it removes the provider from the product.
    """
    from jarvis.realtime.protocol import RealtimeProvider

    assert isinstance(LocalRealtimeProvider(), RealtimeProvider)
    assert LocalRealtimeProvider.credential_candidates == ()


def test_the_factory_actually_builds_it_when_selected() -> None:
    """The contract that matters is not "the class looks right", it is "a call
    that selects this card gets a provider object". The protocol failure above
    was invisible to every check that stopped at the class."""
    from jarvis.core.config import BrainConfig, BrainProviderConfig, BrainTierConfig, JarvisConfig
    from jarvis.realtime import factory

    cfg = JarvisConfig(
        brain=BrainConfig(
            providers={
                "local-realtime": BrainProviderConfig(base_url="http://localhost:8765")
            },
            realtime=BrainTierConfig(provider="local-realtime"),
        )
    )

    candidates = factory._provider_candidates(cfg)

    assert [type(c).__name__ for c in candidates] == ["LocalRealtimeProvider"]


def test_an_unconfigured_card_yields_no_candidate() -> None:
    """The other half: without a server address it must stay out of the chain."""
    from jarvis.core.config import BrainConfig, BrainTierConfig, JarvisConfig
    from jarvis.realtime import factory

    cfg = JarvisConfig(
        brain=BrainConfig(realtime=BrainTierConfig(provider="local-realtime"))
    )

    assert factory._provider_candidates(cfg) == []


async def test_open_session_without_an_address_says_what_to_do() -> None:
    with pytest.raises(RuntimeError) as err:
        await LocalRealtimeProvider().open_session(SimpleNamespace())
    assert "server URL" in str(err.value)


# ── Credentials come from the environment, never from the config file ────
def test_optional_key_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_LOCAL_REALTIME_API_KEY", "sk-local-proxy")
    provider = LocalRealtimeProvider.from_runtime_config(_cfg("http://localhost:8080"))
    assert provider._api_key == "sk-local-proxy"


def test_a_key_in_the_config_file_is_ignored(monkeypatch) -> None:
    """AP-12: a credential in jarvis.toml is a leak, so a stored one must not
    even be honoured."""
    monkeypatch.delenv("JARVIS_LOCAL_REALTIME_API_KEY", raising=False)
    cfg = _cfg("http://localhost:8080")
    cfg.brain.providers["local-realtime"].api_key = "sk-should-be-ignored"
    provider = LocalRealtimeProvider.from_runtime_config(cfg)
    assert provider._api_key == ""


# ── The session payload carries nothing the server did not ask for ───────
def test_local_session_declares_no_hosted_transcription_model() -> None:
    """A hosted OpenAI model id is meaningless on a self-hosted server, and
    naming one would have it reject the whole session over a field the user
    never chose."""
    payload = _session_payload(SimpleNamespace(), transcription_model=None)
    assert payload["audio"]["input"]["transcription"] == {}


def test_hosted_session_keeps_its_transcription_model() -> None:
    payload = _session_payload(SimpleNamespace())
    assert payload["audio"]["input"]["transcription"]["model"] == "gpt-4o-mini-transcribe"


def test_auto_voice_is_a_preference_not_a_voice_name() -> None:
    """"auto" is the only honest entry a self-hosted card can offer; sending it
    as a voice NAME would have the server reject a voice it does not have."""
    assert "voice" not in _session_payload(SimpleNamespace(voice="auto"))["audio"]["output"]
    assert _session_payload(SimpleNamespace(voice="coral"))["audio"]["output"]["voice"] == (
        "coral"
    )


# ── Model resolution: ask the server, do not invent a name ───────────────
class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    payload: dict[str, Any] = {}
    fail: bool = False
    last_url: str | None = None

    def __init__(self, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str] | None = None) -> _FakeResponse:
        _FakeAsyncClient.last_url = url
        if _FakeAsyncClient.fail:
            raise RuntimeError("connection refused")
        return _FakeResponse(_FakeAsyncClient.payload)


@pytest.fixture()
def fake_models(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    _FakeAsyncClient.fail = False
    _FakeAsyncClient.payload = {}
    _FakeAsyncClient.last_url = None
    return _FakeAsyncClient


async def test_model_comes_from_the_server_when_none_is_pinned(fake_models) -> None:
    fake_models.payload = {"data": [{"id": "moshi-v1"}, {"id": "other"}]}
    provider = LocalRealtimeProvider(base_url="http://localhost:8080")
    assert await provider._resolve_model() == "moshi-v1"
    assert fake_models.last_url == "http://localhost:8080/v1/models"


async def test_a_pinned_model_skips_the_probe(fake_models) -> None:
    provider = LocalRealtimeProvider(base_url="http://localhost:8080", model="my-model")
    assert await provider._resolve_model() == "my-model"
    assert fake_models.last_url is None


async def test_a_server_without_a_model_list_still_connects(fake_models) -> None:
    """The probe is a convenience, not a gate: the connect that follows carries
    the honest failure if there is one."""
    fake_models.fail = True
    provider = LocalRealtimeProvider(base_url="http://localhost:8080")
    assert await provider._resolve_model()


# ── A dead local server must not mean a dead call ────────────────────────
#
# Live 2026-08-06 19:57: the self-hosted server's process died silently
# mid-turn and the whole call ended reason=error, although the server was
# healthy again within the minute. Three properties fix that class of
# failure: local sessions opt into the in-place transport rebuild, a failing
# connect is retried long enough for a restarting server to warm up, and —
# when a launch command is configured — Jarvis revives the server itself.


class _FakeConnection:
    def __aiter__(self):
        return self


def _session_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "connection": _FakeConnection(),
        "connection_cm": SimpleNamespace(),
        "client": SimpleNamespace(),
        "session_id": "s-1",
    }
    kwargs.update(overrides)
    return kwargs


def test_transport_rebuild_is_opt_in_and_off_by_default() -> None:
    """Hosted OpenAI-protocol cards keep their deliberate terminal semantics
    (BUG-064): a session that was not asked to rebuild must not."""
    from jarvis.plugins.realtime.openai_realtime import _OpenAIRealtimeSession

    session = _OpenAIRealtimeSession(**_session_kwargs())
    assert session.rebuild_on_transport_death is False
    opted_in = _OpenAIRealtimeSession(
        **_session_kwargs(rebuild_on_transport_death=True)
    )
    assert opted_in.rebuild_on_transport_death is True


async def test_local_sessions_opt_into_transport_rebuild(monkeypatch) -> None:
    """The self-hosted card asks for the in-place rebuild: its server can
    crash and come back, and the call must survive that."""
    from jarvis.plugins.realtime import openai_realtime as module

    captured: dict[str, Any] = {}

    async def fake_open(client: Any, cfg: Any, **kwargs: Any) -> str:
        captured.update(kwargs)
        return "session"

    monkeypatch.setattr(module, "_open_realtime_session", fake_open)
    provider = LocalRealtimeProvider(
        base_url="http://localhost:8080", model="my-model"
    )
    assert await provider.open_session(SimpleNamespace(model="my-model")) == "session"
    assert captured["rebuild_on_transport_death"] is True


async def test_connect_retries_until_the_server_is_back(monkeypatch) -> None:
    """A restarting local server needs seconds to warm up; the first refused
    connects are its warm-up, not its verdict."""
    from jarvis.plugins.realtime import openai_realtime as module

    monkeypatch.setattr(module, "_LOCAL_CONNECT_RETRY_STEP_S", 0.0)
    provider = LocalRealtimeProvider(
        base_url="http://localhost:8080", model="m", launch_command="serve"
    )
    launches: list[bool] = []
    monkeypatch.setattr(
        provider, "_maybe_launch_server", lambda: launches.append(True) or False
    )
    attempts = 0

    async def flaky(cfg: Any) -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("refused")
        return "session"

    monkeypatch.setattr(provider, "_open_session_once", flaky)
    assert await provider.open_session(SimpleNamespace(model="m")) == "session"
    assert attempts == 3
    assert launches  # the revive path was consulted while the server was down


async def test_connect_gives_up_honestly_after_the_window(monkeypatch) -> None:
    from jarvis.plugins.realtime import openai_realtime as module

    monkeypatch.setattr(module, "_LOCAL_CONNECT_RETRY_STEP_S", 0.01)
    provider = LocalRealtimeProvider(base_url="http://localhost:8080", model="m")
    monkeypatch.setattr(provider, "_connect_retry_window_s", lambda: 0.03)

    async def always_down(cfg: Any) -> str:
        raise ConnectionError("refused")

    monkeypatch.setattr(provider, "_open_session_once", always_down)
    with pytest.raises(ConnectionError):
        await provider.open_session(SimpleNamespace(model="m"))


async def test_cancellation_is_never_retried(monkeypatch) -> None:
    """The desktop's startup budget cancels a slow connect; holding the
    cancellation hostage to a retry loop would freeze the call teardown."""
    import asyncio

    provider = LocalRealtimeProvider(
        base_url="http://localhost:8080", model="m", launch_command="serve"
    )
    attempts = 0

    async def cancelled(cfg: Any) -> str:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError()

    monkeypatch.setattr(provider, "_open_session_once", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await provider.open_session(SimpleNamespace(model="m"))
    assert attempts == 1


def test_patience_is_earned_by_a_launch_command() -> None:
    """Without a launch command nobody revives the server — a long silent
    wait would hold the call hostage for nothing."""
    from jarvis.plugins.realtime import openai_realtime as module

    patient = LocalRealtimeProvider(
        base_url="http://localhost:8080", launch_command="serve"
    )
    unattended = LocalRealtimeProvider(base_url="http://localhost:8080")
    assert patient._connect_retry_window_s() == module._LOCAL_CONNECT_PATIENT_WINDOW_S
    assert unattended._connect_retry_window_s() == module._LOCAL_CONNECT_SHORT_WINDOW_S


def _fresh_launch_state(monkeypatch) -> list[dict[str, Any]]:
    """Reset the class-level spawn stamp and capture Popen calls."""
    import subprocess

    monkeypatch.setattr(LocalRealtimeProvider, "_last_launch_at", float("-inf"))
    spawned: list[dict[str, Any]] = []

    def fake_popen(command: Any, **kwargs: Any) -> SimpleNamespace:
        spawned.append({"command": command, **kwargs})
        return SimpleNamespace(pid=4711)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return spawned


def test_revive_spawns_windowless_and_rate_limited(monkeypatch, tmp_path) -> None:
    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    spawned = _fresh_launch_state(monkeypatch)
    provider = LocalRealtimeProvider(
        base_url="http://localhost:8765", launch_command="serve --flag"
    )
    assert provider._maybe_launch_server() is True
    # Immediately again: rate-limited, a crash-looping server is not hammered.
    assert provider._maybe_launch_server() is False
    assert len(spawned) == 1
    assert spawned[0]["creationflags"] == NO_WINDOW_CREATIONFLAGS  # AP-1


def test_revive_refuses_remote_servers(monkeypatch) -> None:
    """A LAN endpoint going down must not start a second server HERE."""
    spawned = _fresh_launch_state(monkeypatch)
    provider = LocalRealtimeProvider(
        base_url="http://gpu.lan:8443", launch_command="serve"
    )
    assert provider._maybe_launch_server() is False
    assert spawned == []


def test_no_launch_command_means_no_spawn(monkeypatch) -> None:
    spawned = _fresh_launch_state(monkeypatch)
    provider = LocalRealtimeProvider(base_url="http://localhost:8765")
    assert provider._maybe_launch_server() is False
    assert spawned == []


def test_declared_handshake_budget_covers_the_patient_window() -> None:
    """The shared 12 s handshake ceiling would behead the patient reconnect
    mid-warm-up; the declared budget must clear the retry window."""
    patient = LocalRealtimeProvider(
        base_url="http://localhost:8765", launch_command="serve"
    )
    assert patient.handshake_budget_s > patient._connect_retry_window_s()
