"""SubJarvisWorker — drives the `openclaw agent --local --json` CLI as a
provider-agnostic Phase-6 worker.

SubJarvisWorker delegates to OpenClaw —
which already supports `google/gemini-*`, `openai/gpt-*`,
`anthropic/claude-*`, and `openrouter/*` behind a single CLI surface.
The provider+model is resolved from `[brain.sub_jarvis]` in jarvis.toml
(provider in jarvis-slug form, e.g. "gemini"; model in OpenClaw form, e.g.
"gemini-3.1-pro-preview"). The jarvis-to-openclaw slug translation lives in
`jarvis.missions.worker_runtime.provider_map.to_provider_slug` so this worker
never hardcodes slugs.

CLI layout (verified live 2026-05-13 against OpenClaw 2026.5.7):

    openclaw agent
        --local
        --json
        --agent main
        --session-id <uuid>
        --message <prompt>
        --model <openclaw_slug>/<model>
        [--timeout <seconds>]

Output is one JSON document on stdout. We extract `payloads[0].text` as
the assistant reply and `meta.agentMeta` for session-id / provider /
model / token-usage. To match the orchestrator contract used by the
other workers, we then emit two synthetic events: a `ClaudeSystemInit`
when we start, and a terminal `ClaudeResult` carrying the assistant
text.

Workspace isolation (empirically confirmed 2026-05-13):

    ENV MISSION_STATE_DIR=<mission_dir>/openclaw_state
    cwd=<worktree>

The state-dir env var diverts agent persona / session / tasks state to
the mission directory so parallel missions don't collide. `cwd` makes
sure `file_write` / `edit` tools land inside the git worktree so
`Kontrollierer._capture_diff(worktree)` (which runs `git add -N . && git
diff HEAD` after the worker exits) sees the changes. The CLI's
`workspaceDir` (`~/.openclaw/workspace`) is the persona-md location and
is intentionally not redirected — it holds AGENTS.md / SOUL.md etc.
which we want every mission to share.

Spawn discipline mirrors GeminiWorker:
- `asyncio.create_subprocess_exec` (NO shell=True, NO PTY).
- Win32 creationflags incl. CREATE_BREAKAWAY_FROM_JOB so the per-mission
  Windows Job Object can assign the subprocess.
- `env=...` strictly from `build_worker_env` (allowlist-only); the
  XAI_API_KEY / GEMINI_API_KEY / OPENAI_API_KEY / OPENROUTER_API_KEY
  the picked provider needs is injected by `_env_builder` in
  `jarvis.missions.init`.

Quota fallback chain: when the primary provider returns a 429 / quota
marker on stderr, the worker re-spawns with the configured
`fallback_provider` / `fallback_model` from `[brain.sub_jarvis]`. A
second fallback (`fallback_provider_2` / `fallback_model_2`) is also
honored. The chain is resolved once per spawn and never cycles.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from jarvis.missions.worker_runtime.provider_map import (
    UnknownJarvisProviderError,
    to_provider_slug,
)

from .process_utils import worker_creationflags as _win32_creationflags
from .stream_consumer import ClaudeResult, ClaudeSystemInit

logger = logging.getLogger(__name__)


# OpenClaw binary candidates (Windows ships .cmd, POSIX ships bare).
_OPENCLAW_BINARIES: tuple[str, ...] = ("openclaw.cmd", "openclaw")

# Quota / rate-limit markers across providers. Detected on stderr after a
# non-zero exit to decide whether to attempt the configured fallback.
_QUOTA_BLOCKED_MARKERS: tuple[str, ...] = (
    "QUOTA_EXHAUSTED",
    "code: 429",
    "429 Too Many Requests",
    "rate limit",
    "RateLimitError",
    "exhausted your capacity",
    "PERMISSION_DENIED",
    "insufficient_quota",
)

# Hard upper bound on a single OpenClaw spawn. Matches the Time-Cap used
# by the OpenClaw harness (AD-19).
_DEFAULT_TIMEOUT_S: float = 600.0


# Model-id sentinels that look like a real "Step.model" value but are in
# fact Claude-aliases the Phase-6 Decomposer emits as a default
# (`kontrollierer/decomposer.py:138 model="sonnet"`). The Decomposer is
# Claude-centric by history; SubJarvisWorker is provider-agnostic, so any
# of these strings coming in as `requested_model` must be ignored and
# resolved from the `[brain.sub_jarvis]` config chain instead.
#
# Discovered live 2026-05-14: mission_019e2572 + mission_019e256f both
# spawned `openclaw agent --model xai/sonnet` and OpenClaw rejected with
# `FailoverError: Unknown model: xai/sonnet`. See stderr.log in those
# mission dirs for the smoking gun.
_DECOMPOSER_FALLBACK_MODELS: frozenset[str] = frozenset({
    "sonnet",
    "opus",
    "haiku",
})


@dataclass(frozen=True, slots=True)
class _FallbackStep:
    """One link in the provider-fallback chain.

    Resolved from `[brain.sub_jarvis]` in jarvis.toml. `provider` is the
    jarvis-slug ("openai", "gemini", "claude-api", "openrouter").
    `model` is the provider-native model id ("gpt-5.5-pro",
    "gemini-3.1-pro-preview", etc.). The OpenClaw slug translation
    happens at spawn time via `to_provider_slug`.
    """

    provider: str
    model: str


def _resolve_openclaw_binary() -> str:
    """Returns the absolute path to the OpenClaw CLI shim.

    Kept for backward compatibility with tests that pin a single string
    and for the fallback path in `_resolve_worker_argv_prefix()`.
    Production code paths should call `_resolve_worker_argv_prefix()`
    instead — it returns the full argv prefix (node + bundle.mjs) that
    sidesteps the cmd.exe metacharacter trap on Windows. See
    `_resolve_worker_argv_prefix` docstring for details.
    """
    if sys.platform == "win32":
        for name in ("openclaw.cmd", "openclaw.exe", "openclaw.bat"):
            path = shutil.which(name)
            if path:
                return path
    for name in _OPENCLAW_BINARIES:
        path = shutil.which(name)
        if path:
            return path
    return "openclaw"


def _resolve_worker_argv_prefix() -> list[str]:
    """Returns the argv prefix for invoking the OpenClaw CLI.

    On Windows, the npm-installed CLI ships as `openclaw.cmd` — a batch
    wrapper around `node openclaw.mjs`. Calling `.cmd` from
    `asyncio.create_subprocess_exec` makes cmd.exe re-parse the full
    argv with batch tokenizer rules, and that tokenizer treats `'`,
    newline, `<`, `>`, `&`, `|`, `^`, `%` as metacharacters. A prompt
    containing a literal apostrophe (`print('hello world')`) or an
    embedded newline therefore arrives at OpenClaw truncated or with
    the `--message` argument silently chopped. The CLI then can't read
    the requested `--model` flag either, falls back to its first listed
    provider (`openai/gpt-5.5`) and dies with `chain_exhausted`.

    BUG-ALT-03 (live repro 2026-05-14): identical pattern to the
    `gemini.cmd` trap already fixed in `gemini_worker.py:
    _resolve_gemini_argv_prefix`. Skipping the `.cmd` wrapper entirely
    by invoking `node ...openclaw.mjs` directly avoids the second-stage
    parser and lets any payload through verbatim.

    We only fall back to the bare `.cmd` shim when we can't locate
    `node` AND the JS entrypoint together — better to have a
    metachar-fragile path than no path at all.
    """
    node = shutil.which("node") or shutil.which("node.exe")
    if node:
        for name in ("openclaw.cmd", "openclaw.exe", "openclaw.bat", "openclaw"):
            cli = shutil.which(name)
            if not cli:
                continue
            cli_dir = Path(cli).resolve().parent
            # npm shim layout: `<npm-root>/openclaw.cmd` points at
            # `<npm-root>/node_modules/openclaw/openclaw.mjs`.
            candidate = (
                cli_dir / "node_modules" / "openclaw" / "openclaw.mjs"
            )
            if candidate.is_file():
                return [node, str(candidate)]
    # Last-resort fallback: bare CLI shim. Still works for prompts that
    # don't contain cmd.exe metacharacters.
    return [_resolve_openclaw_binary()]


def _stderr_signals_quota_block(stderr_bytes: bytes) -> bool:
    """True if the OpenClaw stderr indicates a provider rate-limit / quota
    block — signals that we should try the configured fallback provider.
    """
    if not stderr_bytes:
        return False
    text = stderr_bytes.decode("utf-8", errors="replace")
    return any(marker in text for marker in _QUOTA_BLOCKED_MARKERS)


def _resolve_provider_chain(
    *,
    requested_provider: str | None = None,
    requested_model: str | None = None,
) -> tuple[_FallbackStep, ...]:
    """Builds the provider/model fallback chain from `[brain.sub_jarvis]`.

    Returns a non-empty tuple. The first entry is the primary; subsequent
    entries are honored only when the primary returns a quota-block.

    Resolution order:
        1. Explicit `requested_provider` / `requested_model` args (the
           decomposer can override per-step).
        2. `cfg.brain.sub_jarvis.{provider,model}` plus fallback fields.
        3. Last-ditch fallback to ("gemini", "gemini-3.1-pro-preview") so a
           stub config still produces a runnable argv in tests.
    """
    chain: list[_FallbackStep] = []

    primary_provider = requested_provider or None
    primary_model = requested_model or None

    fallback_provider: str | None = None
    fallback_model: str | None = None
    fallback_provider_2: str | None = None
    fallback_model_2: str | None = None

    try:
        from jarvis.core.config import load_config

        cfg = load_config()
        sub_cfg = getattr(cfg.brain, "sub_jarvis", None)
        if sub_cfg is not None:
            primary_provider = primary_provider or getattr(sub_cfg, "provider", None)
            # Welle 7 (2026-05-20): "openclaw-claude" is a jarvis-side
            # sentinel that means "route through SubJarvisWorker, but use
            # the claude-cli OpenClaw backend". It is NOT a real openclaw
            # provider slug — normalise it to "claude-api" so the rest of
            # the resolver + to_provider_slug() find it in MAPPINGS.
            if isinstance(primary_provider, str) and primary_provider.strip().lower() == "openclaw-claude":
                primary_provider = "claude-api"
            primary_model = primary_model or getattr(sub_cfg, "model", None)
            fallback_provider = getattr(sub_cfg, "fallback_provider", None)
            fallback_model = getattr(sub_cfg, "fallback_model", None)
            fallback_provider_2 = getattr(sub_cfg, "fallback_provider_2", None)
            fallback_model_2 = getattr(sub_cfg, "fallback_model_2", None)

        # If model wasn't set on the sub_jarvis block but the provider was,
        # pull `deep_model` from the matching `[brain.providers.<p>]` slot.
        if primary_provider and not primary_model:
            providers = getattr(cfg.brain, "providers", {}) or {}
            pcfg = providers.get(primary_provider)
            if pcfg is not None:
                primary_model = (
                    getattr(pcfg, "deep_model", None)
                    or getattr(pcfg, "model", None)
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "SubJarvisWorker: config lookup failed (%s) — using stub defaults",
            exc,
        )

    # Welle 6 (2026-05-18): `chatgpt` is a CLI-default-only provider --
    # codex's ChatGPT-OAuth path uses the model configured under
    # ~/.codex/config.toml (or "gpt-5-codex" by default) when --model
    # is not passed. An empty `primary_model` for this provider must
    # therefore NOT drop the chain entry; the CodexDirectWorker treats
    # empty model as "use codex default" and omits the --model flag.
    if primary_provider in ("chatgpt", "openai-codex") and primary_provider and not primary_model:
        chain.append(_FallbackStep(primary_provider, ""))
    elif primary_provider and primary_model:
        chain.append(_FallbackStep(primary_provider, primary_model))
    if fallback_provider and fallback_model:
        candidate = _FallbackStep(fallback_provider, fallback_model)
        if candidate not in chain:
            chain.append(candidate)
    if fallback_provider_2 and fallback_model_2:
        candidate = _FallbackStep(fallback_provider_2, fallback_model_2)
        if candidate not in chain:
            chain.append(candidate)

    if not chain:
        chain.append(_FallbackStep("gemini", "gemini-3.1-pro-preview"))

    return tuple(chain)


def _build_openclaw_cmd(
    prompt: str,
    *,
    binary: str | list[str],
    session_id: str,
    openclaw_slug: str,
    model: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    extra_args: tuple[str, ...] = (),
) -> list[str]:
    """Constructs the OpenClaw CLI argv.

    `binary` accepts either a single executable path (legacy contract,
    used by existing tests that pin one string) or the full argv prefix
    returned by `_resolve_worker_argv_prefix()` (e.g.
    `["node", ".../openclaw.mjs"]`). The latter sidesteps the cmd.exe
    metacharacter trap that mangles apostrophes and newlines on
    Windows (BUG-ALT-03, 2026-05-14).

    Stable order so the dry-run test can pin it argument-for-argument.
    The model arg is `<openclaw_slug>/<model>` — the jarvis-slug must
    have been translated to the openclaw-slug by the caller (e.g. via
    `to_provider_slug("gemini") -> "google"`).
    """
    prefix: list[str] = [binary] if isinstance(binary, str) else list(binary)
    cmd: list[str] = [
        *prefix,
        "agent",
        "--local",
        "--json",
        "--agent",
        "main",
        "--session-id",
        session_id,
        "--message",
        prompt,
        "--model",
        f"{openclaw_slug}/{model}",
        "--timeout",
        str(int(timeout_s)),
    ]
    cmd.extend(extra_args)
    return cmd


def _extract_assistant_text(stdout_bytes: bytes) -> tuple[str, dict[str, Any]]:
    """Parses OpenClaw's `--json` stdout into (text, raw_doc).

    OpenClaw can prepend a few stderr-like log lines to stdout in
    `[skills] failed to create symlink ...` situations; we scan for the
    first `{` and parse from there. Returns ("", {}) on any failure —
    the caller decides whether that's an error condition (by inspecting
    `exit_code`/`is_error`).
    """
    if not stdout_bytes:
        return "", {}
    raw = stdout_bytes.decode("utf-8", errors="replace")
    brace_idx = raw.find("{")
    if brace_idx < 0:
        return "", {}
    candidate = raw[brace_idx:]
    try:
        doc = json.loads(candidate)
    except json.JSONDecodeError:
        return "", {}
    if not isinstance(doc, dict):
        return "", {}

    text = ""
    payloads = doc.get("payloads")
    if isinstance(payloads, list) and payloads:
        first = payloads[0]
        if isinstance(first, dict):
            text = str(first.get("text") or "")
    return text, doc


__all__ = [
    "_build_openclaw_cmd",
    "_resolve_provider_chain",
    "_resolve_openclaw_binary",
    "_resolve_worker_argv_prefix",
    "_extract_assistant_text",
    "_stderr_signals_quota_block",
    "_FallbackStep",
]
