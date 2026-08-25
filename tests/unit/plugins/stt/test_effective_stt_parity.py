"""The health panel and the pipeline must name the SAME recognizer.

The live failure this pins down (2026-08-25): the desktop app booted through a
shortcut aimed at a second Python install. ``jarvis`` was importable there via an
editable install, so the app started normally — but ``faster_whisper`` was not,
so the factory quietly crossed voice input to a cloud family. The health panel
asked a different question (``has a key?``) and went on reporting
``faster-whisper``. Nothing told the user local speech had stopped until the
cloud account ran out of credit and a dictation lost 24.3 s of speech.

One resolver answers "which recognizer is in front" for both sides, and these
tests are what keeps it one.
"""
from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
import jarvis.plugins.stt.fwhisper as fwhisper
from jarvis.core.config import ResolvedEndpoint, STTConfig


class _FakeCloudSTT:
    name = "groq-api"

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeLocalSTT:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _no_proxy(monkeypatch) -> None:
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda pid, **kw: ResolvedEndpoint(base_url=None, credential=None, via_proxy=False),
    )
    monkeypatch.setattr(fwhisper, "FasterWhisperProvider", _FakeLocalSTT)


def test_panel_reports_the_cloud_takeover_when_the_local_engine_is_absent(monkeypatch):
    """The resolver the panel reads must see the engine-missing crossing too.

    Reporting the configured name here is the whole defect: the user believes
    their words stay on this machine while they are being uploaded, and they get
    no warning until the borrowed cloud account fails.
    """
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: False)
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "key")

    assert stt_pkg.resolve_effective_stt_provider("faster-whisper") != "faster-whisper"


def test_panel_and_pipeline_agree_on_the_recognizer(monkeypatch):
    """Same input, same answer — whichever side of the app asks."""
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: False)
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloudSTT)
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "key")

    reported = stt_pkg.resolve_effective_stt_provider("faster-whisper")
    built = stt_pkg.build_stt_from_config(STTConfig(provider="faster-whisper"))

    assert reported != "faster-whisper" and isinstance(built, _FakeCloudSTT), (
        "The panel must not name a recognizer the factory refused to build."
    )


def test_installed_local_engine_is_reported_as_itself(monkeypatch):
    """No crossing, no noise: a working local install stays local on both sides."""
    _no_proxy(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: True)
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "key")

    assert stt_pkg.resolve_effective_stt_provider("faster-whisper") == "faster-whisper"
    assert isinstance(
        stt_pkg.build_stt_from_config(STTConfig(provider="faster-whisper")),
        _FakeLocalSTT,
    )


def test_the_reason_names_the_interpreter_not_the_host(monkeypatch):
    """Which Python is running IS the diagnosis — a host-level sentence hides it.

    Three of the four Python installs on the machine that produced this bug had
    the engine. "not installed on this host" sent the reader looking for a
    package that was demonstrably there.
    """
    import sys

    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: False)

    reason = stt_pkg.local_stt_unavailable_reason()

    assert sys.executable in reason, (
        "The reason must name the interpreter Jarvis is running in; without it "
        "the user cannot tell a missing package from the wrong shortcut."
    )


def test_no_reason_when_the_engine_is_present(monkeypatch):
    monkeypatch.setattr(stt_pkg, "_faster_whisper_installed", lambda: True)

    assert stt_pkg.local_stt_unavailable_reason() == ""
