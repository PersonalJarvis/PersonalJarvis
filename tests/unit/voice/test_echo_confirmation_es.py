"""Spanish coverage for ``echo_confirmation.classify_response``.

Runtime Output Language doctrine (CLAUDE.md): the yes/no classifier must cover
de/en/es. Before this, ``language="es"`` silently fell into the German branch, so
a Spanish "sí"/"no" was misclassified as "unknown" — an es-pinned user could not
confirm or veto a consequential action by voice. Veto keeps priority over confirm
(safety bias, Plan-§AP-12).
"""
from __future__ import annotations

import pytest

from jarvis.voice.echo_confirmation import classify_response


@pytest.mark.parametrize(
    "text",
    ["sí", "si", "vale", "claro", "hazlo", "correcto", "de acuerdo", "adelante"],
)
def test_spanish_confirm(text: str) -> None:
    assert classify_response(text, language="es") == "confirm"


@pytest.mark.parametrize(
    "text",
    ["no", "cancela", "detente", "mal", "déjalo", "olvídalo", "basta"],
)
def test_spanish_veto(text: str) -> None:
    assert classify_response(text, language="es") == "veto"


@pytest.mark.parametrize("text", ["quizás", "espera", "un momento"])
def test_spanish_ambiguous(text: str) -> None:
    assert classify_response(text, language="es") == "ambiguous"


def test_spanish_veto_beats_confirm() -> None:
    # "no, sí" — the "no" must win (safety bias), same property as de/en.
    assert classify_response("no, sí", language="es") == "veto"


def test_spanish_no_se_is_veto_by_safety_bias() -> None:
    # "no sé" ("I don't know") contains the veto token "no". Under the
    # veto-priority safety bias (Plan-§AP-12) this resolves to veto — the safe
    # outcome (do NOT execute the consequential action), mirroring the German
    # "weiß ich nicht" behavior.
    assert classify_response("no sé", language="es") == "veto"


def test_spanish_unknown_when_no_pattern() -> None:
    assert classify_response("la luna es bonita", language="es") == "unknown"
