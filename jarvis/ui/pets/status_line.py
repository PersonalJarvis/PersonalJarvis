"""Condense what Jarvis is doing into one short line for the pet's status bubble.

The bubble sits under a small floating figure, so it shows a *gist*: the first
sentence of a reply, the current tool, the live transcript. Everything a
human would skip when glancing over is stripped: markdown syntax, code
blocks, URLs, file paths and JSON / dict dumps. :class:`StatusFeed` then
drops repeats and rate-limits updates so a fast token stream does not make
the bubble flicker.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

ELLIPSIS = "\u2026"

_FENCE_RE = re.compile(r"(```|~~~).*?(?:\1|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]*)`")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL_RE = re.compile(r"(?:\b[a-zA-Z][a-zA-Z0-9+.-]*://|\bwww\.)\S+")
_WIN_PATH_RE = re.compile(r"(?<![\w/])(?:[A-Za-z]:[\\/]|\\\\)[^\s,;)\]}'\"]*")
_POSIX_PATH_RE = re.compile(r"(?<![\w.])(?:~|\.{1,2})?/(?:[\w.@+-]+/)*[\w.@+-]+/?")
_REL_PATH_RE = re.compile(r"(?<![\w/])[\w.@-]+(?:[\\/][\w.@-]+)+\.[A-Za-z0-9]{1,8}\b")
_DICT_RE = re.compile(r"\{[^{}]*\}")
_LIST_RE = re.compile(r"\[[^\[\]]*[\"':,{][^\[\]]*\]")
_OPEN_DUMP_RE = re.compile(r"[\[{]\s*[\"'{\[].*\Z", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_QUOTE_RE = re.compile(r"^\s*(?:>\s*)+")
_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_RULE_RE = re.compile(r"^\s*(?:[-*_=]\s*){3,}$")
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{2,}")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|~~)(?=\S)(.+?)(?<=\S)\1")
_UNDERSCORE_EM_RE = re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,80}>")
_STRAY_MARKS_RE = re.compile(r"(?:\*\*|__|~~|`)")
_EMPTY_BRACKETS_RE = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?])")
_REPEATED_PUNCT_RE = re.compile(r"([,;:])(?:\s*[,;:])+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?\u2026])\s+(?=\S)")
_WS_RE = re.compile(r"\s+")
_PATH_SPLIT_RE = re.compile(r"[\\/]")
_FILE_NAME_RE = re.compile(r"^[\w@+-][\w.@+-]{0,39}\.[A-Za-z0-9]{1,8}$")

#: Inline code longer than this is a snippet, not a word worth showing.
_MAX_INLINE_CODE = 30

#: A first sentence shorter than this borrows the next one (clipped if needed).
_SHORT_SENTENCE = 20

#: Only the head of a text is condensed. The gist is its first sentence, and
#: running the markdown patterns over pages of reply on a bus handler is waste.
MAX_INPUT_CHARS = 600

_SENTENCE_MARKS = ".!?:" + ELLIPSIS


def _inline_code(match: re.Match[str]) -> str:
    content = match.group(1).strip()
    return content if len(content) <= _MAX_INLINE_CODE else " "


def _path_to_name(match: re.Match[str]) -> str:
    """A file path shrinks to its file name; a folder path disappears."""
    name = _PATH_SPLIT_RE.split(match.group(0).rstrip("/\\"))[-1]
    return name if _FILE_NAME_RE.match(name) else " "


def _clean_line(line: str) -> str:
    line = _QUOTE_RE.sub("", line)
    line = _BULLET_RE.sub("", line)
    line = line.replace("|", " ")
    return line


def _strip_blocks(text: str) -> str:
    """Remove what can span lines: fenced code and JSON / dict / list dumps."""
    text = _FENCE_RE.sub("\n", text)
    previous = None
    while previous != text:  # nested dumps: remove the innermost level each pass
        previous = text
        text = _DICT_RE.sub(" ", text)
        text = _LIST_RE.sub(" ", text)
    return _OPEN_DUMP_RE.sub(" ", text)


def _strip_noise(text: str) -> str:
    """Clean one line: links, URLs, code, paths and markdown emphasis."""
    text = _IMAGE_RE.sub(lambda m: m.group(1), text)
    text = _LINK_RE.sub(lambda m: m.group(1), text)
    text = _URL_RE.sub(" ", text)
    text = _INLINE_CODE_RE.sub(_inline_code, text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _WIN_PATH_RE.sub(_path_to_name, text)
    text = _REL_PATH_RE.sub(_path_to_name, text)
    text = _POSIX_PATH_RE.sub(_path_to_name, text)
    for _ in range(2):  # **bold *italic*** needs two passes
        text = _EMPHASIS_RE.sub(lambda m: m.group(2), text)
        text = _UNDERSCORE_EM_RE.sub(lambda m: m.group(1), text)
    text = _STRAY_MARKS_RE.sub("", text)
    text = _EMPTY_BRACKETS_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _REPEATED_PUNCT_RE.sub(r"\1", text)
    return text.strip(" ,;:-")


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    room = max(1, max_chars - 1)
    cut = text[:room]
    space = cut.rfind(" ")
    if space > room // 3:
        cut = cut[:space]
    cut = cut.rstrip(" ,;:-([{")
    return f"{cut}{ELLIPSIS}" if cut else ""


def condense(text: str, *, max_chars: int = 90) -> str:
    """Return the gist of ``text`` in at most ``max_chars`` characters.

    Takes the first sentence (plus the next one when the first is very short),
    strips markdown syntax, fenced and long inline code, URLs, file paths and
    JSON / dict dumps, collapses whitespace, and clips at a word boundary with
    an ellipsis. Returns ``""`` when nothing readable is left.
    """
    if not isinstance(text, str) or not text.strip() or max_chars < 1:
        return ""
    text = _strip_blocks(text[:MAX_INPUT_CHARS])

    headings: list[str] = []
    body: list[str] = []
    for raw in text.splitlines():
        if _RULE_RE.match(raw) or _TABLE_RULE_RE.match(raw):
            continue
        target = headings if _HEADING_RE.match(raw) else body
        cleaned = _strip_noise(_clean_line(_HEADING_RE.sub("", raw)))
        if cleaned and any(ch.isalnum() for ch in cleaned):
            target.append(cleaned)
    # A heading is a label; show it only when there is nothing else.
    lines = body or headings
    if not lines:
        return ""

    # A line break ends a sentence even without punctuation.
    sentences: list[str] = []
    for line in lines:
        sentences.extend(part for part in _SENTENCE_END_RE.split(line) if part)
    gist = sentences[0]
    if len(gist) < _SHORT_SENTENCE and len(sentences) > 1:
        glue = " " if gist[-1] in _SENTENCE_MARKS else ". "
        gist = f"{gist}{glue}{sentences[1]}"
    return _clip(gist, max_chars)


class StatusFeed:
    """Dedupe and rate-limit the lines the status bubble shows."""

    def __init__(
        self, clock: Callable[[], float] = time.monotonic, min_interval_s: float = 0.3
    ) -> None:
        self._clock = clock
        self._min_interval = max(0.0, float(min_interval_s))
        self._last: tuple[str, str] | None = None
        self._last_at: float | None = None
        self._pending: tuple[str, str] | None = None

    def offer(self, header: str, line: str, *, force: bool = False) -> tuple[str, str] | None:
        """Condense ``(header, line)`` and return it when it should be shown.

        Returns ``None`` for an empty pair, a repeat of the last shown pair, or
        (unless ``force``) a pair arriving within ``min_interval_s`` of the
        last shown one. A throttled pair is kept; :meth:`flush` returns it
        once the interval has passed.
        """
        pair = (condense(header, max_chars=40), condense(line))
        if not pair[0] and not pair[1]:
            return None
        if pair == self._last:
            self._pending = None
            return None
        now = self._clock()
        if not force and self._last_at is not None and now - self._last_at < self._min_interval:
            self._pending = pair
            return None
        return self._show(pair, now)

    def flush(self) -> tuple[str, str] | None:
        """The newest throttled pair once its interval has passed, else ``None``."""
        if self._pending is None:
            return None
        now = self._clock()
        if self._last_at is not None and now - self._last_at < self._min_interval:
            return None
        return self._show(self._pending, now)

    def reset(self) -> None:
        """Forget the last shown and the throttled pair."""
        self._last = None
        self._last_at = None
        self._pending = None

    def _show(self, pair: tuple[str, str], now: float) -> tuple[str, str]:
        self._last = pair
        self._last_at = now
        self._pending = None
        return pair
