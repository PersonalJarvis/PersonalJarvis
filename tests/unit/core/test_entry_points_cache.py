"""The entry-point catalogue is read from disk once, not once per lookup.

``importlib.metadata.entry_points()`` is a sweep over every installed
distribution, and plugin resolution asks per candidate from the asyncio event
loop — the combination produced a captured 16.5 s loop stall with
``entry_points()`` at the bottom of the stack. These tests pin the caching that
removed it, including the part that is easy to lose: that a *second* group does
not silently reuse the first group's answer.
"""

from __future__ import annotations

import threading
from importlib import metadata

import pytest

from jarvis.core import entry_points as ep_module


class _CountingSweep:
    """Stands in for ``metadata.entry_points``, counting real reads per group."""

    def __init__(self, catalogue: dict[str, list[str]]) -> None:
        self._catalogue = catalogue
        self.calls: list[str] = []

    def __call__(self, *, group: str) -> list[metadata.EntryPoint]:
        self.calls.append(group)
        return [
            metadata.EntryPoint(name=name, value="mod:Cls", group=group)
            for name in self._catalogue.get(group, [])
        ]


@pytest.fixture
def sweep(monkeypatch: pytest.MonkeyPatch) -> _CountingSweep:
    counting = _CountingSweep(
        {"jarvis.stt": ["groq-api", "faster-whisper"], "jarvis.tts": ["piper"]}
    )
    monkeypatch.setattr(metadata, "entry_points", counting)
    ep_module.invalidate()
    return counting


def test_repeated_lookups_hit_the_disk_once(sweep: _CountingSweep) -> None:
    """The whole point: N lookups, one sweep."""
    for _ in range(25):
        names = [ep.name for ep in ep_module.entry_points_for("jarvis.stt")]

    assert names == ["groq-api", "faster-whisper"]
    assert sweep.calls == ["jarvis.stt"]


def test_each_group_is_cached_separately(sweep: _CountingSweep) -> None:
    """A per-group cache must not answer one group from another's entry."""
    stt = [ep.name for ep in ep_module.entry_points_for("jarvis.stt")]
    tts = [ep.name for ep in ep_module.entry_points_for("jarvis.tts")]

    assert stt == ["groq-api", "faster-whisper"]
    assert tts == ["piper"]
    assert sweep.calls == ["jarvis.stt", "jarvis.tts"]


def test_unknown_group_is_empty_and_still_cached(sweep: _CountingSweep) -> None:
    """A group nobody declared must not re-sweep on every miss."""
    assert ep_module.entry_points_for("jarvis.nonexistent") == ()
    assert ep_module.entry_points_for("jarvis.nonexistent") == ()

    assert sweep.calls == ["jarvis.nonexistent"]


def test_invalidate_goes_back_to_disk(sweep: _CountingSweep) -> None:
    """The escape hatch for tests and in-process installs actually works."""
    ep_module.entry_points_for("jarvis.stt")
    ep_module.invalidate()
    ep_module.entry_points_for("jarvis.stt")

    assert sweep.calls == ["jarvis.stt", "jarvis.stt"]


def test_result_is_immutable(sweep: _CountingSweep) -> None:
    """Callers share one cached object, so it must not be a mutable list.

    ``list_plugins`` and the STT loader both iterate the result; if one of them
    could sort or pop in place, the next caller would get a mutated catalogue.
    """
    assert isinstance(ep_module.entry_points_for("jarvis.stt"), tuple)


def test_concurrent_first_readers_sweep_once(sweep: _CountingSweep) -> None:
    """Boot resolves plugins from several threads at once.

    That is precisely when the sweep is at its most expensive (cold file cache),
    so the fill is locked. Without the lock each thread pays for its own.
    """
    barrier = threading.Barrier(8)
    results: list[tuple[str, ...]] = []
    lock = threading.Lock()

    def read() -> None:
        barrier.wait()
        names = tuple(ep.name for ep in ep_module.entry_points_for("jarvis.stt"))
        with lock:
            results.append(names)

    threads = [threading.Thread(target=read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sweep.calls == ["jarvis.stt"]
    assert results == [("groq-api", "faster-whisper")] * 8


def test_stt_loader_uses_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The captured stall came from the STT loader — pin that it is on the cache.

    Guards against a future edit reinstating a direct ``entry_points()`` call in
    ``jarvis.plugins.stt``, which is what made a dictation start cost seconds.
    """
    from jarvis.plugins import stt as stt_module

    counting = _CountingSweep({"jarvis.stt": ["groq-api"]})
    monkeypatch.setattr(metadata, "entry_points", counting)
    ep_module.invalidate()

    for _ in range(10):
        stt_module._load_provider_class("no-such-provider")

    assert counting.calls == ["jarvis.stt"]
