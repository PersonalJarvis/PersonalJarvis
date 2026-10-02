"""The clause chunker starts speech early without splitting abbreviations."""

from __future__ import annotations

from jarvis.voice_engine.chunker import ClauseChunker


def _run(deltas: list[str], **kwargs: int) -> list[str]:
    chunker = ClauseChunker(**kwargs)
    out: list[str] = []
    for delta in deltas:
        out.extend(chunker.feed(delta))
    return out + chunker.flush()


def test_sentence_end_cuts_once_whitespace_follows() -> None:
    chunker = ClauseChunker()
    assert chunker.feed("The moon is far away.") == []  # no whitespace yet: could be "away.com"
    assert chunker.feed(" It") == ["The moon is far away."]
    assert chunker.flush() == ["It"]


def test_first_clause_leaves_at_a_comma_but_later_ones_wait() -> None:
    text = "Sure, a black hole is a region of space, from which nothing escapes, not even light."
    clauses = _run([text[i : i + 5] for i in range(0, len(text), 5)])
    assert clauses[0] == "Sure, a black hole is a region of space,"
    assert clauses[-1].endswith("not even light.")


def test_short_first_fragment_is_not_cut() -> None:
    assert _run(["Ja, gern. Das geht."]) == ["Ja, gern.", "Das geht."]


def test_abbreviations_and_ordinals_do_not_end_a_sentence() -> None:
    clauses = _run(["Das ist z. B. am 3. Oktober so. Danach nicht mehr."])  # i18n-allow
    assert clauses == ["Das ist z. B. am 3. Oktober so.", "Danach nicht mehr."]  # i18n-allow


def test_runaway_text_is_split_at_a_space() -> None:
    text = "word " * 80
    clauses = _run([text], max_chars=50)
    assert all(len(c) <= 50 for c in clauses)
    assert " ".join(clauses).split() == text.split()
