"""Contact write-intent detection — input side of the say-do honesty guard.

Why this exists
---------------
Live voice session (2026-06-30): Jarvis offered "Soll ich die anlegen?", the  # i18n-allow: forensic voice-bug docstring quoting the real German utterances involved
user confirmed "ja, legt die mal an … die Mailadresse von Harald ist …", and  # i18n-allow: forensic voice-bug docstring quoting the real German utterances involved
Jarvis answered "Okay, sehr gut" — but never called the ``contact-upsert`` tool,
so the address book stayed empty. The capability was fully wired; the gap was at
the brain decision layer (it acknowledged in words instead of acting).

This module mirrors the read-side evidence gate (``evidence_gate.py``): it spots,
deterministically, that a turn wants to SAVE a person, so the manager can mandate
``contact-upsert`` for the turn and the existing unverified-answer backstop can
replace a fake "okay" with an honest line. Pure regex + a string normalizer —
NO LLM, NO IO (AP-9/AP-11).

Precision over recall by mandate
--------------------------------
Falsely "correcting" the user is a hard anti-pattern here (the maintainer turned
the generic clarify question OFF for exactly that reason). So the detector fires
only on high-confidence signals — a genitive contact detail ("Christophs Nummer
ist …") alone, or a save verb corroborated by a contact noun / dictated detail.
A bare "die Nummer ist falsch" or "schick Harald eine Mail" must NOT fire.  # i18n-allow: forensic voice-bug docstring quoting German counter-example utterances the classifier must NOT match
"""

from __future__ import annotations

import re

from jarvis.core.capabilities import _normalize

# A genitive contact detail ("Christophs Nummer ist …", "Haralds Mail ist …").
# High precision on its own: a possessive name + a contact field + "ist/lautet"
# is almost always "save this person's detail". The non-genitive "<field> von X
# ist" form is intentionally NOT here (it also matches "die Adresse von Berlin  # i18n-allow: quoted German counter-example the classifier must NOT match
# ist zentral") — it only counts as a corroborating ``_DETAIL_RE`` signal.
_GENITIVE_DETAIL_RE = re.compile(
    r"\b\w+s\s+(mail|e-?mail|mailadresse|emailadresse|nummer|telefonnummer|"
    r"handynummer|telefon|adresse|anschrift|number|phone|address)\s+"
    r"(ist|lautet|is)\b"
)

# Any dictated contact field "<field> [von X] ist/lautet …" — corroborating only.  # i18n-allow: quoted German regex-pattern illustration
_DETAIL_RE = re.compile(
    r"\b(mail|e-?mail|mailadresse|emailadresse|nummer|telefonnummer|"
    r"handynummer|telefon|adresse|anschrift|number|phone|address)\b"
    r"(\s+von\s+\w+)?\s+(ist|lautet|is)\b"  # i18n-allow: German/English input-matching data (contact write-intent detector)
)

# Imperative/intent to save, create, add, remember, enter a contact.
_SAVE_VERB_RE = re.compile(
    r"\b("
    r"merk(e|t)?\s+dir"  # merk dir / merke dir
    r"|notier\w*"  # notiere / notier dir
    r"|speicher\w*"  # speichere / speichern / speicher  # i18n-allow: German input-matching data
    r"|anleg\w*"  # anlegen
    r"|leg\w*\b[^?]{0,40}\ban\b"  # leg/legt … an (separable)
    r"|eintrag\w*"  # eintragen  # i18n-allow: German input-matching data
    r"|trag\w*\b[^?]{0,40}\bein\b"  # trag … ein (separable)
    r"|hinzufueg\w*"  # hinzufuegen  # i18n-allow: German input-matching data
    r"|fueg\w*\b[^?]{0,40}\bhinzu\b"  # fueg … hinzu (separable)
    r"|nimm\b[^?]{0,40}\bauf\b"  # nimm … auf
    r"|save|remember|store\b|note\s+down|add\b|create\b"
    r"|guard\w*|anota\w*|agrega\w*|anade\w*"  # ES guardar/anotar/agregar/anadir
    r")"
)

# A contact container noun.
_CONTACT_NOUN_RE = re.compile(
    r"\b(kontakt|kontakte|kontakten|kontakts|adressbuch|adress-?buch"
    r"|contact|contacts|contacto|contactos)\b"
)

# Per-turn directive injected into the system prompt when a write intent is
# detected (English — LLM-facing, mirrors the read evidence gate's directive).
CONTACT_WRITE_DIRECTIVE = (
    "MANDATORY THIS TURN: the user wants to save or update a person in their "
    "contact book. You MUST call the `contact-upsert` tool to actually store "
    "it — one call per person — not just acknowledge it in words. Pass the "
    "name plus every detail the user gave (email, phone, relationship, "
    "address, note) as the tool arguments. If a required detail is clearly "
    "incomplete or malformed (for example an email address with no '@'), do "
    "NOT save the broken value: ask the user one short question to repeat that "
    "detail before saving. Never tell the user a contact was saved unless the "
    "`contact-upsert` tool actually ran this turn."
)


def detect_contact_write_intent(utterance: str) -> bool:
    """True when the turn is a high-confidence request to save/update a contact.

    Deterministic, conservative (precision over recall): a false positive would
    wrongly tell the user "I haven't saved that yet", which the anti-correction
    mandate forbids. The detector is the INPUT trigger; the actual honesty is
    enforced by the manager's unverified-answer backstop only if the mandated
    tool then does not run.
    """
    t = _normalize(utterance or "")
    if not t.strip():
        return False
    # High-precision genitive detail may fire on its own.
    if _GENITIVE_DETAIL_RE.search(t):
        return True
    save = bool(_SAVE_VERB_RE.search(t))
    noun = bool(_CONTACT_NOUN_RE.search(t))
    detail = bool(_DETAIL_RE.search(t))
    # A save verb needs one corroborating contact signal; a contact noun plus a
    # dictated detail is also enough. A lone signal never fires.
    if save and (noun or detail):
        return True
    if noun and detail:
        return True
    return False


# Minimum substance for a wiki note (mirrors wiki_ingest._MIN_INGEST_CHARS) —
# below this the curator's salience filter would drop it anyway.
_MIN_MEMORY_CHARS: int = 12

# An EXPLICIT "write this into my long-term memory" cue. Deliberately narrow:
# "speichere" alone is excluded (it also means save a file / a config), so a
# general memory note needs a clear remember-phrasing.
_MEMORY_VERB_RE = re.compile(
    r"\b("
    r"merk(e|t)?\s+(es\s+)?dir"  # merk dir / merke dir / merk es dir
    r"|notier\w*\s+dir"  # notier dir / notiere dir
    r"|behalt\w*"  # behalte (im Hinterkopf/Kopf)
    r"|halt\w*\b[^?]{0,30}\bfest\b"  # halt … fest
    r"|schreib\w*\b[^?]{0,30}\bauf\b"  # schreib … auf
    r"|vermerk\w*"  # vermerke
    r"|remember\s+(that|this|to)|note\s+(that|down)|keep\s+in\s+mind"
    r"|recuerda\w*|apunta\w*|anota\w*"  # ES recordar/apuntar/anotar
    r")"
)

# Per-turn directive for a general wiki memory note (English — LLM-facing).
WIKI_INGEST_DIRECTIVE = (
    "MANDATORY THIS TURN: the user explicitly asked you to remember a fact "
    "about a person or their life. You MUST call the `wiki-ingest` tool to "
    "actually write it to their long-term wiki — pass the fact as one "
    "self-contained sentence in the `text` argument — instead of only "
    "acknowledging it in words. Never tell the user you noted or saved "
    "something unless the `wiki-ingest` tool actually ran this turn."
)


def detect_memory_save_intent(utterance: str) -> bool:
    """True when the user explicitly asks to REMEMBER a general fact (no contact
    field) — routed to the direct wiki write path (``wiki-ingest``).

    Requires an explicit remember cue ("merk dir", "notier dir", "remember
    that", "behalte im Hinterkopf") AND enough substance to be worth a page, so
    a bare "merk dir" or a plain question never fires.
    """
    t = _normalize(utterance or "")
    if len(t.strip()) < _MIN_MEMORY_CHARS:
        return False
    return bool(_MEMORY_VERB_RE.search(t))


def resolve_save_mandate(utterance: str) -> tuple[str, str] | None:
    """Map a save/remember turn to ``(tool_name, per-turn directive)``, or None.

    Contact-data writes (an email/phone/address, or "save X as a contact") take
    priority over a general wiki memory note, so a dictated phone number lands in
    the address book rather than as free prose in the wiki. This is the single
    routing point the manager consumes — both the contact and the wiki say-do
    guards flow through here.
    """
    if detect_contact_write_intent(utterance):
        return ("contact-upsert", CONTACT_WRITE_DIRECTIVE)
    if detect_memory_save_intent(utterance):
        return ("wiki-ingest", WIKI_INGEST_DIRECTIVE)
    return None


__all__ = [
    "detect_contact_write_intent",
    "detect_memory_save_intent",
    "resolve_save_mandate",
    "CONTACT_WRITE_DIRECTIVE",
    "WIKI_INGEST_DIRECTIVE",
]
