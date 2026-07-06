"""Strict worker environment variable allowlist.

Phase-6 workers run in a subprocess with `env=...` as the complete
environment — NO os.environ inheritance. This prevents secrets
(e.g. AWS_*, GH_*, other API keys from the user's shell) from leaking
into an `openclaw agent`/`codex exec` worker.

Allowlist strategy instead of blocklist (ADR-0009 §3 + Research-Doc §E):
- only explicitly permitted system variables are forwarded,
- API keys are passed explicitly as parameters (no heuristics),
- fixed defaults suppress console color codes, ANSI escape sequences, and
  telemetry traffic (CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1).

The Codex CLI looks up `CODEX_HOME` as its config directory — we pin that
per run to `<run_dir>/.codex/` so every worker has its own cache and there
is no cross-talk between parallel missions.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

from jarvis.missions.workers.process_utils import resolve_node_executable

logger = logging.getLogger(__name__)


def _worker_path_repair_is_windows() -> bool:
    """True on Windows hosts. Extracted so tests can force the POSIX branch."""
    return os.name == "nt"


def _repair_windows_worker_path(
    path: str, *, environ: Mapping[str, str], node_exe: str | None
) -> str:
    """Additively repair a worker PATH so essential dirs are always present.

    The worker env REPLACES os.environ, so it inherits whatever PATH jarvis was
    launched with. When jarvis is started by an agent runtime with a degraded
    PATH (live forensic 2026-06-20: the hermes-agent launch had no Node.js dir),
    the codex worker's ``codex.CMD`` shim resolves bare ``node`` via PATH,
    cmd.exe fails "'node' is not recognized", and every mission dies in ~25 ms.
    A missing System32 also breaks ``chcp`` (the Antigravity-brain symptom).

    This APPENDS the essential System32 / Node.js / npm-global dirs when they
    are absent — it never reorders or drops existing entries, so a binary that
    was already resolvable keeps its original source. Returns ``path`` unchanged
    when nothing needs adding.
    """
    root = environ.get("SystemRoot") or environ.get("windir") or r"C:\Windows"
    essentials: list[str] = [
        os.path.join(root, "System32"),
        root,
        os.path.join(root, "System32", "Wbem"),
        os.path.join(root, "System32", "WindowsPowerShell", "v1.0"),
    ]
    if node_exe:
        essentials.append(str(Path(node_exe).parent))
    appdata = environ.get("APPDATA")
    if appdata:
        # npm-global shims (codex.cmd / claude.cmd / gemini.cmd) live here.
        essentials.append(os.path.join(appdata, "npm"))

    existing = [p for p in path.split(os.pathsep) if p]
    seen = {p.rstrip("\\/").lower() for p in existing}
    additions = [d for d in essentials if d.rstrip("\\/").lower() not in seen]
    if not additions:
        return path
    return os.pathsep.join(existing + additions)

# System variables that are ALWAYS forwarded from os.environ (when set).
# - PATH: without it the worker cannot find binaries (git, claude, codex, python).
# - SystemRoot: Windows internals; cmd.exe/python.exe need it.
# - TEMP: subprocesses may need to create temporary files.
# - USERPROFILE: claude/codex write their configs under %USERPROFILE%/.claude and .codex.
# - LOCALAPPDATA: VSCode-Extensions, npm caches, ms-playwright cache liegen dort.
# - APPDATA: npm-installed CLIs (gemini, claude) resolve their tool/skill bundles
#   via %APPDATA%/Roaming/npm. Without this, the `gemini` CLI falls back to a
#   stripped-down "generalist" agent that has NO file-writing tools — the worker
#   then runs to exit-0 with zero diff and the user sees "Mission failed" with
#   no actionable error (verified live 2026-05-13: Gemini answers
#   "I currently lack the required file-writing capabilities" when APPDATA is
#   missing, but writes the file successfully once APPDATA is in env).
_ALLOWLIST_SYSTEM_VARS: tuple[str, ...] = (
    "PATH",
    "SystemRoot",
    "TEMP",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    # POSIX (macOS/Linux): claude/codex resolve their credential + config files
    # via $HOME (and the XDG base dir), the cross-platform analogue of
    # %USERPROFILE%. Because this env fully REPLACES os.environ, omitting HOME
    # leaves a POSIX worker unable to find ~/.claude/.credentials.json or
    # ~/.codex/auth.json -> auth failure. Harmless on Windows (usually unset).
    "HOME",
    "XDG_CONFIG_HOME",
)


def build_worker_env(
    *,
    run_dir: Path,
    anthropic_api_key: str | None = None,
    openai_api_key: str | None = None,
    gemini_api_key: str | None = None,
    xai_api_key: str | None = None,
    openrouter_api_key: str | None = None,
) -> dict[str, str]:
    """Build the complete environment for a Phase-6 worker.

    Inputs:
        run_dir: path to the mission run directory (typically the worktree parent).
            Used as the CODEX_HOME root: `<run_dir>/.codex`.
        anthropic_api_key: optional, set as ANTHROPIC_API_KEY.
        openai_api_key: optional, set as OPENAI_API_KEY.
        gemini_api_key: optional, set as GEMINI_API_KEY + GOOGLE_API_KEY.
        xai_api_key: optional, set as XAI_API_KEY + GROK_API_KEY
            (xAI SDK accepts either name; OpenClaw reads XAI_API_KEY for the
            ``xai`` provider slug).
        openrouter_api_key: optional, set as OPENROUTER_API_KEY.

    Output:
        dict[str, str] containing exclusively allowlist vars + fixed defaults +
        optional API keys. Can be passed directly to `subprocess.Popen(env=...)`.
    """
    env: dict[str, str] = {}

    # System allowlist
    for key in _ALLOWLIST_SYSTEM_VARS:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value

    # Fixed defaults — non-negotiable
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["CODEX_HOME"] = str(run_dir / ".codex")

    # ROOT-CAUSE FIX (2026-06-20): jarvis can be launched by an agent runtime
    # (hermes-agent) with a degraded PATH that lacks the Node.js dir / System32.
    # Because this env REPLACES os.environ, the worker inherits that broken PATH
    # verbatim, the codex `codex.CMD` shim can't find `node`, and EVERY mission
    # dies `task_error` in ~25 ms ("The worker was aborted."). Repair the
    # PATH additively and forward the Windows shell vars a `.cmd` shim / `chcp`
    # need, so a worker runs regardless of how jarvis itself was started.
    if _worker_path_repair_is_windows():
        env["PATH"] = _repair_windows_worker_path(
            env.get("PATH", ""),
            environ=os.environ,
            node_exe=resolve_node_executable(),
        )
        for var in (
            "ComSpec",
            "PATHEXT",
            "windir",
            "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_ARCHITEW6432",
            "TMP",
        ):
            value = os.environ.get(var)
            if value is not None and var not in env:
                env[var] = value

    # ROOT-CAUSE FIX (2026-05-29): pin the worker/critic `claude` CLI to an
    # ISOLATED config dir so it does NOT load the user's global ~/.claude
    # plugins + hooks. A Phase-6 worker runs `claude --print` as the same OS
    # user; without this redirect it inherits every enabled plugin's hooks.
    # The superpowers plugin's SessionStart hook ("async": false, a Windows
    # run-hook.cmd -> Git-bash polyglot) INTERMITTENTLY HANGS under the
    # headless CREATE_NO_WINDOW spawn: claude emits a single
    # {"subtype":"hook_started","hook_name":"SessionStart:startup"} line and
    # then blocks the full 630s hard cap -> WorkerKilled(timeout) -> empty
    # diff -> task_error. Live forensics: 100% of recently FAILED missions had
    # a 253-byte stream.jsonl ending at exactly that hook_started line, while
    # APPROVED missions streamed past it. A non-interactive worker needs ZERO
    # plugins/hooks/skills, so the clean config eliminates the hang vector
    # entirely (and cross-platform — on Linux/macOS the same hook runs bash
    # directly). Auth then comes from CLAUDE_CODE_OAUTH_TOKEN below (the
    # credentials file lives in the *user's* ~/.claude, not here).
    env["CLAUDE_CONFIG_DIR"] = str(_seed_worker_claude_config(run_dir))
    # OpenClaw reads `<MISSION_STATE_DIR>/openclaw.json` to find
    # `agents.defaults.workspace`; without that redirect, file_write/edit
    # tool calls land in `~/.openclaw/workspace` instead of the per-mission
    # git worktree, and `Kontrollierer._capture_diff(worktree)` then sees an
    # empty diff and the mission is rejected as a no-op. Pinning the
    # state-dir to the mission root keeps the openclaw.json materialized in
    # provider_chain reachable to the CLI. Previously this was derived
    # inside provider_chain from `log_dir.parent`, which pointed one level
    # too deep (mission_dir/tasks/<id>/) — fixed by sourcing the path from
    # the env-builder where `run_dir` is canonically the mission root.
    env["MISSION_STATE_DIR"] = str(run_dir / "openclaw_state")

    # Pre-create the per-mission `plugin-skills/browser-automation/` so
    # OpenClaw's first-spawn symlink call doesn't crash with EPERM on
    # Windows non-admin users (Windows reserves `CreateSymbolicLink` for
    # accounts with `SeCreateSymbolicLinkPrivilege`, which the default
    # interactive user does not have unless Developer Mode is enabled).
    #
    # Live forensic 2026-05-16 mission_019e3288: the Critic spawn died
    # with `EPERM: operation not permitted, symlink '…/dist/extensions/
    # browser/skills/browser-automation' -> '…/openclaw_state/plugin-
    # skills/browser-automation'`, two non-zero returncodes in a row →
    # `CriticSchemaInvalid`. With the target directory already present
    # and populated with the source `SKILL.md`, OpenClaw's symlink call
    # either short-circuits on `EEXIST` or skips the step entirely;
    # either way the subprocess no longer dies on this code path.
    #
    # Best-effort: we copy from the npm-installed source if we can find
    # it, otherwise just create an empty marker file. Either is enough
    # to avoid `EPERM` on the symlink syscall — the worker doesn't
    # actually use the browser-automation skill for our task shapes.
    _seed_openclaw_plugin_skills(Path(env["MISSION_STATE_DIR"]))

    # Optional API keys (explicitly passed as parameters, no os.environ heuristics)
    if anthropic_api_key:
        # BUG-LIVE-FIX (2026-05-18): claude --print strictly validates
        # ANTHROPIC_API_KEY as a classic API key (sk-ant-api03-...) and
        # rejects OAuth tokens (sk-ant-oat01-...) with "Invalid API key ·
        # Fix external API key". OAuth tokens belong in
        # ANTHROPIC_OAUTH_TOKEN. Live repro: when Jarvis runs inside a
        # Claude Code session, the parent ENV exposes
        # ANTHROPIC_API_KEY=sk-ant-oat01-... (Claude Code's own bearer),
        # which the credential lookup picks up and would have re-injected
        # into the worker's API-key slot -- where claude --print then
        # refuses it. Mission outcome: "Worker abgebrochen" within
        # seconds, with stream.jsonl containing exactly one
        # result-is_error=True frame and an empty diff.
        #
        # Fix: route the token by format. OAuth -> OAuth slot; classic
        # API key -> API-key slot. OpenClaw + claude --print both look
        # in both slots (OAuth-first), so either path works after this.
        if anthropic_api_key.startswith("sk-ant-oat"):
            env["ANTHROPIC_OAUTH_TOKEN"] = anthropic_api_key
            # `claude --print` with an isolated CLAUDE_CONFIG_DIR authenticates
            # via CLAUDE_CODE_OAUTH_TOKEN (the headless OAuth env var). It does
            # NOT honour ANTHROPIC_OAUTH_TOKEN alone in that mode — verified
            # live 2026-05-29: it answers "Not logged in · Please run /login"
            # and produces an empty diff. ANTHROPIC_OAUTH_TOKEN stays set above
            # for the OpenClaw-backed path / other consumers.
            env["CLAUDE_CODE_OAUTH_TOKEN"] = anthropic_api_key
        else:
            env["ANTHROPIC_API_KEY"] = anthropic_api_key
    if openai_api_key:
        env["OPENAI_API_KEY"] = openai_api_key
    if gemini_api_key:
        # Gemini CLI accepts either GEMINI_API_KEY or GOOGLE_API_KEY (the
        # latter is the Google SDK default). Setting both makes the worker
        # robust against either lookup path the CLI happens to take.
        env["GEMINI_API_KEY"] = gemini_api_key
        env["GOOGLE_API_KEY"] = gemini_api_key
    if xai_api_key:
        # OpenClaw reads XAI_API_KEY for the ``xai`` provider slug; the
        # legacy GROK_API_KEY name is set as a fallback for older SDK
        # versions and matches what Jarvis stores in the credential
        # manager (``grok_api_key`` -> ENV ``GROK_API_KEY``).
        env["XAI_API_KEY"] = xai_api_key
        env["GROK_API_KEY"] = xai_api_key
    if openrouter_api_key:
        env["OPENROUTER_API_KEY"] = openrouter_api_key

    return env


# Settings written into every worker's isolated CLAUDE_CONFIG_DIR. The point is
# what is ABSENT: no `enabledPlugins`, no `hooks`. A fresh config dir would also
# trigger claude's first-run onboarding + trust dialog (which, headless, leaves
# the model in a no-tool "Not logged in"-style restricted mode that writes no
# files), so we pre-accept onboarding + trust + bypass-permissions so the worker
# has full tool access from byte one. This dict is intentionally minimal and
# cross-platform (no Windows-only keys).
_WORKER_CLAUDE_SETTINGS: dict[str, object] = {
    "hooks": {},
    "enabledPlugins": {},
    "hasCompletedOnboarding": True,
    "hasTrustDialogAccepted": True,
    "bypassPermissionsModeAccepted": True,
    "permissions": {"defaultMode": "bypassPermissions"},
}


# Minimum remaining lifetime for an injected OAuth token. A token that dies
# seconds after the spawn would 401 mid-mission (the isolated worker holds no
# refresh token), so anything expiring within this slack counts as expired.
_OAUTH_EXPIRY_SLACK_S: float = 120.0


def _claude_credentials_path() -> Path:
    """Path of the user's live Claude CLI credentials file (test seam)."""
    return Path(os.path.expanduser("~/.claude/.credentials.json"))


def live_claude_oauth_status(
    *, now_fn: Callable[[], float] = time.time
) -> Literal["valid", "expired", "absent"]:
    """Classify the Claude Max OAuth login in ``~/.claude/.credentials.json``.

    - ``"valid"``: an ``sk-ant-oat`` bearer with ``expiresAt`` comfortably in
      the future (or no ``expiresAt`` at all — older credential shapes stay
      fail-open).
    - ``"expired"``: the bearer exists but its ``expiresAt`` has passed (or is
      within :data:`_OAUTH_EXPIRY_SLACK_S`). Injecting it guarantees a 401 —
      the isolated worker cannot refresh (2026-07-06 incident, missions
      019f36e5 + 019f38b1: token expired 02:53, every spawn died
      "401 Invalid authentication credentials").
    - ``"absent"``: no readable ``sk-ant-oat`` bearer at all.
    """
    try:
        data = json.loads(_claude_credentials_path().read_text(encoding="utf-8"))
        oauth = data.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        expires_at = oauth.get("expiresAt")
    except (OSError, ValueError, AttributeError, TypeError):
        return "absent"
    if not (isinstance(token, str) and token.startswith("sk-ant-oat")):
        return "absent"
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        # `claude` writes epoch milliseconds; tolerate seconds defensively.
        expires_s = expires_at / 1000.0 if expires_at > 1e12 else float(expires_at)
        if expires_s <= now_fn() + _OAUTH_EXPIRY_SLACK_S:
            return "expired"
    return "valid"


def read_live_claude_oauth_token(
    *, now_fn: Callable[[], float] = time.time
) -> str | None:
    """Return the current, NON-EXPIRED Claude Max OAuth access token from the
    user's ``~/.claude/.credentials.json``, or ``None`` if unavailable.

    Why this exists: ``claude`` refreshes its OAuth access token IN PLACE in
    that file, but Jarvis' own secret store (Windows Credential Manager / ENV /
    ``.env``) can hold a STALE copy from an earlier login. Pre-fix, that didn't
    matter — the worker ran with the *user's* config dir and ``claude`` read
    the live file itself. Once the worker is pinned to an isolated
    CLAUDE_CONFIG_DIR (see ``build_worker_env``), the injected
    ``CLAUDE_CODE_OAUTH_TOKEN`` becomes the only auth surface, and a stale token
    fails with ``401 Invalid authentication`` (verified live 2026-05-29). So we
    prefer the live file token. Only OAuth (``sk-ant-oat``) tokens are returned;
    a classic API-key user keeps their own key.

    EXPIRY-aware since 2026-07-06: the live file itself can hold a DEAD token —
    when nothing on the host runs the ``claude`` CLI against that config dir
    anymore, the access token expires in place and is never refreshed again.
    Injecting it produced a deterministic 401 on every subagent spawn (missions
    019f36e5 + 019f38b1). An expired token is treated exactly like an absent
    one; the caller's provider-viability gate then routes the worker to a
    different family instead of a guaranteed-dead Claude spawn.
    """
    if live_claude_oauth_status(now_fn=now_fn) != "valid":
        return None
    try:
        data = json.loads(_claude_credentials_path().read_text(encoding="utf-8"))
        token = data.get("claudeAiOauth", {}).get("accessToken")
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if isinstance(token, str) and token.startswith("sk-ant-oat"):
        return token
    return None


def _seed_worker_claude_config(run_dir: Path) -> Path:
    """Create (idempotently) an isolated ``claude`` config dir for a mission
    and return its path.

    The dir holds a single ``settings.json`` that enables NO plugins and NO
    hooks, so the worker/critic ``claude --print`` subprocess never loads the
    user's global ~/.claude plugin hooks (the superpowers SessionStart hook in
    particular intermittently hangs the headless spawn — see build_worker_env).

    Best-effort: any I/O failure is logged and swallowed; a worker that can't
    write its isolated config still falls back to the user config dir (the
    pre-fix behaviour) rather than failing outright.
    """
    config_dir = run_dir / "claude_config"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        settings_path = config_dir / "settings.json"
        # Re-write every call (idempotent) so a stale/edited file can't reattach
        # plugins or hooks to a worker.
        settings_path.write_text(
            json.dumps(_WORKER_CLAUDE_SETTINGS, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        logger.warning(
            "env: could not seed isolated claude config at %s — worker will "
            "fall back to the user config dir (plugin hooks may load)",
            config_dir,
            exc_info=True,
        )
    return config_dir


# Probable install locations of the npm-bundled OpenClaw browser-automation
# skill on Windows. Listed in order of likelihood for the default install
# path; the first one that exists wins. Empty paths are skipped.
_OPENCLAW_BROWSER_SKILL_CANDIDATES: tuple[str, ...] = (
    r"%APPDATA%\npm\node_modules\openclaw\dist\extensions\browser\skills\browser-automation",
    r"%PROGRAMFILES%\nodejs\node_modules\openclaw\dist\extensions\browser\skills\browser-automation",
    r"%LOCALAPPDATA%\Programs\openclaw\dist\extensions\browser\skills\browser-automation",
)


def _seed_openclaw_plugin_skills(state_dir: Path) -> None:
    """Pre-create the OpenClaw browser-automation skill location as a
    symlink, if the privilege is available; otherwise leave the path
    *missing* and let OpenClaw handle its own EPERM.

    2026-05-17 (CRIT-3 from audit-team 10): the previous implementation
    materialised a plain *directory* at
    ``<state_dir>/plugin-skills/browser-automation/`` so OpenClaw's
    first-spawn symlink call would short-circuit on EEXIST. That trade
    looked clean on paper, but live forensics (Audit-2 + Audit-6)
    showed it actually trades EPERM for EINVAL: OpenClaw later does a
    ``readlink()`` against the path, which the kernel rejects with
    EINVAL (Linux/POSIX) or ERROR_INVALID_FUNCTION (Win32) because a
    plain directory is not a symbolic link. That EINVAL pulse fired
    ~12×/hour in the live mission log and crashed the same Critic
    spawn we tried to protect.

    Correct fix: try to *create the symlink ourselves* using
    ``os.symlink``. If that succeeds (Developer Mode or admin user),
    OpenClaw's later ``readlink()`` returns a valid pointer and the
    whole pipeline works. If ``os.symlink`` raises EPERM/EACCES (the
    common case -- ``asInvoker`` user without
    ``SeCreateSymbolicLinkPrivilege``), we simply *do nothing*. The
    path stays nonexistent; OpenClaw's own symlink call will then
    raise EPERM exactly once and OpenClaw's bootstrap can fall back to
    its copy-not-symlink branch.

    The only thing we deliberately do NOT do anymore is materialise a
    directory at the target -- that was the EINVAL trap.

    Idempotent and best-effort: every failure path is logged at INFO
    or WARNING and swallowed. The worker doesn't actually need the
    browser-automation skill for our task shapes; degraded skill
    availability is strictly better than a 100 %-fail Critic spawn.

    Args:
        state_dir: The mission's MISSION_STATE_DIR.
    """
    # Step 1: find a real source the symlink can point at.
    source_dir: Path | None = None
    for candidate in _OPENCLAW_BROWSER_SKILL_CANDIDATES:
        expanded = Path(os.path.expandvars(candidate))
        if expanded.is_dir() and (expanded / "SKILL.md").is_file():
            source_dir = expanded
            break

    if source_dir is None:
        logger.debug(
            "env: no source browser-automation skill found in any candidate "
            "path — leaving plugin-skills target missing (OpenClaw will "
            "handle its own bootstrap)"
        )
        return

    target_dir = state_dir / "plugin-skills" / "browser-automation"

    # Step 2: ensure the *parent* exists but NOT the target itself --
    # ``os.symlink`` refuses to overwrite an existing path on Windows
    # and POSIX equally.
    try:
        target_dir.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning(
            "env: cannot create plugin-skills parent in %s: %s",
            state_dir, exc,
        )
        return

    # Idempotent: previous run already created the symlink, leave it.
    if target_dir.is_symlink():
        return
    if target_dir.exists():
        # A non-symlink left over from a previous (buggy) run -- the
        # exact EINVAL trap. Replace it with the correct shape: drop
        # the directory and try the symlink. We swallow any error
        # because aggressive cleanup is not worth crashing the worker
        # over.
        try:
            if target_dir.is_dir() and not target_dir.is_symlink():
                shutil.rmtree(target_dir, ignore_errors=True)
            elif target_dir.exists():
                target_dir.unlink()
        except OSError as exc:
            logger.info(
                "env: could not clear stale non-symlink at %s: %s -- "
                "leaving as-is and letting OpenClaw handle it",
                target_dir, exc,
            )
            return

    # Step 3: try the real symlink. Windows requires
    # ``target_is_directory=True`` so the resulting link reports as a
    # directory junction-compatible target -- without this OpenClaw's
    # ``readdir`` on the link would also fail.
    try:
        os.symlink(
            str(source_dir),
            str(target_dir),
            target_is_directory=True,
        )
        logger.info(
            "env: symlink %s -> %s for OpenClaw browser-automation",
            target_dir, source_dir,
        )
    except (OSError, NotImplementedError) as exc:
        # EPERM / EACCES on Windows when the user lacks
        # SeCreateSymbolicLinkPrivilege (Developer Mode or admin).
        # NotImplementedError on platforms where os.symlink is unsupported
        # (theoretical; covered for completeness).
        logger.info(
            "env: cannot symlink plugin-skills (likely no Developer Mode): "
            "%s — leaving target missing so OpenClaw's own EPERM path runs "
            "consistently (was previously crashing on EINVAL/readlink)",
            exc,
        )
