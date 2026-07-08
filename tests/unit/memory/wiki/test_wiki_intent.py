"""Deterministic wiki-write intent (spec A1).

The de/en/es utterances below are speech-recognition input vocabulary /
fixtures under test (closed-list categories 3+4).
"""
import pytest

from jarvis.memory.wiki.intent import match_wiki_intent


@pytest.mark.parametrize("text", [
    "Schreib das ins Wiki",                                  # i18n-allow
    "Jarvis, schreib das bitte ins Wiki.",                   # i18n-allow
    "Merk dir das im Wiki",                                  # i18n-allow
    "Notier das im Wiki",                                    # i18n-allow
    "write that to the wiki",
    "save this in my wiki please",
    "guarda eso en la wiki",                                 # i18n-allow
])
def test_anaphoric_commands_match_with_no_inline_content(text):
    m = match_wiki_intent(text)
    assert m is not None
    assert m.content is None


@pytest.mark.parametrize(("text", "expected_fragment"), [
    ("Schreib ins Wiki, dass Joys Geburtstag am 14. August ist",  # i18n-allow
     "geburtstag"),
    ("Merk dir im Wiki: die VPS-IP ist jetzt statisch",           # i18n-allow
     "vps-ip"),
    ("save to the wiki that the deploy key rotated today",
     "deploy key"),
    ("anota en la wiki que el vuelo sale el viernes",             # i18n-allow
     "vuelo"),
])
def test_inline_content_is_extracted(text, expected_fragment):
    m = match_wiki_intent(text)
    assert m is not None
    assert m.content is not None
    assert expected_fragment in m.content.lower()


@pytest.mark.parametrize("text", [
    "Was steht im Wiki über Joy?",           # recall, not write  # i18n-allow
    "Wie funktioniert ein Wiki?",            # general question   # i18n-allow
    "what's in the wiki about the server?",
    "Merk dir das",                          # no wiki object     # i18n-allow
    "remember that for later",
    "Ich habe gestern einen Wiki-Artikel gelesen",  # mention     # i18n-allow
    "open the wiki tab",
    # Verb at the start + "wiki" in a SUBORDINATE/recall clause: the span
    # between the verb and the wiki-object carries a real word, so this is a
    # recall/conditional utterance, not a write command (precision gate).
    "Notiere mal, was im Wiki über die Serverkonfiguration steht",  # i18n-allow
    "Save me the trouble and tell me what's in the wiki about the server",
    "Schreib mir, wenn du fertig bist, damit ich es ins Wiki eintragen kann",  # i18n-allow
    "note down what the wiki says about the deploy key",
])
def test_non_write_utterances_do_not_match(text):
    assert match_wiki_intent(text) is None
