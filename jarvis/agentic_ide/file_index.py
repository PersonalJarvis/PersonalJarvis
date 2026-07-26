"""A bounded map of the open workspace's files, so spoken words become paths.

The point of the Agentic IDE is that you never type at the agent. But a coding
agent works far better when the prompt *points* at files — Claude Code's ``@``
reference and Codex's file mentions both pull a file into context immediately,
which is the difference between "find the wake word code" (three tool calls of
searching) and "here it is" (zero). A person speaking cannot dictate
``jarvis/plugins/wake/vosk_kws_provider.py``; they say "the Vosk wake thing".
This module closes that gap.

Design constraints that shaped it:

* **Off the hot path (AP-26 / AP-9).** The index is built once per workspace, in
  a worker thread, and cached. Nothing here runs at boot, and a voice turn never
  waits for a directory walk — if the index is not ready yet, the composer
  simply gets no file suggestions and the prompt still goes out.
* **Bounded, always.** A user can open ANY folder, including a home directory or
  a network share. The walk carries a hard file budget, a depth limit, and the
  shared skip list, so the worst case is a truncated index, never a hang.
* **Lexical, not semantic.** Matching is token overlap against path segments and
  file stems. No embedding, no model call — it must work on a machine with no
  API key at all, and a wrong suggestion costs the agent one glance.

The ranking encodes what someone actually means when they name a file out loud:
an exact stem hit beats a partial one, a hit in the file NAME beats one in a
parent directory, and tests rank below implementation unless the request is
about tests.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .folders import _SKIP_DIRS

# Hard bounds. 30k files covers a large monorepo; past that the tail is noise
# for this purpose (nobody dictates the name of the 30001st file).
_MAX_FILES = 30_000
_MAX_DEPTH = 12

# Extensions worth indexing: source, config, and docs. Binaries and media are
# never what someone points a coding agent at.
_INTERESTING_SUFFIXES = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
        ".rs", ".go", ".java", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp",
        ".cs", ".rb", ".php", ".sh", ".ps1", ".bat", ".sql", ".graphql", ".proto",
        ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".env.example",
        ".md", ".mdx", ".rst", ".txt", ".css", ".scss", ".html",
    }
)

# Files whose bare name matters even without an interesting suffix.
_ALWAYS_INDEX = frozenset(
    {"Dockerfile", "Makefile", "Procfile", "LICENSE", "CODEOWNERS", ".gitignore"}
)

# Directory names that hold a COPY of the repository. Indexing them means every
# query returns the same file four times over from stale checkouts — measured on
# this repo: `.claude/worktrees` pushed the real hit out of the top results for
# nearly every query. The working copy is the only one anyone means.
_COPY_DIRS = frozenset({"worktrees", ".worktrees", "worktree", "jarvis-agent-outputs"})

# Hidden directories worth indexing anyway: a coding request routinely points at
# the agent instruction trees ("the wake skill", "the review workflow"), and they
# are hidden only by convention.
_KEEP_HIDDEN = frozenset({".claude", ".agents", ".github"})

# Words that carry no addressing power — they appear in every request and would
# match half the tree. The question words and auxiliaries earn their place the
# hard way: without them, "was ist mit dem audio capture" scored two stopword
# hits against a voice-session transcript named `...-was-ist.md` and buried
# `jarvis/audio/capture.py`.
_STOPWORDS = frozenset(
    {
        # structural / generic
        "the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "for", "with",
        "der", "die", "das", "den", "dem", "des", "und", "oder", "von", "im",
        "auf", "für", "fuer", "mit", "bei", "zum", "zur", "el", "la", "los", "las",
        "de", "del", "y", "o", "con", "para", "por",
        # question words / auxiliaries — pure conversational scaffolding
        "was", "wer", "wie", "wo", "wann", "warum", "welche", "welcher", "welches",
        "ist", "sind", "war", "waren", "hat", "haben", "wird", "werden", "kann",
        "soll", "sollte", "muss", "gibt", "gib", "dass", "wenn", "aber", "auch",
        "noch", "schon", "nur", "sich", "sein", "seine", "ihre", "einen", "eine",
        "einer", "eines", "etwas", "irgendwas", "irgendwie", "dann", "denn",
        "what", "who", "how", "where", "when", "why", "which", "is", "are",
        "were", "has", "have", "will", "would", "should", "must", "can", "does",
        "this", "that", "these", "those", "there", "here", "some", "any", "about",
        "qué", "que", "quien", "cómo", "como", "donde", "cuando", "cual", "está",
        "estan", "tiene", "puede", "debe", "esto", "eso", "aqui", "algo",
        # request verbs (they say what to DO, not which file)
        "mach", "mache", "machen", "bau", "baue", "schreib", "schreibe", "fix",
        "fixe", "behebe", "prüf", "pruef", "prüfe", "check", "checke", "teste",
        "test", "starte", "start", "build", "write", "make", "run", "change",
        "create", "add", "remove", "delete", "look", "read", "find", "search",
        "review", "analyse", "analyze", "analysiere", "untersuche", "schau",
        "haz", "escribe", "crea", "revisa", "arregla",
        # meta words about the code itself
        "code", "datei", "dateien", "file", "files", "funktion", "function",
        "klasse", "class", "modul", "module", "bug", "fehler", "error", "problem",
        "sache", "thing", "stuff", "part", "teil", "mal", "bitte", "kurz", "nochmal",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß0-9_]+")
# Split identifiers the way they are spoken: camelCase, snake_case, kebab-case.
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(slots=True)
class _Entry:
    """One indexed file: its repo-relative path plus its searchable tokens."""

    rel: str
    stem_tokens: frozenset[str]
    path_tokens: frozenset[str]
    is_test: bool


@dataclass(slots=True)
class FileIndex:
    """Searchable, bounded view of one workspace folder."""

    root: str
    entries: list[_Entry] = field(default_factory=list)
    truncated: bool = False

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self.entries)

    def suggest(self, text: str, *, limit: int = 6) -> list[str]:
        """Repo-relative paths the words in ``text`` most plausibly point at."""
        wanted = tokenize(text)
        if not wanted or not self.entries:
            return []
        wants_tests = bool(wanted & {"test", "tests", "spec", "specs"})
        scored: list[tuple[float, int, str]] = []
        for entry in self.entries:
            score = _score(entry, wanted, wants_tests=wants_tests)
            if score > 0:
                # Shorter paths win ties: the top-level `config.py` is far more
                # likely meant than a same-named file nested six levels deep.
                scored.append((score, -entry.rel.count("/"), entry.rel))
        if not scored:
            return []
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [rel for _s, _d, rel in scored[: max(1, limit)]]


def _singular(token: str) -> str:
    """Fold a trailing plural ``s`` so speech matches file names.

    People say "the terminal theme"; the file is ``terminalThemes.ts``. Only a
    trailing ``s`` on a word long enough for it to be a plural — nothing that
    would collapse two genuinely different identifiers.
    """
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> set[str]:
    """Meaningful lowercase tokens of ``text``, identifiers split apart."""
    out: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        for piece in _CAMEL_RE.split(raw):
            for chunk in re.split(r"[_\-]+", piece):
                token = chunk.lower()
                if len(token) < 3 or token in _STOPWORDS or token.isdigit():
                    continue
                out.add(_singular(token))
    return out


def _score(entry: _Entry, wanted: set[str], *, wants_tests: bool) -> float:
    """How strongly this file answers to the words the user used."""
    stem_hits = entry.stem_tokens & wanted
    path_hits = (entry.path_tokens - entry.stem_tokens) & wanted
    if not stem_hits and not path_hits:
        return 0.0
    # A hit in the file's own name is what someone means when they name a file;
    # a hit in a parent directory only narrows the area.
    score = 3.0 * len(stem_hits) + 1.0 * len(path_hits)
    # Every token of the file's name being named is near-certainty.
    if stem_hits and stem_hits == entry.stem_tokens:
        score += 2.0
    if entry.is_test and not wants_tests:
        score *= 0.35
    return score


def _is_interesting(name: str) -> bool:
    if name in _ALWAYS_INDEX:
        return True
    suffix = os.path.splitext(name)[1].lower()
    return suffix in _INTERESTING_SUFFIXES


def build_index(root: str | Path) -> FileIndex:
    """Walk ``root`` once and return its bounded file index.

    Blocking by design — callers run it in a worker thread (see ``index_for``).
    """
    base = Path(root).expanduser()
    index = FileIndex(root=str(base))
    try:
        if not base.is_dir():
            return index
    except OSError:
        return index

    stack: list[tuple[Path, int]] = [(base, 0)]
    while stack:
        current, depth = stack.pop()
        if len(index.entries) >= _MAX_FILES:
            index.truncated = True
            break
        try:
            with os.scandir(current) as it:
                for item in it:
                    if len(index.entries) >= _MAX_FILES:
                        index.truncated = True
                        break
                    name = item.name
                    if name in _SKIP_DIRS or name in _COPY_DIRS:
                        continue
                    try:
                        if item.is_dir(follow_symlinks=False):
                            # Hidden directories are skipped except the agent
                            # instruction trees, which are exactly what a coding
                            # request refers to ("the wake skill", ".claude").
                            if name.startswith(".") and name not in _KEEP_HIDDEN:
                                continue
                            if depth < _MAX_DEPTH:
                                stack.append((Path(item.path), depth + 1))
                            continue
                        if not _is_interesting(name):
                            continue
                    except OSError:
                        continue
                    rel = os.path.relpath(item.path, base).replace(os.sep, "/")
                    stem = os.path.splitext(name)[0]
                    parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
                    index.entries.append(
                        _Entry(
                            rel=rel,
                            stem_tokens=frozenset(tokenize(stem)),
                            path_tokens=frozenset(tokenize(parent)) | frozenset(tokenize(stem)),
                            is_test=(
                                "test" in rel.lower().split("/")[0]
                                or stem.lower().startswith("test_")
                                or stem.lower().endswith(("_test", ".test", ".spec"))
                            ),
                        )
                    )
        except (PermissionError, OSError):
            continue
    return index


# One cached index per folder. Keyed rather than global because several
# workspaces can be open at once, and each one's `@file` suggestions must come
# from ITS tree — a shared slot would hand the agent in one workspace the file
# list of another. Small on purpose: entries are dropped when their workspace
# closes, so this never grows into a stale index of every folder ever browsed.
_CACHE: dict[str, FileIndex] = {}
_BUILDING: set[str] = set()
_LOCK = threading.Lock()


def cached_index(root: str | Path) -> FileIndex | None:
    """The index for ``root`` if it is already built, else ``None``."""
    with _LOCK:
        return _CACHE.get(str(Path(root).expanduser()))


def prime_index(root: str | Path) -> None:
    """Build the index for ``root`` in the background, once.

    Called when a workspace opens so the first spoken instruction already has
    file suggestions available. Never raises and never blocks the caller — a
    failed or slow build degrades to "no suggestions", which costs the agent one
    extra search and nothing else.
    """
    key = str(Path(root).expanduser())
    with _LOCK:
        if key in _CACHE or key in _BUILDING:
            return
        _BUILDING.add(key)

    def _run() -> None:
        try:
            built = build_index(key)
        except Exception:  # noqa: BLE001 - an index is a convenience, never fatal
            built = FileIndex(root=key)
        with _LOCK:
            _CACHE[key] = built
            _BUILDING.discard(key)

    threading.Thread(target=_run, name="agentic-ide-file-index", daemon=True).start()


def forget_index(root: str | Path) -> None:
    """Drop the cached index for ONE folder — its workspace just closed.

    Per-folder rather than a blanket reset, because closing one workspace must
    leave the others' suggestions intact. A build still in flight is left to
    finish and re-populate the cache; it is a few kilobytes of index for a
    folder nobody is looking at, and interrupting a worker thread to save that
    is the worse trade.
    """
    key = str(Path(root).expanduser())
    with _LOCK:
        _CACHE.pop(key, None)


def reset_cache() -> None:
    """Drop EVERY cached index — tests, and when the last workspace closes."""
    with _LOCK:
        _CACHE.clear()
        _BUILDING.clear()


__all__ = [
    "FileIndex",
    "build_index",
    "cached_index",
    "forget_index",
    "prime_index",
    "reset_cache",
    "tokenize",
]
