"""Reply-language pin: the UI-selected reply language must drive the brain.

Root cause this guards against (2026-05-25): the desktop "Languages" view let
the user pick a Reply Language (DE/EN/ES), but the value died in localStorage —
``_build_system_prompt`` hardcoded ``"Nutzer-Sprache: DE oder EN — antworte in
derselben."`` so Jarvis ignored the choice entirely (no Spanish, no hard pin).

These tests lock in that:
  * a pinned language emits a strong, language-named directive,
  * the directive lands in the system prompt as the LAST (highest-salience)
    block and overrides the otherwise German prompt,
  * "auto" preserves the bilingual mirror behaviour,
  * the legacy hardcoded fragment is gone.
"""
from __future__ import annotations

from jarvis.brain.manager import BrainManager, normalize_reply_language
from jarvis.core.config import load_config


def _manager(reply_language: str) -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
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
    return m


# ---------------------------------------------------------------- normalize


def test_normalize_accepts_known_languages() -> None:
    for code in ("auto", "de", "en", "es"):
        assert normalize_reply_language(code) == code


def test_normalize_lowercases_and_trims() -> None:
    assert normalize_reply_language("  EN ") == "en"


def test_normalize_falls_back_to_auto_for_garbage() -> None:
    assert normalize_reply_language("klingon") == "auto"
    assert normalize_reply_language("") == "auto"
    assert normalize_reply_language(None) == "auto"


# ---------------------------------------------------------------- directive


def test_directive_names_target_language() -> None:
    assert "English" in _manager("en")._reply_language_directive()
    assert "German" in _manager("de")._reply_language_directive()
    assert "Spanish" in _manager("es")._reply_language_directive()


def test_directive_is_mandatory_and_keeps_proper_nouns() -> None:
    d = _manager("en")._reply_language_directive()
    assert "MANDATORY" in d
    # the proper-noun carve-out the user explicitly asked for
    assert "Anthropic" in d


def test_auto_directive_mirrors_user_language() -> None:
    d = _manager("auto")._reply_language_directive()
    # auto = mirror; must mention all three supported languages, no hard pin
    assert "German" in d and "English" in d and "Spanish" in d
    assert "MANDATORY" not in d


def test_auto_directive_forbids_defaulting_to_german() -> None:
    """Auto mode must not let the German-heavy prompt pull replies to German.

    The whole system prompt above this directive is German; a soft "please
    mirror" line let the model anchor to German on clean English input. The
    directive must explicitly forbid defaulting to the prompt language while
    staying a soft mirror (no hard pin) so it remains byte-stable across turns.
    """
    d = _manager("auto")._reply_language_directive()
    assert "default" in d.lower()
    assert "English in English" in d  # the explicit per-language mirror rule
    assert "MANDATORY" not in d  # still not a hard pin (cache-stable contract)


# ---------------------------------------------------- system-prompt injection


def test_system_prompt_includes_pinned_directive() -> None:
    sp = _manager("es")._reply_language_directive()
    full = _manager("es")._build_system_prompt()
    assert sp in full


def test_pinned_directive_is_last_block() -> None:
    # Highest-salience position: nothing after the directive can re-bias the
    # output language back to the surrounding German prompt.
    full = _manager("en")._build_system_prompt()
    blocks = full.split("\n\n")
    assert "REPLY LANGUAGE" in blocks[-1]


def test_legacy_hardcoded_language_fragment_is_gone() -> None:
    full = _manager("en")._build_system_prompt()
    assert "antworte in derselben" not in full


# ----------------------------------------------------------------- setter


def test_set_reply_language_updates_runtime() -> None:
    m = _manager("de")
    m.set_reply_language("es")
    assert m._reply_language == "es"
    assert "Spanish" in m._build_system_prompt()


def test_set_reply_language_rejects_unknown() -> None:
    import pytest

    m = _manager("de")
    with pytest.raises(ValueError):
        m.set_reply_language("klingon")


def test_reply_language_property_reflects_value() -> None:
    assert _manager("en").reply_language == "en"
