"""Opening file and folder paths printed inside Agentic-IDE terminals.

The modifier-click is explicit, but the text came from an untrusted terminal.
These tests keep the useful path forms together with their boundary: only an
open workspace on the local desktop, and never anything outside that workspace.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.platform import open_path
from jarvis.ui.web import agentic_ide_routes as routes


def _request(*, native: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        scope={},
        app=SimpleNamespace(state=SimpleNamespace(native_file_actions=native)),
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    session = SimpleNamespace(folder=str(root))
    registry = SimpleNamespace(
        get=lambda workspace_id: session if workspace_id == "workspace-1" else None
    )
    monkeypatch.setattr(routes, "get_registry", lambda: registry)
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: True)
    return root


def _record_opens(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    opened: list[Path] = []
    monkeypatch.setattr(
        open_path,
        "open_file",
        lambda target: opened.append(target) or True,
    )
    return opened


async def test_ctrl_click_opens_a_relative_file_with_a_line_locator(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = workspace / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('ready')\n", encoding="utf-8")
    opened = _record_opens(monkeypatch)

    result = await routes.open_terminal_target(
        _request(),
        routes.OpenTerminalTargetRequest(workspace_id="workspace-1", target="src/main.py:42:7"),
    )

    assert result["opened"] is True
    assert result["kind"] == "file"
    assert opened == [target.resolve()]


async def test_ctrl_click_opens_a_quoted_folder(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = workspace / "folder with spaces"
    target.mkdir()
    opened = _record_opens(monkeypatch)

    result = await routes.open_terminal_target(
        _request(),
        routes.OpenTerminalTargetRequest(workspace_id="workspace-1", target=f'"{target}"'),
    )

    assert result["kind"] == "directory"
    assert opened == [target.resolve()]


async def test_a_local_file_uri_is_unwrapped_before_opening(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = workspace / "notes.md"
    target.write_text("notes\n", encoding="utf-8")
    opened = _record_opens(monkeypatch)

    await routes.open_terminal_target(
        _request(),
        routes.OpenTerminalTargetRequest(
            workspace_id="workspace-1", target=target.resolve().as_uri()
        ),
    )

    assert opened == [target.resolve()]


@pytest.mark.parametrize("printed", ["../outside.txt", "https://example.com/file"])
async def test_terminal_output_cannot_open_outside_the_workspace(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    printed: str,
) -> None:
    (workspace.parent / "outside.txt").write_text("private\n", encoding="utf-8")
    opened = _record_opens(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        await routes.open_terminal_target(
            _request(),
            routes.OpenTerminalTargetRequest(workspace_id="workspace-1", target=printed),
        )

    assert excinfo.value.status_code == 404
    assert opened == []


async def test_an_unknown_workspace_is_not_resolved_against_the_front_one(
    workspace: Path,
) -> None:
    with pytest.raises(HTTPException) as excinfo:
        await routes.open_terminal_target(
            _request(),
            routes.OpenTerminalTargetRequest(workspace_id="gone", target=str(workspace)),
        )

    assert excinfo.value.status_code == 404


async def test_remote_and_headless_callers_cannot_launch_native_paths(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: False)

    with pytest.raises(HTTPException) as remote:
        await routes.open_terminal_target(
            _request(),
            routes.OpenTerminalTargetRequest(workspace_id="workspace-1", target=str(workspace)),
        )
    assert remote.value.status_code == 403

    monkeypatch.setattr(routes, "is_loopback_request", lambda _scope: True)
    with pytest.raises(HTTPException) as headless:
        await routes.open_terminal_target(
            _request(native=False),
            routes.OpenTerminalTargetRequest(workspace_id="workspace-1", target=str(workspace)),
        )
    assert headless.value.status_code == 404
