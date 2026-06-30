"""Contact write-intent detection — the input side of the say-do honesty guard.

The recurring failure (voice session 2026-06-30): Jarvis offered "Soll ich die
anlegen?", the user confirmed "ja, legt die mal an … die Mailadresse von Harald
ist …", and Jarvis replied "Okay, sehr gut" WITHOUT ever calling the
``contact-upsert`` tool — the address book stayed empty. The detector below
mandates the real tool on a contact-write turn so the read-style evidence gate's
backstop can catch a fake confirmation. Pure regex, no LLM (AP-9/AP-11).
"""

from jarvis.brain.contact_intent import (
    CONTACT_WRITE_DIRECTIVE,
    detect_contact_write_intent,
)

# --- positive: the turn really asks to save a person ------------------------


def test_reported_transcript_turn_fires():
    # The exact utterance from the live session (save verb + dictated detail).
    assert (
        detect_contact_write_intent(
            "Ähm, ja, legt die mal an. Also die Mailadresse von Harald ist harald.10.de."
        )
        is True
    )


def test_genitive_detail_alone_fires():
    # "Christoph's number is …" is a self-contained save intent.
    assert detect_contact_write_intent("Merk dir, Christophs Nummer ist 0171 1234567.") is True
    assert detect_contact_write_intent("Haralds E-Mail ist harald@example.com") is True


def test_save_verb_plus_contact_noun_fires():
    assert detect_contact_write_intent("Speichere Laura als Kontakt.") is True
    assert detect_contact_write_intent("Leg Tom als Kontakt an.") is True
    assert detect_contact_write_intent("Füge Anna zu meinen Kontakten hinzu.") is True


def test_english_and_spanish_fire():
    assert detect_contact_write_intent("Save Tom as a contact.") is True
    assert detect_contact_write_intent("Remember, Laura's number is 0151 22334.") is True
    assert detect_contact_write_intent("Guarda a Laura como contacto.") is True


# --- negative: must NOT fire (anti-false-correction is a hard user mandate) ---


def test_lookup_questions_do_not_fire():
    assert detect_contact_write_intent("Was steht heute in meinem Kalender?") is False
    assert detect_contact_write_intent("Habe ich irgendwelche Kontakte?") is False
    assert detect_contact_write_intent("Was geht ab?") is False
    assert detect_contact_write_intent("Wie ist das Wetter?") is False


def test_send_or_call_actions_do_not_fire():
    # Messaging / dialing a person is not saving them.
    assert detect_contact_write_intent("Schick Harald eine Mail.") is False
    assert detect_contact_write_intent("Ruf Harald an.") is False


def test_bare_detail_statement_does_not_fire():
    # A non-possessive "the number is wrong" must not be mistaken for a save.
    assert detect_contact_write_intent("Die Nummer ist falsch.") is False
    assert detect_contact_write_intent("Harald ist 1976 geboren.") is False
    assert detect_contact_write_intent("Die Adresse von Berlin ist zentral.") is False


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
