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


def _compile(options: SearchOptions) -> re.Pattern[str]:
    if not options.query:
        raise EditError("Type something to search for.")
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
    try:
        if target.stat().st_size > MAX_SEARCH_BYTES:
            return None
        data = target.read_bytes()
    except OSError:
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
        for number, line in enumerate(text.splitlines(keepends=True), start=1):
            for match in pattern.finditer(line):
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
    replaced_files: list[str] = []
    skipped: list[dict[str, str]] = []
    total = 0
    for path in paths:
        try:
            loaded = read_text_file(root, path)
            if loaded.text is None:
                skipped.append({"path": path, "reason": "not a text file"})
                continue
            new_text, count = pattern.subn(_template(replacement, regex=options.regex), loaded.text)
            if count == 0:
                continue
            write_text_file(
                root,
                loaded.path,
                new_text,
                expected_version=loaded.version,
                encoding=loaded.encoding,
            )
        except EditConflict:
            skipped.append({"path": path, "reason": "changed while replacing"})
            continue
        except (EditError, re.error) as exc:
            skipped.append({"path": path, "reason": str(exc)})
            continue
        replaced_files.append(loaded.path)
        total += count
    return {"replaced_files": replaced_files, "replacements": total, "skipped": skipped}
