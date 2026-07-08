"""The eval corpus must cover de/en/es and the five hard-input categories."""
from __future__ import annotations

from jarvis.speech.tts_eval.corpus import HARD_CORPUS, EvalItem, items_for_language


def test_all_three_languages_present():
    langs = {i.language for i in HARD_CORPUS}
    assert {"de", "en", "es"} <= langs


def test_every_hard_category_present_per_language():
    hard = {"numbers", "acronyms", "code", "long", "names"}
    for lang in ("de", "en", "es"):
        tags = set().union(*(set(i.tags) for i in items_for_language(lang)))
        missing = hard - tags
        assert not missing, f"{lang} missing hard categories: {missing}"


def test_items_have_nonempty_text_and_valid_language():
    for i in HARD_CORPUS:
        assert isinstance(i, EvalItem)
        assert i.text.strip()
        assert i.language in ("de", "en", "es")
        assert i.tags


def test_items_for_language_filters():
    de = items_for_language("de")
    assert de and all(i.language == "de" for i in de)


def test_ids_are_unique():
    ids = [i.id for i in HARD_CORPUS]
    assert len(ids) == len(set(ids))


def test_long_items_are_actually_long():
    # A "long" passage must be long enough to expose voice drift.
    for i in HARD_CORPUS:
        if "long" in i.tags:
            assert len(i.text) >= 200, (i.id, len(i.text))
