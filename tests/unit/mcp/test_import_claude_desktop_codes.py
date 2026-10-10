"""``import_claude_desktop_coded`` names each English note with a stable code."""

from __future__ import annotations

import json

from jarvis.mcp import state


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("JARVIS_MCP_CONFIG", raising=False)
    monkeypatch.setattr(state, "MCP_JSON_PATH", tmp_path / "project" / "mcp.json")


def test_missing_appdata_has_a_code(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    count, added, note, code, params = state.import_claude_desktop_coded()
    assert (count, added, code, params) == (0, [], "appdata_missing", {})
    assert note == "APPDATA variable not set."


def test_missing_config_names_the_path(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    _count, _added, note, code, params = state.import_claude_desktop_coded()
    assert code == "config_not_found"
    assert params["path"] in note


def test_import_counts_added_and_skipped(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    appdata = tmp_path / "appdata"
    (appdata / "Claude").mkdir(parents=True)
    (appdata / "Claude" / "claude_desktop_config.json").write_text(
        json.dumps({"mcpServers": {"one": {"command": "a"}, "two": {"command": "b"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))
    state.save_config({"mcpServers": {"two": {"command": "b", "enabled": False}}})

    count, added, note, code, params = state.import_claude_desktop_coded()

    assert (count, added) == (1, ["one"])
    assert code == "imported_with_skipped"
    assert params == {"added": "1", "skipped": "1"}
    assert note == "1 new servers imported, 1 skipped (already exist)."
    # The legacy three-value API is unchanged.
    assert state.import_claude_desktop()[2] == "0 new servers imported, 2 skipped (already exist)."
