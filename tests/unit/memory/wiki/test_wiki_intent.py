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


@pytest.mark.parametrize(("text", "expected_fragment"), [
    (
        "Kannst du bitte mein Wiki-System eintragen, dass ich morgen nach "  # i18n-allow
        "San Francisco reisen will?",  # i18n-allow: production transcript under test
        "san francisco",
    ),
    (
        "Kannst du bitte in mein Wikisystem eintragen, dass mein Zug um acht fährt?",  # i18n-allow
        "zug",
    ),
    (
        "Trag bitte ins Wiki ein, dass der Schlüssel heute rotiert wurde?",  # i18n-allow
        "schluessel",
    ),
    (
        "Could you please add to my wiki that the deploy key rotated today?",
        "deploy key",
    ),
    (
        "Can you please enter in my wiki that the train leaves at eight?",
        "train",
    ),
    (
        "¿Puedes anotar en mi wiki que el vuelo sale el viernes?",  # i18n-allow
        "vuelo",
    ),
])
def test_polite_question_commands_and_object_first_forms_match(
    text: str,
    expected_fragment: str,
) -> None:
    """Polite question grammar and terminal question marks remain writes."""
    match = match_wiki_intent(text)

    assert match is not None
    assert match.content is not None
    assert expected_fragment in match.content


@pytest.mark.parametrize("text", [
    "Schreib die letzte Transkription ins Wikisystem.",  # i18n-allow
    "Kannst du bitte die letzte Transkription in mein Wiki eintragen?",  # i18n-allow
    (
        "Wenn du die letzte Transkription anschaust, kannst du bitte etwas "  # i18n-allow
        "in dein Wikisystem eintragen?"  # i18n-allow: reported production wording
    ),
    "Please write the previous turn into my wiki.",
    "Anota la ultima transcripcion en mi wiki.",  # i18n-allow
])
def test_latest_transcript_reference_is_anaphoric(text: str) -> None:
    match = match_wiki_intent(text)

    assert match is not None
    assert match.content is None


def test_reported_obsidian_follow_up_resolves_locative_wiki_target() -> None:
    prior = "Was steht in meiner Obsidian-Wiki drin?"  # i18n-allow
    match = match_wiki_intent(
        (
            "Kannst du bitte einen Eintrag da eintragen, dass ich ziemlich "  # i18n-allow
            "genervt bin und dass ich in San Francisco "  # i18n-allow
            "wohne?"  # i18n-allow: production transcript under test
        ),
        prior_text=prior,
    )

    assert match is not None
    assert match.content is not None
    assert "genervt" in match.content
    assert "san francisco" in match.content


def test_entry_noun_before_explicit_wiki_target_is_control_syntax() -> None:
    match = match_wiki_intent(
        "Kannst du bitte einen Eintrag in mein Wiki eintragen, dass ich "  # i18n-allow
        "in San Francisco wohne?"  # i18n-allow: production syntax under test
    )

    assert match is not None
    assert match.content is not None
    assert match.content.startswith("ich in san francisco")


def test_locative_write_requires_immediate_wiki_context() -> None:
    text = "Kannst du bitte einen Eintrag da eintragen, dass ich morgen frei habe?"  # i18n-allow

    assert match_wiki_intent(text) is None
    assert match_wiki_intent(
        text,
        prior_text="Was steht morgen in meinem Kalender?",  # i18n-allow
    ) is None


def test_general_wiki_discussion_does_not_authorize_contextual_write() -> None:
    text = "Kannst du bitte einen Eintrag da eintragen, dass ich morgen frei habe?"  # i18n-allow

    assert match_wiki_intent(
        text,
        prior_text="Wie funktioniert ein Wiki?",  # i18n-allow
    ) is None


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
    "Kannst du mir sagen, was ich in mein Wiki eintragen soll?",  # i18n-allow
    "Could you tell me what I should write in my wiki?",
    "¿Puedes decirme qué debo anotar en mi wiki?",  # i18n-allow
    "What should I write in my wiki?",
])
def test_non_write_utterances_do_not_match(text):
    assert match_wiki_intent(text) is None
