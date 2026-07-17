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


# Marker substrings for classify_pip_failure, matched case-insensitively.
# Network first would be wrong: a source-build log can mention retries, so the
# more specific build/no-wheel signatures win before network generalities.
# Covers BOTH pip and uv wordings — the no-pip fallback path (BUG-073) may
# install via ``uv pip install``, whose resolver phrases the same failures
# differently (captured empirically from uv 0.11).
_PIP_NO_WHEEL_MARKERS = (
    "failed to build",
    "getting requirements to build wheel",
    "pkg-config could not find",
    "no matching distribution found",
    "could not find a version that satisfies",
    "microsoft visual c++",
    "fatal error:",
    # uv resolver wordings
    "no solution found when resolving",
    "has no usable wheels",
    "building from source is disabled",
)
_PIP_NETWORK_MARKERS = (
    "newconnectionerror",
    "temporary failure in name resolution",
    "connection refused",
    "readtimeouterror",
    "network is unreachable",
    "proxyerror",
    "ssl: certificate",
    # uv network wordings
    "failed to fetch",
    "error sending request",
    "request failed after",
)


def classify_pip_failure(stderr: str) -> str | None:
    """Turn the installer's stderr tail into an honest one-line diagnosis.

    BUG-059: on the first real-Mac onboarding a missing cp314/macOS wheel for
    ``av`` sent pip into an FFmpeg SOURCE build that no end user can satisfy —
    and the UI blamed the internet connection. A missing prebuilt wheel (or
    the source build it triggers) must be named as such; only genuine network
    signatures may point at the network. Returns ``None`` when neither
    signature class matches.
    """
    s = (stderr or "").lower()
    if any(marker in s for marker in _PIP_NO_WHEEL_MARKERS):
        ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
        return (
            f"No prebuilt package exists for Python {ver} on this system yet "
            "(pip tried to build it from source, which needs developer "
            "libraries). Python 3.12 or 3.13 has full prebuilt support - "
            "install one from python.org and re-run the Jarvis installer."
        )
    if any(marker in s for marker in _PIP_NETWORK_MARKERS):
        return (
            "Network problem reaching the package index - check your "
            "connection or proxy and try again."
        )
    return None


_ENSUREPIP_TIMEOUT_S: Final[float] = 180.0

# The exact failure a pip-less interpreter prints for ``<python> -m pip``;
# uv-created venvs omit pip by design, so this is a repairable state, not a
# terminal one (BUG-073).
_NO_PIP_MARKER: Final[str] = "no module named pip"


def _pip_install_cmd(package: str, *, only_binary: bool) -> list[str]:
    """Build the ``<python> -m pip install`` argv for ``package``."""
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check",
    ]
    if only_binary:
        cmd += ["--only-binary", ":all:"]
    cmd.append(package)
    return cmd


def _run_installer(
    cmd: list[str], *, timeout_s: float, flavor: str
) -> tuple[bool, str]:
    """Run one install command. Never raises. Returns ``(ok, message)``.

    ``flavor`` names the tool ("pip" / "uv") in failure messages so the UI
    detail is honest about which installer actually ran.
    """
    logger.info("install_pip_package: %s", " ".join(cmd))
    try:
        result = subprocess.run(  # noqa: S603 -- args are controlled, not user input
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired:
        return False, f"{flavor} install timed out after {timeout_s:.0f}s"
    except OSError as exc:
        return False, f"{flavor} spawn failed: {exc}"

    if result.returncode != 0:
        # The tail of stderr carries the installer's actual reason (resolver
        # conflict, no matching wheel for the platform, network error).
        stderr = (result.stderr or "").strip()[-600:]
        raw = f"{flavor} exited {result.returncode}: {stderr or 'no stderr'}"
        diagnosis = classify_pip_failure(stderr)
        return False, f"{diagnosis} [{raw}]" if diagnosis else raw

    return True, (result.stdout or "").strip()[-400:] or "install reported success"


def _install_without_pip(
    package: str, *, timeout_s: float, only_binary: bool, pip_error: str
) -> tuple[bool, str]:
    """Recover an in-app install when the environment has NO pip module.

    BUG-073: environments created by ``uv venv`` ship without pip by design,
    so ``<python> -m pip`` dies with "No module named pip" before it can
    install anything — hit on both the maintainer's Windows box and the
    first real-Mac test run. Recovery order:

    1. ``<python> -m ensurepip --upgrade`` (stdlib) installs pip INTO the
       environment — a permanent repair — then the pip install is retried.
    2. When ensurepip cannot help (some system Pythons strip it), fall back
       to ``uv pip install --python <python>`` with the uv binary on PATH —
       near-certain to exist given a uv-created venv.
    3. Otherwise fail with an actionable message naming both escapes.
    """
    logger.info("pip module missing; bootstrapping via ensurepip")
    try:
        bootstrap = subprocess.run(  # noqa: S603 -- args are controlled
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            capture_output=True,
            text=True,
            timeout=_ENSUREPIP_TIMEOUT_S,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        bootstrapped = bootstrap.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("ensurepip bootstrap failed: %s", exc)
        bootstrapped = False

    if bootstrapped:
        ok, message = _run_installer(
            _pip_install_cmd(package, only_binary=only_binary),
            timeout_s=timeout_s,
            flavor="pip",
        )
        if ok or _NO_PIP_MARKER not in message.lower():
            return ok, message

    uv_path = _resolve_binary("uv")
    if uv_path is not None:
        cmd = [uv_path, "pip", "install", "--python", sys.executable]
        if only_binary:
            cmd += ["--only-binary", ":all:"]
        cmd.append(package)
        return _run_installer(cmd, timeout_s=timeout_s, flavor="uv")

    return False, (
        "This Python environment was created without pip (uv does this by "
        "design) and could not be repaired automatically: ensurepip is "
        "unavailable and no uv binary is on PATH. Run "
        f'`"{sys.executable}" -m ensurepip --upgrade` once, then retry. '
        f"[{pip_error}]"
    )


def install_pip_package(
    package: str, *, timeout_s: float = 600.0, only_binary: bool = False
) -> tuple[bool, str]:
    """Best-effort install of ``package`` into the RUNNING interpreter's
    environment. Never raises. Returns ``(ok, message)``.

    Used to pull an opt-in runtime extra from inside the app (e.g.
    ``faster-whisper`` for the any-phrase local wake path), so a user never has
    to drop to a shell — the CLAUDE.md §3 "recoverable in-app" contract. Runs
    against ``sys.executable`` so the package lands in the same environment the
    app imports from, and passes ``NO_WINDOW_CREATIONFLAGS`` so a ``pythonw.exe``
    host does not flash a console (AP-1). Cross-platform: ``python -m pip`` is
    the primary invocation on Windows/macOS/Linux; an environment with no pip
    module at all (uv-created venvs omit it — BUG-073) is repaired via
    ensurepip or served through ``uv pip install`` instead of failing.

    ``only_binary=True`` adds ``--only-binary=:all:`` — for end-user-facing
    installs of native packages, so the installer fails fast with the honest
    no-wheel diagnosis instead of attempting a source build (FFmpeg/toolchain)
    that no end user can satisfy (BUG-059).
    """
    if not sys.executable:
        return False, "no Python interpreter available to run pip"

    ok, message = _run_installer(
        _pip_install_cmd(package, only_binary=only_binary),
        timeout_s=timeout_s,
        flavor="pip",
    )
    if ok or _NO_PIP_MARKER not in message.lower():
        return ok, message
    return _install_without_pip(
        package, timeout_s=timeout_s, only_binary=only_binary, pip_error=message
    )


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
    "classify_pip_failure",
    "install_pip_package",
]
