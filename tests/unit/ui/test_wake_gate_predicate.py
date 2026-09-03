"""Which wake detectors the heavy backend waits for.

The boot gate holds the server, brain, MCP, workflows and the conductor behind
the wake model so the load the user is waiting for is not starved. It used to be
armed only when voice boot built a local Whisper, which left ``vosk_kws`` — the
engine that loads one Kaldi model per installed language — completely ungated:
on a cold boot the gate opened before the load began (BUG-214).

The predicate is a capability check, never an engine name (AP-21).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.ui.desktop_app import detector_gates_the_backend


def _pipeline(detector: Any, *, enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(_wake=detector, _openwakeword_enabled=enabled)


def test_a_detector_that_reports_warmth_gates_the_backend() -> None:
    """vosk_kws shape: it owns model loads the STT probes know nothing about."""
    assert detector_gates_the_backend(_pipeline(SimpleNamespace(is_warm=False))) is True
    # Still gates once warm — the caller reads warmth separately.
    assert detector_gates_the_backend(_pipeline(SimpleNamespace(is_warm=True))) is True


def test_a_detector_without_a_warm_signal_does_not_gate() -> None:
    """openWakeWord shape: nothing to wait for, so waiting would be a guess and
    the backend would pay a ceiling for no reason."""
    assert detector_gates_the_backend(_pipeline(SimpleNamespace())) is False


def test_a_detector_that_will_not_be_started_does_not_gate() -> None:
    """A second instance lowers ``_openwakeword_enabled``; it must never tax the
    primary instance's boot with a wait for a detector nobody starts."""
    pipeline = _pipeline(SimpleNamespace(is_warm=False), enabled=False)

    assert detector_gates_the_backend(pipeline) is False


@pytest.mark.parametrize(
    "pipeline",
    [
        SimpleNamespace(),  # no detector attribute at all
        SimpleNamespace(_wake=None, _openwakeword_enabled=True),  # voice off
        SimpleNamespace(_wake=SimpleNamespace(is_warm=False)),  # no enabled flag
    ],
)
def test_a_host_without_a_detector_releases_immediately(pipeline: Any) -> None:
    """Headless, voice-off and half-built pipelines keep today's behaviour: the
    backend starts at once rather than waiting out a ceiling."""
    assert detector_gates_the_backend(pipeline) is False
