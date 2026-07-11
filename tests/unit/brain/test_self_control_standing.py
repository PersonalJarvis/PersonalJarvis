"""The always-on self-control line must reach EVERY system prompt.

Forensic 2026-07-10: a keyword-free self-control utterance ("ich will dich
ab jetzt Edith rufen koennen") missed _SELF_CONTROL_PATTERN, so the router
LLM got no self-control guidance, answered in prose, and CLAIMED the wake-
word change without any tool call. The standing line closes that hole
without keyword whack-a-mole; these tests pin it.
"""  # i18n-allow: quoted German utterance under test
from __future__ import annotations

from jarvis.brain.manager import (
    _SELF_CONTROL_STANDING,
    BrainManager,
)
from jarvis.core.config import load_config


def _manager() -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = ""
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    m._active_turn_identity = None
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    m._config = cfg
    return m


def test_standing_line_present_without_keyword_directive() -> None:
    """No keyworded directive set — the standing line must be there anyway."""
    m = _manager()
    assert getattr(m, "_self_control_directive", "") == ""
    prompt = m._build_system_prompt()
    assert _SELF_CONTROL_STANDING in prompt


def test_standing_line_names_the_structured_path_and_forbids_claiming() -> None:
    # Flat registry tools (post-2026-07-11 rework): the line must name real
    # callable tool names, not the retired `app-command` umbrella interface.
    assert "brain-switch" in _SELF_CONTROL_STANDING
    assert "wake-word-set" in _SELF_CONTROL_STANDING
    low = _SELF_CONTROL_STANDING.lower()
    # The anti-"said it, did nothing" rule, in plain words.
    assert "never" in low
    assert "tool call" in low
    # Wake-phrase renames are the reproduced failure — must be named so a
    # keyword-free phrasing still maps.
    assert "wake" in low


def test_keyworded_directive_still_stacks_on_top() -> None:
    from jarvis.brain.manager import _SELF_CONTROL_DIRECTIVE

    m = _manager()
    m._self_control_directive = _SELF_CONTROL_DIRECTIVE
    prompt = m._build_system_prompt()
    assert _SELF_CONTROL_STANDING in prompt
    assert _SELF_CONTROL_DIRECTIVE in prompt
