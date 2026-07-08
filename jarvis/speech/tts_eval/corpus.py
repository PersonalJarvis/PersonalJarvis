"""The hard TTS evaluation corpus — representative de/en/es texts plus the five
hard-input categories where cheap/experimental models break: numbers, acronyms,
code tokens, a long passage (voice drift), and de/en/es proper names.

Design: docs/superpowers/specs/2026-07-07-tts-quality-curation-design.md §3.6.
Builds on the persona scenarios in scripts/voice_compare.py. The de/es strings
are the *content under test* (a multilingual product surface), so they carry an
`i18n-allow` marker per file — this is fixture data, not artifact prose.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalItem:
    """One evaluation utterance. ``tags`` name the categories it exercises
    (``persona`` | ``numbers`` | ``acronyms`` | ``code`` | ``long`` | ``names``)."""

    id: str
    language: str  # "de" | "en" | "es"
    text: str
    tags: tuple[str, ...]


# i18n-allow: the de/es strings below are the multilingual CONTENT under test
# (round-trip ASR reads them back), not artifact prose — fixture data by design.
HARD_CORPUS: tuple[EvalItem, ...] = (
    # ---- English ----------------------------------------------------------
    EvalItem("en-persona", "en", "Good evening. Everything is on schedule.", ("persona",)),
    EvalItem(
        "en-numbers", "en",
        "The meeting is at 9:45 on March 3rd, and the invoice total is 1,284.50 dollars.",
        ("numbers",),
    ),
    EvalItem(
        "en-acronyms", "en",
        "The NASA and NATO reports were sent as a PDF via the HTTP API to the CEO.",
        ("acronyms",),
    ),
    EvalItem(
        "en-code", "en",
        "Call build_tts_from_config with tts_cfg, then await synthesize and read the AudioChunk.",
        ("code",),
    ),
    EvalItem(
        "en-names", "en",
        "Please forward this to Siobhan, Rajesh, Björk, and Xavier Nguyen.",
        ("names",),
    ),
    EvalItem(
        "en-long", "en",
        "Let me summarize the situation for you. The deployment finished about ten "
        "minutes ago, and all three services came back healthy on the first try. "
        "There is one warning in the logs about a slow database query, but it did "
        "not affect any requests, and the latency is already back within its normal "
        "range. I will keep an eye on it and let you know if anything changes.",
        ("long",),
    ),
    # ---- German -----------------------------------------------------------
    EvalItem("de-persona", "de", "Guten Abend. Es ist alles im Zeitplan.", ("persona",)),
    EvalItem(
        "de-numbers", "de",
        "Der Termin ist am 3. März um 9:45 Uhr, und die Rechnung beträgt 1.284,50 Euro.",
        ("numbers",),
    ),
    EvalItem(
        "de-acronyms", "de",
        "Die GmbH schickte den DSGVO-Bericht als PDF über die HTTP-Schnittstelle an die IHK.",
        ("acronyms",),
    ),
    EvalItem(
        "de-code", "de",
        "Rufe build_tts_from_config mit tts_cfg auf und prüfe danach den AudioChunk.",
        ("code",),
    ),
    EvalItem(
        "de-names", "de",
        "Bitte leite das an Häberle, Grzegorz, Søren und Xavier Nguyen weiter.",
        ("names",),
    ),
    EvalItem(
        "de-long", "de",
        "Lass mich die Lage kurz zusammenfassen. Der Deploy ist vor etwa zehn Minuten "
        "durchgelaufen, und alle drei Dienste sind beim ersten Versuch wieder gesund "
        "geworden. Es gibt eine Warnung im Log über eine langsame Datenbankabfrage, "
        "aber sie hat keine Anfrage beeinträchtigt, und die Latenz ist längst wieder "
        "im normalen Bereich. Ich behalte es im Auge und melde mich, falls sich etwas "
        "ändert.",
        ("long",),
    ),
    # ---- Spanish ----------------------------------------------------------
    EvalItem("es-persona", "es", "Buenas noches. Todo va según lo previsto.", ("persona",)),
    EvalItem(
        "es-numbers", "es",
        "La reunión es el 3 de marzo a las 9:45, y la factura asciende a 1.284,50 euros.",
        ("numbers",),
    ),
    EvalItem(
        "es-acronyms", "es",
        "La ONU y la OTAN enviaron el informe en PDF a través de la API HTTP al director.",
        ("acronyms",),
    ),
    EvalItem(
        "es-code", "es",
        "Llama a build_tts_from_config con tts_cfg y luego revisa el AudioChunk.",
        ("code",),
    ),
    EvalItem(
        "es-names", "es",
        "Reenvía esto a Íñigo, Jokin, Søren y Xavier Nguyen, por favor.",
        ("names",),
    ),
    EvalItem(
        "es-long", "es",
        "Déjame resumir la situación. El despliegue terminó hace unos diez minutos, y "
        "los tres servicios volvieron a estar sanos al primer intento. Hay un aviso en "
        "el registro sobre una consulta lenta a la base de datos, pero no afectó a "
        "ninguna petición, y la latencia ya está de nuevo dentro de su rango normal. "
        "Lo seguiré vigilando y te aviso si algo cambia.",
        ("long",),
    ),
)


def items_for_language(language: str) -> tuple[EvalItem, ...]:
    """The corpus items for a two-letter language code."""
    short = (language or "").lower().split("-", 1)[0]
    return tuple(i for i in HARD_CORPUS if i.language == short)
