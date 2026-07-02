"""Boot-time wake-model prefetch — hand-over cache contract.

TTU iteration 10 (docs/diagnostics/BOOT-TTU-NOTES.md): the fast-first wake
model load (~3.1 s) is started in a daemon thread right after the UI shell is
served, and ``FasterWhisperProvider._ensure_model`` ADOPTS the loaded engine
when its exact key matches. Contracts pinned here:

- adoption returns the SAME object the prefetch loaded (no double load),
- the cache entry is single-use (popped), so ``recover()`` always rebuilds a
  FRESH engine and can never re-adopt a possibly wedged prefetch (AP-24),
- a key mismatch or a failed prefetch falls back to the normal lazy load,
- an in-flight prefetch of the same key is awaited instead of double-loaded.
"""
from __future__ import annotations

import threading

import pytest

import jarvis.plugins.stt.fwhisper as fw
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider, prefetch_model


@pytest.fixture(autouse=True)
def _clean_cache():
    with fw._PREFETCH_LOCK:
        fw._PREFETCH_EVENTS.clear()
        fw._PREFETCHED_MODELS.clear()
    yield
    with fw._PREFETCH_LOCK:
        fw._PREFETCH_EVENTS.clear()
        fw._PREFETCHED_MODELS.clear()


def _provider(model: str = "base") -> FasterWhisperProvider:
    return FasterWhisperProvider(
        model=model, device="cpu", compute_type="int8", cpu_threads=2
    )


def test_ensure_model_adopts_prefetched_engine(monkeypatch) -> None:
    loads: list[tuple] = []
    sentinel = object()

    def _fake_new(name, device, compute, threads=0):  # noqa: ANN001
        loads.append((name, device, compute, threads))
        return sentinel

    monkeypatch.setattr(fw, "_new_whisper_model", _fake_new)
    assert prefetch_model("base", "cpu", "int8", 2) is True

    p = _provider()
    p._ensure_model()
    assert p._model is sentinel, "provider must adopt the prefetched engine"
    assert len(loads) == 1, "the weights must be loaded exactly once"


def test_recover_after_adoption_rebuilds_fresh(monkeypatch) -> None:
    objs = [object(), object()]
    loads: list[int] = []

    def _fake_new(name, device, compute, threads=0):  # noqa: ANN001
        loads.append(1)
        return objs[len(loads) - 1]

    monkeypatch.setattr(fw, "_new_whisper_model", _fake_new)
    prefetch_model("base", "cpu", "int8", 2)

    p = _provider()
    p._ensure_model()
    assert p._model is objs[0]
    p.recover()  # wedged mid-session -> the cache must NOT hand the old one back
    p._ensure_model()
    assert p._model is objs[1], "recover() must rebuild fresh, not re-adopt"


def test_key_mismatch_falls_back_to_lazy_load(monkeypatch) -> None:
    prefetched = object()
    own = object()
    calls: list[str] = []

    def _fake_new(name, device, compute, threads=0):  # noqa: ANN001
        calls.append(name)
        return prefetched if name == "small" else own

    monkeypatch.setattr(fw, "_new_whisper_model", _fake_new)
    prefetch_model("small", "cpu", "int8", 2)  # different model than the provider

    p = _provider(model="base")
    p._ensure_model()
    assert p._model is own
    with fw._PREFETCH_LOCK:
        assert ("small", "cpu", "int8", 2) in fw._PREFETCHED_MODELS, (
            "a mismatched prefetch must stay cached, not be consumed"
        )


def test_failed_prefetch_degrades_to_lazy_load(monkeypatch) -> None:
    own = object()

    def _boom(name, device, compute, threads=0):  # noqa: ANN001
        raise RuntimeError("no faster_whisper on this host")

    monkeypatch.setattr(fw, "_new_whisper_model", _boom)
    assert prefetch_model("base", "cpu", "int8", 2) is False

    monkeypatch.setattr(fw, "_new_whisper_model", lambda *a, **k: own)
    p = _provider()
    p._ensure_model()
    assert p._model is own


def test_in_flight_prefetch_is_awaited_not_double_loaded(monkeypatch) -> None:
    release = threading.Event()
    sentinel = object()
    loads: list[int] = []

    def _slow_new(name, device, compute, threads=0):  # noqa: ANN001
        loads.append(1)
        assert release.wait(timeout=10)
        return sentinel

    monkeypatch.setattr(fw, "_new_whisper_model", _slow_new)
    t = threading.Thread(
        target=prefetch_model, args=("base", "cpu", "int8", 2), daemon=True
    )
    t.start()
    # Give the prefetch thread time to claim the key and enter the slow load.
    for _ in range(100):
        with fw._PREFETCH_LOCK:
            claimed = ("base", "cpu", "int8", 2) in fw._PREFETCH_EVENTS
        if claimed and loads:
            break
        threading.Event().wait(0.01)

    p = _provider()
    consumer = threading.Thread(target=p._ensure_model, daemon=True)
    consumer.start()
    release.set()
    consumer.join(timeout=10)
    t.join(timeout=10)
    assert p._model is sentinel
    assert len(loads) == 1, "consumer must wait for the in-flight load, not duplicate it"


def test_second_prefetch_call_is_noop(monkeypatch) -> None:
    loads: list[int] = []
    monkeypatch.setattr(
        fw, "_new_whisper_model", lambda *a, **k: loads.append(1) or object()
    )
    assert prefetch_model("base", "cpu", "int8", 2) is True
    assert prefetch_model("base", "cpu", "int8", 2) is False
    assert len(loads) == 1
