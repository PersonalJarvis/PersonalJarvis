"""An on-device provider may never present itself as usable before it is installed.

Field report 2026-07-29: the Test button on the Nemotron card answered "ready"
on a machine where the user had never installed anything. Root cause was one
conflation repeated in three places — ``auth_mode == "none"`` (no credential
needed) was read as "works":

* the provider TEST returned OK after merely CONSTRUCTING the provider, which
  for an on-device recogniser is deliberately cheap: the weights load on first
  use (AP-26), so a successful construction proves nothing;
* the section-health rollup reported the tier green for the same reason;
* the shared switch path (voice command, CLI, brain tool) checked only for a
  credential, so an uninstalled engine could be ACTIVATED.

Each is pinned below. The rule: readiness is proven against the disk, never
inferred from the absence of a key.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from jarvis.brain import app_control, provider_test
from jarvis.brain.provider_test import run_provider_test


@dataclass(frozen=True)
class _Spec:
    """Minimal stand-in for a keyless on-device provider card."""

    id: str = "nemotron-local"
    label: str = "Nemotron (on this machine)"
    tier: str = "stt"
    auth_mode: str = "none"
    secret_keys: tuple[str, ...] = ()
    optional: bool = False
    brain_switchable: bool = True


@dataclass(frozen=True)
class _Status:
    ready: bool
    detail: str
    model_label: str = "Nemotron 3.5 (streaming)"
    engine_installed: bool = False
    model_present: bool = False
    runtime: str = "sherpa-onnx"


class _NeverLoadedProvider:
    """What an on-device provider looks like before first use: an empty shell.

    Constructing it succeeds on ANY machine — that is the whole point of the
    lazy load, and the whole reason construction cannot stand in for a test.
    """

    name = "nemotron-local"

    async def transcribe(self, audio: Any) -> Any:
        raise AssertionError("the test must not reach inference when not installed")


NOT_INSTALLED = _Status(
    ready=False,
    detail=(
        "The local speech runtime is not installed yet. Install it from here "
        "to run this model on your own machine."
    ),
)
INSTALLED = _Status(
    ready=True,
    detail="Ready — Nemotron 3.5 (streaming) runs entirely on this machine.",
    engine_installed=True,
    model_present=True,
)


def _patch_status(monkeypatch: pytest.MonkeyPatch, status: _Status | None) -> None:
    """Point both call sites at one fake on-disk verdict."""
    import jarvis.speech.local_models as local_models

    monkeypatch.setattr(
        local_models, "local_status", lambda pid, **kw: status, raising=False
    )


def test_test_button_reports_not_configured_when_nothing_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reported bug: 'Test' said ready on an install with no engine.

    It must answer "not configured" with the sentence that says what to do —
    and must not even attempt inference, since there is nothing to run.
    """
    _patch_status(monkeypatch, NOT_INSTALLED)

    result = asyncio.run(
        run_provider_test(
            _Spec(),
            cfg=None,
            make_stt=lambda cfg, provider: _NeverLoadedProvider(),
        )
    )

    assert result.status == "not_configured"
    assert "not installed" in result.detail


def test_test_button_runs_real_inference_once_it_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the files present, "ok" must be earned by actually decoding.

    Construction alone used to be the whole test; now the model has to load and
    produce a transcript before the card is allowed to claim anything.
    """
    _patch_status(monkeypatch, INSTALLED)
    decoded: list[str] = []

    class _WorkingProvider:
        name = "nemotron-local"

        async def transcribe(self, audio: Any) -> Any:
            decoded.append("ran")

            class _T:
                text = ""

            return _T()

    result = asyncio.run(
        run_provider_test(
            _Spec(), cfg=None, make_stt=lambda cfg, provider: _WorkingProvider()
        )
    )

    assert decoded == ["ran"], "the test must actually run the model, not just build it"
    assert result.status == "ok"


def test_cloud_providers_are_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The local probe must never gate a provider that is not local at all."""
    _patch_status(monkeypatch, None)

    assert provider_test._local_readiness("groq-api") is None


def test_switch_refuses_an_uninstalled_local_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared lock: voice command, CLI and brain tool all pass through here."""
    _patch_status(monkeypatch, NOT_INSTALLED)
    monkeypatch.setattr(app_control, "get_spec", lambda pid: _Spec(), raising=False)

    result = asyncio.run(
        app_control.apply_provider_switch(
            "stt", "nemotron-local", cfg=None, persist=False
        )
    )

    assert result["ok"] is False
    assert result["error_kind"] == "not_installed"
    assert "not installed" in result["error"]


def test_switch_allows_it_once_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_status(monkeypatch, INSTALLED)
    monkeypatch.setattr(app_control, "get_spec", lambda pid: _Spec(), raising=False)
    switched: list[str] = []
    monkeypatch.setattr(
        app_control,
        "_switch_stt",
        lambda provider, **kw: switched.append(provider) or {"ok": True},
        raising=False,
    )

    result = asyncio.run(
        app_control.apply_provider_switch(
            "stt", "nemotron-local", cfg=None, persist=False
        )
    )

    assert result["ok"] is True
    assert switched == ["nemotron-local"]


def test_readiness_error_is_none_for_cloud_and_for_ready_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_status(monkeypatch, None)
    assert app_control.local_readiness_error(_Spec(id="groq-api")) is None

    _patch_status(monkeypatch, INSTALLED)
    assert app_control.local_readiness_error(_Spec()) is None

    _patch_status(monkeypatch, NOT_INSTALLED)
    assert app_control.local_readiness_error(_Spec()) == NOT_INSTALLED.detail
