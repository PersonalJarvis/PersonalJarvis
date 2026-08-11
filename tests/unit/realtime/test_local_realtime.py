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
        # "localhost" is pinned to 127.0.0.1: the resolver tries ::1 first
        # while the common server binds IPv4 only — that dead IPv6 attempt
        # measured 2,050 ms per connect (2026-08-08) and WAS the user-felt
        # connect delay.
        ("http://localhost:8080", "http://127.0.0.1:8080/v1"),
        ("http://localhost:8080/", "http://127.0.0.1:8080/v1"),
        # An already-complete API root must not double up to /v1/v1.
        ("http://localhost:8080/v1", "http://127.0.0.1:8080/v1"),
        ("localhost:8080", "http://127.0.0.1:8080/v1"),
        # What a realtime server actually PRINTS on startup is its websocket
        # endpoint, and that is what a user pastes. The SDK wants the HTTP API
        # root and derives the socket itself, so the paste has to survive.
        ("ws://localhost:8765/v1/realtime", "http://127.0.0.1:8765/v1"),
        ("ws://localhost:8765", "http://127.0.0.1:8765/v1"),
        ("wss://gpu.lan:8443/v1/realtime", "https://gpu.lan:8443/v1"),
        ("http://localhost:8765/v1/realtime", "http://127.0.0.1:8765/v1"),
        # 0.0.0.0 is a server BIND address; as a client target it fails.
        ("0.0.0.0:8080", "http://127.0.0.1:8080/v1"),
        ("https://gpu.lan:8443", "https://gpu.lan:8443/v1"),
        # An explicit IPv6 loopback is a deliberate choice and survives.
        ("http://[::1]:8765", "http://[::1]:8765/v1"),
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


async def test_cold_managed_server_starts_but_does_not_hold_the_call(
    monkeypatch,
) -> None:
    """Cold loading continues under supervision while the interactive probe
    returns immediately instead of spending 120 seconds without a pool slot."""
    from jarvis.realtime.local_server import supervisor

    calls: list[str] = []
    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: True
    )
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: calls.append(f"run:{kwargs['reason']}") or "spawned",
    )
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append("monitor") or True,
    )
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="managed-server --mode realtime",
    )

    assert await provider.can_open_duplex_session() is False
    assert calls == ["run:interactive-preflight", "monitor"]


async def test_slow_managed_spawn_continues_after_the_call_fails_fast(
    monkeypatch, tmp_path
) -> None:
    # Isolate the data dir: the refusal path asks the supervisor for live boot
    # progress, which reads the REAL pidfile and server log of the developer's
    # machine. On a host that happens to run a managed server, parsing that
    # multi-megabyte log both changes the refusal text and costs enough time to
    # invert this test's timing assertion.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    import asyncio
    import time

    from jarvis.plugins.realtime import openai_realtime as module
    from jarvis.realtime.local_server import supervisor

    calls: list[str] = []
    monkeypatch.setattr(module, "_LOCAL_MANAGED_PREFLIGHT_WAIT_S", 0.005)
    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: True
    )

    def slow_start(**kwargs: Any) -> str:
        time.sleep(0.05)
        calls.append(kwargs["reason"])
        return "spawned"

    monkeypatch.setattr(supervisor, "ensure_running", slow_start)
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append("monitor") or True,
    )
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="managed-server --mode realtime",
    )

    assert (
        await asyncio.wait_for(provider.can_open_duplex_session(), timeout=0.2)
        is False
    )
    assert calls == []
    await asyncio.gather(*LocalRealtimeProvider._background_start_tasks)
    assert calls == ["interactive-preflight", "monitor"]


async def test_ready_managed_pool_opens_only_with_idle_capacity(monkeypatch) -> None:
    from jarvis.realtime.local_server import supervisor

    pool = {"size": 1, "in_use": 0, "available": 1, "active": 0}
    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: True
    )
    monkeypatch.setattr(
        supervisor, "ensure_running", lambda **kwargs: "already-running"
    )
    monkeypatch.setattr(supervisor, "start_runtime_monitor", lambda **kwargs: True)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: pool)
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="managed-server --mode realtime",
    )

    assert await provider.can_open_duplex_session() is True
    pool.update({"in_use": 1, "available": 0, "active": 1})
    assert await provider.can_open_duplex_session() is False


async def test_a_serving_pool_is_never_judged_by_the_cold_start_clock(
    monkeypatch,
) -> None:
    """A running server answers for itself, in its own budget.

    Live 2026-08-09 11:50:47: the verdict came only from the tail of the
    revive attempt, so a healthy pool waited behind a spawn that first spends
    ~1 s preparing its Ollama model — and lost the 0.75 s interactive window
    to work it did not need.
    """
    import asyncio
    import time

    from jarvis.plugins.realtime import openai_realtime as module
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(module, "_LOCAL_MANAGED_PREFLIGHT_WAIT_S", 0.05)
    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: True
    )

    def slow_revive(**kwargs: Any) -> str:
        time.sleep(0.5)
        return "already-running"

    monkeypatch.setattr(supervisor, "ensure_running", slow_revive)
    monkeypatch.setattr(supervisor, "start_runtime_monitor", lambda **kwargs: True)
    monkeypatch.setattr(
        supervisor,
        "probe_runtime",
        lambda *args, **kwargs: {
            "size": 1,
            "in_use": 0,
            "available": 1,
            "active": 0,
        },
    )
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="managed-server --mode realtime",
    )

    assert (
        await asyncio.wait_for(provider.can_open_duplex_session(), timeout=0.3)
        is True
    )
    assert provider.duplex_unavailable_reason == ""
    await asyncio.gather(*LocalRealtimeProvider._background_start_tasks)


async def test_a_refusal_explains_itself_in_words_a_user_can_act_on(
    monkeypatch, tmp_path
) -> None:
    """Each refusal names a DIFFERENT next step, so it may not be one text.

    The data dir is isolated because the starting refusal is deliberately
    upgraded with live boot progress: without this, a developer machine with a
    real managed server under way answers with its actual stage instead of the
    static sentence pinned here.
    """
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    from jarvis.plugins.realtime import openai_realtime as module
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: True
    )
    monkeypatch.setattr(
        supervisor, "ensure_running", lambda **kwargs: "already-running"
    )
    monkeypatch.setattr(supervisor, "start_runtime_monitor", lambda **kwargs: True)

    pool: dict[str, int] | None = None
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: pool)
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="managed-server --mode realtime",
    )

    # No pool at all: the server is cold and somebody is starting it.
    assert await provider.can_open_duplex_session() is False
    assert provider.duplex_unavailable_reason == module._LOCAL_REASON_STARTING

    # Every slot in use: healthy, just occupied — a different situation.
    pool = {"size": 1, "in_use": 1, "available": 0, "active": 1}
    assert await provider.can_open_duplex_session() is False
    assert provider.duplex_unavailable_reason == module._LOCAL_REASON_BUSY

    # Slots exist, none usable, nobody served: loading or wedged.
    pool = {"size": 1, "in_use": 0, "available": 0, "active": 0}
    assert await provider.can_open_duplex_session() is False
    assert provider.duplex_unavailable_reason == module._LOCAL_REASON_NO_CAPACITY

    # Recovery clears the explanation instead of leaving a stale one behind.
    pool = {"size": 1, "in_use": 0, "available": 1, "active": 0}
    assert await provider.can_open_duplex_session() is True
    assert provider.duplex_unavailable_reason == ""


async def test_a_missing_address_reads_as_a_setup_gap_not_an_outage() -> None:
    from jarvis.brain.provider_test import NOT_CONFIGURED, classify_provider_error

    provider = LocalRealtimeProvider()

    assert await provider.can_open_duplex_session() is False
    reason = provider.duplex_unavailable_reason
    assert "server URL" in reason
    # The shared classifier must read this as "finish the setup", which is
    # what routes it to the actionable amber instead of a red error.
    assert classify_provider_error(reason) == NOT_CONFIGURED


async def test_byo_server_never_requires_the_private_pool(monkeypatch) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: False
    )
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: pytest.fail("BYO launch must retain protocol-only behavior"),
    )
    monkeypatch.setattr(
        supervisor,
        "probe_runtime",
        lambda *args, **kwargs: pytest.fail("BYO server need not implement /v1/pool"),
    )
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="custom-realtime-server --flag",
    )

    assert await provider.can_open_duplex_session() is True


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
    # localhost is pinned to 127.0.0.1 at normalization (IPv6-fallback fix).
    assert fake_models.last_url == "http://127.0.0.1:8080/v1/models"


async def test_a_pinned_model_skips_the_probe(fake_models) -> None:
    provider = LocalRealtimeProvider(base_url="http://localhost:8080", model="my-model")
    assert await provider._resolve_model() == "my-model"
    assert fake_models.last_url is None


async def test_the_managed_server_skips_its_unsupported_models_route(
    fake_models, monkeypatch
) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(supervisor, "is_managed_launch_command", lambda command: True)
    provider = LocalRealtimeProvider(
        base_url="http://localhost:8080",
        launch_command="managed-server --mode realtime",
    )

    assert await provider._resolve_model()
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


def test_prompted_response_retry_is_opt_in_and_off_by_default() -> None:
    """Hosted cards keep the bare response.create retry; only a transport
    that keeps the cancelled answer in its conversation opts into the
    send_text retry (2026-08-10: the local retry returned one empty token)."""
    from jarvis.plugins.realtime.openai_realtime import _OpenAIRealtimeSession

    session = _OpenAIRealtimeSession(**_session_kwargs())
    assert session.supports_prompted_response_retry is False
    opted_in = _OpenAIRealtimeSession(
        **_session_kwargs(prompted_response_retry=True)
    )
    assert opted_in.supports_prompted_response_retry is True


def test_surface_fallback_render_is_opt_in_and_off_by_default() -> None:
    """Hosted cards keep the surface re-render (their TTS sibling exists);
    only a transport whose voice lives solely behind the live session claims
    the session-voice fallback render."""
    from jarvis.plugins.realtime.openai_realtime import _OpenAIRealtimeSession

    session = _OpenAIRealtimeSession(**_session_kwargs())
    assert session.renders_surface_fallback is False
    opted_in = _OpenAIRealtimeSession(
        **_session_kwargs(renders_surface_fallback=True)
    )
    assert opted_in.renders_surface_fallback is True


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
    assert captured["response_start_timeout_s"] > module._RESPONSE_STALL_S
    assert captured["disconnect_before_rebuild"] is True
    assert captured["rebuild_retry_window_s"] > 0.0
    assert captured["prompted_response_retry"] is True
    assert captured["renders_surface_fallback"] is True


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


async def test_managed_initial_open_race_never_uses_the_recovery_window(
    monkeypatch,
) -> None:
    """A pool can disappear between readiness and WebSocket acquisition. That
    first-call race is bounded separately from the patient mid-call rebuild."""
    import asyncio

    from jarvis.plugins.realtime import openai_realtime as module
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(module, "_LOCAL_MANAGED_INTERACTIVE_OPEN_S", 0.02)
    monkeypatch.setattr(
        supervisor, "is_managed_launch_command", lambda command: True
    )
    monkeypatch.setattr(
        supervisor, "ensure_running", lambda **kwargs: "already-running"
    )
    monkeypatch.setattr(supervisor, "start_runtime_monitor", lambda **kwargs: True)
    monkeypatch.setattr(
        supervisor,
        "probe_runtime",
        lambda *args, **kwargs: {
            "size": 1,
            "in_use": 0,
            "available": 1,
            "active": 0,
        },
    )
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        model="m",
        launch_command="managed-server --mode realtime",
    )
    assert await provider.can_open_duplex_session() is True

    async def wedged_handshake(cfg: Any) -> str:
        await asyncio.sleep(1.0)
        return "too-late"

    monkeypatch.setattr(provider, "_open_session_once", wedged_handshake)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            provider.open_session(SimpleNamespace(model="m")),
            timeout=0.5,
        )


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
    """Reset the class-level spawn stamp and capture Popen calls.

    The port probe is pinned closed: a REAL listener on 8765 (a live dev
    server on this machine) would otherwise turn the spawn into an honest
    "already-running" and fail the test for the wrong reason.
    """
    import subprocess

    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(supervisor, "_port_open", lambda port, timeout=1.0: False)
    monkeypatch.setattr(supervisor, "probe_runtime", lambda *args, **kwargs: None)
    monkeypatch.setattr(supervisor, "_process_create_time", lambda pid: 1000.0)
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
    creationflags = int(spawned[0]["creationflags"])
    if NO_WINDOW_CREATIONFLAGS:
        assert creationflags & NO_WINDOW_CREATIONFLAGS  # AP-1
    else:
        assert creationflags == 0


def test_transient_lifecycle_refusal_is_immediately_retryable(monkeypatch) -> None:
    from jarvis.realtime.local_server import supervisor

    monkeypatch.setattr(LocalRealtimeProvider, "_last_launch_at", float("-inf"))
    outcomes = iter(["refused:spawn-in-progress", "spawned"])
    calls: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: calls.append(kwargs["reason"]) or next(outcomes),
    )
    provider = LocalRealtimeProvider(
        base_url="http://127.0.0.1:8765",
        launch_command="serve --mode realtime",
    )

    assert provider._maybe_launch_server() is False
    assert provider._maybe_launch_server() is True
    assert calls == ["connect-revive", "connect-revive"]


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


# ── Stale launch commands (deleted managed install) ──────────────────────
def test_launch_command_state_missing_for_a_deleted_path(tmp_path) -> None:
    """A quoted path whose target is gone is exactly the deleted managed
    install of 2026-08-08 — the one case that must be judged decisively."""
    from jarvis.plugins.realtime import openai_realtime as module

    gone = tmp_path / "venv" / "Scripts" / "speech-to-speech.exe"
    assert (
        module._launch_command_target_state(f'"{gone}" --mode realtime') == "missing"
    )


def test_launch_command_state_present_for_an_existing_path(tmp_path) -> None:
    from jarvis.plugins.realtime import openai_realtime as module

    exe = tmp_path / "server"
    exe.write_bytes(b"")
    assert module._launch_command_target_state(f'"{exe}" --flag') == "present"


def test_launch_command_state_fails_open_on_ambiguity() -> None:
    """Bare names and empty commands are never judged: a bring-your-own
    command must keep exactly the pre-fix behavior."""
    from jarvis.plugins.realtime import openai_realtime as module

    assert module._launch_command_target_state("serve --flag") == "unknown"
    assert module._launch_command_target_state("") == "unknown"
    assert module._launch_command_target_state('"unclosed --flag') == "unknown"


def test_a_stale_install_shrinks_the_retry_window(tmp_path) -> None:
    """Patience only pays when the revive could ever succeed; a deleted
    entry point earns the short window, not 120 s of knocking."""
    from jarvis.plugins.realtime import openai_realtime as module

    gone = tmp_path / "venv" / "Scripts" / "speech-to-speech.exe"
    stale = LocalRealtimeProvider(
        base_url="http://localhost:8765", launch_command=f'"{gone}" --mode realtime'
    )
    assert stale._connect_retry_window_s() == module._LOCAL_CONNECT_SHORT_WINDOW_S


async def test_a_stale_install_fails_fast_with_the_fixing_action(
    monkeypatch, tmp_path
) -> None:
    """The first refused connect against a deleted install must end the
    attempt with the fixing action — not sit out any retry window (live
    2026-08-08: 120 s of "Connecting…" ending in a silent idle)."""
    from jarvis.plugins.realtime import openai_realtime as module

    monkeypatch.setattr(module, "_LOCAL_CONNECT_RETRY_STEP_S", 0.0)
    gone = tmp_path / "venv" / "Scripts" / "speech-to-speech.exe"
    provider = LocalRealtimeProvider(
        base_url="http://localhost:8765", launch_command=f'"{gone}" --mode realtime'
    )
    attempts = 0

    async def refused(cfg: Any) -> str:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("refused")

    monkeypatch.setattr(provider, "_open_session_once", refused)
    with pytest.raises(RuntimeError, match="not installed anymore"):
        await provider.open_session(SimpleNamespace(model="m"))
    assert attempts == 1


async def test_a_running_orphan_still_connects_despite_a_stale_command(
    monkeypatch, tmp_path
) -> None:
    """A server that outlived its deleted install keeps serving; the stale
    check must only fire once a connect actually FAILED."""
    gone = tmp_path / "venv" / "Scripts" / "speech-to-speech.exe"
    provider = LocalRealtimeProvider(
        base_url="http://localhost:8765", launch_command=f'"{gone}" --mode realtime'
    )

    async def healthy(cfg: Any) -> str:
        return "session"

    monkeypatch.setattr(provider, "_open_session_once", healthy)
    assert await provider.open_session(SimpleNamespace(model="m")) == "session"


# ── SDK-client reuse (the ~230 ms per-call client build, 2026-08-08) ─────
async def test_the_sdk_client_is_cached_across_sessions(monkeypatch) -> None:
    """Building AsyncOpenAI costs ~230 ms (httpx + SSL context); one cached
    client per endpoint turns that into a one-time cost."""
    from jarvis.plugins.realtime import openai_realtime as module

    captured: list[Any] = []

    async def fake_open(client: Any, cfg: Any, **kwargs: Any) -> str:
        captured.append((client, kwargs.get("owns_client")))
        return "session"

    monkeypatch.setattr(module, "_open_realtime_session", fake_open)
    provider = LocalRealtimeProvider(base_url="http://localhost:8765", model="m")
    await provider.open_session(SimpleNamespace(model="m"))
    await provider.open_session(SimpleNamespace(model="m"))
    assert captured[0][0] is captured[1][0]  # the SAME client object
    assert captured[0][1] is False  # sessions never own the cached client


async def test_closing_a_local_session_keeps_the_cached_client_alive() -> None:
    """A cached client that dies with its first session would make every
    LATER call pay the rebuild — and closing a shared client under a
    concurrent session would kill that session's transport."""
    from jarvis.plugins.realtime.openai_realtime import _OpenAIRealtimeSession

    class _FakeCm:
        async def __aexit__(self, *exc: Any) -> None:
            return None

    closed: list[str] = []

    class _FakeClient:
        async def close(self) -> None:
            closed.append("client")

    class _FakeConn:
        def __aiter__(self):
            return self

    shared = _OpenAIRealtimeSession(
        connection=_FakeConn(),
        connection_cm=_FakeCm(),
        client=_FakeClient(),
        session_id="s1",
        owns_client=False,
    )
    await shared.close()
    assert closed == []  # cached client survives

    owned = _OpenAIRealtimeSession(
        connection=_FakeConn(),
        connection_cm=_FakeCm(),
        client=_FakeClient(),
        session_id="s2",
    )
    await owned.close()
    assert closed == ["client"]  # hosted default behavior is unchanged


# ── Boot-time prewarm (warm_transport capability) ────────────────────────
def _warm_cfg(base_url: str, launch_command: str) -> SimpleNamespace:
    provider = SimpleNamespace(
        base_url=base_url, model="", launch_command=launch_command
    )
    return SimpleNamespace(
        brain=SimpleNamespace(providers={"local-realtime": provider})
    )


async def test_warm_transport_prewarms_via_the_supervisor(
    monkeypatch, tmp_path
) -> None:
    """The shared warm worker calls this after boot: server up + brain model
    resident BEFORE the first call, which is what makes connects instant."""
    from jarvis.realtime.local_server import install, supervisor

    # Isolated data dir: the readiness budget is derived from THIS install's
    # recorded boot durations, so without isolation the assertion below would
    # measure whatever the developer's machine last booted in.
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    calls: list[str] = []
    readiness_timeouts: list[float] = []
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: calls.append(f"run:{kwargs['reason']}") or "spawned",
    )
    monkeypatch.setattr(supervisor, "is_managed_launch_command", lambda command: True)

    def wait_until_ready(*args: Any, **kwargs: Any) -> bool:
        del args
        calls.append("ready")
        # The prewarm passes no explicit budget — the supervisor's measured
        # default IS the background budget. What matters here is that the warm
        # worker never inherits the short interactive one.
        timeout = kwargs.get("timeout")
        readiness_timeouts.append(
            float(timeout) if timeout is not None else supervisor.ready_timeout_s()
        )
        return True

    monkeypatch.setattr(supervisor, "wait_until_ready", wait_until_ready)
    monkeypatch.setattr(
        supervisor, "warm_brain", lambda **kwargs: calls.append("warm") or True
    )
    monkeypatch.setattr(
        install,
        "repair_smoke_marker_from_live_runtime",
        lambda base_url: calls.append("marker") or True,
    )
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append("monitor") or True,
    )
    exe = tmp_path / "server"
    exe.write_bytes(b"")
    cfg = _warm_cfg("http://localhost:8765", f'"{exe}" --model_name m')
    assert await LocalRealtimeProvider.warm_transport(cfg) is True
    assert calls == ["run:prewarm", "ready", "marker", "monitor", "warm"]
    assert readiness_timeouts == [supervisor.RUNTIME_READY_TIMEOUT_S]
    assert readiness_timeouts[0] >= 300.0


async def test_warm_transport_never_warms_the_brain_before_speech_is_ready(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import supervisor

    calls: list[str] = []
    monkeypatch.setattr(supervisor, "ensure_running", lambda **kwargs: "spawned")
    monkeypatch.setattr(supervisor, "is_managed_launch_command", lambda command: True)
    monkeypatch.setattr(supervisor, "wait_until_ready", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        supervisor, "warm_brain", lambda **kwargs: calls.append("warm") or True
    )
    exe = tmp_path / "server"
    exe.write_bytes(b"")
    cfg = _warm_cfg("http://127.0.0.1:8765", f'"{exe}" --model_name m')
    assert await LocalRealtimeProvider.warm_transport(cfg) is False
    assert calls == []


async def test_byo_warm_transport_does_not_require_the_private_pool(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import supervisor

    calls: list[str] = []
    monkeypatch.setattr(supervisor, "ensure_running", lambda **kwargs: "already-running")
    monkeypatch.setattr(supervisor, "is_managed_launch_command", lambda command: False)

    def forbidden_wait(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("BYO servers do not promise /v1/pool")

    monkeypatch.setattr(supervisor, "wait_until_ready", forbidden_wait)
    monkeypatch.setattr(
        supervisor, "warm_brain", lambda **kwargs: calls.append("warm") or False
    )

    async def resolved_model(_provider: LocalRealtimeProvider) -> str:
        return "m"

    monkeypatch.setattr(LocalRealtimeProvider, "_resolve_model", resolved_model)
    exe = tmp_path / "server"
    exe.write_bytes(b"")
    cfg = _warm_cfg("http://127.0.0.1:8765", f'"{exe}" --model_name m')

    assert await LocalRealtimeProvider.warm_transport(cfg) is True
    assert calls == ["warm"]


async def test_warm_transport_skips_a_deleted_install(monkeypatch, tmp_path) -> None:
    """Prewarming a deleted entry point would just burn the rate limit and
    log noise at every boot."""
    from jarvis.realtime.local_server import supervisor

    spawned: list[str] = []
    monkeypatch.setattr(
        supervisor, "ensure_running", lambda **kwargs: spawned.append("x") or "spawned"
    )
    gone = tmp_path / "venv" / "Scripts" / "speech-to-speech.exe"
    cfg = _warm_cfg("http://localhost:8765", f'"{gone}" --model_name m')
    assert await LocalRealtimeProvider.warm_transport(cfg) is False
    assert spawned == []


async def test_warm_transport_requires_an_address_and_a_command() -> None:
    assert await LocalRealtimeProvider.warm_transport(_warm_cfg("", "serve")) is False
    assert (
        await LocalRealtimeProvider.warm_transport(
            _warm_cfg("http://localhost:8765", "")
        )
        is False
    )
    # A LAN endpoint is never spawned here — wrong host.
    assert (
        await LocalRealtimeProvider.warm_transport(
            _warm_cfg("http://gpu.lan:8443", "serve")
        )
        is False
    )


async def test_warm_transport_never_raises(monkeypatch) -> None:
    """Best-effort by contract: no failure may reach the warm worker."""
    assert await LocalRealtimeProvider.warm_transport(None) is False


# ── Boot-time prespawn (prespawn_transport capability) ───────────────────
def test_local_realtime_is_eagerly_warmed_as_a_fallback() -> None:
    """Live 2026-08-10: with an expired subscription primary, the un-warmed
    local FALLBACK was still stone cold when the first call arrived — the
    call died on a machine that could have answered it. A local stack costs
    no account round-trip to warm, so it must not sit out fallback warming."""
    assert LocalRealtimeProvider.eager_warm_as_fallback is True


async def test_prespawn_spawns_and_arms_the_monitor_without_waiting(
    monkeypatch, tmp_path
) -> None:
    """The boot-time prestart only spawns: readiness and brain residency stay
    with warm_transport, so the prespawn can run before the warm worker's
    gates without ever blocking a boot."""
    from jarvis.realtime.local_server import supervisor

    calls: list[str] = []
    monkeypatch.setattr(
        supervisor,
        "ensure_running",
        lambda **kwargs: calls.append(f"run:{kwargs['reason']}") or "spawned",
    )
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append("monitor") or True,
    )

    def forbidden(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("prespawn must never block on readiness or the brain")

    monkeypatch.setattr(supervisor, "wait_until_ready", forbidden)
    monkeypatch.setattr(supervisor, "warm_brain", forbidden)
    exe = tmp_path / "server"
    exe.write_bytes(b"")
    cfg = _warm_cfg("http://localhost:8765", f'"{exe}" --model_name m')
    assert await LocalRealtimeProvider.prespawn_transport(cfg) is True
    assert calls == ["run:boot-prespawn", "monitor"]


async def test_prespawn_does_not_arm_the_monitor_on_a_refused_spawn(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import supervisor

    calls: list[str] = []
    monkeypatch.setattr(
        supervisor, "ensure_running", lambda **kwargs: "refused:rate-limited"
    )
    monkeypatch.setattr(
        supervisor,
        "start_runtime_monitor",
        lambda **kwargs: calls.append("monitor") or True,
    )
    exe = tmp_path / "server"
    exe.write_bytes(b"")
    cfg = _warm_cfg("http://localhost:8765", f'"{exe}" --model_name m')
    assert await LocalRealtimeProvider.prespawn_transport(cfg) is False
    assert calls == []


async def test_prespawn_requires_an_address_and_a_local_command(
    monkeypatch, tmp_path
) -> None:
    from jarvis.realtime.local_server import supervisor

    def forbidden(**kwargs: Any) -> str:
        raise AssertionError("nothing may spawn without a local endpoint")

    monkeypatch.setattr(supervisor, "ensure_running", forbidden)
    assert await LocalRealtimeProvider.prespawn_transport(_warm_cfg("", "serve")) is False
    assert (
        await LocalRealtimeProvider.prespawn_transport(
            _warm_cfg("http://localhost:8765", "")
        )
        is False
    )
    # A LAN endpoint is never spawned here — wrong host.
    assert (
        await LocalRealtimeProvider.prespawn_transport(
            _warm_cfg("http://gpu.lan:8443", "serve")
        )
        is False
    )
    # A deleted managed install must not burn the spawn rate limit at boot.
    gone = tmp_path / "venv" / "Scripts" / "speech-to-speech.exe"
    assert (
        await LocalRealtimeProvider.prespawn_transport(
            _warm_cfg("http://localhost:8765", f'"{gone}" --model_name m')
        )
        is False
    )


async def test_prespawn_never_raises() -> None:
    """Best-effort by contract: no failure may reach the boot worker."""
    assert await LocalRealtimeProvider.prespawn_transport(None) is False
