"""Python completions, hovers and definitions for the code editor."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import python_intel
from jarvis.ui.web import agentic_ide_routes as routes

pytest.importorskip("jedi")

SOURCE = 'from pkg.util import shout\nshout("x").upp\n'


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "util.py").write_text(
        'def shout(text: str) -> str:\n    """Upper-case the text."""\n    return text.upper()\n',
        encoding="utf-8",
    )
    return tmp_path


def test_completion_uses_the_live_buffer(project: Path) -> None:
    names = [item["name"] for item in python_intel.complete(project, "main.py", SOURCE, 2, 15)]
    assert "upper" in names


def test_hover_shows_the_signature_and_docstring(project: Path) -> None:
    text = python_intel.hover(project, "main.py", SOURCE, 2, 2)
    assert text is not None
    assert "shout(text: str) -> str" in text
    assert "Upper-case the text." in text


def test_definition_leads_into_another_workspace_file(project: Path) -> None:
    assert python_intel.definitions(project, "main.py", SOURCE, 2, 2) == [
        {"path": "pkg/util.py", "line": 1, "column": 5}
    ]


def test_definitions_outside_the_workspace_are_left_out(project: Path) -> None:
    text = "import os\nos.getcwd()\n"
    assert python_intel.definitions(project, "main.py", text, 2, 5) == []


async def test_route_answers_each_action(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = SimpleNamespace(folder=str(project))
    registry = SimpleNamespace(get=lambda wid: session if wid == "w1" else None)
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    request = routes.PythonIntelRequest(path="main.py", text=SOURCE, line=2, column=2)

    answer = await routes.python_intel("w1", "definition", request)

    assert answer["result"] == [{"path": "pkg/util.py", "line": 1, "column": 5}]
