"""Joining window transcripts back into one text, without the seam.

Why the windows overlap at all
------------------------------
A long dictation cannot be uploaded as one file, and cutting it into pieces at
exact clock positions cuts words in half. The final transcription therefore
cuts at the quietest point near the nominal length AND lets consecutive windows
share a second or two of audio, so a word that straddles a boundary is spoken
in full inside at least one window.

That guarantee is bought with a duplicate: the shared audio is transcribed
twice, so a naive join reads "the final version the final version is ready".
Removing the duplicate is this module's whole job.

Why it is not a string comparison
---------------------------------
The two transcriptions of the same audio are rarely byte-identical. The second
window heard the words with different context, so it may capitalise
differently, punctuate differently, or spell a number as a word. Matching
therefore happens on a NORMALISED view — case-folded, punctuation removed,
Unicode-composed — while what gets kept is the original text, untouched.

Why it must not assume spaces
-----------------------------
Chinese, Japanese, Thai, Lao, Khmer and Burmese are written without spaces
between words, so a token-based overlap search finds nothing there and a
token-based join inserts spaces a reader of those scripts would call wrong. The
search falls back to characters, and the join asks the two boundary characters
whether a space belongs between them. No language is the default one
(AGENTS.md §1).

Pure functions over strings: no model, no I/O, no configuration.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

#: Longest overlap the search will consider, in words. A window overlap is one
#: or two seconds of speech — roughly two to six words — so this is generous by
#: a wide margin while keeping the search cheap and stopping a coincidental
#: repetition far from the seam from being mistaken for one.
MAX_OVERLAP_WORDS = 40

#: The same bound for the character-level search used on space-free scripts.
#: Two seconds of Mandarin is on the order of ten characters.
MAX_OVERLAP_CHARS = 80

#: Below this, an overlap match is more likely to be a coincidence ("und",
#: "the") than the seam. One matching word is not evidence; two are, because
#: the words on either side of a seam come from the same audio.
MIN_OVERLAP_WORDS = 2

#: Same idea for characters — a single shared character means nothing.
MIN_OVERLAP_CHARS = 2

_PUNCTUATION = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE = re.compile(r"\s+", flags=re.UNICODE)


def _normalize(text: str) -> str:
    """The comparison view: same words, none of the presentation.

    NFKC first so a composed and a decomposed umlaut compare equal — the same
    normalisation the transcript filter applies — then case folding and
    punctuation removal, because the second reading of a seam routinely
    capitalises or punctuates it differently.
    """
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", folded)).strip()


#: Unicode blocks written WITHOUT spaces between words. Korean is deliberately
#: absent: Hangul is written with spaces, so treating it as space-free would
#: run its words together.
_SPACE_FREE_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2FFF),  # CJK radicals, Kangxi
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0xFF66, 0xFF9F),  # Halfwidth Katakana
    (0x0E00, 0x0E7F),  # Thai
    (0x0E80, 0x0EFF),  # Lao
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
)


def _is_space_free(char: str) -> bool:
    """Whether ``char`` belongs to a script that writes without word spaces."""
    if not char:
        return False
    code = ord(char)
    return any(low <= code <= high for low, high in _SPACE_FREE_RANGES)


def _join_boundary(left: str, right: str) -> str:
    """``left`` and ``right`` joined the way their scripts are written."""
    if not left:
        return right
    if not right:
        return left
    if _is_space_free(left[-1]) and _is_space_free(right[0]):
        return left + right
    return f"{left} {right}"


def _word_overlap(left_words: list[str], right_words: list[str]) -> int:
    """How many trailing words of ``left`` repeat as leading words of ``right``.

    The LARGEST match wins: a one-second overlap can repeat several words, and
    stopping at the first (shortest) match would leave most of the duplicate in
    place.
    """
    limit = min(len(left_words), len(right_words), MAX_OVERLAP_WORDS)
    for size in range(limit, MIN_OVERLAP_WORDS - 1, -1):
        if left_words[-size:] == right_words[:size]:
            return size
    return 0


def _char_overlap(left: str, right: str) -> int:
    """The same search on characters, for scripts written without spaces."""
    limit = min(len(left), len(right), MAX_OVERLAP_CHARS)
    for size in range(limit, MIN_OVERLAP_CHARS - 1, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def _drop_leading_words(text: str, count: int) -> str:
    """``text`` without its first ``count`` NORMALISED words.

    Walks the ORIGINAL string rather than re-joining normalised tokens, so the
    kept remainder keeps its own punctuation and capitalisation — the point of
    normalising only for comparison.
    """
    if count <= 0:
        return text
    prefix = " ".join(_normalize(text).split()[:count])
    return _drop_normalized_prefix(text, prefix)


def _drop_normalized_prefix(text: str, prefix: str) -> str:
    """Map an overlap back to an exact source boundary, or keep the text.

    A space-delimited source token can represent several comparison tokens
    (``read-only``, ``it's``, ``2.5``). Counting source words would discard
    unrelated words after the seam, including negations. Normalize prefixes
    with the SAME function used for matching instead. Whole-prefix Unicode
    normalization also keeps composed characters and compatibility expansions
    intact; normalizing one character at a time cannot do that.

    Only the matched prefix and its trailing separators are scanned. If the
    seam falls inside an indivisible expansion, retain the original text:
    repeating part of a word is preferable to deleting the rest of it.
    """
    if not prefix:
        return text
    if prefix == _normalize(text):
        return ""
    end: int | None = None
    for index in range(1, len(text) + 1):
        normalized = _normalize(text[:index])
        if normalized == prefix and (
            end is None or all(unicodedata.category(c).startswith("M") for c in text[end:index])
        ):
            end = index
        elif len(normalized) > len(prefix):
            break
    # Remove punctuation attached to the matched word, then whitespace. Do
    # not skip every non-word character: a following "-5" or "--verbose"
    # belongs to the continuation, including its sign/prefix.
    return text[end:].lstrip(",.;:!?、。，；：！？").lstrip() if end is not None else text


def merge_transcripts(parts: Sequence[str]) -> str:
    """One text from consecutive, overlapping window transcripts.

    Each part is appended after the words it repeats from the accumulated text
    have been removed. Parts that are empty (a window of silence) are skipped
    without breaking the chain — the NEXT part is still matched against
    everything kept so far, so one silent window between two speaking ones
    cannot smuggle a duplicate through.

    Never raises and never drops a part it cannot match: an unmatched part is
    appended whole, because a duplicated phrase is a blemish while a missing
    one is lost speech.
    """
    merged = ""
    merged_words: list[str] = []
    for raw in parts:
        piece = str(raw or "").strip()
        if not piece:
            continue
        if not merged:
            merged = piece
            merged_words = _normalize(piece).split()
            continue

        piece_words = _normalize(piece).split()
        overlap = _word_overlap(merged_words, piece_words)
        if overlap:
            kept = _drop_leading_words(piece, overlap)
            if kept:
                merged = _join_boundary(merged, kept)
                merged_words = _normalize(merged).split()
            # A window whose transcript was ENTIRELY a repetition adds nothing;
            # dropping it is the correct outcome, not a lost window.
            continue

        # No word-level seam. Either the scripts write without spaces, or the
        # two readings of the seam disagree on every word in it.
        left_norm = _normalize(merged)
        right_norm = _normalize(piece)
        chars = (
            _char_overlap(left_norm, right_norm)
            if _is_space_free(left_norm[-1:]) or _is_space_free(right_norm[:1])
            else 0
        )
        if chars:
            # Character offsets are taken on the normalised view, and the
            # original may differ in length; scanning forward from the start
            # keeps the two in step without rebuilding the string.
            kept = _drop_leading_chars(piece, chars)
            if kept:
                merged = _join_boundary(merged, kept)
                merged_words = _normalize(merged).split()
            continue

        merged = _join_boundary(merged, piece)
        merged_words = _normalize(merged).split()
    return merged.strip()


def transcript_token_count(text: str) -> int:
    """How many spoken tokens ``text`` plausibly represents.

    The unit a transcript is compared against seconds of speech in: words for
    scripts written with spaces, characters for the space-free scripts (an
    ideograph or kana is roughly one spoken syllable). Counting a Chinese
    sentence as "one word" would make every Chinese transcript look truncated,
    and no language is the default one (AGENTS.md §1).
    """
    total = 0
    for token in _normalize(text).split():
        if all(_is_space_free(char) for char in token):
            total += len(token)
        else:
            total += 1
    return total


def _drop_leading_chars(text: str, count: int) -> str:
    """``text`` without the first ``count`` characters OF ITS NORMALISED view.

    Use the same source-boundary mapping as the word search: punctuation adds
    separators and NFKC can combine or expand characters, so per-character
    counts are not offsets in the normalized whole string.
    """
    if count <= 0:
        return text
    return _drop_normalized_prefix(text, _normalize(text)[:count])


__all__ = [
    "MAX_OVERLAP_CHARS",
    "MAX_OVERLAP_WORDS",
    "MIN_OVERLAP_CHARS",
    "MIN_OVERLAP_WORDS",
    "merge_transcripts",
    "transcript_token_count",
]
