"""FasterWhisperProvider.is_warm — the boot-time ready signal.

The rolling-whisper wake poll loop and the heavy-backend gate wait on this
flag instead of poking the model while it loads (the 114.7 s load-cascade TTU
forensic, 2026-07-02). Contract: False until ``warm_up`` completes, True even
when the priming inference fails (the model object exists, lazy paths cover
the rest), back to False after ``recover()`` drops a wedged model.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.stt.fwhisper import FasterWhisperProvider


@pytest.fixture()
def provider(monkeypatch) -> FasterWhisperProvider:
    p = FasterWhisperProvider()

    def _fake_ensure() -> None:
        p._model = object()

    monkeypatch.setattr(p, "_ensure_model", _fake_ensure)
    monkeypatch.setattr(
        p, "_transcribe_sync", lambda *a, **k: None, raising=True
    )
    return p


def test_cold_provider_is_not_warm(provider: FasterWhisperProvider) -> None:
    assert provider.is_warm is False


def test_warm_up_sets_the_flag(provider: FasterWhisperProvider) -> None:
    provider.warm_up()
    assert provider.is_warm is True


def test_warm_up_sets_the_flag_even_if_priming_fails(
    provider: FasterWhisperProvider, monkeypatch
) -> None:
    def _boom(*a, **k):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("priming failed")

    monkeypatch.setattr(provider, "_transcribe_sync", _boom)
    provider.warm_up()
    # The model object exists, so pollers may transcribe (lazy paths cover the
    # rest) — leaving the flag False would park the wake poll loop forever.
    assert provider.is_warm is True


def test_recover_resets_the_flag(provider: FasterWhisperProvider) -> None:
    provider.warm_up()
    provider.recover()
    assert provider.is_warm is False
    assert provider._model is None
