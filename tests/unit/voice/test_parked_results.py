"""Parked results (ADR-0034): the queue never expires, the vocabulary is closed."""

from __future__ import annotations

import pytest

from jarvis.voice.parked_results import (
    WAIT_QUERY_NONE,
    WAIT_QUERY_PROGRESS,
    WAIT_QUERY_RESULT,
    ParkedResult,
    ParkedResultLedger,
    anchor_pool,
    classify_wait_query,
    reanchor,
    topic_of,
)


def _result(text: str = "42 degrees", request: str = "wie wird das Wetter in Berlin", **kw):
    return ParkedResult(text=text, language="de", request_text=request, **kw)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


# --- ledger -----------------------------------------------------------------


def test_park_stamps_clock_and_assigns_delivery_id() -> None:
    clock = _Clock()
    ledger = ParkedResultLedger(clock=clock)
    item = ledger.park(_result())
    assert item.delivery_id.startswith("parked:")
    assert item.queued_at == 100.0
    clock.now = 160.0
    assert item.waited_s(clock.now) == 60.0
    assert len(ledger) == 1 and bool(ledger)


def test_park_is_idempotent_per_delivery_id() -> None:
    ledger = ParkedResultLedger(clock=_Clock())
    first = ledger.park(_result(delivery_id="d1"))
    again = ledger.park(_result(text="other", delivery_id="d1"))
    assert again is first
    assert len(ledger) == 1


def test_no_time_based_expiry_only_named_exits() -> None:
    clock = _Clock()
    ledger = ParkedResultLedger(clock=clock)
    ledger.park(_result(delivery_id="d1"))
    clock.now += 3600.0  # an hour of talking
    assert ledger.peek() is not None and ledger.peek().delivery_id == "d1"
    assert ledger.pop("d1") is not None
    assert ledger.pop("d1") is None
    assert not ledger


def test_cancel_and_drain_return_everything() -> None:
    ledger = ParkedResultLedger(clock=_Clock())
    ledger.park(_result(delivery_id="a"))
    ledger.park(_result(delivery_id="b"))
    dropped = ledger.cancel_all()
    assert [d.delivery_id for d in dropped] == ["a", "b"]
    assert not ledger
    ledger.park(_result(delivery_id="c"))
    assert [d.delivery_id for d in ledger.drain()] == ["c"]


def test_supersede_drops_only_the_same_order() -> None:
    ledger = ParkedResultLedger(clock=_Clock())
    ledger.park(_result(delivery_id="a", request="Wetter in Berlin"))
    ledger.park(_result(delivery_id="b", request="Termine morgen"))
    dropped = ledger.supersede("wetter in berlin!")
    assert [d.delivery_id for d in dropped] == ["a"]
    assert [d.delivery_id for d in ledger] == ["b"]
    assert ledger.supersede("") == []


def test_note_turn_completed_counts_intervening_turns() -> None:
    ledger = ParkedResultLedger(clock=_Clock())
    item = ledger.park(_result())
    assert item.intervening_turns == 0
    ledger.note_turn_completed()
    ledger.note_turn_completed()
    assert item.intervening_turns == 2


def test_retrieve_for_prefers_the_named_result_then_the_oldest() -> None:
    ledger = ParkedResultLedger(clock=_Clock())
    weather = ledger.park(_result(delivery_id="w", request="wie wird das Wetter in Berlin"))
    mail = ledger.park(_result(delivery_id="m", request="schick die Mail an Anna"))
    assert ledger.retrieve_for("und was kam bei der Mail raus") is mail
    assert ledger.retrieve_for("hast du das Ergebnis") is weather
    ledger.pop("w")
    assert ledger.retrieve_for("irgendwas") is mail
    ledger.pop("m")
    assert ledger.retrieve_for("hast du das Ergebnis") is None


# --- vocabulary ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "wie weit bist du",
        "Bist du schon fertig?",
        "und, dauert es noch lange",
        "how far are you",
        "How's it going?",
        "are you done yet",
        "still working on it?",
        "cómo vas",
        "¿ya has terminado?",
    ],
)
def test_progress_questions_are_recognised(text: str) -> None:
    assert classify_wait_query(text) == WAIT_QUERY_PROGRESS


@pytest.mark.parametrize(
    "text",
    [
        "was kam dabei raus",
        "Und? Was ist rausgekommen?",
        "hast du schon ein Ergebnis",
        "what did you find",
        "do you have the result",
        "any results yet?",
        "qué encontraste",
        "ya tienes el resultado",
    ],
)
def test_result_requests_are_recognised(text: str) -> None:
    assert classify_wait_query(text) == WAIT_QUERY_RESULT


@pytest.mark.parametrize(
    "text",
    [
        "",
        "ja",
        "what did you find about the flight prices in my wiki notes",
        "wie weit ist es bis Berlin",
        "spiel Musik",
        "the result of the match was surprising, tell me more about the team",
    ],
)
def test_real_requests_stay_native(text: str) -> None:
    assert classify_wait_query(text) == WAIT_QUERY_NONE


# --- re-anchoring -------------------------------------------------------------


def test_topic_drops_fillers_and_bounds_length() -> None:
    assert topic_of("Bitte kannst du mal das Wetter in Berlin nachschauen?", "de") == (
        "Wetter in Berlin nachschauen"
    )
    long = " ".join(f"w{i}" for i in range(12))
    assert topic_of(long, "en") == "w0 w1 w2 w3 w4 w5 w6 w7 …"
    assert topic_of("bitte", "de") == ""


def test_reanchor_only_when_something_happened_in_between() -> None:
    assert reanchor("42", request_text="Wetter", language="de", intervening_turns=0) == "42"
    line = reanchor(
        "42 Grad.",
        request_text="Wetter in Berlin",
        language="de",
        intervening_turns=2,
        choose=lambda pool: pool[0],
    )
    assert line == "Zu deiner Anfrage von vorhin – Wetter in Berlin: 42 Grad."


def test_reanchor_without_topic_uses_the_topicless_pool_and_every_locale() -> None:
    for language in ("de", "en", "es"):
        with_topic = anchor_pool(language)
        without = anchor_pool(language, with_topic=False)
        assert with_topic and without
        assert all("{result}" in t and "{topic}" in t for t in with_topic)
        assert all("{result}" in t and "{topic}" not in t for t in without)
    line = reanchor("done", request_text="please", language="en", choose=lambda pool: pool[0])
    assert line == "About your earlier request: done"


def test_reanchor_rotates_prefixes() -> None:
    seen = {reanchor("x", request_text="mail to Anna", language="en") for _ in range(3)}
    assert len(seen) >= 2
