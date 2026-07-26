"""Split a document into overlapping passages for embedding.

Why this module is the precondition for everything else (audit, 2026-07-26):
the embed stage cut every item at 8 000 characters and produced exactly ONE
vector for it. A 200 KB source file, a 4 000-message channel export or a long
design thread reached the vector space as its opening paragraph; the rest sat
in SQL, findable by exact keyword and invisible to meaning. Pulling more data
would have changed nothing a user could retrieve — the cap, not the connectors,
decided what was searchable.

Two things make a passage useful, and they pull against each other:

* **Small enough to be specific.** One vector averages everything inside it.
  A whole file collapses into a blur that matches every question weakly and
  none of them well.
* **Large enough to be self-contained.** A passage cut mid-sentence, or one
  that drops the heading it belongs under, retrieves as a fragment nobody can
  act on.

So the splitter prefers real boundaries — paragraph breaks, then line breaks,
then sentence ends — and only falls back to a hard cut when a single run of
text has none. Consecutive passages overlap, because the sentence that answers
a question is regularly the one straddling a boundary.

Code gets one extra rule: a fenced block is never cut open. Half a function
body is worse than useless — it retrieves as plausible code that does not do
what it appears to.

Pure text in, pure text out: no I/O, no provider knowledge, no config. The
embedding backends keep their own token ceilings; this module only bounds
characters, which is the thing a splitter can actually reason about.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Chunk",
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "chunk_text",
]

#: Target passage size. ~2 000 characters is roughly 500 tokens: specific
#: enough that one vector means one thing, long enough to carry its context.
DEFAULT_CHUNK_CHARS = 2000

#: How much of the previous passage each one repeats. The answer to a question
#: sits on a boundary often enough that ~15 % overlap measurably beats none.
DEFAULT_OVERLAP_CHARS = 300

#: Below this a trailing remnant is merged into the previous passage instead of
#: becoming a passage of its own — a 40-character chunk is noise in the index.
_MIN_TAIL_CHARS = 200

#: Boundaries to prefer, best first. Each is searched for in the tail of the
#: window, so a cut lands on a real seam rather than mid-word.
_BREAKS: tuple[str, ...] = ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ")

#: How far back from the ideal cut a boundary may be found. Beyond this the
#: passage would be so short that honouring the seam costs more than it buys.
_BREAK_WINDOW = 400

_FENCE = "```"


@dataclass(frozen=True, slots=True)
class Chunk:
    """One passage, with its position in the original text."""

    index: int
    text: str
    char_start: int
    char_end: int


def _fence_depth(text: str) -> int:
    """How many code fences are left OPEN in ``text`` (0 or 1 in practice)."""
    return text.count(_FENCE) % 2


def _cut_at(text: str, start: int, limit: int) -> int:
    """The best end offset for a passage starting at ``start``.

    Walks the preferred boundaries in order and takes the latest one inside the
    window. Returns the hard limit when the run of text offers no seam at all —
    a minified bundle or a base64 blob legitimately has none.
    """
    if limit >= len(text):
        return len(text)
    window_start = max(start + 1, limit - _BREAK_WINDOW)
    for token in _BREAKS:
        found = text.rfind(token, window_start, limit)
        if found > start:
            return found + len(token)
    return limit


def chunk_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[Chunk]:
    """Split ``text`` into overlapping passages, longest-boundary-first.

    A short text yields exactly one passage covering all of it, so the common
    case costs nothing and behaves as before. An empty text yields no passages
    at all — there is nothing to embed, and a vector of whitespace is a false
    hit waiting to happen.
    """
    body = text.strip()
    if not body:
        return []
    chunk_chars = max(200, int(chunk_chars))
    overlap_chars = max(0, min(int(overlap_chars), chunk_chars // 2))
    if len(body) <= chunk_chars:
        return [Chunk(index=0, text=body, char_start=0, char_end=len(body))]

    chunks: list[Chunk] = []
    start = 0
    while start < len(body):
        limit = min(start + chunk_chars, len(body))
        end = _cut_at(body, start, limit)

        # Never leave a code fence hanging open: extend to its closing fence
        # so a passage always contains whole blocks. Bounded by a second
        # window so one unterminated fence cannot swallow the entire document.
        if _fence_depth(body[start:end]):
            close = body.find(_FENCE, end)
            if close != -1 and close - start <= chunk_chars * 3:
                end = close + len(_FENCE)

        # A remnant too small to stand alone joins this passage instead.
        if len(body) - end < _MIN_TAIL_CHARS:
            end = len(body)

        piece = body[start:end].strip()
        if piece:
            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=piece,
                    char_start=start,
                    char_end=end,
                )
            )
        if end >= len(body):
            break
        # Step back by the overlap, but always make forward progress: without
        # the floor a pathological boundary could return the same cut forever.
        start = max(end - overlap_chars, start + 1)
    return chunks
