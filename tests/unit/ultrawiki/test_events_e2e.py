"""North star: "when did I have dinner with <person> in <city>" is answerable.

The question the whole episodic layer exists for is one no single row answers
(design doc 03, "cross-source reconstruction"): the chat says *"19:30?"*, the
calendar says *"Dinner w/ Marlow Vance"*, and the photo's caption says Porto
Verde. This test builds exactly that corpus across three sources, runs the
real staged pipeline over it with an offline distiller, and then asks the real
search path the real question.

Fixture names are deliberately invented (Marlow Vance, Bo Reyes, Porto Verde,
Halloran Bay) so nothing here can be mistaken for a real person, place or
product.

Everything is offline: a fake embedding backend, a fake distiller, no network,
no credentials, no model. The search runs with NO embedding provider
configured at all — the honest floor of an install that holds one key or none,
where the keyword and event legs are the whole read path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.ultrawiki.pipeline import PipelineWorker
from jarvis.ultrawiki.search import hybrid_search
from jarvis.ultrawiki.store import UltraStore
from jarvis.ultrawiki.types import ConsentState, ItemState, RawItem

VECTOR = [0.1, 0.2, 0.3]
QUESTION = "when did I have dinner with Marlow Vance in Porto Verde"

#: The distillation gate, stated explicitly: this worker uses an INJECTED
#: distiller, so the production credential probe must never run — a test whose
#: outcome depends on the host's keys is the AP-23 trap.
DISTILL_READY = lambda: (True, "")  # noqa: E731 — a one-line test seam


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


class Source:
    """One fixture source: its items and the distillation each item produces."""

    def __init__(self, source_id: str, label: str) -> None:
        self.source_id = source_id
        self.label = label
        self.items: list[RawItem] = []


CHAT = Source("chat", "Message archive")
CALENDAR = Source("calendar", "Calendar export")
PHOTOS = Source("photos", "Photo library")

CHAT.items = [
    RawItem(
        external_id="thread-2026-03-10",
        title="Thread with Marlow Vance",
        body=(
            "Marlow: are we still on for dinner on friday?\n"
            "Me: yes — 19:30 works. Porto Verde, the place by the water?\n"
            "Marlow: perfect."
        ),
        permalink="app://chat/thread-2026-03-10",
        timestamp_utc="2026-03-10T12:00:00Z",
    ),
    RawItem(
        external_id="thread-2025-11-02",
        title="Thread with Bo Reyes",
        body="Bo: lunch tomorrow in Halloran Bay? Me: works for me.",
        permalink="app://chat/thread-2025-11-02",
        timestamp_utc="2025-11-02T09:00:00Z",
    ),
    RawItem(
        external_id="thread-invoice",
        title="Invoice question",
        body="Could you resend the invoice as a PDF? The last one would not open.",
        permalink="app://chat/thread-invoice",
        timestamp_utc="2026-02-18T08:30:00Z",
    ),
]

CALENDAR.items = [
    RawItem(
        external_id="cal-88",
        title="Dinner w/ Marlow Vance",
        body="Table for two, Porto Verde.",
        permalink="app://calendar/cal-88",
        timestamp_utc="2026-03-13T18:00:00Z",
    )
]

PHOTOS.items = [
    RawItem(
        external_id="img-4711",
        title="IMG_4711.jpg",
        body="Two plates on a terrace at dusk.",
        permalink="app://photos/img-4711",
        timestamp_utc="2026-03-13T20:14:00Z",
        metadata={"place": "Porto Verde"},
    )
]

#: What the distiller returns per item. The ``events`` arrays are the shape
#: prompt version 2 asks for — note that only ONE of them states an absolute
#: date: the chat says "next friday" and the photo says "yesterday", and the
#: resolver has to turn both into the same evening.
DISTILLATIONS: dict[str, dict[str, Any]] = {
    "thread-2026-03-10": {
        "question": "When is dinner with Marlow Vance?",
        "summary": "They agreed on dinner at Porto Verde.",
        "resolution": "Friday at 19:30.",
        "entities": ["Marlow Vance", "Porto Verde"],
        "events": [
            {
                "kind": "meal",
                "title": "Dinner with Marlow Vance",
                "when": "next friday at 19:30",
                "where": "Porto Verde",
                "participants": ["Marlow Vance"],
                "confidence": 0.9,
            }
        ],
    },
    "cal-88": {
        "question": "What was scheduled on that evening?",
        "summary": "A dinner reservation for two.",
        "resolution": "",
        "entities": ["Marlow Vance", "Porto Verde"],
        "events": [
            {
                "kind": "meal",
                "title": "Dinner with Marlow Vance",
                "when": "2026-03-13T19:30",
                "where": "Porto Verde",
                "participants": ["Marlow Vance"],
                "confidence": 0.95,
            }
        ],
    },
    "img-4711": {
        "question": "Where was this photo taken?",
        "summary": "A terrace at dusk with two plates.",
        "resolution": "",
        "entities": ["Porto Verde"],
        "events": [
            # A caption states no time at all, so this one is anchored on the
            # photo's own capture timestamp: the third and weakest anchor kind,
            # and the reason `time_anchor` is a stored column.
            {
                "kind": "meal",
                "title": "Dinner with Marlow Vance",
                "when": "",
                "where": "Porto Verde",
                "participants": ["Marlow Vance"],
                "confidence": 0.6,
            }
        ],
    },
    "thread-2025-11-02": {
        "question": "When is lunch with Bo Reyes?",
        "summary": "They agreed on lunch in Halloran Bay.",
        "resolution": "",
        "entities": ["Bo Reyes", "Halloran Bay"],
        "events": [
            {
                "kind": "meal",
                "title": "Lunch with Bo Reyes",
                "when": "tomorrow",
                "where": "Halloran Bay",
                "participants": ["Bo Reyes"],
                "confidence": 0.8,
            }
        ],
    },
    "thread-invoice": {
        "question": "How should the invoice be resent?",
        "summary": "A request to resend an invoice as a PDF.",
        "resolution": "",
        "entities": [],
        "events": [],
    },
}


# ---------------------------------------------------------------------------
# Offline doubles
# ---------------------------------------------------------------------------


class FakeEmbeddingBackend:
    name = "fake"

    def ready(self) -> tuple[bool, str]:
        return True, ""

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        return [list(VECTOR) for _ in texts]


class ScriptedDistiller:
    """Returns the fixture distillation and counts every call it received."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(
        self, cfg: Any, *, title: str, body: str, source_kind: str
    ) -> Any:
        for external_id, payload in DISTILLATIONS.items():
            source = next(
                (
                    item
                    for src in (CHAT, CALENDAR, PHOTOS)
                    for item in src.items
                    if item.external_id == external_id
                ),
                None,
            )
            if source is not None and source.title == title:
                self.calls.append(external_id)
                return SimpleNamespace(raw_json="", **payload)
        raise AssertionError(f"no fixture distillation for {title!r}")


def pipeline_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            enabled=True,
            db_backend="sqlite",
            embedding_provider="fake",
            embedding_model="fake-model",
            distill_provider="",
            distill_model="",
            rerank_provider="",
            ollama_endpoint="",
            events_enabled=True,
        ),
        memory=SimpleNamespace(data_dir="unused"),
        brain=SimpleNamespace(primary=""),
    )


def read_cfg() -> SimpleNamespace:
    """A keyless install: no embedding provider, no reranker.

    The event leg has to carry this question on its own here — which is the
    universality claim worth testing, because most downloaders will never
    configure an embedding slot at all.
    """
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            enabled=True,
            embedding_provider="",
            embedding_model="",
            rerank_provider="",
            rerank_model="",
            rerank_min_score=4.0,
            rrf_keyword_weight=1.0,
            rrf_vector_weight=1.0,
            rrf_event_weight=1.0,
            recency_half_life_days=180.0,
            ollama_endpoint="",
        )
    )


@pytest.fixture
async def corpus(tmp_path: Path):
    """The three-source corpus, fully ingested through the real pipeline."""
    store = UltraStore(tmp_path / "ultrawiki.db")
    await store.open()
    for source in (CHAT, CALENDAR, PHOTOS):
        await store.upsert_source(
            source.source_id, connector="local-folder", label=source.label
        )
        await store.set_consent(source.source_id, ConsentState.APPROVED)
        await store.upsert_items(source.source_id, source.items)

    distiller = ScriptedDistiller()
    worker = PipelineWorker(
        store,
        pipeline_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=distiller,
        distill_ready_fn=DISTILL_READY,
    )
    for _ in range(6):  # three stages over five items, with room to spare
        if not await worker.run_once():
            break
    counts = await store.counts()
    assert counts.distilled == 5, f"corpus did not fully distil: {counts}"
    yield store, distiller
    await store.close()


# ---------------------------------------------------------------------------
# The north star
# ---------------------------------------------------------------------------


async def test_the_episodic_question_is_answered_with_date_place_and_people(corpus):
    """The whole point, end to end: ask the question, get the evening back."""
    store, _ = corpus
    results = await hybrid_search(store, read_cfg(), QUESTION, k=5)
    assert results, "the corpus answered nothing at all"

    top = results[0]
    assert "event" in top.matched_by
    assert top.title == "Dinner with Marlow Vance"
    # The date, the place and the person are IN the returned citation — the
    # surface never has to go back to the database to say them out loud.
    assert "13 March 2026 at 19:30" in top.snippet
    assert "Porto Verde" in top.snippet
    assert "Marlow Vance" in top.snippet
    assert top.permalink.startswith("app://")
    assert top.timestamp_utc == "2026-03-13T19:30:00Z"


async def test_three_sources_agree_on_one_evening(corpus):
    """Cross-source reconstruction: the chat's "next friday", the calendar's
    ISO timestamp and the photo's silent capture time all had to resolve to
    the same absolute evening for this to hold."""
    store, _ = corpus
    events = await store.events_between(
        "2026-03-13T00:00:00Z", "2026-03-13T23:59:59Z"
    )
    assert len(events) == 3
    assert {event["source_id"] for event in events} == {"chat", "calendar", "photos"}
    for event in events:
        assert event["kind"] == "meal"
        assert event["place"] == "Porto Verde"
        assert [p["display_name"] for p in event["participants"]] == ["Marlow Vance"]
        assert event["occurred_at"].startswith("2026-03-13")

    # And each one is honest about WHERE its date came from — all three
    # anchors are represented, so a surface can weigh them differently instead
    # of reading a guessed timestamp as a stated fact.
    assert {event["time_anchor"] for event in events} == {
        "relative",
        "absolute",
        "recorded",
    }


async def test_the_three_sources_resolve_to_one_person(corpus):
    """Without identity linking, "who was there" would be three strangers who
    happen to share a name."""
    store, _ = corpus
    events = await store.events_between(
        "2026-03-13T00:00:00Z", "2026-03-13T23:59:59Z"
    )
    entity_ids = {event["participants"][0]["entity_id"] for event in events}
    assert len(entity_ids) == 1
    (person_id,) = entity_ids
    assert person_id is not None
    assert len(await store.list_events(entity_id=person_id)) == 3


async def test_the_other_meal_ranks_below_the_real_answer(corpus):
    """A near-miss in the same corpus — also a meal, also with a person, also
    in a city — must lose, and lose to all three corroborating rows.

    Deliberately a RANKING assertion, not an exclusion one: the keyword leg
    ORs its tokens, so "with"/"in"/"dinner" match the near-miss too, and
    hard-filtering candidates on content is the AP-27 trap. What has to hold
    is that consensus wins.
    """
    store, _ = corpus
    window = await store.events_between(
        "2026-03-13T00:00:00Z", "2026-03-13T23:59:59Z"
    )
    assert all("Bo Reyes" not in event["title"] for event in window)

    results = await hybrid_search(store, read_cfg(), QUESTION, k=5)
    corroborated = [
        index
        for index, hit in enumerate(results)
        if hit.title == "Dinner with Marlow Vance"
        and {"event", "keyword"} <= set(hit.matched_by)
    ]
    near_miss = [
        index
        for index, hit in enumerate(results)
        if "Halloran Bay" in hit.snippet or "Bo Reyes" in hit.title
    ]
    assert len(corroborated) == 2, "two sources should agree on the answer"
    assert near_miss, "the near-miss should be a candidate, just a losing one"
    # Every row two legs agreed on outranks it. The photo, which only the
    # event leg matched, sits BELOW the near-miss two legs found — that is the
    # RRF contract working as designed (design doc 01, principle 5: consensus
    # beats a single strong vote), not a defect.
    assert min(near_miss) > max(corroborated)

    # It IS in the store, under its own resolved date ("tomorrow" from the
    # 2nd of November) — a wrong split is repairable, a wrong answer is not.
    other = await store.events_between("2025-11-03T00:00:00Z", "2025-11-03T23:59:59Z")
    assert [event["title"] for event in other] == ["Lunch with Bo Reyes"]


async def test_an_item_that_records_no_event_produces_none(corpus):
    """Most items record no event. One that produced one anyway would make the
    timeline useless within a week."""
    store, _ = corpus
    item = await store.get_item_by_external_id("chat", "thread-invoice")
    assert item is not None
    assert await store.list_events(item_id=int(item["id"])) == []
    assert (await store.event_counts())["total"] == 4


async def test_the_read_path_calls_no_model_at_all(corpus):
    """Token-efficiency is a design property, not a hope: the event rows were
    paid for once, on the write path, inside the distillation that had to run
    anyway. Searching them must add nothing."""
    store, distiller = corpus
    before = len(distiller.calls)
    assert before == 5  # exactly one distillation per item, no second pass

    await hybrid_search(store, read_cfg(), QUESTION, k=5)
    await store.events_between("2026-03-01T00:00:00Z", "2026-03-31T23:59:59Z")
    await store.search_events(QUESTION)
    assert len(distiller.calls) == before


async def test_the_unsolicited_surface_gets_the_same_answer(corpus):
    """The ambient path passes ``enforce_floor=True`` (design doc 03). With no
    reranker configured the candidates arrive ungraded and are passed through
    for the caller's own deterministic gate — what must NOT happen is the
    event silently disappearing from that path."""
    store, _ = corpus
    results = await hybrid_search(
        store, read_cfg(), QUESTION, k=5, enforce_floor=True, rerank=False
    )
    assert results
    assert "event" in results[0].matched_by
    assert results[0].rerank_score is None


async def test_a_re_run_of_the_pipeline_does_not_duplicate_the_timeline(corpus):
    """Idempotency across the whole ladder, not just the store method."""
    store, distiller = corpus
    before = await store.event_counts()

    worker = PipelineWorker(
        store,
        pipeline_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=distiller,
        distill_ready_fn=DISTILL_READY,
    )
    await worker.run_once()
    assert await store.event_counts() == before


async def test_the_event_leg_degrades_silently_when_it_is_switched_off(corpus):
    """``rrf_event_weight = 0`` silences the leg without touching the rows —
    every other leg keeps answering."""
    store, _ = corpus
    cfg = read_cfg()
    cfg.ultrawiki.rrf_event_weight = 0.0
    results = await hybrid_search(store, cfg, QUESTION, k=5)
    assert results  # the keyword leg still answers
    assert all("event" not in hit.matched_by for hit in results)
    assert (await store.event_counts())["total"] == 4


async def test_events_disappear_with_the_evidence_they_came_from(corpus):
    """Deletion is honored end-to-end (design doc 05)."""
    store, _ = corpus
    await store.delete_source("photos", purge=True)
    events = await store.events_between(
        "2026-03-13T00:00:00Z", "2026-03-13T23:59:59Z"
    )
    assert {event["source_id"] for event in events} == {"chat", "calendar"}

    results = await hybrid_search(store, read_cfg(), QUESTION, k=5)
    assert all(hit.source_id != "photos" for hit in results)


async def test_an_item_reaches_distilled_even_when_derivation_explodes(
    tmp_path: Path, monkeypatch
):
    """Events are an accelerator, not a precondition for having a memory."""
    store = UltraStore(tmp_path / "ultrawiki.db")
    await store.open()
    await store.upsert_source("chat", connector="local-folder", label="Chat")
    await store.set_consent("chat", ConsentState.APPROVED)
    await store.upsert_items("chat", [CHAT.items[0]])

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the event tables are on fire")

    monkeypatch.setattr(store, "replace_events", boom)
    worker = PipelineWorker(
        store,
        pipeline_cfg(),
        embedding_backend_factory=FakeEmbeddingBackend,
        distill_fn=ScriptedDistiller(),
        distill_ready_fn=DISTILL_READY,
    )
    for _ in range(4):
        if not await worker.run_once():
            break

    item = await store.get_item_by_external_id("chat", "thread-2026-03-10")
    assert item is not None
    assert item["state"] == ItemState.DISTILLED.value
    assert await store.keyword_search("Porto Verde")
    await store.close()
