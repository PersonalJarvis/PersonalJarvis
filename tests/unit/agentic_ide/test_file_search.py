"""Search and replace across a workspace for the code editor."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.agentic_ide import file_search
from jarvis.agentic_ide.file_editing import EditError
from jarvis.agentic_ide.file_search import SearchOptions, replace_in_files, search_workspace


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "node_modules").mkdir()
    (root / "src" / "app.py").write_text(
        "def greet():\n    return 'Hello'\n# hello again\n", "utf-8"
    )
    (root / "src" / "util.ts").write_text("export const hello = 1;\n", encoding="utf-8")
    (root / "docs" / "guide.md").write_text("Say Hello to the editor.\n", encoding="utf-8")
    (root / "node_modules" / "dep.js").write_text("hello\n", encoding="utf-8")
    (root / "logo.bin").write_bytes(b"\x00hello")
    return root


def _hits(answer: dict[str, object]) -> list[tuple[str, int, int]]:
    return [
        (result["path"], match["line"], match["column"])
        for result in answer["results"]  # type: ignore[union-attr]
        for match in result["matches"]
    ]


def test_plain_search_is_case_insensitive_and_skips_binaries_and_deps(project: Path) -> None:
    answer = search_workspace(project, SearchOptions("hello"))

    assert sorted(_hits(answer)) == [
        ("docs/guide.md", 1, 5),
        ("src/app.py", 2, 13),
        ("src/app.py", 3, 3),
        ("src/util.ts", 1, 14),
    ]
    assert answer["match_count"] == 4
    assert answer["truncated"] is False


def test_case_word_and_regex_options(project: Path) -> None:
    assert len(_hits(search_workspace(project, SearchOptions("Hello", case_sensitive=True)))) == 2
    assert _hits(search_workspace(project, SearchOptions("greet", whole_word=True))) == [
        ("src/app.py", 1, 5)
    ]
    regex = search_workspace(project, SearchOptions(r"def \w+\(", regex=True))
    assert _hits(regex) == [("src/app.py", 1, 1)]
    with pytest.raises(EditError):
        search_workspace(project, SearchOptions("(", regex=True))


def test_include_and_exclude_globs(project: Path) -> None:
    only_py = search_workspace(project, SearchOptions("hello", include="*.py"))
    assert {path for path, _, _ in _hits(only_py)} == {"src/app.py"}
    no_src = search_workspace(project, SearchOptions("hello", exclude="src"))
    assert {path for path, _, _ in _hits(no_src)} == {"docs/guide.md"}


def test_search_stops_at_the_match_cap(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_search, "MAX_MATCHES", 2)
    answer = search_workspace(project, SearchOptions("hello"))
    assert answer["match_count"] == 2
    assert answer["truncated"] is True


def test_symlinked_files_outside_the_workspace_are_not_searched(
    project: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("hello secret\n", encoding="utf-8")
    try:
        os.symlink(secret, project / "leak.txt")
    except (OSError, NotImplementedError):
        pytest.skip("this machine cannot create symlinks")
    assert "leak.txt" not in {
        path for path, _, _ in _hits(search_workspace(project, SearchOptions("secret")))
    }


def test_replace_plain_and_regex_groups(project: Path) -> None:
    answer = replace_in_files(project, SearchOptions("hello"), "Hi", ["src/app.py", "src/util.ts"])
    assert answer["replacements"] == 3
    assert (project / "src" / "app.py").read_text(
        "utf-8"
    ) == "def greet():\n    return 'Hi'\n# Hi again\n"

    replace_in_files(
        project, SearchOptions(r"const (\w+)", regex=True), "let $1_value", ["src/util.ts"]
    )
    assert (project / "src" / "util.ts").read_text("utf-8") == "export let Hi_value = 1;\n"


def test_replace_keeps_crlf_and_reports_unreadable_files(project: Path) -> None:
    (project / "win.txt").write_bytes(b"one\r\ntwo\r\n")
    answer = replace_in_files(project, SearchOptions("two"), "2", ["win.txt", "logo.bin"])
    assert (project / "win.txt").read_bytes() == b"one\r\n2\r\n"
    assert answer["skipped"] == [{"path": "logo.bin", "reason": "not a text file"}]
