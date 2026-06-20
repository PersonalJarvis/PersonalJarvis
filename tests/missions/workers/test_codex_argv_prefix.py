"""CodexDirectWorker must not depend on the fragile ``codex.CMD`` shim.

Live forensic 2026-06-20 (missions 019ee554 / 019ee555 / 019ee558, the
"alle Subagent-Missionen schlagen fehl" incident): jarvis was launched by an
agent runtime (hermes-agent) with a PATH that did NOT contain the Node.js
directory. The worker invoked ``codex.CMD`` — an npm batch shim whose tail line
resolves the interpreter as the *bare* command ``node`` via PATH:

    ... || title %COMSPEC% & "%_prog%" "...\\bin\\codex.js" %*   (_prog == "node")

With ``node`` off the inherited PATH, cmd.exe died with
``Der Befehl "node" ... konnte nicht gefunden werden`` and exited 1 in ~25 ms —
BEFORE codex ever started. Every mission then failed ``task_error`` →
"Der Worker ist abgebrochen." (reproduced: codex.CMD + node-less PATH → exit 1
wall_ms=24; ``node`` + absolute path + same PATH → exit 0).

The fix mirrors the existing ``gemini_worker._resolve_gemini_argv_prefix`` and
``provider_chain._resolve_worker_argv_prefix``: invoke ``node <bin/codex.js>``
with an ABSOLUTE node path, bypassing the .CMD shim, the cmd.exe layer, and the
inherited-PATH dependency entirely.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from jarvis.missions.workers.codex_direct_worker import (
    _build_codex_direct_cmd,
    _resolve_codex_argv_prefix,
)


def _fake_npm_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Create a fake npm-global layout: codex.cmd + .../bin/codex.js."""
    npm = tmp_path / "npm"
    npm.mkdir()
    cmd_shim = npm / "codex.cmd"
    cmd_shim.write_text("@echo off\n", encoding="utf-8")
    codex_js = npm / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    codex_js.parent.mkdir(parents=True)
    codex_js.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return cmd_shim, codex_js


def test_argv_prefix_uses_node_and_codex_js_when_available(
    tmp_path: Path, monkeypatch
) -> None:
    """With node + the codex.js entrypoint locatable, the prefix is
    ``[<abs node>, <abs codex.js>]`` — never the bare .CMD shim."""
    cmd_shim, codex_js = _fake_npm_layout(tmp_path)
    fake_node = tmp_path / "nodejs" / "node.exe"
    fake_node.parent.mkdir()
    fake_node.write_text("", encoding="utf-8")

    def fake_which(name: str, *a, **k) -> str | None:
        low = name.lower()
        if low in ("node", "node.exe"):
            return str(fake_node)
        if low in ("codex", "codex.cmd", "codex.exe"):
            return str(cmd_shim)
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    prefix = _resolve_codex_argv_prefix()
    assert prefix == [str(fake_node), str(codex_js)], (
        f"expected [node, codex.js], got {prefix}"
    )


def test_build_cmd_drives_node_not_cmd_shim_when_node_available(
    tmp_path: Path, monkeypatch
) -> None:
    """The full argv must START with node (not codex.CMD) so a degraded
    inherited PATH lacking the Node.js dir can no longer kill the worker."""
    cmd_shim, codex_js = _fake_npm_layout(tmp_path)
    fake_node = tmp_path / "nodejs" / "node.exe"
    fake_node.parent.mkdir()
    fake_node.write_text("", encoding="utf-8")

    def fake_which(name: str, *a, **k) -> str | None:
        low = name.lower()
        if low in ("node", "node.exe"):
            return str(fake_node)
        if low in ("codex", "codex.cmd", "codex.exe"):
            return str(cmd_shim)
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    cmd = _build_codex_direct_cmd(worktree=tmp_path / "wt", model=None)
    assert cmd[0] == str(fake_node), f"argv[0] must be node, got {cmd[0]!r}"
    assert cmd[1] == str(codex_js), f"argv[1] must be codex.js, got {cmd[1]!r}"
    assert not cmd[0].lower().endswith(".cmd"), "must not invoke the .cmd shim"
    # The codex sub-command + discipline flags still follow the prefix.
    assert "exec" in cmd
    assert "multi_agent" in cmd  # D9 guard preserved


def test_argv_prefix_falls_back_to_binary_when_node_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """When node cannot be located, fall back to the bare codex binary so a
    node-less-but-codex-present host still runs (prompt is on stdin, so the
    cmd.exe metachar trap does not apply to this fallback)."""
    cmd_shim, _ = _fake_npm_layout(tmp_path)

    def fake_which(name: str, *a, **k) -> str | None:
        low = name.lower()
        if low in ("node", "node.exe"):
            return None  # node not found
        if low in ("codex", "codex.cmd", "codex.exe"):
            return str(cmd_shim)
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    prefix = _resolve_codex_argv_prefix()
    assert prefix == [str(cmd_shim)], f"expected [codex shim], got {prefix}"
