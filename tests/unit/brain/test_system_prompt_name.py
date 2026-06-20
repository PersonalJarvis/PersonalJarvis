"""The configurable assistant name must flow into the brain system prompt.

User mandate 2026-05-29: renaming the assistant (e.g. to "Micron") must make it
call itself Micron instead of the hardcoded "Jarvis". These lock that the name
reaches ``_build_system_prompt`` — both the base prompt and, for a non-default
name, the prominent identity directive that overrides the persona files.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager
from jarvis.core.config import load_config


def _manager_with_name(*, wake_phrase: str = "Hey Jarvis") -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    cfg.trigger.wake_word.phrase = wake_phrase
    m._config = cfg
    return m


def test_default_name_keeps_jarvis_and_no_identity_directive() -> None:
    prompt = _manager_with_name(wake_phrase="Hey Jarvis")._build_system_prompt()
    assert "Du bist Jarvis" in prompt
    # "Jarvis" is the persona-file baseline (SOUL.md / JARVIS_PERSONA.md already
    # say "Jarvis"), so no identity-override directive is needed — emitting one
    # would produce the self-contradictory "Du heisst Jarvis — nicht Jarvis".
    assert "DEIN NAME IST" not in prompt


def test_wake_phrase_micron_makes_assistant_micron() -> None:
    prompt = _manager_with_name(wake_phrase="Micron")._build_system_prompt()
    assert "Du bist Micron" in prompt
    # The prominent identity directive overrides the persona files' "Jarvis".
    assert "DEIN NAME IST MICRON" in prompt
    assert "nicht Jarvis" in prompt


def test_wake_phrase_is_the_only_name_source() -> None:
    # "Hey Computer" wake → the assistant is "Computer"; there is no override.
    prompt = _manager_with_name(wake_phrase="Hey Computer")._build_system_prompt()
    assert "Du bist Computer" in prompt
    assert "DEIN NAME IST COMPUTER" in prompt
    assert "nicht Jarvis" in prompt
