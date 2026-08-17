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
def _fresh_cache():
    gg.reset_route_cache()
    yield
    gg.reset_route_cache()


@pytest.fixture
def _no_ambient_adc(monkeypatch: pytest.MonkeyPatch):
    """Start from a host with no ``GOOGLE_APPLICATION_CREDENTIALS`` set."""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)


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

    assert stub_genai.calls == [{"api_key": "AIza-cloud-restricted", "vertexai": True}]


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
    assert stub_genai.calls == [
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
    assert stub_genai.calls == [{"api_key": "AQ.key", "vertexai": True}]


def test_aistudio_build_does_not_read_the_project_config(
    monkeypatch: pytest.MonkeyPatch, stub_genai: _StubGenaiModule
) -> None:
    """The overwhelmingly common path must not gain a config read per client."""

    def _must_not_run():  # pragma: no cover - guard
        raise AssertionError("the AI Studio path must not read Vertex settings")

    monkeypatch.setattr(gg, "vertex_project_settings", _must_not_run)
    gg.build_genai_client("AIza-studio", route="aistudio")
    assert stub_genai.calls == [{"api_key": "AIza-studio"}]


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
