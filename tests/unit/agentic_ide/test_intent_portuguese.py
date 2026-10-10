"""European Portuguese speech input reaches the same workspace intents as the
other supported locales — and ordinary Portuguese sentences stay out of them.

Every utterance below is spoken input under test (AGENTS.md §1 list #4).
"""
from __future__ import annotations

import pytest

from jarvis.agentic_ide import intent
from jarvis.agentic_ide.names import spoken_positions

NAMES = ["Alex", "Blake", "Casey", "Dana"]


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("Diz ao Alex para correr os testes", "Alex"),  # i18n-allow
        ("O Blake deve rever o provider do vosk", "Blake"),  # i18n-allow
        ("A Casey tem de corrigir o bug do áudio", "Casey"),  # i18n-allow
        ("Pede à Dana que reveja o código", "Dana"),  # i18n-allow
        ("Deixa o Alex investigar o bug no caminho do wake", "Alex"),  # i18n-allow
        ("Quero que o Blake faça uma revisão do módulo", "Blake"),  # i18n-allow
        ("Que a Dana corrija os testes", "Dana"),  # i18n-allow
        ("Instrui o terminal Casey a fazer um deep dive", "Casey"),  # i18n-allow
    ],
)
def test_addressing_shapes(utterance: str, terminal: str) -> None:
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_PROMPT


@pytest.mark.parametrize(
    ("utterance", "terminal"),
    [
        ("O que está o Alex a fazer?", "Alex"),  # i18n-allow
        ("O que fez a Dana?", "Dana"),  # i18n-allow
        ("O Blake já está pronto?", "Blake"),  # i18n-allow
        ("Em que ponto está a Casey?", "Casey"),  # i18n-allow
    ],
)
def test_questions_about_a_pane_are_reads(utterance: str, terminal: str) -> None:
    found = intent.detect(utterance, names=NAMES)
    assert found is not None, utterance
    assert found.terminal == terminal
    assert found.kind == intent.KIND_REPORT


@pytest.mark.parametrize(
    "utterance",
    [
        "Como está o tempo hoje?",  # i18n-allow
        "Faz um deep dive aos meus custos da Google Cloud",  # i18n-allow
        "Dana é um nome bonito para uma criança",  # i18n-allow
        "Explica-me como funciona o Vosk",  # i18n-allow
    ],
)
def test_unrelated_turns_are_left_alone(utterance: str) -> None:
    assert intent.detect(utterance, names=NAMES) is None
    assert intent.owns_turn(utterance, names=NAMES) is False


def test_naming_the_spawn_vehicle_still_wins() -> None:
    assert intent.owns_turn(
        "Lança uma missão em segundo plano que ajude a Dana", names=NAMES  # i18n-allow
    ) is False


def test_a_collective_address_reaches_every_pane() -> None:
    found = intent.detect_all("Diz a todos que parem os testes", names=NAMES)  # i18n-allow
    assert [item.terminal for item in found] == NAMES


def test_an_exception_list_keeps_its_panes_out() -> None:
    found = intent.detect_all(
        "Instrui todos os terminais exceto o Blake a correr os testes",  # i18n-allow
        names=NAMES,
    )
    assert "Blake" not in [item.terminal for item in found]
    assert len(found) == len(NAMES) - 1


def test_an_enumeration_with_e_addresses_both() -> None:
    found = intent.detect_all(
        "Diz ao Alex e ao Blake para corrigirem os testes", names=NAMES  # i18n-allow
    )
    assert [item.terminal for item in found] == ["Alex", "Blake"]


@pytest.mark.parametrize(
    ("utterance", "count", "agent"),
    [
        ("Abre três terminais do Codex", 3, "codex"),  # i18n-allow
        ("Cria quatro terminais", 4, None),  # i18n-allow
        ("Mais dois terminais, por favor", 2, None),  # i18n-allow
        ("Abre um terminal do Claude", 1, "claude"),  # i18n-allow
        ("Abre outro terminal", 1, None),  # i18n-allow
    ],
)
def test_spoken_terminal_requests(utterance: str, count: int, agent: str | None) -> None:
    found = intent.detect_spawn(utterance)
    assert found is not None, utterance
    assert found.count == count, utterance
    assert found.agent == agent, utterance


def test_a_mixed_fleet_keeps_both_groups() -> None:
    found = intent.detect_spawn(
        "Abre três terminais do Codex e dois do Claude", names=NAMES  # i18n-allow
    )
    assert found is not None
    assert [(g.count, g.agent) for g in found.groups] == [(3, "codex"), (2, "claude")]


@pytest.mark.parametrize(
    "utterance",
    [
        "Como abro um terminal novo?",  # i18n-allow
        "Com que atalho posso abrir um separador do browser?",  # i18n-allow
        "Abre o painel de controlo",  # i18n-allow
    ],
)
def test_questions_and_other_panels_never_open_panes(utterance: str) -> None:
    assert intent.detect_spawn(utterance) is None


@pytest.mark.parametrize(
    ("utterance", "agent"),
    [
        ("Fecha todos os terminais", None),  # i18n-allow
        ("Podes fechar todos os terminais do Codex?", "codex"),  # i18n-allow
    ],
)
def test_a_real_close_request_closes(utterance: str, agent: str | None) -> None:
    found = intent.detect_close_fleet(utterance)
    assert found is not None
    assert found.agent == agent


@pytest.mark.parametrize(
    "utterance",
    [
        "Diz a todos os terminais para fecharem os ficheiros",  # i18n-allow
        "Os terminais estão todos fechados?",  # i18n-allow
    ],
)
def test_fleet_instructions_and_questions_never_close_the_workspace(utterance: str) -> None:
    assert intent.detect_close_fleet(utterance) is None


def test_positions_are_named_in_portuguese() -> None:
    two = spoken_positions("prompt o terminal dois")  # i18n-allow
    third = spoken_positions("o terceiro terminal")  # i18n-allow
    assert [value for _, _, value in two] == [2]
    assert [value for _, _, value in third] == [3]


def test_undelivered_complaints_are_recognised() -> None:
    assert intent.reports_undelivered("Não lhe enviaste nada")  # i18n-allow
    assert intent.reports_undelivered("Tenta outra vez")  # i18n-allow
    assert not intent.reports_undelivered("O Alex enviou o relatório?")  # i18n-allow


def test_split_and_plural_signals() -> None:
    assert intent.wants_split("Dividam o trabalho entre vocês")  # i18n-allow
    assert intent.expects_several("Os dois devem fazer um deep dive")  # i18n-allow
    assert intent.distributes_tasks(
        "um trata do backend, o outro fica com o frontend"  # i18n-allow
    )


def test_portuguese_group_briefs_route_by_kind_and_trim_the_next_article() -> None:
    """ "… e o codex deve …" leaves neither "e" nor "o" on the claudes' task."""
    utterance = (
        "Abre dois claudes e um codex. Os claudes devem corrigir os testes "  # i18n-allow
        "e o codex deve atualizar a documentação."  # i18n-allow
    )
    assert intent.spawn_group_tasks(utterance) == {
        "claude": "corrigir os testes",  # i18n-allow
        "codex": "atualizar a documentação",  # i18n-allow
    }
