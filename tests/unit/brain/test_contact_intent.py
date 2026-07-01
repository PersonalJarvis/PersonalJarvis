"""Contact write-intent detection — the input side of the say-do honesty guard.

The recurring failure (voice session 2026-06-30): Jarvis offered "Soll ich die  # i18n-allow
anlegen?", the user confirmed "ja, legt die mal an … die Mailadresse von Sam  # i18n-allow
ist …", and Jarvis replied "Okay, sehr gut" WITHOUT ever calling the  # i18n-allow
``contact-upsert`` tool — the address book stayed empty. The detector below
mandates the real tool on a contact-write turn so the read-style evidence gate's
backstop can catch a fake confirmation. Pure regex, no LLM (AP-9/AP-11).
"""

from jarvis.brain.contact_intent import (
    CONTACT_WRITE_DIRECTIVE,
    WIKI_INGEST_DIRECTIVE,
    detect_contact_write_intent,
    detect_memory_save_intent,
    resolve_save_mandate,
)

# --- positive: the turn really asks to save a person ------------------------


def test_reported_transcript_turn_fires():
    # The exact utterance from the live session (save verb + dictated detail).
    assert (
        detect_contact_write_intent(
            "Ähm, ja, legt die mal an. Also die Mailadresse von Sam ist sam.10.de."  # i18n-allow
        )
        is True
    )


def test_genitive_detail_alone_fires():
    # "Christoph's number is …" is a self-contained save intent.
    assert detect_contact_write_intent("Merk dir, Christophs Nummer ist 0171 1234567.") is True
    assert detect_contact_write_intent("Sams E-Mail ist sam@example.com") is True


def test_save_verb_plus_contact_noun_fires():
    assert detect_contact_write_intent("Speichere Laura als Kontakt.") is True
    assert detect_contact_write_intent("Leg Tom als Kontakt an.") is True
    assert detect_contact_write_intent("Füge Anna zu meinen Kontakten hinzu.") is True  # i18n-allow


def test_english_and_spanish_fire():
    assert detect_contact_write_intent("Save Tom as a contact.") is True
    assert detect_contact_write_intent("Remember, Laura's number is 0151 22334.") is True
    assert detect_contact_write_intent("Guarda a Laura como contacto.") is True


# --- negative: must NOT fire (anti-false-correction is a hard user mandate) ---


def test_lookup_questions_do_not_fire():
    assert detect_contact_write_intent("Was steht heute in meinem Kalender?") is False
    assert detect_contact_write_intent("Habe ich irgendwelche Kontakte?") is False
    assert detect_contact_write_intent("Was geht ab?") is False
    assert detect_contact_write_intent("Wie ist das Wetter?") is False  # i18n-allow


def test_send_or_call_actions_do_not_fire():
    # Messaging / dialing a person is not saving them.
    assert detect_contact_write_intent("Schick Sam eine Mail.") is False
    assert detect_contact_write_intent("Ruf Sam an.") is False


def test_bare_detail_statement_does_not_fire():
    # A non-possessive "the number is wrong" must not be mistaken for a save.
    assert detect_contact_write_intent("Die Nummer ist falsch.") is False  # i18n-allow
    assert detect_contact_write_intent("Sam ist 1976 geboren.") is False
    assert detect_contact_write_intent("Die Adresse von Berlin ist zentral.") is False  # i18n-allow


def test_empty_input_is_safe():
    assert detect_contact_write_intent("") is False
    assert detect_contact_write_intent("   ") is False


# --- the per-turn directive ---------------------------------------------------


def test_directive_forces_the_real_tool_and_clarifies_bad_fields():
    d = CONTACT_WRITE_DIRECTIVE
    assert "contact-upsert" in d
    assert "MANDATORY" in d
    # Must instruct asking on a malformed required field (the '@'-less email).
    assert "@" in d
    # Must forbid claiming a save that did not run.
    assert "never" in d.lower() or "not" in d.lower()


# --- general "merk dir …" facts → wiki-ingest (the wiki document path) --------


def test_memory_save_intent_fires_on_explicit_remember():
    assert detect_memory_save_intent("Merk dir, dass Sam gerne Fußball schaut.") is True  # i18n-allow
    assert detect_memory_save_intent("Notier dir, dass Joy am 14. August Geburtstag hat.") is True
    assert detect_memory_save_intent("Remember that Tom prefers tea over coffee.") is True
    assert (
        detect_memory_save_intent("Behalte im Hinterkopf, dass ich allergisch gegen Nüsse bin.")  # i18n-allow
        is True
    )


def test_memory_save_intent_needs_a_remember_cue_and_substance():
    assert detect_memory_save_intent("Was geht ab?") is False
    assert detect_memory_save_intent("Wie ist das Wetter?") is False  # i18n-allow
    # "speichere die Datei" is file/config, not a personal memory note.
    assert detect_memory_save_intent("Speichere die Datei.") is False
    assert detect_memory_save_intent("Merk.") is False  # no real content


# --- routing: contact data vs general fact -----------------------------------


def test_resolve_save_mandate_routes_contact_data_to_contact_upsert():
    mandate = resolve_save_mandate(
        "Ähm, ja, legt die mal an. Also die Mailadresse von Sam ist sam.10.de."  # i18n-allow
    )
    assert mandate is not None
    tool, directive = mandate
    assert tool == "contact-upsert"
    assert directive == CONTACT_WRITE_DIRECTIVE


def test_resolve_save_mandate_routes_general_fact_to_wiki_ingest():
    mandate = resolve_save_mandate("Merk dir, dass Sam gerne Fußball schaut.")  # i18n-allow
    assert mandate is not None
    tool, directive = mandate
    assert tool == "wiki-ingest"
    assert directive == WIKI_INGEST_DIRECTIVE


def test_resolve_save_mandate_prefers_contact_when_both_match():
    # "merk dir … Nummer ist …" is BOTH a remember cue AND contact data — the
    # address-book path wins so the number lands where it belongs.
    mandate = resolve_save_mandate("Merk dir, Christophs Nummer ist 0171 1234567.")  # i18n-allow
    assert mandate is not None
    assert mandate[0] == "contact-upsert"


def test_resolve_save_mandate_none_on_plain_turn():
    assert resolve_save_mandate("Was geht ab?") is None  # i18n-allow
    assert resolve_save_mandate("Wie ist das Wetter?") is None  # i18n-allow


def test_wiki_ingest_directive_forces_the_real_tool():
    d = WIKI_INGEST_DIRECTIVE
    assert "wiki-ingest" in d
    assert "MANDATORY" in d
    assert "never" in d.lower() or "not" in d.lower()
