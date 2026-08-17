"""The dedicated Vertex AI path: pinned route, project mode, ADC.

Sibling of ``test_google_genai.py``, which covers the INFERRED route for a key
stored in a Gemini slot. Everything here is about the case where the user picked
Vertex explicitly, and the three facts that makes true:

1. The route is PINNED — no probe, no AI Studio fallback. Without this a Google
   Cloud API key restricted to ``aiplatform.googleapis.com`` (ordinary ``AIza``
   shape, so indistinguishable from an AI Studio key) would be sent to the wrong
   host and fail every call with an auth error that names nothing useful.
2. The express key and the Cloud project are MUTUALLY EXCLUSIVE at the SDK
   boundary. ``genai.Client`` refuses both together, so exactly one shape may
   ever be assembled.
3. A configured project counts as a credential even with no key stored anywhere,
   because Application Default Credentials do the signing on that path.

No network and no SDK: the kwargs assembly is pure, and the client build is
driven through a stub module so these run on a base install.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from jarvis.core import config as cfg
from jarvis.core import google_genai as gg


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch):
    gg.reset_route_cache()
    gg.reset_vertex_credentials_cache()
    gg.reset_shared_tls_context()
    # No test here may reach google-auth: on a gcloud-login host that call
    # spawns the gcloud CLI (5-8 s) and on a bare host it raises. The seam
    # answers "no ambient credential" unless a test installs its own fake.
    monkeypatch.setattr(gg, "_load_application_default_credentials", lambda: None)
    yield
    gg.reset_route_cache()
    gg.reset_vertex_credentials_cache()
    gg.reset_shared_tls_context()


@pytest.fixture
def _no_ambient_adc(monkeypatch: pytest.MonkeyPatch):
    """Start from a host with no ``GOOGLE_APPLICATION_CREDENTIALS`` set.

    ``setenv`` first, then ``delenv``: the SUT writes this variable itself with
    a raw ``os.environ`` assignment, and ``delenv(raising=False)`` on an unset
    name records nothing — so a value written during the test would leak into
    every later test. Recording the original state first makes the teardown
    restore it whatever the test body did.
    """
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS")


def _project(
    project: str | None,
    location: str = "global",
    sa: str | None = None,
    realtime_location: str | None = None,
):
    return gg.VertexProject(
        project=project,
        location=location,
        service_account_path=sa,
        realtime_location=realtime_location,
    )


# ── chat and Live are different endpoints ────────────────────────────────────
#
# Measured 2026-08-17 against a live Cloud project: `global` serves the current
# Gemini generation for ordinary requests and opens NO Live session at all —
# every attempt closes with 1008, including the gemini-live-* id its own
# catalogue lists there. Regional endpoints do open it. One location therefore
# cannot drive both tiers, and the split below is what stops a user from having
# to choose between a current brain and a working voice.


def test_an_explicit_realtime_region_always_wins() -> None:
    settings = _project("p", location="global", realtime_location="europe-west4")
    assert settings.for_realtime().location == "europe-west4"
    assert settings.location == "global", "the chat endpoint must be untouched"


def test_a_regional_location_is_used_for_live_as_is() -> None:
    """No second setting needed when the user already named a region."""
    settings = _project("p", location="europe-west4")
    assert settings.for_realtime().location == "europe-west4"


def test_a_global_location_falls_back_to_a_region_for_live() -> None:
    """Otherwise the socket would be opened where nothing answers."""
    settings = _project("p", location="global")
    resolved = settings.for_realtime()
    assert resolved.location == gg._REALTIME_FALLBACK_LOCATION
    assert resolved.location.lower() != "global"


def test_the_fallback_keeps_the_rest_of_the_project_identical() -> None:
    settings = _project("p", location="global", sa="~/.config/jarvis/vertex-sa.json")
    resolved = settings.for_realtime()
    assert resolved.project == "p"
    assert resolved.service_account_path == "~/.config/jarvis/vertex-sa.json"
    assert resolved.configured is True


def test_the_region_fallback_is_announced(caplog) -> None:
    """A silent region change is a data-residency decision nobody asked for."""
    import logging

    with caplog.at_level(logging.WARNING, logger="jarvis.google_genai"):
        _project("p", location="global").for_realtime()
    assert any(gg._REALTIME_FALLBACK_LOCATION in record.message for record in caplog.records), (
        "the fallback must say which region it picked"
    )


# ── kwargs assembly: the two shapes never mix ────────────────────────────────


def test_express_mode_sends_the_key_and_no_project() -> None:
    kwargs = gg._client_kwargs("AQ.express", "vertex", None, _project(None))
    assert kwargs == {"api_key": "AQ.express", "vertexai": True}


def test_project_mode_sends_project_and_location_and_no_key() -> None:
    """The SDK rejects api_key together with project/location — so must we.

    Leaking the key into this shape is not a cosmetic issue: it is a hard
    construction error, i.e. every Vertex call on a correctly configured Cloud
    project would die before it left the process.
    """
    kwargs = gg._client_kwargs("AQ.ignored", "vertex", None, _project("my-proj", "europe-west4"))
    assert kwargs == {
        "vertexai": True,
        "project": "my-proj",
        "location": "europe-west4",
    }
    assert "api_key" not in kwargs


def test_aistudio_route_is_untouched_by_the_project_settings() -> None:
    """Configuring a Cloud project must not change the AI Studio path at all."""
    kwargs = gg._client_kwargs("AIza-studio", "aistudio", None, _project("my-proj"))
    assert kwargs == {"api_key": "AIza-studio"}


def test_http_options_survive_both_shapes() -> None:
    opts = object()
    express = gg._client_kwargs("AQ.k", "vertex", opts, _project(None))
    project = gg._client_kwargs("", "vertex", opts, _project("p"))
    assert express["http_options"] is opts
    assert project["http_options"] is opts


# ── project settings are read defensively ────────────────────────────────────


def test_settings_fall_back_to_express_mode_when_config_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom():
        raise RuntimeError("config mid-boot")

    monkeypatch.setattr(cfg, "load_config", _boom)
    settings = gg.vertex_project_settings()
    assert settings.configured is False
    assert settings.location == "global"


def test_blank_project_and_location_normalise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace in a hand-edited TOML must not become a real project id."""
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: SimpleNamespace(
            google=SimpleNamespace(
                vertex_project="   ", vertex_location="", service_account_path=" "
            )
        ),
    )
    settings = gg.vertex_project_settings()
    assert settings.project is None
    assert settings.configured is False
    assert settings.location == "global"
    assert settings.service_account_path is None


# ── service account export: only when the file is really there ───────────────


def test_service_account_is_exported_when_the_file_exists(tmp_path, _no_ambient_adc) -> None:
    sa = tmp_path / "vertex-sa.json"
    sa.write_text("{}", encoding="utf-8")
    gg._export_service_account(str(sa))
    import os

    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == str(sa)


def test_missing_service_account_file_leaves_adc_alone(tmp_path, _no_ambient_adc) -> None:
    """A pointer at a missing file makes google-auth fail outright.

    Leaving the ambient chain (gcloud login, workload identity) to answer is
    strictly better than breaking it with a dead path.
    """
    gg._export_service_account(str(tmp_path / "absent.json"))
    import os

    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


def test_an_env_the_user_set_is_never_overwritten(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/user/choice.json")
    sa = tmp_path / "ours.json"
    sa.write_text("{}", encoding="utf-8")
    gg._export_service_account(str(sa))
    import os

    assert os.environ["GOOGLE_APPLICATION_CREDENTIALS"] == "/user/choice.json"


# ── the pinned client build ──────────────────────────────────────────────────


class _StubGenaiModule:
    """Stands in for ``google.genai``, recording the kwargs it was built with."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        outer = self

        class Client:
            def __init__(self, **kwargs):
                outer.calls.append(kwargs)

        self.Client = Client


@pytest.fixture
def stub_genai(monkeypatch: pytest.MonkeyPatch) -> _StubGenaiModule:
    stub = _StubGenaiModule()
    google_pkg = SimpleNamespace(genai=stub)
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", stub)
    return stub


def _sans_transport(call: dict) -> dict:
    """The auth/endpoint shape of a build, without the shared transport args.

    Every builder hands the client the process-wide TLS context via
    ``http_options`` (see the TLS tests below); the shape tests here are about
    which credential travels to which host and read past that.
    """
    return {k: v for k, v in call.items() if k != "http_options"}


def test_build_vertex_client_never_probes(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    """An ``AIza`` key would classify as AI Studio — pinning must ignore that.

    This is the whole reason the dedicated family exists: a Cloud API key
    restricted to aiplatform wears the AI Studio shape, so any shape- or
    probe-based decision sends it to the wrong host.
    """

    def _must_not_run(*_args, **_kwargs):  # pragma: no cover - guard
        raise AssertionError("build_vertex_client must not resolve a route")

    monkeypatch.setattr(gg, "resolve_google_key_route", _must_not_run)
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project(None))

    gg.build_vertex_client("AIza-cloud-restricted")

    assert [_sans_transport(c) for c in stub_genai.calls] == [
        {"api_key": "AIza-cloud-restricted", "vertexai": True}
    ]


def test_the_realtime_build_resolves_its_own_endpoint(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    """One config, two endpoints — the whole point of the split."""
    monkeypatch.setattr(
        gg,
        "vertex_project_settings",
        lambda: _project("prod-proj", location="global", realtime_location="us-central1"),
    )
    gg.build_vertex_client("")
    gg.build_vertex_client("", realtime=True)
    assert stub_genai.calls[0]["location"] == "global"
    assert stub_genai.calls[1]["location"] == "us-central1"


def test_build_vertex_client_uses_the_project_when_configured(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "us-central1"))
    gg.build_vertex_client("")
    assert [_sans_transport(c) for c in stub_genai.calls] == [
        {"vertexai": True, "project": "prod-proj", "location": "us-central1"}
    ]


def test_build_vertex_client_without_any_credential_says_so(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    """No key and no project is a setup problem, and the error must name both fixes."""
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project(None))
    with pytest.raises(RuntimeError) as exc:
        gg.build_vertex_client("")
    message = str(exc.value)
    assert "Vertex AI API key" in message
    assert "vertex_project" in message
    assert stub_genai.calls == []


@pytest.mark.asyncio
async def test_async_twin_builds_the_same_client(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project(None))
    await gg.build_vertex_client_async("AQ.key")
    assert [_sans_transport(c) for c in stub_genai.calls] == [
        {"api_key": "AQ.key", "vertexai": True}
    ]


def test_aistudio_build_does_not_read_the_project_config(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    """The overwhelmingly common path must not gain a config read per client."""

    def _must_not_run():  # pragma: no cover - guard
        raise AssertionError("the AI Studio path must not read Vertex settings")

    monkeypatch.setattr(gg, "vertex_project_settings", _must_not_run)
    gg.build_genai_client("AIza-studio", route="aistudio")
    assert [_sans_transport(c) for c in stub_genai.calls] == [{"api_key": "AIza-studio"}]


# ── "is Vertex configured" is one question with one answer ───────────────────


def test_a_stored_key_alone_counts_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    with cfg.override_provider_secrets({"vertex": "AQ.stored"}):
        assert cfg.vertex_credential_configured() is True


def test_a_project_alone_counts_as_configured() -> None:
    """The documented production setup stores no key at all."""
    config = SimpleNamespace(google=SimpleNamespace(vertex_project="prod-proj"))
    with cfg.override_provider_secrets({"vertex": None}):
        assert cfg.vertex_credential_configured(config) is True


def test_neither_key_nor_project_is_unconfigured() -> None:
    config = SimpleNamespace(google=SimpleNamespace(vertex_project=""))
    with cfg.override_provider_secrets({"vertex": None}):
        assert cfg.vertex_credential_configured(config) is False


# ── Application Default Credentials are resolved ONCE per process ────────────
#
# Measured 2026-08-17: a client that resolves ADC itself pays 5.3-8.5 s on a
# gcloud-login host (google-auth spawns ``gcloud config config-helper`` for a
# project id) plus the OAuth exchange — inside every Live handshake and on the
# first call of every brain instance. One shared credentials object took the
# handshake from 5.7-12.2 s to 1.1-1.3 s.


class _FakeCredentials:
    """The shape the SDK and the warm-up read: ``valid`` plus ``refresh``."""

    def __init__(self, *, valid: bool = False) -> None:
        self.valid = valid
        self.refreshes = 0

    def refresh(self, _request) -> None:
        self.refreshes += 1
        self.valid = True


@pytest.fixture
def adc(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A fake ADC loader that counts how often google-auth would have run."""
    state = {"loads": 0, "credentials": _FakeCredentials()}

    def _load():
        state["loads"] += 1
        return state["credentials"]

    monkeypatch.setattr(gg, "_load_application_default_credentials", _load)
    return state


def test_credentials_are_loaded_once_and_shared(adc: dict) -> None:
    first = gg.vertex_credentials()
    second = gg.vertex_credentials()
    assert first is second is adc["credentials"]
    assert adc["loads"] == 1, "google.auth.default() must run once per process"


def test_a_failed_resolution_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A login that appears later must be picked up, not shadowed by a miss."""
    attempts = {"n": 0}

    def _boom():
        attempts["n"] += 1
        raise RuntimeError("no ADC on this host")

    monkeypatch.setattr(gg, "_load_application_default_credentials", _boom)
    assert gg.vertex_credentials() is None
    assert gg.cached_vertex_credentials() is None
    assert gg.vertex_credentials() is None
    assert attempts["n"] == 2


def test_the_project_client_is_handed_the_shared_credentials(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule, adc: dict
) -> None:
    """The whole point: no client resolves auth on its own any more."""
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "global"))
    gg.build_vertex_client("")
    gg.build_vertex_client("", realtime=True)
    assert [c["credentials"] for c in stub_genai.calls] == [adc["credentials"]] * 2
    assert adc["loads"] == 1


def test_an_express_key_client_never_touches_adc(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule, adc: dict
) -> None:
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project(None))
    gg.build_vertex_client("AQ.express")
    assert "credentials" not in stub_genai.calls[0]
    assert adc["loads"] == 0


@pytest.mark.asyncio
async def test_a_cold_cache_is_never_filled_on_the_event_loop_by_the_sync_build(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule, adc: dict
) -> None:
    """On a running loop the sync build must not block; the SDK's lazy path stays."""
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "global"))
    gg.build_vertex_client("")
    assert adc["loads"] == 0
    assert "credentials" not in stub_genai.calls[0]


@pytest.mark.asyncio
async def test_the_async_build_resolves_a_cold_cache_off_the_loop(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule, adc: dict
) -> None:
    """The Live handshake path: pays the resolution once, in a thread, then shares."""
    import threading

    loop_thread = threading.get_ident()
    seen: list[int] = []
    real_load = gg._load_application_default_credentials

    def _load_recording_thread():
        seen.append(threading.get_ident())
        return real_load()

    monkeypatch.setattr(gg, "_load_application_default_credentials", _load_recording_thread)
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "global"))

    await gg.build_vertex_client_async("", realtime=True)
    await gg.build_vertex_client_async("", realtime=True)

    assert adc["loads"] == 1
    assert seen and all(t != loop_thread for t in seen), "resolution ran on the loop thread"
    assert [c["credentials"] for c in stub_genai.calls] == [adc["credentials"]] * 2


def test_warm_mints_the_token_once(monkeypatch: pytest.MonkeyPatch, adc: dict) -> None:
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "global"))
    assert gg.warm_vertex_credentials() is True
    assert adc["credentials"].refreshes == 1
    # Already valid: a second warm neither reloads nor re-mints.
    assert gg.warm_vertex_credentials() is True
    assert adc["loads"] == 1
    assert adc["credentials"].refreshes == 1


def test_warm_is_a_no_op_without_a_project(monkeypatch: pytest.MonkeyPatch, adc: dict) -> None:
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project(None))
    assert gg.warm_vertex_credentials() is False
    assert adc["loads"] == 0


def test_a_service_account_exported_later_gets_its_own_load(
    adc: dict, tmp_path, _no_ambient_adc
) -> None:
    """The cache is keyed by the credential file google-auth would read."""
    gg.vertex_credentials()
    sa = tmp_path / "sa.json"
    sa.write_text("{}", encoding="utf-8")
    gg._export_service_account(str(sa))
    gg.vertex_credentials()
    assert adc["loads"] == 2


# ── one TLS trust store per process ──────────────────────────────────────────
#
# Measured 2026-08-17: ``genai.Client()`` builds three SSL contexts and parses
# the certifi bundle for each — 1.34 s per client on the maintainer box, paid
# on the event loop for every Live session and by every brain instance. The
# SDK skips its own contexts when the caller supplies one.


def test_every_builder_hands_the_client_the_shared_tls_context(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "global"))
    gg.build_vertex_client("")
    gg.build_genai_client("AIza-studio", route="aistudio")
    ctx = gg._shared_tls_context()
    for call in stub_genai.calls:
        opts = call["http_options"]
        assert opts["client_args"]["verify"] is ctx
        assert opts["async_client_args"]["verify"] is ctx
        assert opts["async_client_args"]["ssl"] is ctx
    assert gg._shared_tls_context() is ctx, "built once, shared by every client"


def test_a_callers_transport_options_are_merged_not_replaced() -> None:
    """A dict with a timeout keeps it; a caller-supplied verify wins."""
    ctx = gg._shared_tls_context()
    merged = gg._with_shared_tls({"timeout": 1500, "client_args": {"verify": "mine"}})
    assert merged["timeout"] == 1500
    assert merged["client_args"]["verify"] == "mine"
    assert merged["async_client_args"]["verify"] is ctx
    assert merged["async_client_args"]["ssl"] is ctx


def test_a_typed_http_options_object_is_copied_with_the_context() -> None:
    class _Typed:
        client_args = None
        async_client_args = {"ssl": "theirs"}

        def model_copy(self, *, update):
            copy = _Typed()
            for key, value in update.items():
                setattr(copy, key, value)
            return copy

    ctx = gg._shared_tls_context()
    typed = gg._with_shared_tls(_Typed())
    assert typed.client_args["verify"] is ctx
    assert typed.async_client_args["ssl"] == "theirs"
    assert typed.async_client_args["verify"] is ctx


def test_an_unbuildable_context_leaves_the_options_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom():
        raise RuntimeError("no certifi here")

    monkeypatch.setattr(gg, "_shared_tls_context", _boom)
    assert gg._with_shared_tls(None) is None
    opts = {"timeout": 5}
    assert gg._with_shared_tls(opts) is opts


def test_the_vertex_warm_up_also_builds_the_trust_store(
    monkeypatch: pytest.MonkeyPatch, adc: dict
) -> None:
    """One warm, both process-wide costs: credentials AND the TLS context."""
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project("prod-proj", "global"))
    assert gg._TLS_CONTEXT is None
    assert gg.warm_vertex_credentials() is True
    assert gg._TLS_CONTEXT is not None


def test_the_transport_warm_is_provider_agnostic() -> None:
    assert gg._TLS_CONTEXT is None
    assert gg.warm_shared_transport() is True
    assert gg._TLS_CONTEXT is gg._shared_tls_context()


@pytest.mark.asyncio
async def test_the_async_builders_build_a_cold_trust_store_off_the_loop(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    """The first client of the process must not parse the CA bundle on the loop."""
    import threading

    loop_thread = threading.get_ident()
    built_on: list[int] = []
    real_build = gg._shared_tls_context

    def _recording_build():
        built_on.append(threading.get_ident())
        return real_build()

    monkeypatch.setattr(gg, "_shared_tls_context", _recording_build)
    monkeypatch.setattr(gg, "vertex_project_settings", lambda: _project(None))

    await gg.build_genai_client_async("AIza-studio", route="aistudio")
    await gg.build_vertex_client_async("AQ.express")

    assert built_on, "the context was never built"
    assert built_on[0] != loop_thread, "the cold build ran on the loop thread"
    ctx = stub_genai.calls[0]["http_options"]["client_args"]["verify"]
    assert stub_genai.calls[1]["http_options"]["client_args"]["verify"] is ctx
