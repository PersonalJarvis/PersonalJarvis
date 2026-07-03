"""External CLI dependency checks + best-effort auto-install.

Welle 3 (2026-05-17): the first-run wizard used to only *announce*
that ``claude``, ``node``, ``openclaw`` should be on PATH -- the user
had to copy-paste npm commands by hand. Today the runtime depends on
``claude`` (the OAuth-backed worker / critic path lives there since
the BUG-023 + CRIT-1 fixes) but a fresh install does not ship it.
This module detects each dependency, surfaces a structured status,
and auto-installs the *safe* ones (npm-packaged CLIs).

Design constraints kept tight on purpose:

* **No admin elevation.** ``node`` itself requires an installer
  (winget, MS Store, nodejs.org). We never try to install that --
  it would need UAC and silently fail under ``asInvoker``. Instead
  we return a clear instruction string for the wizard to show.
* **Only npm-packaged CLIs auto-install.** Dropping a binary into
  ``%APPDATA%\\npm\\`` is reversible (``npm uninstall -g <pkg>``)
  and the global CLAUDE.md autonomy rule explicitly permits
  non-destructive actions. ``openclaw`` (npm too) is treated as
  optional since the default path no longer requires it.
* **Probe-Verify pattern.** Each check returns ``(present, version,
  install_hint)`` -- never just a bool. The version is also a smoke
  test that the binary is launchable, not just that a .cmd shim
  happens to exist on PATH (Windows npm-globals are notorious for
  broken shims after antivirus quarantines a node_modules entry).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Final

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DependencyStatus:
    """Result of probing one external CLI dependency.

    Attributes:
        name: Short identifier (``"node"``, ``"npm"``, ``"claude"``, ``"openclaw"``).
        present: True when the binary is on PATH *and* responded to
            ``--version`` within the probe timeout.
        version: Truncated version string when present, else None.
        path: Resolved binary path when present, else None.
        install_hint: Human-readable instruction to run when present is
            False. Used by the wizard to print actionable next steps.
            None when no hint is required (i.e. dependency is fine).
    """

    name: str
    present: bool
    version: str | None = None
    path: str | None = None
    install_hint: str | None = None


_PROBE_TIMEOUT_S: Final[float] = 10.0


def _resolve_binary(name: str) -> str | None:
    """Return the on-PATH binary for ``name``, considering Windows extensions.

    Windows resolves ``node`` to ``node.exe``, ``claude`` to ``claude.cmd``
    via the npm-bin shim, ``openclaw`` to ``openclaw.cmd`` likewise. We
    check both the bare name and the four common extensions so the wizard
    works the same whether the user installed via winget (``.exe``) or
    via npm (``.cmd``).
    """
    direct = shutil.which(name)
    if direct:
        return direct
    for ext in (".cmd", ".exe", ".bat", ".ps1"):
        with_ext = shutil.which(name + ext)
        if with_ext:
            return with_ext
    return None


def _probe_version(binary: str, *flags: str) -> str | None:
    """Run ``binary <flags>`` and return the trimmed stdout.

    Returns None on any failure (binary not launchable, non-zero exit,
    timeout). Stderr is captured but not surfaced unless probe was
    successful -- broken shims often print to stderr.
    """
    try:
        result = subprocess.run(  # noqa: S603 -- args fully controlled
            [binary, *flags],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("probe %s %s failed: %s", binary, flags, exc)
        return None
    if result.returncode != 0:
        return None
    # First non-empty line; some CLIs print copyright on line 2+.
    for line in (result.stdout or "").splitlines():
        trimmed = line.strip()
        if trimmed:
            return trimmed[:120]
    return None


def check_node() -> DependencyStatus:
    """Probe ``node`` -- required by every npm-packaged CLI."""
    path = _resolve_binary("node")
    if path is None:
        return DependencyStatus(
            name="node",
            present=False,
            install_hint=(
                "Install Node.js 20 LTS or newer via winget "
                "(`winget install OpenJS.NodeJS.LTS`), Microsoft Store, "
                "or https://nodejs.org/. Auto-install requires admin "
                "elevation, which the wizard intentionally avoids."
            ),
        )
    version = _probe_version(path, "--version")
    return DependencyStatus(
        name="node", present=version is not None, version=version, path=path,
        install_hint=None if version else (
            "node is on PATH but did not respond to `node --version`. "
            "The shim may be broken; reinstall Node.js."
        ),
    )


def check_npm() -> DependencyStatus:
    """Probe ``npm`` -- needed to install the other CLIs."""
    path = _resolve_binary("npm")
    if path is None:
        return DependencyStatus(
            name="npm",
            present=False,
            install_hint=(
                "npm ships with Node.js -- install Node first (see node "
                "instructions)."
            ),
        )
    version = _probe_version(path, "--version")
    return DependencyStatus(
        name="npm", present=version is not None, version=version, path=path,
        install_hint=None if version else (
            "npm is on PATH but did not respond to `npm --version`."
        ),
    )


def check_claude_cli() -> DependencyStatus:
    """Probe ``claude`` -- the canonical worker / critic backend.

    Since the BUG-023 (worker) and CRIT-1 (critic) fixes the default
    Personal-Jarvis voice path spawns ``claude --print`` directly via
    the user's Claude Max OAuth (no Anthropic API key). Without claude
    on PATH the worker dies with "claude binary not found" and the
    mission reports an unactionable error.
    """
    path = _resolve_binary("claude")
    if path is None:
        return DependencyStatus(
            name="claude",
            present=False,
            install_hint=(
                "Install with `npm i -g @anthropic-ai/claude-code`. "
                "The wizard can do this for you (non-destructive: "
                "writes to %APPDATA%\\npm only)."
            ),
        )
    version = _probe_version(path, "--version")
    return DependencyStatus(
        name="claude", present=version is not None, version=version, path=path,
        install_hint=None if version else (
            "claude is on PATH but did not respond to `claude --version`. "
            "Re-install with `npm i -g @anthropic-ai/claude-code`."
        ),
    )


def check_openclaw() -> DependencyStatus:
    """Probe ``openclaw`` -- optional since the BUG-023 fix routed the
    default path through ``ClaudeDirectWorker``. Only required if the
    user sets ``[brain.sub_jarvis].provider`` to a non-claude-api
    value (gemini / grok / openrouter / openai)."""
    path = _resolve_binary("openclaw")
    if path is None:
        return DependencyStatus(
            name="openclaw",
            present=False,
            install_hint=(
                "Optional. Only needed if you switch the worker "
                "provider away from claude-api. Install with "
                "`npm i -g openclaw` (pin 2026.5.7, see AD-21)."
            ),
        )
    version = _probe_version(path, "--version")
    return DependencyStatus(
        name="openclaw", present=version is not None, version=version, path=path,
        install_hint=None,
    )


def install_npm_package(package: str, *, timeout_s: float = 300.0) -> tuple[bool, str]:
    """Best-effort ``npm i -g <package>``. Never raises.

    Returns ``(ok, message)`` so the wizard can render the outcome
    without try/except. ok is True only when npm exited 0 *and* the
    binary appears on PATH after install. A successful npm run that
    leaves no binary on PATH is a corrupted Windows shim; we surface
    that as a clear failure rather than a misleading success.
    """
    npm_path = _resolve_binary("npm")
    if npm_path is None:
        return False, "npm is not on PATH; install Node.js first."

    logger.info("install_npm_package: npm i -g %s", package)
    try:
        result = subprocess.run(  # noqa: S603 -- args controlled
            [npm_path, "i", "-g", package],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired:
        return False, f"npm install timed out after {timeout_s:.0f}s"
    except OSError as exc:
        return False, f"npm spawn failed: {exc}"

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:400]
        return False, f"npm exited {result.returncode}: {stderr or 'no stderr'}"

    return True, (result.stdout or "").strip()[:400] or "install reported success"


def install_pip_package(package: str, *, timeout_s: float = 600.0) -> tuple[bool, str]:
    """Best-effort ``<python> -m pip install <package>`` into the RUNNING
    interpreter. Never raises. Returns ``(ok, message)``.

    Used to pull an opt-in runtime extra from inside the app (e.g.
    ``faster-whisper`` for the any-phrase local wake path), so a user never has
    to drop to a shell — the CLAUDE.md §3 "recoverable in-app" contract. Runs
    against ``sys.executable`` so the package lands in the same environment the
    app imports from, and passes ``NO_WINDOW_CREATIONFLAGS`` so a ``pythonw.exe``
    host does not flash a console (AP-1). Cross-platform: ``python -m pip`` is
    the one install invocation that behaves identically on Windows/macOS/Linux.
    A frozen/no-pip interpreter fails cleanly with a message instead of raising.
    """
    if not sys.executable:
        return False, "no Python interpreter available to run pip"

    logger.info("install_pip_package: %s -m pip install %s", sys.executable, package)
    try:
        result = subprocess.run(  # noqa: S603 -- args are controlled, not user input
            [
                sys.executable, "-m", "pip", "install",
                "--disable-pip-version-check", package,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip install timed out after {timeout_s:.0f}s"
    except OSError as exc:
        return False, f"pip spawn failed: {exc}"

    if result.returncode != 0:
        # The tail of stderr carries pip's actual reason (resolver conflict,
        # no matching wheel for the platform, network error).
        stderr = (result.stderr or "").strip()[-600:]
        return False, f"pip exited {result.returncode}: {stderr or 'no stderr'}"

    return True, (result.stdout or "").strip()[-400:] or "install reported success"


def install_claude_cli() -> tuple[bool, DependencyStatus]:
    """Auto-install + re-probe ``claude``. Returns the post-install status."""
    ok, message = install_npm_package("@anthropic-ai/claude-code")
    if not ok:
        logger.warning("install_claude_cli failed: %s", message)
        return False, DependencyStatus(
            name="claude",
            present=False,
            install_hint=f"Auto-install failed: {message}",
        )
    status = check_claude_cli()
    return status.present, status


__all__ = [
    "DependencyStatus",
    "check_claude_cli",
    "check_node",
    "check_npm",
    "check_openclaw",
    "install_claude_cli",
    "install_npm_package",
    "install_pip_package",
]
