"""The passage splitter — the precondition for full-depth ingestion.

Until this existed the embed stage cut every item at 8 000 characters and gave
it one vector, so a 200 KB file reached the vector space as its opening
paragraph. These tests pin the properties that make a passage retrievable
rather than merely present: full coverage, real boundaries, overlap across
seams, and code blocks that survive intact.
"""

from __future__ import annotations

from jarvis.ultrawiki.chunking import (
    DEFAULT_CHUNK_CHARS,
    chunk_text,
)


def test_a_short_text_is_one_passage_covering_all_of_it():
    chunks = chunk_text("A single short note.")
    assert len(chunks) == 1
    assert chunks[0].text == "A single short note."
    assert chunks[0].index == 0
    assert (chunks[0].char_start, chunks[0].char_end) == (0, 20)


def test_empty_text_yields_nothing_rather_than_a_blank_vector():
    """A vector built from whitespace is a false hit waiting to happen."""
    assert chunk_text("") == []
    assert chunk_text("   \n\t  ") == []


def test_every_character_of_a_long_text_lands_in_some_passage():
    """The whole point: nothing may be silently dropped.

    Reconstructing the text from the passages (accounting for the deliberate
    overlap) must lose no sentence — that is the property the 8 000-char cap
    violated for every large item.
    """
    paragraphs = [f"Paragraph {i} about topic number {i}." * 4 for i in range(120)]
    body = "\n\n".join(paragraphs)
    chunks = chunk_text(body)

    assert len(chunks) > 1
    joined = "".join(c.text for c in chunks)
    for i in (0, 37, 60, 119):
        assert f"Paragraph {i} about topic number {i}." in joined
    # Offsets must tile the text without leaving a gap.
    assert chunks[0].char_start == 0
    assert chunks[-1].char_end == len(body.strip())
    for previous, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.char_start <= previous.char_end, "a gap would drop text"


def test_passages_overlap_so_a_boundary_sentence_is_not_lost():
    """The sentence that answers a question is regularly on a seam."""
    body = "\n\n".join(f"Block {i} " + "filler " * 40 for i in range(30))
    chunks = chunk_text(body)
    assert len(chunks) > 2
    overlaps = [
        previous.char_end - nxt.char_start
        for previous, nxt in zip(chunks, chunks[1:], strict=False)
    ]
    assert all(o > 0 for o in overlaps), overlaps


def test_cuts_prefer_a_paragraph_break_over_mid_sentence():
    body = "\n\n".join("Sentence about widgets. " * 12 for _ in range(20))
    chunks = chunk_text(body)
    # A passage that ends mid-word is the failure this avoids.
    for chunk in chunks[:-1]:
        assert not chunk.text.endswith("Sen")
        assert chunk.text == chunk.text.strip()


def test_text_with_no_boundaries_at_all_is_still_split():
    """A minified bundle or a base64 blob legitimately has no seam.

    It must still be chunked — falling back to a hard cut is correct; refusing
    to split would put the whole blob back into one vector.
    """
    body = "x" * (DEFAULT_CHUNK_CHARS * 4)
    chunks = chunk_text(body)
    assert len(chunks) >= 3
    assert all(c.text for c in chunks)


def test_a_code_fence_is_never_left_open_in_a_passage():
    """Half a function retrieves as plausible code that does not do what it says."""
    code = "```python\n" + "\n".join(f"    line_{i}()" for i in range(150)) + "\n```"
    body = "Intro paragraph.\n\n" + code + "\n\nClosing paragraph."
    chunks = chunk_text(body)
    for chunk in chunks:
        assert chunk.text.count("```") % 2 == 0, chunk.text[:120]


def test_a_tiny_remnant_is_merged_rather_than_indexed_alone():
    """A 40-character passage is noise in the index."""
    body = ("word " * 500).strip() + "\n\nend."
    chunks = chunk_text(body)
    assert all(len(c.text) > 50 for c in chunks)


def test_indices_are_sequential_and_start_at_zero():
    body = "\n\n".join(f"Para {i}. " + "text " * 60 for i in range(25))
    chunks = chunk_text(body)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_the_splitter_always_makes_forward_progress():
    """A pathological boundary must not loop forever.

    Guarded explicitly because the overlap step-back is what could otherwise
    return the same cut indefinitely and hang the pipeline on one item.
    """
    body = ("a" + "\n") * 5000
    chunks = chunk_text(body, chunk_chars=200, overlap_chars=100)
    assert len(chunks) > 5
    assert chunks[-1].char_end == len(body.strip())


def test_source_code_is_not_shredded_into_half_lines():
    """The bug the maintainer's "why 2 000?" question uncovered.

    Source code is dense in blank lines. A passage would end on one, the
    overlap stepped `start` back BEHIND that same blank line, the next search
    found it again as the only paragraph break in its window, and every
    following pass returned the identical offset — crawling forward one
    character at a time. A 10 KB Python file became ~800 passages averaging 41
    characters: half a line each, useless as vectors and 20× the embedding
    cost. It only appeared below ~500-char targets, i.e. exactly the range
    worth using.
    """
    body = "\n".join(
        f"def function_{i}(argument):\n"
        f'    """Docstring for function {i}."""\n'
        f"    return argument + {i}\n"
        for i in range(80)
    )
    for size in (300, 400, 500, 900):
        chunks = chunk_text(body, chunk_chars=size, overlap_chars=size // 7)
        assert chunks
        average = sum(len(c.text) for c in chunks) / len(chunks)
        # Half the target is the floor the fix guarantees; the crawl produced
        # roughly a tenth of it.
        assert average >= size * 0.5, f"size={size} average={average:.0f}"
        assert len(chunks) < len(body) / (size * 0.4)


def test_the_walk_never_revisits_a_boundary_it_already_consumed():
    """Each passage must start strictly after the previous one."""
    body = "\n\n".join(f"Block {i}\n    line\n    line" for i in range(60))
    chunks = chunk_text(body, chunk_chars=400, overlap_chars=60)
    starts = [c.char_start for c in chunks]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    for previous, nxt in zip(chunks, chunks[1:], strict=False):
        assert nxt.char_start > previous.char_start


def test_overlap_can_never_exceed_half_a_passage():
    """Otherwise the walk stalls: each step would re-read most of the last."""
    body = "word " * 4000
    chunks = chunk_text(body, chunk_chars=500, overlap_chars=100_000)
    assert len(chunks) > 3
    assert chunks[-1].char_end == len(body.strip())
