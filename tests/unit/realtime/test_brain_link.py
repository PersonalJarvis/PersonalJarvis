"""The voice brain talks to the SAME Ollama as every other role.

Pins the address resolution of ``brain_link._ollama_base``: ``OLLAMA_HOST``
keeps the vendor's own convention, and without it the configured
``[brain.providers.ollama].base_url`` override reaches the voice brain too —
a remote setup no longer splits the voice brain from chat, wiki and ack.
"""

from __future__ import annotations

import pytest

from jarvis.brain import ollama_pull
from jarvis.realtime.local_server import brain_link


@pytest.fixture(autouse=True)
def _no_env_host(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)


def test_env_host_still_wins_and_is_normalized(monkeypatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "gpu-box:11434/")
    monkeypatch.setattr(
        ollama_pull, "server_root", lambda: pytest.fail("config must not be consulted")
    )
    assert brain_link._ollama_base() == "http://gpu-box:11434"


def test_without_env_the_shared_configured_root_is_used(monkeypatch) -> None:
    monkeypatch.setattr(ollama_pull, "server_root", lambda: "http://lan-box:11434/")
    assert brain_link._ollama_base() == "http://lan-box:11434"


def test_an_unreadable_config_falls_back_to_the_vendor_default(monkeypatch) -> None:
    def boom() -> str:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(ollama_pull, "server_root", boom)
    assert brain_link._ollama_base() == brain_link._OLLAMA_DEFAULT
