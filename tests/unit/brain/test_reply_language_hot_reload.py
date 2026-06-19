"""Hot-reload: a ``brain.reply_language`` config change applies to the live
BrainManager on the next turn — no restart.

This is the missing subscriber the codebase lacked. Step 3 of the Jarvis
Control API build: the allowlist promises ``brain.reply_language`` is SAFE and
``needs_restart=False``, so a mutation through ``AtomicConfigWriter`` (which
dispatches ``ConfigReloaded``) must reach ``BrainManager.set_reply_language``
without the user restarting Jarvis. Without this, "switch your language to
English" would silently take effect only after a restart.
"""
from __future__ import annotations

import time
from uuid import uuid4

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import load_config
from jarvis.core.events import ConfigReloaded


def _manager_with_bus(bus: EventBus, reply_language: str) -> BrainManager:
    """A BrainManager with __init__ bypassed (mirrors test_reply_language._manager)
    plus a live bus reference for the reload subscription."""
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = ""
    m._reply_language = reply_language
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    m._config = cfg
    m._bus = bus
    return m


def _config_reloaded(*keys: str) -> ConfigReloaded:
    return ConfigReloaded(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        source_layer="self_mod",
        changed_keys=tuple(keys),
    )


async def test_reply_language_change_applies_live(monkeypatch, tmp_path) -> None:
    target = tmp_path / "jarvis.toml"
    target.write_text('[brain]\nreply_language = "en"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(target))

    bus = EventBus()
    manager = _manager_with_bus(bus, "de")
    manager.attach_to_bus()
    assert manager.reply_language == "de"

    await bus.publish(_config_reloaded("brain.reply_language"))

    assert manager.reply_language == "en"
    assert "English" in manager._build_system_prompt()


async def test_unrelated_reload_leaves_language_untouched(monkeypatch, tmp_path) -> None:
    target = tmp_path / "jarvis.toml"
    target.write_text('[brain]\nreply_language = "en"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(target))

    bus = EventBus()
    manager = _manager_with_bus(bus, "de")
    manager.attach_to_bus()

    await bus.publish(_config_reloaded("ui.theme"))

    # A theme change must not silently re-pin the reply language.
    assert manager.reply_language == "de"


async def test_garbage_config_value_falls_back_to_auto(monkeypatch, tmp_path) -> None:
    target = tmp_path / "jarvis.toml"
    target.write_text('[brain]\nreply_language = "klingon"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(target))

    bus = EventBus()
    manager = _manager_with_bus(bus, "de")
    manager.attach_to_bus()

    # Must not raise (ValueError from set_reply_language would break the bus);
    # an unknown code normalises to "auto" rather than wedging the subscriber.
    await bus.publish(_config_reloaded("brain.reply_language"))

    assert manager.reply_language == "auto"
