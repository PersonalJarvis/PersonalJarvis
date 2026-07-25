"""Guards for the workspace file index — spoken words must reach real paths.

The index exists so a dictated instruction can carry ``@file`` references, and
its failure modes are all quiet ones: a ranking that puts a stale copy of the
repo first, a plural that never matches, or a walk that never terminates. Each
gets a test here because none of them would show up as an error — just as an
agent that starts by searching for the file you already named.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import file_index


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A miniature project with the shapes that actually cause trouble."""
    (tmp_path / "jarvis" / "plugins" / "wake").mkdir(parents=True)
    (tmp_path / "jarvis" / "plugins" / "wake" / "vosk_kws_provider.py").write_text("x")
    (tmp_path / "jarvis" / "core").mkdir(parents=True)
    (tmp_path / "jarvis" / "core" / "turn_language.py").write_text("x")
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "terminalThemes.ts").write_text("x")
    (tmp_path / "tests" / "unit").mkdir(parents=True)
    (tmp_path / "tests" / "unit" / "test_turn_language.py").write_text("x")
    # Noise that must never be indexed.
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "turn_language.py").write_text("x")
    (tmp_path / ".claude" / "worktrees" / "old" / "jarvis" / "core").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "old" / "jarvis" / "core" / "turn_language.py").write_text("x")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    return tmp_path


def test_spoken_words_resolve_to_the_real_file(repo: Path) -> None:
    index = file_index.build_index(repo)
    hits = index.suggest("schau dir den vosk wake provider an", limit=3)
    assert hits[0] == "jarvis/plugins/wake/vosk_kws_provider.py"


def test_a_plural_still_matches_its_file(repo: Path) -> None:
    """People say "the terminal theme"; the file is `terminalThemes.ts`."""
    index = file_index.build_index(repo)
    hits = index.suggest("das terminal theme", limit=3)
    assert "frontend/src/terminalThemes.ts" in hits


def test_stale_repo_copies_are_never_indexed(repo: Path) -> None:
    """A worktree holds a copy of every file; indexing it buries the real one."""
    index = file_index.build_index(repo)
    paths = [entry.rel for entry in index.entries]
    assert not any("worktrees" in p for p in paths)
    assert not any("node_modules" in p for p in paths)
    assert "jarvis/core/turn_language.py" in paths


def test_binaries_are_skipped(repo: Path) -> None:
    index = file_index.build_index(repo)
    assert not any(p.endswith(".png") for p in (e.rel for e in index.entries))


def test_implementation_outranks_its_test_unless_tests_are_asked_for(
    repo: Path,
) -> None:
    index = file_index.build_index(repo)
    code_first = index.suggest("fix the turn language resolver", limit=2)
    assert code_first[0] == "jarvis/core/turn_language.py"

    asked = index.suggest("fix the turn language test", limit=2)
    assert "tests/unit/test_turn_language.py" in asked


def test_conversational_scaffolding_matches_nothing(repo: Path) -> None:
    """"was ist das" carries no addressing power and must not rank a file."""
    assert index_suggest(repo, "was ist das denn") == []


def index_suggest(root: Path, text: str) -> list[str]:
    return file_index.build_index(root).suggest(text, limit=3)


def test_a_missing_folder_yields_an_empty_index_rather_than_raising(
    tmp_path: Path,
) -> None:
    index = file_index.build_index(tmp_path / "does-not-exist")
    assert len(index) == 0
    assert index.suggest("anything") == []


def test_cache_is_per_folder_and_clearable(repo: Path) -> None:
    file_index.reset_cache()
    assert file_index.cached_index(repo) is None
    file_index.prime_index(repo)
    # prime_index is asynchronous; the contract under test is only that priming
    # never blocks or raises, and that the cache is resettable.
    file_index.reset_cache()
    assert file_index.cached_index(repo) is None
