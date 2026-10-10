"""Every supported reply language is a mission language, end to end.

A Portuguese (or Spanish) turn used to be capped to a German mission: the
dispatch contract was ``Literal["de", "en"]`` and the readback tables carried
de/en only, so a pt user heard the mission readback in German. These tests pin
that the dispatch contract, the readback tables and the announcer cover every
supported reply language.
"""
from __future__ import annotations

from typing import get_args

import pytest

from jarvis.brain.manager import SUPPORTED_REPLY_LANGUAGES
from jarvis.missions.events import (
    MISSION_LANGUAGES,
    MissionApproved,
    MissionDispatched,
    MissionLanguage,
    coerce_mission_language,
)
from jarvis.missions.kontrollierer.deliverable import (
    MISSION_COMPLETED_PHRASES,
    build_delivered_summary,
)
from jarvis.missions.voice.readback import (
    CAPACITY_WAIT_PHRASES,
    FAILURE_REASON_PHRASES,
    READBACK_TEMPLATES,
    Lang,
    MissionReadback,
    approved_summary,
    render_capacity_wait,
)

_SPOKEN = tuple(code for code in SUPPORTED_REPLY_LANGUAGES if code != "auto")


def test_mission_languages_match_supported_reply_languages() -> None:
    assert set(MISSION_LANGUAGES) == set(_SPOKEN)
    assert set(get_args(MissionLanguage)) == set(_SPOKEN)
    assert set(get_args(Lang)) == set(_SPOKEN)


def test_dispatch_payload_accepts_portuguese() -> None:
    assert MissionDispatched(prompt="x", language="pt").language == "pt"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("pt", "pt"), ("ES", "es"), ("fr", "de"), (None, "de"), ("", "de")],
)
def test_coerce_mission_language(value: object, expected: str) -> None:
    assert coerce_mission_language(value) == expected


@pytest.mark.parametrize("lang", _SPOKEN)
def test_every_readback_template_has_every_language(lang: str) -> None:
    for key, by_lang in READBACK_TEMPLATES.items():
        assert by_lang.get(lang), f"{key} has no {lang} template"


@pytest.mark.parametrize("lang", _SPOKEN)
def test_failure_and_capacity_tables_share_the_de_key_set(lang: str) -> None:
    assert set(FAILURE_REASON_PHRASES[lang]) == set(FAILURE_REASON_PHRASES["de"])
    assert set(CAPACITY_WAIT_PHRASES[lang]) == set(CAPACITY_WAIT_PHRASES["de"])


def test_portuguese_readback_is_portuguese() -> None:
    rb = MissionReadback()
    assert rb.render_approved(summary="X", language="pt") in {"Feito. X", "Pronto. X"}
    failed = rb.render_failed(reason="budget_exceeded", language="pt")
    assert "O limite de custo foi atingido." in failed  # i18n-allow
    assert rb.render_cancelled(language="pt") == "Tarefa cancelada."  # i18n-allow
    assert rb.render_failed(reason="", language="pt").endswith("erro desconhecido")  # i18n-allow
    assert "esta ação" in rb.render_destructive_confirm(language="pt")  # i18n-allow


def test_portuguese_capacity_wait_has_no_german_or_english() -> None:
    text = render_capacity_wait(
        reason="provider_quota",
        provider="",
        steps_done=1,
        steps_total=3,
        files_saved=2,
        checkpoint_saved=True,
        language="pt",
    )
    assert text.startswith("A capacidade de fornecedor de IA")  # i18n-allow
    assert "Artefactos" in text
    assert "KI-Anbieter" not in text and "AI provider" not in text


def test_unknown_language_readback_speaks_english() -> None:
    rb = MissionReadback()
    assert rb.render_cancelled(language="fr") == "Task cancelled."  # type: ignore[arg-type]


def _approved(**summaries: str) -> MissionApproved:
    return MissionApproved(
        result_uri="mission://x", tokens_used=0, cost_usd=0.0, wall_ms=0,
        summary_de=summaries.get("de", "DE"), summary_en=summaries.get("en", "EN"),
        summary_local=summaries.get("local", ""),
    )


def test_approved_summary_prefers_the_local_summary_for_pt() -> None:
    event = _approved(local="Feito. Guardei a.html como artefacto.")  # i18n-allow
    assert approved_summary(event, "pt") == "Feito. Guardei a.html como artefacto."  # i18n-allow
    assert approved_summary(event, "de") == "DE"
    assert approved_summary(event, "en") == "EN"


def test_approved_summary_falls_back_to_english_without_local() -> None:
    assert approved_summary(_approved(), "pt") == "EN"


def test_delivered_summary_and_completion_phrase_cover_portuguese(tmp_path) -> None:
    out = build_delivered_summary([tmp_path / "a.html"], language="pt")
    assert out == "Feito. Guardei a.html como artefacto."  # i18n-allow
    assert set(MISSION_COMPLETED_PHRASES) == set(_SPOKEN)
