"""Contact write-intent detection — input side of the say-do honesty guard.

Why this exists
---------------
Live voice session (2026-06-30): Jarvis offered "Soll ich die anlegen?", the
user confirmed "ja, legt die mal an … die Mailadresse von Harald ist …", and
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
A bare "die Nummer ist falsch" or "schick Harald eine Mail" must NOT fire.
"""
from __future__ import annotations

import re

from jarvis.core.capabilities import _normalize

# A genitive contact detail ("Christophs Nummer ist …", "Haralds Mail ist …").
# High precision on its own: a possessive name + a contact field + "ist/lautet"
# is almost always "save this person's detail". The non-genitive "<field> von X
# ist" form is intentionally NOT here (it also matches "die Adresse von Berlin
# ist zentral") — it only counts as a corroborating ``_DETAIL_RE`` signal.
_GENITIVE_DETAIL_RE = re.compile(
    r"\b\w+s\s+(mail|e-?mail|mailadresse|emailadresse|nummer|telefonnummer|"
    r"handynummer|telefon|adresse|anschrift|number|phone|address)\s+"
    r"(ist|lautet|is)\b"
)

# Any dictated contact field "<field> [von X] ist/lautet …" — corroborating only.
_DETAIL_RE = re.compile(
    r"\b(mail|e-?mail|mailadresse|emailadresse|nummer|telefonnummer|"
    r"handynummer|telefon|adresse|anschrift|number|phone|address)\b"
    r"(\s+von\s+\w+)?\s+(ist|lautet|is)\b"
)

# Imperative/intent to save, create, add, remember, enter a contact.
_SAVE_VERB_RE = re.compile(
    r"\b("
    r"merk(e|t)?\s+dir"                  # merk dir / merke dir
    r"|notier\w*"                        # notiere / notier dir
    r"|speicher\w*"                      # speichere / speichern / speicher
    r"|anleg\w*"                         # anlegen
    r"|leg\w*\b[^?]{0,40}\ban\b"        # leg/legt … an (separable)
    r"|eintrag\w*"                       # eintragen
    r"|trag\w*\b[^?]{0,40}\bein\b"      # trag … ein (separable)
    r"|hinzufueg\w*"                     # hinzufuegen
    r"|fueg\w*\b[^?]{0,40}\bhinzu\b"    # fueg … hinzu (separable)
    r"|nimm\b[^?]{0,40}\bauf\b"          # nimm … auf
    r"|save|remember|store\b|note\s+down|add\b|create\b"
    r"|guard\w*|anota\w*|agrega\w*|anade\w*"   # ES guardar/anotar/agregar/anadir
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


__all__ = ["detect_contact_write_intent", "CONTACT_WRITE_DIRECTIVE"]
