"""The Board bio follows the background billing rule.

Once a subscription is connected the bio is written on a subscription or a
free local model; when neither is usable it waits for the next board event and
never falls back to a per-token key. A key-only install keeps the frontier
chain it always had.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jarvis.board import bio_brain
from jarvis.board.profile import BioGenerator, BioStore
from jarvis.board.store import BoardStore
from jarvis.brain import background_policy as policy
from jarvis.brain import resolver
from jarvis.core.config import JarvisConfig


class _Brain:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRegistry:
    """Records every instantiation; a keyed one in subscription mode is the bug."""

    def __init__(
        self,
        available: list[str],
        connected: tuple[str, ...] = (),
        fail: tuple[str, ...] = (),
        no_probe: tuple[str, ...] = (),
    ) -> None:
        self._available = list(available)
        self._connected = set(connected)
        self._fail = set(fail)
        self._no_probe = set(no_probe)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def available(self) -> list[str]:
        return list(self._available)

    def get_class(self, name: str) -> type:
        connected = name in self._connected
        attrs: dict[str, Any] = {"native_system_prompt": True}
        if name not in self._no_probe:
            attrs["subscription_connected"] = staticmethod(lambda: connected)
        return type("FakeBrainClass", (), attrs)

    def instantiate(self, name: str, **kwargs: Any) -> _Brain:
        self.calls.append((name, dict(kwargs)))
        if name in self._fail:
            raise RuntimeError("not instantiable")
        return _Brain(name)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    policy.reset_for_tests()
    resolver._reset_for_tests()
    yield
    policy.reset_for_tests()
    resolver._reset_for_tests()


def _world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    available: list[str],
    connected: tuple[str, ...] = (),
    fail: tuple[str, ...] = (),
    no_probe: tuple[str, ...] = (),
) -> _FakeRegistry:
    """A provider in ``no_probe`` has no login probe: its login is unknown."""
    registry = _FakeRegistry(available, connected, fail, no_probe)
    monkeypatch.setattr(resolver, "_get_registry", lambda: registry)
    policy._probe_override = lambda name: None if name in no_probe else name in connected
    return registry


def _frontier_must_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("subscription mode walked the keyed frontier chain")

    monkeypatch.setattr(resolver, "resolve_frontier_brain", _forbidden)


def _keyed(names: list[str]) -> list[str]:
    return [name for name in names if policy._billing(name) == "api"]


def test_key_only_install_keeps_the_frontier_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _world(monkeypatch, available=["openai", "claude-cli", "ollama"])
    frontier = _Brain("openai")
    monkeypatch.setattr(resolver, "resolve_frontier_brain", lambda cfg, **kw: frontier)

    assert bio_brain.resolve_bio_brain(JarvisConfig()) is frontier
    assert registry.calls == []


def test_connected_subscription_writes_the_bio_and_no_key_is_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _world(
        monkeypatch,
        available=["openai", "gemini", "claude-api", "claude-cli", "ollama"],
        connected=("claude-cli",),
    )
    _frontier_must_not_run(monkeypatch)
    cfg = JarvisConfig()
    cfg.brain.primary = "openai"

    brain = bio_brain.resolve_bio_brain(cfg)

    assert brain.name == "claude-cli"
    assert registry.names() == ["claude-cli"]
    kwargs = registry.calls[0][1]
    assert kwargs["structured_prompts"] is True
    assert kwargs["cli_timeout_s"] == bio_brain.BIO_TIMEOUT_S


def test_signed_out_subscription_falls_back_to_a_local_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy.note_connected("claude-cli")  # seen connected earlier, signed out now
    registry = _world(
        monkeypatch, available=["openai", "gemini", "claude-cli", "ollama"],
    )
    _frontier_must_not_run(monkeypatch)

    brain = bio_brain.resolve_bio_brain(JarvisConfig())

    assert brain.name == "ollama"
    assert _keyed(registry.names()) == []


def test_no_usable_subscription_defers_without_touching_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy.note_connected("claude-cli")
    registry = _world(
        monkeypatch, available=["openai", "gemini", "claude-api", "claude-cli"],
    )
    _frontier_must_not_run(monkeypatch)

    with pytest.raises(policy.BackgroundDeferred):
        bio_brain.resolve_bio_brain(JarvisConfig())
    assert registry.calls == []


def test_a_refused_subscription_does_not_hide_the_next_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """antigravity comes first by card order and has no login probe; with an
    API-key slot and an unknown login the policy refuses it. The bio must still
    reach the signed-in claude-cli instead of deferring."""
    registry = _world(
        monkeypatch,
        available=["openai", "antigravity", "claude-cli"],
        connected=("claude-cli",),
        no_probe=("antigravity",),
    )
    _frontier_must_not_run(monkeypatch)

    brain = bio_brain.resolve_bio_brain(JarvisConfig())

    assert brain.name == "claude-cli"
    assert registry.names() == ["claude-cli"]


def test_every_allowed_subscription_is_tried_before_deferring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _world(
        monkeypatch,
        available=["openai", "grok-build", "claude-cli"],
        connected=("claude-cli",),
        no_probe=("grok-build",),
        fail=("grok-build",),
    )
    _frontier_must_not_run(monkeypatch)

    brain = bio_brain.resolve_bio_brain(JarvisConfig())

    assert brain.name == "claude-cli"
    assert registry.names() == ["grok-build", "claude-cli"]


def test_override_is_the_only_way_the_bio_bills_a_key_while_subscribed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The override is the user's deliberate pin and is honoured even when it
    names a keyed provider; without it the same install never touches a key."""
    registry = _world(
        monkeypatch, available=["openai", "claude-cli"], connected=("claude-cli",),
    )
    _frontier_must_not_run(monkeypatch)
    assert policy._billing("openai") == "api"

    assert bio_brain.resolve_bio_brain(JarvisConfig()).name == "claude-cli"
    assert _keyed(registry.names()) == []

    registry.calls.clear()
    cfg = JarvisConfig()
    cfg.board.bio.override_provider = "openai"
    cfg.board.bio.override_model = "picked-model"

    brain = bio_brain.resolve_bio_brain(cfg)

    assert brain.name == "openai"
    assert registry.calls == [("openai", {"model": "picked-model"})]


def test_broken_override_falls_through_to_the_subscription_not_to_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _world(
        monkeypatch,
        available=["openai", "gemini", "claude-cli"],
        connected=("claude-cli",),
        fail=("openai",),
    )
    _frontier_must_not_run(monkeypatch)
    cfg = JarvisConfig()
    cfg.board.bio.override_provider = "openai"

    brain = bio_brain.resolve_bio_brain(cfg)

    assert brain.name == "claude-cli"
    assert registry.names() == ["openai", "claude-cli"]


@pytest.mark.asyncio
async def test_generator_skips_a_deferred_bio_without_any_key_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy.note_connected("claude-cli")
    registry = _world(monkeypatch, available=["openai", "gemini", "claude-cli"])
    _frontier_must_not_run(monkeypatch)
    cfg = JarvisConfig()
    db = tmp_path / "personal.db"
    bio_store = BioStore(db)
    gen = BioGenerator(
        brain_resolver=lambda: bio_brain.resolve_bio_brain(cfg),
        store=BoardStore(db),
        bio_store=bio_store,
    )

    assert await gen.generate_bio(triggered_by="milestone:first_mcp") is None
    assert bio_store.latest() is None
    assert registry.calls == []
    assert gen.last_skip_reason and "subscription" in gen.last_skip_reason


def test_retired_cold_start_key_still_loads() -> None:
    cfg = JarvisConfig.model_validate({"board": {"bio": {"cold_start_min_days": 3}}})
    assert cfg.board.bio.override_provider is None
    assert "cold_start_min_days" not in type(cfg.board.bio).model_fields
