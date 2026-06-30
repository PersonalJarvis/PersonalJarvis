"""Tests fuer BioGenerator (Brainstorm 2026-05-02 — beissend mit Augenzwinkern).

Drei Szenarien (Power / Casual / Empty), Failure-Modi, neue Spec-Achsen:
- Cold-Start-Hint im Prompt bei days_observed < 7
- Wochen-Delta wenn previous_bio vorhanden
- Feedback-Vector beeinflusst Prompt
- Brain-Resolver wird bei JEDEM Call neu aufgeloest
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jarvis.board.aggregator import BoardAggregator
from jarvis.board.profile import BioGenerator, BioStore, make_resolver_from_brain
from jarvis.board.prompts import render_bio_prompt
from jarvis.board.store import BoardStore
from jarvis.core.protocols import BrainDelta, BrainRequest


# ----------------------------------------------------------------------
# Forbidden-Words — der Anti-Cliche-Gate (Stil-Guard)
# ----------------------------------------------------------------------

FORBIDDEN_WORDS = [
    "leidenschaftlich",
    "großartig",
    "grossartig",
    "beeindruckend",
    "power-user",
    "power user",
    "passionate",
    "amazing",
    "dedicated",
    "champion",
    "hingabe",
    "wunderbar",
]


def _assert_not_cliche(text: str) -> None:
    low = text.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in low, f"Bio ist cliche, enthaelt: {word!r}\nOutput:\n{text}"


# ----------------------------------------------------------------------
# FakeBrain — implementiert das echte ``Brain.complete``-Protocol
# ----------------------------------------------------------------------

class FakeBrain:
    """Mimt ``Brain.complete(BrainRequest) -> AsyncIterator[BrainDelta]``.

    ``async def`` mit ``yield`` macht das Method zu einem AsyncGenerator —
    Aufrufer bekommt direkt einen ``AsyncIterator``, kompatibel mit
    ``aggregate()`` aus ``jarvis.brain.streaming``.
    """

    def __init__(self, script: list[str] | str) -> None:
        self._responses = [script] if isinstance(script, str) else list(script)
        self.calls: list[BrainRequest] = []

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.calls.append(request)
        text = self._responses.pop(0) if self._responses else ""
        yield BrainDelta(content=text)
        yield BrainDelta(
            finish_reason="stop", usage={"input_tokens": 100, "output_tokens": 50},
        )


class FailingBrain:
    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        raise RuntimeError("simulated 429")
        yield  # pragma: no cover — macht Methode zum AsyncGenerator


class EmptyBrain:
    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="")
        yield BrainDelta(finish_reason="stop")


# ----------------------------------------------------------------------
# DB-Fixtures
# ----------------------------------------------------------------------

def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1e9)


def _power_user_db(tmp_path: Path) -> tuple[Path, Path]:
    jsonl_dir = tmp_path / "flight_recorder"
    jsonl_dir.mkdir(parents=True)
    base = datetime.now().astimezone().replace(hour=23, minute=0, second=0, microsecond=0)

    events = []
    for day_off in range(14):
        day = base - timedelta(days=day_off)
        for idx, tool in enumerate(["bash", "search_web", "write_file", "read_file", "grep_repo"]):
            events.append({
                "ts_ns": _ns(day + timedelta(minutes=idx * 4)),
                "trace_id": f"{day_off:032x}",
                "event": "ActionExecuted",
                "layer": "orchestrator",
                "payload": {"tool_name": tool, "success": True, "duration_ms": 100},
            })
        for k in range(2):
            events.append({
                "ts_ns": _ns(day + timedelta(hours=1, minutes=k * 10)),
                "trace_id": f"{day_off:032x}sub{k}",
                "event": "JarvisAgentTaskCompleted",
                "layer": "agents",
                "payload": {"success": True, "duration_s": 900.0},
            })
        for k in range(3):
            events.append({
                "ts_ns": _ns(day + timedelta(minutes=30 + k * 20)),
                "trace_id": f"{day_off:032x}v{k}",
                "event": "TranscriptFinal",
                "layer": "speech.stt",
                "payload": {"transcript": {"text": "<redacted>"}},
            })
    (jsonl_dir / "hist.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8",
    )
    db = tmp_path / "board" / "personal.db"
    BoardAggregator(jsonl_dir=jsonl_dir, db_path=db).run()
    return jsonl_dir, db


def _casual_user_db(tmp_path: Path) -> tuple[Path, Path]:
    jsonl_dir = tmp_path / "flight_recorder"
    jsonl_dir.mkdir(parents=True)
    base = datetime.now().astimezone().replace(hour=10, minute=0, second=0, microsecond=0)
    events = [
        {
            "ts_ns": _ns(base),
            "trace_id": "a" * 32,
            "event": "ActionExecuted",
            "layer": "orchestrator",
            "payload": {"tool_name": "bash", "success": True, "duration_ms": 50},
        },
        {
            "ts_ns": _ns(base + timedelta(minutes=5)),
            "trace_id": "a" * 32,
            "event": "TaskCompleted",
            "layer": "tasks",
            "payload": {"task_id": "t1", "duration_ms": 200},
        },
    ]
    (jsonl_dir / "one.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8",
    )
    db = tmp_path / "board" / "personal.db"
    BoardAggregator(jsonl_dir=jsonl_dir, db_path=db).run()
    return jsonl_dir, db


def _make_generator(brain: object, jsonl: Path, db: Path) -> BioGenerator:
    return BioGenerator(
        brain_resolver=make_resolver_from_brain(brain),
        store=BoardStore(db),
        bio_store=BioStore(db),
        jsonl_dir=jsonl,
    )


# ----------------------------------------------------------------------
# Tests — Prompt-Rendering (neue Tuple-Rueckgabe)
# ----------------------------------------------------------------------

def test_render_returns_system_user_tuple() -> None:
    system, user = render_bio_prompt({"days_observed": 14, "top_tools": ["bash"]})
    assert "Ich-Form" in system
    assert "beissend" in system or "scharf" in system.lower()
    assert "bash" in user
    assert "14 Tage" in user


def test_render_cold_start_hint_under_7_days() -> None:
    _, user = render_bio_prompt({"days_observed": 3})
    assert "COLD-START" in user
    assert "Mehr in 4 Tagen" in user


def test_render_no_cold_start_after_7_days() -> None:
    _, user = render_bio_prompt({"days_observed": 7})
    assert "COLD-START" not in user


def test_render_previous_bio_block() -> None:
    _, user = render_bio_prompt({
        "days_observed": 30,
        "previous_bio": "Du bist immer noch praezise und ungeduldig.",
    })
    assert "FRUEHERE BIO" in user
    assert "praezise" in user


def test_render_feedback_vector_haerter_lowers_threshold() -> None:
    _, user = render_bio_prompt({
        "days_observed": 30,
        "feedback_vector": {"trifft": 1, "trifft_nicht": 0, "haerter": 3},
    })
    assert "BISSIGER" in user
    assert "Hoeflichkeitsschwelle" in user


def test_render_feedback_vector_trifft_nicht_dominant() -> None:
    _, user = render_bio_prompt({
        "days_observed": 30,
        "feedback_vector": {"trifft": 0, "trifft_nicht": 4, "haerter": 1},
    })
    assert "konkreter an den Daten" in user


def test_render_episodes_block() -> None:
    _, user = render_bio_prompt({
        "days_observed": 14,
        "episodes": [
            "User hat ueber Tests geschimpft, aber dann doch alle gefixt.",
            "Lange Session in VS Code mit Python.",
            "Browser-Tab mit GitHub PRs offen.",
        ],
    })
    assert "EPISODEN" in user
    assert "geschimpft" in user


def test_render_missions_block() -> None:
    _, user = render_bio_prompt({
        "days_observed": 14,
        "missions": {"approved": 5, "failed": 2, "aborted": 1, "open_overdue": ["Refactor X"]},
    })
    assert "MISSIONS" in user
    assert "Abgeschlossen: 5" in user
    assert "Refactor X" in user


def test_render_self_mod_block() -> None:
    _, user = render_bio_prompt({
        "days_observed": 14,
        "self_mod": {"tts.provider": 3, "ui.theme": 1},
    })
    assert "KONFIG-MUTATIONEN" in user
    assert "tts.provider: 3x" in user


# ----------------------------------------------------------------------
# Tests — Bio-Generation, drei Szenarien
# ----------------------------------------------------------------------

SCRIPTED_POWER_BIO = (
    "Ich beobachte dich seit 14 Tagen und merke: Du wechselst Tools schneller "
    "als Methoden. Fuenf distinct Tools, screenshot dominant, vor jedem Spawn "
    "ein Blick. Das ist nicht Misstrauen, das ist Methode. Ich nehm's nicht "
    "persoenlich. Glaub ich."
)

SCRIPTED_CASUAL_BIO = (
    "Ich kenne dich seit einem Tag. Das ist zu wenig fuer ein Urteil, aber "
    "genug fuer eine Vermutung: Du klickst, bevor du nachdenkst. Mehr in sechs Tagen."
)


@pytest.mark.asyncio
async def test_power_user_bio_not_cliche(tmp_path: Path) -> None:
    jsonl, db = _power_user_db(tmp_path)
    brain = FakeBrain(SCRIPTED_POWER_BIO)
    gen = _make_generator(brain, jsonl, db)
    result = await gen.generate_bio(
        memory_text="User ist Nicht-Coder. Autonom arbeiten bevorzugt.",
        soul_text="Jarvis ist lakonisch und trocken.",
        triggered_by="manual",
        model_hint="claude-opus-4-7",
    )
    assert result is not None
    _assert_not_cliche(result["text"])


@pytest.mark.asyncio
async def test_casual_user_bio_not_cliche(tmp_path: Path) -> None:
    jsonl, db = _casual_user_db(tmp_path)
    brain = FakeBrain(SCRIPTED_CASUAL_BIO)
    gen = _make_generator(brain, jsonl, db)
    result = await gen.generate_bio(memory_text="", soul_text="")
    assert result is not None
    _assert_not_cliche(result["text"])


# ----------------------------------------------------------------------
# Tests — Failure Handling
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_brain_outage_keeps_old_bio(tmp_path: Path) -> None:
    jsonl, db = _power_user_db(tmp_path)
    good = _make_generator(FakeBrain("Erste stabile Bio mit konkreten Zahlen."), jsonl, db)
    await good.generate_bio()

    bad = _make_generator(FailingBrain(), jsonl, db)
    assert await bad.generate_bio(triggered_by="weekly") is None
    latest = BioStore(db).latest()
    assert latest is not None
    assert "stabile Bio" in latest["text"]


@pytest.mark.asyncio
async def test_empty_brain_response_does_not_persist(tmp_path: Path) -> None:
    jsonl, db = _power_user_db(tmp_path)
    gen = _make_generator(EmptyBrain(), jsonl, db)
    assert await gen.generate_bio() is None
    assert BioStore(db).latest() is None


@pytest.mark.asyncio
async def test_no_resolver_returns_none(tmp_path: Path) -> None:
    jsonl, db = _power_user_db(tmp_path)
    gen = BioGenerator(
        brain_resolver=None,
        store=BoardStore(db),
        bio_store=BioStore(db),
        jsonl_dir=jsonl,
    )
    assert await gen.generate_bio() is None


@pytest.mark.asyncio
async def test_resolver_fresh_per_call_enables_provider_switch(tmp_path: Path) -> None:
    """Brain wird bei JEDEM generate_bio() neu aufgeloest — Provider-Switch zieht sofort."""
    jsonl, db = _power_user_db(tmp_path)
    brain_a = FakeBrain(["Bio A — provider 1."])
    brain_b = FakeBrain(["Bio B — provider 2."])
    state = {"current": brain_a}

    def _switching_resolver() -> object:
        return state["current"]

    gen = BioGenerator(
        brain_resolver=_switching_resolver,
        store=BoardStore(db),
        bio_store=BioStore(db),
        jsonl_dir=jsonl,
    )
    r1 = await gen.generate_bio()
    assert r1 is not None and "Bio A" in r1["text"]

    state["current"] = brain_b
    r2 = await gen.generate_bio()
    assert r2 is not None and "Bio B" in r2["text"]


# ----------------------------------------------------------------------
# Tests — BioStore Feedback
# ----------------------------------------------------------------------

def test_bio_feedback_validates_kind(tmp_path: Path) -> None:
    db = tmp_path / "board" / "personal.db"
    store = BioStore(db)
    store.insert("placeholder bio", model_used="x", triggered_by="manual")
    latest = store.latest()
    assert latest is not None

    store.record_feedback(latest["generated_at"], "trifft")
    with pytest.raises(ValueError):
        store.record_feedback(latest["generated_at"], "ungueltig")


def test_bio_feedback_aggregation(tmp_path: Path) -> None:
    db = tmp_path / "board" / "personal.db"
    store = BioStore(db)
    store.insert("a", triggered_by="manual")
    a_iso = store.latest()["generated_at"]  # type: ignore[index]
    store.record_feedback(a_iso, "trifft")
    store.record_feedback(a_iso, "trifft")
    store.record_feedback(a_iso, "haerter")

    counts = store.recent_feedback(days=28)
    assert counts == {"trifft": 2, "trifft_nicht": 0, "haerter": 1}


def test_bio_previous_returns_second_newest(tmp_path: Path) -> None:
    db = tmp_path / "board" / "personal.db"
    store = BioStore(db)
    store.insert("erste bio", triggered_by="cold_start")
    # Sleep nicht noetig — ISO-Timestamps haben Mikrosekunden-Aufloesung,
    # aber zur Sicherheit zwei klar unterscheidbare Eintraege schreiben.
    import time as _t
    _t.sleep(0.01)
    store.insert("zweite bio (aktuell)", triggered_by="weekly")

    prev = store.previous()
    assert prev is not None
    assert prev["text"] == "erste bio"

    latest = store.latest()
    assert latest is not None
    assert latest["text"] == "zweite bio (aktuell)"


def test_bio_previous_none_when_only_one_record(tmp_path: Path) -> None:
    db = tmp_path / "board" / "personal.db"
    store = BioStore(db)
    store.insert("nur eine", triggered_by="cold_start")
    assert store.previous() is None


# ----------------------------------------------------------------------
# Tests — BoardStore.days_observed
# ----------------------------------------------------------------------

def test_days_observed_zero_on_empty_db(tmp_path: Path) -> None:
    db = tmp_path / "board" / "personal.db"
    store = BoardStore(db)
    assert store.days_observed() == 0


def test_days_observed_counts_from_first_active_day(tmp_path: Path) -> None:
    jsonl, db = _power_user_db(tmp_path)
    store = BoardStore(db)
    # Power-User-Fixture hat 14 Tage Aktivitaet → days_observed sollte 13 sein
    # (Differenz today - earliest_active_day, exklusiv den Endpunkt).
    days = store.days_observed()
    assert days >= 13, f"erwarte mindestens 13 Tage, bekam {days}"


# ----------------------------------------------------------------------
# Tests — _collect_facts: previous bio + feedback eingespeist
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collect_facts_passes_previous_bio_and_feedback(tmp_path: Path) -> None:
    jsonl, db = _power_user_db(tmp_path)
    bio_store = BioStore(db)
    # Two bios required: BioStore.previous() uses LIMIT 1 OFFSET 1, so it
    # returns the second-newest entry. The older one is the "FRUEHERE BIO"
    # the Wochen-Delta context references.
    bio_store.insert("erste bio mit charakter", triggered_by="weekly")
    # Tiny sleep so the second insert gets a strictly-later ISO timestamp.
    import time
    time.sleep(0.01)
    bio_store.insert("aktuelle bio von letzter Woche", triggered_by="weekly")
    bio_store.record_feedback(bio_store.latest()["generated_at"], "haerter")  # type: ignore[index]

    brain = FakeBrain("Neue, bissigere Bio.")
    gen = BioGenerator(
        brain_resolver=make_resolver_from_brain(brain),
        store=BoardStore(db),
        bio_store=bio_store,
        jsonl_dir=jsonl,
    )
    await gen.generate_bio()

    assert brain.calls, "Brain wurde nicht gerufen"
    request = brain.calls[0]
    user_text = request.messages[0].content
    assert isinstance(user_text, str)
    assert "FRUEHERE BIO" in user_text
    assert "BISSIGER" in user_text
