"""Cut a streaming LLM reply into speakable clauses.

The first clause leaves as early as possible (a comma is enough once a few
words arrived) so speech can start while the model is still writing; later
clauses prefer sentence ends. Abbreviations and ordinal numbers ("am 3.
Oktober", "z. B.", "e.g.") do not end a sentence.
"""

from __future__ import annotations

import re

_ABBREVIATIONS = frozenset(
    {
        # German
        "z", "b", "bzw", "usw", "ca", "dr", "nr", "str", "d", "h", "u", "a", "etc",
        "vgl", "inkl", "evtl", "ggf", "bspw", "sog", "zzgl", "abs", "st", "mio", "mrd",
        # English
        "e", "g", "i", "mr", "mrs", "ms", "vs", "approx", "no", "jr", "sr",
    }
)
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*(?=\s)")
_CLAUSE_END = re.compile(r"[,;:–—](?=\s)")


class ClauseChunker:
    def __init__(self, *, first_min_chars: int = 12, min_chars: int = 40, max_chars: int = 220):
        self.first_min_chars = first_min_chars
        self.min_chars = min_chars
        self.max_chars = max_chars
        self._buffer = ""
        self._emitted = 0

    def feed(self, delta: str) -> list[str]:
        self._buffer += delta
        out: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            clause, self._buffer = self._buffer[:cut].strip(), self._buffer[cut:].lstrip()
            if clause:
                out.append(clause)
                self._emitted += 1
        return out

    def flush(self) -> list[str]:
        rest, self._buffer = self._buffer.strip(), ""
        if rest:
            self._emitted += 1
            return [rest]
        return []

    def _find_cut(self) -> int | None:
        text = self._buffer
        for match in _SENTENCE_END.finditer(text):
            if not self._is_abbreviation(text, match.start()):
                return match.end()
        threshold = self.first_min_chars if self._emitted == 0 else self.min_chars
        for match in _CLAUSE_END.finditer(text):
            if match.end() >= threshold:
                return match.end()
        if len(text) > self.max_chars:
            space = text.rfind(" ", 0, self.max_chars)
            return space if space > 0 else self.max_chars
        return None

    @staticmethod
    def _is_abbreviation(text: str, dot: int) -> bool:
        if text[dot] != ".":
            return False
        before = text[:dot]
        word = re.search(r"([A-Za-zÄÖÜäöüß]+|\d+)$", before)  # i18n-allow: German letters
        if word is None:
            return False
        token = word.group(1)
        if token.isdigit():
            # German ordinals ("am 3. Oktober", "1. Platz") use a dot. Treating
            # every one- or two-digit number as an ordinal costs at most a
            # later cut when a sentence really ends on a small number.
            return len(token) <= 2
        return token.lower() in _ABBREVIATIONS
