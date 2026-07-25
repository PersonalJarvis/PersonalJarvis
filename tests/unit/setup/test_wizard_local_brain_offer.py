"""First-run wizard: the local-brain offer (first-sixty-seconds mandate).

A zero-key user with a running Ollama must be OFFERED the local path before
any key question; a host without one must never see the prompt or a probe
delay on the boot path (the probe lives inside the interactive step only).
"""

from __future__ import annotations

import pytest

from jarvis.setup import wizard


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """The probe must never hit a real server in tests."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def test_offer_skipped_when_no_local_server(monkeypatch) -> None:
    monkeypatch.setattr(wizard, "_ollama_reachable", lambda **kw: False)

    def _fail_ask(*a, **kw):  # pragma: no cover - defended path
        raise AssertionError("no prompt may render without a reachable server")

    monkeypatch.setattr(wizard, "_ask", _fail_ask)
    assert wizard._offer_local_brain() is False


def test_accepting_pins_ollama_as_primary(monkeypatch) -> None:
    from jarvis.core import config_writer

    monkeypatch.setattr(wizard, "_ollama_reachable", lambda **kw: True)
    monkeypatch.setattr(wizard, "_ask", lambda *a, **kw: "y")
    pinned: list[str] = []
    monkeypatch.setattr(config_writer, "set_brain_primary", lambda name: pinned.append(name))

    assert wizard._offer_local_brain() is True
    assert pinned == ["ollama"]


def test_declining_changes_nothing(monkeypatch) -> None:
    from jarvis.core import config_writer

    monkeypatch.setattr(wizard, "_ollama_reachable", lambda **kw: True)
    monkeypatch.setattr(wizard, "_ask", lambda *a, **kw: "n")
    pinned: list[str] = []
    monkeypatch.setattr(config_writer, "set_brain_primary", lambda name: pinned.append(name))

    assert wizard._offer_local_brain() is False
    assert pinned == []


def test_probe_failure_is_a_quiet_no(monkeypatch) -> None:
    """A refused connection is the NORMAL no-server state — never an error."""

    class _Refused:
        def get(self, *a, **kw):
            raise OSError("connection refused")

    import httpx

    monkeypatch.setattr(httpx, "get", _Refused().get)
    assert wizard._ollama_reachable(timeout_s=0.01) is False
