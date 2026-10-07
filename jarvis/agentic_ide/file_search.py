"""Search (and replace) text across a workspace for the code editor.

The editor's "Search" tab asks for every line in the workspace that matches a
query: plain text or a regular expression, optionally case-sensitive or whole
word, narrowed by include/exclude globs. Replace runs the same match over a
chosen set of files and writes them atomically.

Bounded on purpose. A workspace may be a home directory: the file list comes
from :func:`file_editing.list_files` (git's view when there is one, so ignored
build output is skipped), large and binary files are skipped, and both the
number of matches and the time spent are capped. A capped answer says so
(``truncated``) instead of running on.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .file_editing import (
    EditConflict,
    EditError,
    _decode,
    list_files,
    read_text_file,
    write_text_file,
)

log = logging.getLogger(__name__)

__all__ = [
    "MAX_MATCHES",
    "SearchOptions",
    "replace_in_files",
    "search_workspace",
]

#: Matches returned for one query; enough to scan, few enough to render.
MAX_MATCHES = 2000
#: Files bigger than this are not searched (minified bundles, data dumps).
MAX_SEARCH_BYTES = 1024 * 1024
#: Wall-clock budget for one search.
SEARCH_BUDGET_S = 8.0
#: Lines longer than this are skipped by a regular-expression search: a
#: backtracking pattern on a minified megabyte line could hold the server.
MAX_REGEX_LINE = 4000
#: One line, a line ending included (only CRLF, CR and LF end a line, as in
#: the editor, so line numbers agree with what it shows).
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$")
_PREVIEW_CHARS = 240


@dataclass(frozen=True, slots=True)
class SearchOptions:
    query: str
    regex: bool = False
    case_sensitive: bool = False
    whole_word: bool = False
    include: str = ""
    exclude: str = ""


@dataclass(slots=True)
class _FileHits:
    path: str
    matches: list[dict[str, object]] = field(default_factory=list)


def _repeats(tree: object, inside_repeat: bool = False) -> bool:
    r"""True when a variably repeated group contains an unbounded repeat.

    ``(a+)+``, ``(\w*)*`` and ``(a|b+)*`` backtrack exponentially in Python's
    ``re``, which has no timeout and holds the interpreter lock while it runs.
    ``(\d{2})+`` or ``(?:x+)?`` are fine and pass.
    """
    from re import _constants as constants  # noqa: PLC0415 — stdlib internals, parse only

    for op, value in tree:  # type: ignore[attr-defined]
        if op in (constants.MAX_REPEAT, constants.MIN_REPEAT):
            low, high, body = value
            unbounded = high is constants.MAXREPEAT
            variable = unbounded or (high > 1 and low != high)
            if unbounded and inside_repeat:
                return True
            if _repeats(body, inside_repeat or variable):
                return True
        elif op is constants.SUBPATTERN:
            if _repeats(value[-1], inside_repeat):
                return True
        elif op is constants.BRANCH:
            if any(_repeats(branch, inside_repeat) for branch in value[1]):
                return True
    return False


def _risky(pattern: str) -> bool:
    try:
        from re import _parser as parser  # noqa: PLC0415 — stdlib internals, parse only

        return _repeats(parser.parse(pattern))
    except Exception:  # noqa: BLE001 — an unparseable pattern fails in re.compile instead
        return False


def _compile(options: SearchOptions) -> re.Pattern[str]:
    if not options.query:
        raise EditError("Type something to search for.")
    if options.regex and _risky(options.query):
        raise EditError(
            "That expression repeats a repeated group, e.g. (a+)+; it could take minutes. "
            "Simplify it, for example (a+) or a+."
        )
    pattern = options.query if options.regex else re.escape(options.query)
    if options.whole_word:
        pattern = rf"\b(?:{pattern})\b"
    try:
        return re.compile(pattern, 0 if options.case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise EditError(f"That regular expression is not valid: {exc}") from exc


def _globs(raw: str) -> list[str]:
    return [part.strip().replace("\\", "/") for part in raw.split(",") if part.strip()]


def _glob_match(path: str, pattern: str) -> bool:
    """``*.py``, ``src/**``, ``docs`` — matched like the editor's search box."""
    name = path.rsplit("/", 1)[-1]
    if fnmatch.fnmatch(path, pattern) or ("/" not in pattern and fnmatch.fnmatch(name, pattern)):
        return True
    # A folder name or folder glob covers everything below it.
    folder = pattern.rstrip("/").removesuffix("/**")
    return path.startswith(f"{folder}/") or fnmatch.fnmatch(path, f"{folder}/*")


def _selected(path: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(_glob_match(path, pattern) for pattern in include):
        return False
    return not any(_glob_match(path, pattern) for pattern in exclude)


def _read_searchable(base: Path, path: str) -> str | None:
    target = base / path
    # A symlink that leads out of the workspace is not searched: the search
    # must not become a way to read files the editor itself refuses to open.
    real = os.path.realpath(target)
    if not real.startswith(f"{base}{os.sep}"):
        return None
    # Nor a link into git's own database, which the editor refuses as well.
    if any(part.lower() == ".git" for part in Path(real[len(str(base)) + 1 :]).parts):
        return None
    try:
        if target.stat().st_size > MAX_SEARCH_BYTES:
            return None
        data = target.read_bytes()
    except OSError:  # an unreadable file is left out of the search results
        return None
    # Read it exactly the way the editor opens it (UTF-8, UTF-16, Windows code
    # pages, …), so a search finds what the editor shows; binaries stay out.
    decoded = _decode(data)
    return decoded[0] if decoded else None


def _preview(line: str, start: int, end: int) -> tuple[str, int]:
    """The line around a match, trimmed; returns the text and the match offset in it."""
    stripped = line.rstrip("\r\n")
    if len(stripped) <= _PREVIEW_CHARS:
        return stripped, start
    left = max(0, start - 60)
    cut = stripped[left : left + _PREVIEW_CHARS]
    return ("…" if left else "") + cut, start - left + (1 if left else 0)


def search_workspace(root: str | os.PathLike[str], options: SearchOptions) -> dict[str, object]:
    """Every matching line in the workspace, grouped by file."""
    pattern = _compile(options)
    include, exclude = _globs(options.include), _globs(options.exclude)
    base = Path(os.path.realpath(os.fspath(root)))
    paths, listing_cut = list_files(base)
    deadline = time.monotonic() + SEARCH_BUDGET_S
    results: list[_FileHits] = []
    total = 0
    searched = 0
    truncated = listing_cut
    for path in paths:
        if not _selected(path, include, exclude):
            continue
        if time.monotonic() > deadline:
            truncated = True
            break
        text = _read_searchable(base, path)
        if text is None:
            continue
        searched += 1
        hits: _FileHits | None = None
        for number, found in enumerate(_LINE.finditer(text), start=1):
            line = found.group(0)
            if options.regex and len(line) > MAX_REGEX_LINE:
                continue
            if time.monotonic() > deadline:
                truncated = True
                break
            for match in pattern.finditer(line.rstrip("\r\n")):
                if match.end() == match.start():
                    continue  # an empty match marks nothing
                if hits is None:
                    hits = _FileHits(path)
                    results.append(hits)
                preview, offset = _preview(line, match.start(), match.end())
                hits.matches.append(
                    {
                        "line": number,
                        "column": match.start() + 1,
                        "length": match.end() - match.start(),
                        "preview": preview,
                        "preview_start": offset,
                    }
                )
                total += 1
                if total >= MAX_MATCHES:
                    truncated = True
                    break
            if total >= MAX_MATCHES:
                break
        if total >= MAX_MATCHES:
            break
    return {
        "results": [{"path": hits.path, "matches": hits.matches} for hits in results],
        "match_count": total,
        "file_count": len(results),
        "searched_files": searched,
        "truncated": truncated,
    }


def _template(replacement: str, *, regex: bool) -> str:
    r"""The replacement as :func:`re.sub` reads it.

    Plain text is taken literally. A regex replacement accepts the editor's
    ``$1`` / ``$&`` group syntax (``$$`` for a dollar) besides Python's ``\1``.
    """
    if not regex:
        return replacement.replace("\\", "\\\\")

    def group(match: re.Match[str]) -> str:
        ref = match.group(1)
        if ref == "$":
            return "$"
        return r"\g<0>" if ref == "&" else rf"\g<{ref}>"

    return re.sub(r"\$(\$|&|\d+)", group, replacement)


def _replace_lines(
    pattern: re.Pattern[str], template: str, text: str, *, regex: bool
) -> tuple[str, int]:
    """Replace line by line, exactly the matches the search listed.

    A pattern then never spans a line break, ``^``/``$`` mean a line's start
    and end, empty matches are left alone, and a line the search skipped for
    its length is skipped here too.
    """
    count = 0

    def one(match: re.Match[str]) -> str:
        nonlocal count
        if match.end() == match.start():
            return match.group(0)
        count += 1
        return match.expand(template)

    out: list[str] = []
    for found in _LINE.finditer(text):
        line = found.group(0)
        body = line.rstrip("\r\n")
        if regex and len(line) > MAX_REGEX_LINE:
            out.append(line)
            continue
        out.append(pattern.sub(one, body) + line[len(body) :])
    return "".join(out), count


def replace_in_files(
    root: str | os.PathLike[str],
    options: SearchOptions,
    replacement: str,
    paths: list[str],
) -> dict[str, object]:
    """Replace every match in the given files; each file is written atomically.

    A file that cannot be read as text, or that changed between reading and
    writing, is reported back instead of being written.
    """
    pattern = _compile(options)
    template = _template(replacement, regex=options.regex)
    replaced_files: list[str] = []
    skipped: list[dict[str, str]] = []
    total = 0
    deadline = time.monotonic() + SEARCH_BUDGET_S * 2
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(path.replace("\\", "/"))
        if key in seen:
            continue  # the same file twice would be replaced twice
        seen.add(key)
        if time.monotonic() > deadline:
            skipped.append({"path": path, "reason": "out of time"})
            continue
        try:
            loaded = read_text_file(root, path)
            if loaded.text is None:
                skipped.append({"path": path, "reason": "not a text file"})
                continue
            new_text, count = _replace_lines(pattern, template, loaded.text, regex=options.regex)
            if count == 0:
                continue
            write_text_file(
                root,
                loaded.path,
                new_text,
                expected_version=loaded.version,
                encoding=loaded.encoding,
            )
        except EditConflict:  # reported to the person in the skipped list
            skipped.append({"path": path, "reason": "changed while replacing"})
            continue
        except (EditError, re.error) as exc:  # reported to the person in the skipped list
            skipped.append({"path": path, "reason": str(exc)})
            continue
        replaced_files.append(loaded.path)
        total += count
    return {"replaced_files": replaced_files, "replacements": total, "skipped": skipped}
