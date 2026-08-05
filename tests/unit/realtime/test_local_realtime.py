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
