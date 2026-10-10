"""European Portuguese speech input reaches the same society intents as the
other supported languages, and ordinary pt sentences stay out of them."""

from __future__ import annotations

import pytest

from jarvis.society.intent import (
    explicitly_requests_worker,
    is_inventory_question,
    is_team_management_request,
)
from jarvis.society.message_routing import is_internal_message_request


@pytest.mark.parametrize(
    "text",
    [
        "Que agentes tens?",  # i18n-allow: pt voice input
        "Quantos agentes temos?",  # i18n-allow: pt voice input
        "Mostra-me os meus agentes",  # i18n-allow: pt voice input
        "¿Qué agentes tienes?",  # i18n-allow: es voice input still recognised
        "Welche Agenten hast du?",  # i18n-allow: de voice input still recognised
    ],
)
def test_inventory_questions(text: str) -> None:
    assert is_inventory_question(text)


def test_an_assignment_is_not_an_inventory_question() -> None:
    assert not is_inventory_question(
        "Que agentes tens? Dá esta tarefa ao Scout."  # i18n-allow: pt voice input
    )


def test_team_management_request() -> None:
    assert is_team_management_request(
        "Cria um agente para os meus emails."  # i18n-allow: pt voice input
    )


def test_explicit_worker_request() -> None:
    assert explicitly_requests_worker(
        "Lança uma missão em segundo plano para isto."  # i18n-allow: pt voice input
    )


def test_internal_message_request() -> None:
    assert is_internal_message_request(
        "Escreve uma mensagem ao Gmail agent.",  # i18n-allow: pt voice input
        ["Gmail agent"],
    )
    assert not is_internal_message_request(
        "Envia um email à Alice pelo Gmail.",  # i18n-allow: pt voice input
        ["Gmail agent"],
    )
