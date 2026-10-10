"""European Portuguese coverage for ``echo_confirmation.classify_response``.

Runtime Output Language doctrine (AGENTS.md): the yes/no classifier must cover
every supported language. Without its own branch ``language="pt"`` would fall
into the German patterns, so a Portuguese "sim"/"não" would read as "unknown"
and a pt-pinned user could not confirm or veto a consequential action by voice.
Veto keeps priority over confirm (safety bias, Plan-§AP-12).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.self_mod.pending import PendingMutation
from jarvis.voice.echo_confirmation import (
    classify_response,
    format_confirmation,
    format_outcome,
)


@pytest.mark.parametrize(
    "text",
    ["sim", "claro", "pode ser", "força", "faz", "avança", "ok", "certo",
     "de acordo", "confirmo", "vá lá"],
)
def test_portuguese_confirm(text: str) -> None:
    assert classify_response(text, language="pt") == "confirm"


@pytest.mark.parametrize(
    "text",
    ["não", "nao", "cancela", "esquece", "deixa estar", "nem pensar", "basta",
     "errado"],
)
def test_portuguese_veto(text: str) -> None:
    assert classify_response(text, language="pt") == "veto"


@pytest.mark.parametrize("text", ["talvez", "espera", "um momento", "se calhar"])
def test_portuguese_ambiguous(text: str) -> None:
    assert classify_response(text, language="pt") == "ambiguous"


def test_portuguese_veto_beats_confirm() -> None:
    # "não, sim" — the "não" must win (safety bias), same property as de/en/es.
    assert classify_response("não, sim", language="pt") == "veto"


def test_portuguese_nao_sei_is_veto_by_safety_bias() -> None:
    # "não sei" ("I don't know") carries the veto token, so the consequential
    # action is NOT executed — mirroring the Spanish "no sé" behaviour.
    assert classify_response("não sei", language="pt") == "veto"


def test_para_is_not_a_veto() -> None:
    # "para" is the everyday preposition ("para enviar" = "to send").
    assert classify_response("sim, para enviar", language="pt") == "confirm"


def test_portuguese_unknown_when_no_pattern() -> None:
    assert classify_response("a lua está bonita", language="pt") == "unknown"


def _pending(path: str = "tts.speed", old: object = False, new: object = True) -> PendingMutation:
    return PendingMutation(
        id=uuid4(), path=path, old_value=old, new_value=new,
        needs_confirmation=True, risk_tier="ask", requires_restart=False,
        applied=False, backup_path=None, description="Modo rápido (hot-reload)",  # i18n-allow
    )


def test_portuguese_echo_question() -> None:
    assert format_confirmation(_pending(), language="pt") == (
        "Entendido — Modo rápido muda de desligado para ligado. Confirmas?"  # i18n-allow
    )


def test_portuguese_sensitive_echo_question_hides_the_value() -> None:
    text = format_confirmation(_pending(path="api_key", new="sk-secret"), language="pt")
    assert "sk-secret" not in text
    assert text.endswith("para um valor novo. Confirmas?")  # i18n-allow


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("applied", "Pronto — Modo rápido agora é ligado."),  # i18n-allow
        ("vetoed", "Está bem, deixo estar."),  # i18n-allow
        ("timeout", "Não ouvi resposta, vou cancelar. A definição fica em desligado."),
    ],
)
def test_portuguese_outcomes(kind: str, expected: str) -> None:
    assert format_outcome(kind, _pending(), language="pt") == expected  # type: ignore[arg-type]


def test_unknown_language_outcome_speaks_english() -> None:
    assert format_outcome("vetoed", _pending(), language="fr") == "Okay, leaving it."
