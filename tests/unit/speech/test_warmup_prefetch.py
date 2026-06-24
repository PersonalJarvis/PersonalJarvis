"""Wake-import prefetch: take the heavy OpenWakeWord/onnxruntime import off the
wake-critical Phase-A warm-up path.

Pins the contract:
* a successful import returns True;
* a missing openWakeWord (headless VPS base install) is a swallowed no-op, NOT
  an exception that breaks boot;
* the starter spawns a daemon thread when voice is enabled;
* the starter is a no-op when ``JARVIS_VOICE`` disables voice.
"""
from __future__ import annotations

import threading

from jarvis.speech.warmup_prefetch import (
    prefetch_wake_imports,
    start_wake_import_prefetch,
)


def test_prefetch_returns_true_on_successful_import() -> None:
    called: list[int] = []
    assert prefetch_wake_imports(lambda: called.append(1)) is True
    assert called == [1]


def test_prefetch_swallows_import_error_and_returns_false() -> None:
    def _boom() -> None:
        raise ImportError("no openwakeword on this host")

    # Must not raise — a headless VPS base install has no [desktop] extra.
    assert prefetch_wake_imports(_boom) is False


def test_start_spawns_daemon_thread_when_voice_enabled(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_VOICE", raising=False)
    done = threading.Event()

    thread = start_wake_import_prefetch(importer=done.set)

    assert thread is not None
    assert thread.daemon is True
    thread.join(timeout=2)
    assert done.is_set()


def test_start_is_noop_when_voice_disabled(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_VOICE", "0")
    called: list[int] = []

    thread = start_wake_import_prefetch(importer=lambda: called.append(1))

    assert thread is None
    assert called == []


def test_start_noop_for_off_and_false_tokens(monkeypatch) -> None:
    for token in ("off", "FALSE", " 0 "):
        monkeypatch.setenv("JARVIS_VOICE", token)
        assert start_wake_import_prefetch(importer=lambda: None) is None
