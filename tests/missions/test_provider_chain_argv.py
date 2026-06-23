"""Unit tests for the OpenClaw argv-prefix resolver (BUG-ALT-03).

Goal: prove that the OpenClaw worker invokes `node openclaw.mjs` directly
when possible (sidestepping the cmd.exe metacharacter trap that mangles
apostrophes and newlines in `--message` arguments), and that the
argv-builder accepts either the legacy single-string `binary` arg or
the new list prefix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.missions.workers import provider_chain as sjw


# --- _resolve_worker_argv_prefix -----------------------------------------


def test_resolver_returns_node_plus_mjs_when_both_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When `node` and `<npm-root>/node_modules/openclaw/openclaw.mjs`
    both resolve, the prefix must be ["node", "<...mjs>"] — no `.cmd`
    in sight."""
    npm_root = tmp_path / "npm"
    npm_root.mkdir()
    cmd_shim = npm_root / "openclaw.cmd"
    cmd_shim.write_text("@echo placeholder", encoding="utf-8")
    bundle_dir = npm_root / "node_modules" / "openclaw"
    bundle_dir.mkdir(parents=True)
    bundle = bundle_dir / "openclaw.mjs"
    bundle.write_text("// placeholder", encoding="utf-8")

    fake_node = tmp_path / "node.exe"
    fake_node.write_text("// placeholder", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name in ("node", "node.exe"):
            return str(fake_node)
        if name in ("openclaw.cmd", "openclaw"):
            return str(cmd_shim)
        return None

    monkeypatch.setattr(sjw.shutil, "which", fake_which)

    prefix = sjw._resolve_worker_argv_prefix()

    assert isinstance(prefix, list)
    assert len(prefix) == 2, f"expected [node, mjs]; got {prefix}"
    assert prefix[0] == str(fake_node)
    assert prefix[1].endswith("openclaw.mjs")
    assert "openclaw.cmd" not in prefix[0]
    assert "openclaw.cmd" not in prefix[1]


def test_resolver_falls_back_to_cmd_when_bundle_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If the bundle is missing, the resolver may fall back to the .cmd
    shim (legacy behaviour). This is intentional — better a fragile
    path than no path."""
    npm_root = tmp_path / "npm"
    npm_root.mkdir()
    cmd_shim = npm_root / "openclaw.cmd"
    cmd_shim.write_text("@echo placeholder", encoding="utf-8")
    # NOTE: no node_modules/openclaw/openclaw.mjs file

    fake_node = tmp_path / "node.exe"
    fake_node.write_text("// placeholder", encoding="utf-8")

    def fake_which(name: str) -> str | None:
        if name in ("node", "node.exe"):
            return str(fake_node)
        if name in ("openclaw.cmd", "openclaw"):
            return str(cmd_shim)
        return None

    monkeypatch.setattr(sjw.shutil, "which", fake_which)

    prefix = sjw._resolve_worker_argv_prefix()

    assert prefix == [str(cmd_shim)], (
        f"fallback should be the bare .cmd shim, got {prefix}"
    )


def test_resolver_returns_bare_default_when_nothing_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No node, no shim — resolver still returns a single-element list
    so callers can iterate uniformly."""
    monkeypatch.setattr(sjw.shutil, "which", lambda _name: None)

    prefix = sjw._resolve_worker_argv_prefix()

    assert prefix == ["openclaw"], (
        f"expected bare-name singleton list, got {prefix}"
    )


# --- _build_openclaw_cmd ---------------------------------------------------


def test_build_openclaw_cmd_accepts_string_binary_legacy() -> None:
    """The old contract — `binary` as a single path string — must still
    work for existing callers and tests."""
    cmd = sjw._build_openclaw_cmd(
        "Create hello.py",
        binary="C:/npm/openclaw.cmd",
        session_id="s-1",
        openclaw_slug="google",
        model="gemini-3.1-pro-preview",
        timeout_s=600.0,
    )

    assert cmd[0] == "C:/npm/openclaw.cmd"
    assert cmd[1] == "agent"
    assert "--message" in cmd
    assert cmd[cmd.index("--message") + 1] == "Create hello.py"
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "google/gemini-3.1-pro-preview"


def test_build_openclaw_cmd_accepts_argv_prefix_list() -> None:
    """The new contract — `binary` as a list — must place the prefix
    elements at the head of the argv, in order, and then append the
    OpenClaw arguments unchanged."""
    cmd = sjw._build_openclaw_cmd(
        "Create hello.py with print('hi')",
        binary=["C:/Program Files/nodejs/node.exe", "C:/npm/openclaw.mjs"],
        session_id="s-2",
        openclaw_slug="google",
        model="gemini-3.1-pro-preview",
        timeout_s=600.0,
    )

    assert cmd[0] == "C:/Program Files/nodejs/node.exe"
    assert cmd[1] == "C:/npm/openclaw.mjs"
    assert cmd[2] == "agent"
    assert "--message" in cmd
    # Verbatim payload (apostrophe survives intact — that's the whole point)
    assert cmd[cmd.index("--message") + 1] == "Create hello.py with print('hi')"


def test_build_openclaw_cmd_extra_args_appended_after_timeout() -> None:
    """Stable order: `extra_args` go at the very end so they can override
    or supplement standard flags without re-ordering parsing."""
    cmd = sjw._build_openclaw_cmd(
        "p",
        binary=["node", "openclaw.mjs"],
        session_id="s",
        openclaw_slug="google",
        model="gemini-3.1-pro-preview",
        timeout_s=10.0,
        extra_args=("--verbose", "--max-tokens", "4096"),
    )

    assert cmd[-3:] == ["--verbose", "--max-tokens", "4096"]
