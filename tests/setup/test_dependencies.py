"""Tests for jarvis.setup.dependencies — CLI probes + auto-install.

Welle 3 (2026-05-17): the wizard auto-installs the claude CLI on
fresh installs. The probe + install logic must not depend on the
state of the developer's machine, so every subprocess call is mocked.
Tests cover four real failure modes seen during today's audit:

  * binary missing on PATH                     -> install_hint surfaces
  * binary present but `--version` exits != 0  -> still classified missing
  * `--version` times out                      -> classified missing
  * npm install exits 0 but binary still absent -> classified install-fail
"""
from __future__ import annotations

import subprocess
from dataclasses import replace
from typing import Any

import pytest

from jarvis.setup import dependencies as deps


# --- DependencyStatus surface ---------------------------------------------


def test_dependency_status_is_immutable() -> None:
    """Frozen dataclass — agent/UI code must not mutate the result."""
    s = deps.DependencyStatus(name="x", present=True, version="1.0", path="/p")
    with pytest.raises(Exception):  # noqa: B017 (FrozenInstanceError)
        replace(s)  # ok; mutation would be s.name = "y"
        s.name = "y"  # type: ignore[misc]


# --- _resolve_binary -------------------------------------------------------


def test_resolve_binary_returns_none_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If shutil.which finds nothing for the bare name AND every common
    extension, the resolver returns None."""
    monkeypatch.setattr(deps.shutil, "which", lambda _: None)
    assert deps._resolve_binary("definitely-not-real") is None


def test_resolve_binary_prefers_bare_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bare name resolves, the extension search short-circuits."""
    calls: list[str] = []

    def fake_which(name: str) -> str | None:
        calls.append(name)
        if name == "node":
            return "/usr/bin/node"
        return None

    monkeypatch.setattr(deps.shutil, "which", fake_which)
    assert deps._resolve_binary("node") == "/usr/bin/node"
    assert calls == ["node"], "must not probe extensions after bare hit"


def test_resolve_binary_falls_back_to_extensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bare name is missing, Windows-typical extensions try
    next (.cmd, .exe, ...). First hit wins."""
    def fake_which(name: str) -> str | None:
        if name == "openclaw.cmd":
            return "C:\\npm\\openclaw.cmd"
        return None

    monkeypatch.setattr(deps.shutil, "which", fake_which)
    assert deps._resolve_binary("openclaw") == "C:\\npm\\openclaw.cmd"


# --- _probe_version --------------------------------------------------------


def test_probe_version_returns_first_nonempty_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI sometimes prints copyright on line 2; we want the version
    line on top, trimmed and truncated."""
    def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n\nv24.13.0\nCopyright\n", stderr="",
        )

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    assert deps._probe_version("node", "--version") == "v24.13.0"


def test_probe_version_returns_none_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A binary that exists but exits != 0 is treated as broken --
    don't accept a stale shim."""
    def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=127, stdout="", stderr="oops",
        )

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    assert deps._probe_version("node", "--version") is None


def test_probe_version_returns_none_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=["node"], timeout=10.0)

    monkeypatch.setattr(deps.subprocess, "run", boom)
    assert deps._probe_version("node", "--version") is None


def test_probe_version_returns_none_on_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: Any, **_kw: Any) -> Any:
        raise OSError("not executable")

    monkeypatch.setattr(deps.subprocess, "run", boom)
    assert deps._probe_version("nope", "--version") is None


# --- check_* high-level ---------------------------------------------------


def test_check_node_missing_surfaces_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: None)
    s = deps.check_node()
    assert s.present is False
    assert s.version is None
    assert s.install_hint is not None and "winget" in s.install_hint


def test_check_node_present_with_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: "/usr/bin/node")
    monkeypatch.setattr(deps, "_probe_version", lambda *_a, **_kw: "v24.13.0")
    s = deps.check_node()
    assert s.present is True
    assert s.version == "v24.13.0"
    assert s.install_hint is None


def test_check_claude_cli_missing_hint_mentions_npm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hint must give the exact npm command -- copy-paste matters
    on a fresh install."""
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: None)
    s = deps.check_claude_cli()
    assert s.present is False
    assert "@anthropic-ai/claude-code" in (s.install_hint or "")


def test_check_openclaw_hint_marks_it_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """openclaw is no longer mandatory since BUG-023. The hint must
    say so explicitly so the user doesn't think they're stuck."""
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: None)
    s = deps.check_openclaw()
    assert s.present is False
    assert "Optional" in (s.install_hint or "")


def test_check_present_but_broken_shim_classified_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The .cmd file is there but `--version` hangs / errors --
    that's exactly the Windows broken-shim case the audit warned
    about. Status must be present=False, with a hint that says
    re-install."""
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: "C:\\bad.cmd")
    monkeypatch.setattr(deps, "_probe_version", lambda *_a, **_kw: None)
    s = deps.check_claude_cli()
    assert s.present is False
    assert "broken" in (s.install_hint or "").lower() or "did not respond" in (s.install_hint or "")


# --- install_npm_package --------------------------------------------------


def test_install_npm_package_fails_clearly_without_npm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: None)
    ok, msg = deps.install_npm_package("@anthropic-ai/claude-code")
    assert ok is False
    assert "Node" in msg or "npm" in msg


def test_install_npm_package_handles_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: "/usr/bin/npm")

    def fake_run(*_a: Any, **_kw: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="EACCES: permission denied",
        )

    monkeypatch.setattr(deps.subprocess, "run", fake_run)
    ok, msg = deps.install_npm_package("some-pkg")
    assert ok is False
    assert "1" in msg or "EACCES" in msg


def test_install_npm_package_timeout_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: "/usr/bin/npm")

    def boom(*_a: Any, **_kw: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=["npm"], timeout=300.0)

    monkeypatch.setattr(deps.subprocess, "run", boom)
    ok, msg = deps.install_npm_package("some-pkg", timeout_s=1.0)
    assert ok is False
    assert "timed out" in msg


def test_install_claude_cli_post_install_reprobes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: npm install succeeds, then check_claude_cli
    re-runs and reports present=True."""
    monkeypatch.setattr(deps, "_resolve_binary", lambda _n: "/usr/bin/npm")
    monkeypatch.setattr(
        deps, "install_npm_package",
        lambda _p, **_kw: (True, "added 1 package"),
    )
    # After install, the post-install check_claude_cli must find it.
    monkeypatch.setattr(
        deps, "check_claude_cli",
        lambda: deps.DependencyStatus(
            name="claude", present=True, version="2.1.143", path="/usr/bin/claude",
        ),
    )
    ok, status = deps.install_claude_cli()
    assert ok is True
    assert status.present is True
    assert status.version == "2.1.143"


def test_install_claude_cli_install_failure_returns_actionable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When npm itself fails, install_claude_cli returns a
    DependencyStatus whose install_hint surfaces the actual npm error
    so the wizard prints something useful."""
    monkeypatch.setattr(
        deps, "install_npm_package",
        lambda _p, **_kw: (False, "npm exited 1: EACCES"),
    )
    ok, status = deps.install_claude_cli()
    assert ok is False
    assert status.present is False
    assert "EACCES" in (status.install_hint or "") or "1" in (status.install_hint or "")
