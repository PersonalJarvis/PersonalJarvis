"""Chat history titles: a topic, never the greeting the conversation opened with."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from jarvis.core.protocols import BrainDelta, BrainRequest
from jarvis.sessions import chat_titles
from jarvis.sessions.chat_titles import (
    ChatTitler,
    TitleBook,
    TitleRequest,
    clean_model_title,
    parse_answer,
    tidy_title,
)

# --------------------------------------------------------------------- rules


@pytest.mark.parametrize(
    "utterances",
    [
        ["Was geht ab"],
        ["Hallo"],
        ["Was"],
        ["Kannst"],
        ["Kannst du"],
        ["Was geht ab", "ähm, nichts", "ähm", ", ähm, ähm"],
        ["Was geht ab", "Nicht so viel", ". Hallo"],
        ["Hi. What's good up", "Mm."],
        ["Olá, tudo bem?", "hum"],  # i18n-allow: pt voice input
        [""],
        [],
    ],
)
def test_greetings_and_fragments_have_no_topic(utterances: list[str]) -> None:
    assert tidy_title(utterances) == ""


@pytest.mark.parametrize(
    ("utterances", "title"),
    [
        (["Hallo was geht ab Kannst du bitte meine gmail Analysieren"],
         "Meine gmail Analysieren"),
        (["Ich verspre... Was ist Personal Jarvis? Was ist es überhaupt"],
         "Was ist Personal Jarvis"),
        (["Weißt du noch worum es ging"], "Weißt du noch worum es ging"),
        (["Browser acceptance"], "Browser acceptance"),
        (["Hey George, hallo", "Can you please summarize my inbox"], "Summarize my inbox"),
        (["Hi, was geht ab? Kannst du bitte schnell sagen, was alles zu tun ist?"],
         "Sagen, was alles zu tun ist"),
        (["Olá, tudo bem? Podes por favor resumir a minha caixa de entrada"],  # i18n-allow
         "Resumir a minha caixa de entrada"),  # i18n-allow
    ],
)
def test_the_request_survives_its_lead_in(utterances: list[str], title: str) -> None:
    assert tidy_title(utterances) == title


def test_split_voice_segments_are_read_as_one_sentence() -> None:
    title = tidy_title([
        "Hey George, was geht ab",
        "Kannst du bitte mal",
        "einen ähm einen Claude Code Agent spawnen. Er soll einen Deep Dive machen",
    ])
    assert title == "Einen Claude Code Agent spawnen"


def test_transcription_annotations_are_dropped() -> None:
    assert tidy_title(["Was geht ab [tongue click", "Kannst du bitte das Wetter prüfen"]) == (
        "Das Wetter prüfen"
    )
    assert tidy_title(["[laughs] Plan the next release"]) == "Plan the next release"


def test_long_titles_end_at_a_clause_or_say_they_were_cut() -> None:
    clause = tidy_title([
        "Kannst du für mich bitte einen neuen Jarvis-Agent erstellen namens Trendfinder, "
        "und ich möchte, dass er jeden Tag die besten Projekte analysiert"
    ])
    assert clause == "Einen neuen Jarvis-Agent erstellen namens Trendfinder"
    cut = tidy_title(["Research " + " ".join(f"topic{n}" for n in range(30))])
    assert cut.endswith("…") and len(cut) <= chat_titles.TITLE_MAX_CHARS


# --------------------------------------------------------------------- model


def test_model_answers_are_parsed_by_number_and_cleaned() -> None:
    answer = '1: "Security-Audit für den Workspace".\n2: -\nnoise\n3) **Wetter in Berlin**\n9: x'
    assert parse_answer(answer, 3) == {
        0: "Security-Audit für den Workspace",
        1: "",
        2: "Wetter in Berlin",
    }
    assert clean_model_title("—") == ""


@dataclass
class _ScriptedBrain:
    """Answers every request with one scripted text; records the prompts."""

    answer: str
    prompts: list[BrainRequest] = field(default_factory=list)

    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        self.prompts.append(request)
        yield BrainDelta(content=self.answer)


class _FailingBrain:
    async def complete(self, request: BrainRequest) -> AsyncIterator[BrainDelta]:
        raise RuntimeError("subscription rate limited")
        yield BrainDelta()  # pragma: no cover - makes this an async generator


def _voice(conv_id: str, user: list[str], *, settled: bool = True, seed: str = "") -> TitleRequest:
    return TitleRequest(
        kind=chat_titles.KIND_VOICE,
        conv_id=conv_id,
        version="2000" if settled else "",
        message_count=len(user),
        updated_ms=2_000,
        settled=settled,
        seed=seed or (user[0] if user else ""),
        loader=lambda: [("user", text) for text in user] + [("assistant", "Klar.")],
    )


def _drain_queue(titler: ChatTitler) -> None:
    """Run the queued work on this thread, the way the worker would."""
    batch = list(titler._queue.values())
    titler._queue.clear()
    titler.drain(batch)


def _titler(brain: object | None) -> ChatTitler:
    titler = ChatTitler(TitleBook(), brain_factory=lambda: brain)
    # Keep the work on the test thread.
    titler._ensure_worker = lambda: None  # type: ignore[method-assign]
    return titler


def test_listing_answers_at_once_and_the_model_names_settled_chats() -> None:
    brain = _ScriptedBrain("1: Claude-Code-Agent für Deep Dive\n2: -")
    titler = _titler(brain)
    requests = [
        _voice("a", ["Hey George, was geht ab", "Kannst du einen Agent spawnen"]),
        _voice("b", ["Hallo"]),
    ]
    first = titler.titles_for(requests)
    assert first == {("voice", "a"): "", ("voice", "b"): ""}

    _drain_queue(titler)
    assert titler.titles_for(requests) == {
        ("voice", "a"): "Claude-Code-Agent für Deep Dive",
        ("voice", "b"): "",
    }
    assert len(brain.prompts) == 1  # both conversations in one call
    prompt = brain.prompts[0].messages[0].content
    assert "Conversation 2:" in prompt and "Kannst du einen Agent spawnen" in prompt


def test_unsettled_chats_get_rules_only() -> None:
    brain = _ScriptedBrain("1: Should not be asked")
    titler = _titler(brain)
    request = _voice("live", ["Kannst du bitte das Wetter prüfen"], settled=False)
    titler.titles_for([request])
    _drain_queue(titler)
    assert titler.titles_for([request]) == {("voice", "live"): "Das Wetter prüfen"}
    assert brain.prompts == []


def test_without_a_subscription_the_rules_title_stands() -> None:
    titler = _titler(None)
    request = _voice("a", ["Hallo", "Kannst du bitte das Wetter prüfen"])
    titler.titles_for([request])
    _drain_queue(titler)
    assert titler.titles_for([request]) == {("voice", "a"): "Das Wetter prüfen"}


def test_a_failing_model_keeps_the_rules_title_and_goes_quiet() -> None:
    titler = _titler(_FailingBrain())
    requests = [_voice(str(n), [f"Kannst du bitte Thema {n} recherchieren"]) for n in range(2)]
    for _ in range(chat_titles.MODEL_FAILURES_BEFORE_QUIET):
        titler.titles_for(requests)
        _drain_queue(titler)
    assert titler.titles_for(requests)[("voice", "0")] == "Thema 0 recherchieren"
    assert titler._queue == {}  # quiet: nothing re-queued for the model


def test_a_title_the_user_typed_is_kept() -> None:
    brain = _ScriptedBrain("1: Model title")
    titler = _titler(brain)
    request = TitleRequest(
        kind=chat_titles.KIND_TYPED,
        conv_id="t1",
        version="4",
        message_count=4,
        updated_ms=1,
        settled=True,
        seed="My release notes",
        loader=lambda: [("user", "Hallo was geht ab"), ("assistant", "Hi!")],
        auto_title=lambda text: " ".join(text.split()),
    )
    titler.titles_for([request])
    _drain_queue(titler)
    assert titler.titles_for([request]) == {("agent", "t1"): "My release notes"}
    assert brain.prompts == []


def test_a_short_typed_title_is_shown_as_is_before_the_read() -> None:
    titler = _titler(None)
    request = TitleRequest(
        kind=chat_titles.KIND_TYPED, conv_id="t2", version="1", message_count=1,
        updated_ms=1, settled=False, seed="Budget",
        loader=lambda: [("user", "Plan next month")], auto_title=lambda text: text,
    )
    # The rules alone would blank a one-word title; the user may have typed it.
    assert titler.titles_for([request]) == {("agent", "t2"): "Budget"}


def test_titles_survive_a_restart(tmp_path) -> None:
    book_path = tmp_path / "chat_titles.db"
    titler = ChatTitler(TitleBook(book_path), brain_factory=lambda: _ScriptedBrain("1: Wetter"))
    titler._ensure_worker = lambda: None  # type: ignore[method-assign]
    request = _voice("a", ["Kannst du das Wetter prüfen"])
    titler.titles_for([request])
    _drain_queue(titler)

    reopened = ChatTitler(TitleBook(book_path), brain_factory=lambda: None)
    reopened._ensure_worker = lambda: None  # type: ignore[method-assign]
    assert reopened.titles_for([request]) == {("voice", "a"): "Wetter"}
    assert reopened._queue == {}


def test_an_app_without_a_data_folder_never_queues_work() -> None:
    chat_titles.reset_for_tests()
    try:
        titler = chat_titles.titler_in(None)
        request = _voice("a", ["Hallo", "Kannst du das Wetter prüfen"], seed="Hallo")
        assert titler.titles_for([request]) == {("voice", "a"): ""}
        assert titler._queue == {} and titler._thread is None
    finally:
        chat_titles.reset_for_tests()
