"""CLI-backed turns — a vendor agent binary drives the session.

Four binaries, three wire shapes:

* **Claude-shaped NDJSON** — ``claude`` (Claude Code) and ``grok`` (Grok
  Build, whose ``--output-format streaming-messages-json`` is the Anthropic
  Messages wire shape on purpose). One translator turns their ``system`` /
  ``stream_event`` / ``assistant`` / ``user`` / ``result`` lines into
  agent-chat events.
* **Codex NDJSON** — ``codex exec --json`` with ``thread.*`` / ``turn.*`` /
  ``item.*`` lines.
* **Antigravity NDJSON** — ``agy --output-format stream-json`` with
  ``event: init | step_update | result`` lines (``step_update`` carries text
  deltas and tool steps keyed by ``step_index``; verified against agy 1.1.19).

Every CLI resumes its own conversation natively (``claude --resume``,
``codex exec resume``, ``grok -r``, ``agy --conversation``), so the session
row keeps the vendor's id in ``vendor_session`` and a later turn continues
the same conversation — the tools, skills, MCP servers and permissions are
the CLI's own, exactly as in a terminal. The person's permission mode maps
onto the closest stance each CLI offers; a print-mode CLI cannot ask back,
so ``ask`` means "edits yes, anything riskier is declined by the CLI and
reported to the model", and ``auto`` bypasses.

Spawning follows the mission workers: shell-free argv, the prompt on stdin
where the binary accepts it, ``NO_WINDOW_CREATIONFLAGS``, UTF-8 decoding,
and the agent-account environment (``jarvis.agent_accounts.spawn_env``) so
the active subscription seat is the one that answers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jarvis.agent_chat import jarvis_harness
from jarvis.agent_chat.effort import normalize_effort, snap_to_ladder
from jarvis.agent_chat.events import make_event
from jarvis.agent_chat.permissions import normalize_permission
from jarvis.agent_chat.runner_api import TurnHandle
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_READLINE_LIMIT: Final[int] = 16 * 1024 * 1024
_TURN_TIMEOUT_S: Final[float] = 3600.0


class CliUnavailable(RuntimeError):
    """The binary is not installed (or not on PATH) — the UI says so."""


# ------------------------------------------------------------ binaries


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def claude_argv_prefix() -> list[str]:
    binary = _which("claude", "claude.cmd", "claude.exe")
    if not binary:
        raise CliUnavailable("Claude Code (claude) is not installed or not on PATH.")
    from jarvis.claude_auth import claude_cli_argv_prefix

    return claude_cli_argv_prefix(binary)


def codex_argv_prefix() -> list[str]:
    try:
        from jarvis.missions.workers.codex_direct_worker import _resolve_codex_argv_prefix

        prefix = _resolve_codex_argv_prefix()
    except Exception:  # noqa: BLE001 — the worker helper is a convenience, not a contract
        prefix = []
    if prefix:
        return list(prefix)
    binary = _which("codex", "codex.cmd", "codex.exe")
    if not binary:
        raise CliUnavailable("Codex CLI (codex) is not installed or not on PATH.")
    return [binary]


def agy_argv_prefix() -> list[str]:
    try:
        from jarvis.google_cli.resolver import resolve_google_cli

        cli = resolve_google_cli()
    except Exception:  # noqa: BLE001 — fall back to PATH below
        cli = None
    if cli is not None and cli.kind == "agy":
        return list(cli.argv_prefix)
    binary = _which("agy", "agy.exe")
    if not binary:
        raise CliUnavailable("Antigravity CLI (agy) is not installed or not on PATH.")
    return [binary]


def grok_argv_prefix() -> list[str]:
    binary = _which("grok", "grok.exe", "grok.cmd")
    if not binary:
        raise CliUnavailable("Grok Build (grok) is not installed or not on PATH.")
    return [binary]


def _account_env(platform: str) -> dict[str, str]:
    """The child environment for the active subscription seat of ``platform``."""
    try:
        from jarvis import agent_accounts

        account = agent_accounts.active_account(platform)  # type: ignore[arg-type]
        env = agent_accounts.spawn_env(platform, account.id, base=os.environ)  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001 — no account layer for this platform → plain env
        env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


# ------------------------------------------------------------ argv per CLI

#: Prepended to the prompt for a runner without a plan mode of its own: the
#: sandbox already forbids changes; this tells the model what to do instead.
_PLAN_PREAMBLE: Final[str] = (
    "PLAN MODE: do not change any files or run commands that write. Investigate "
    "(read, search, list), then answer with a concrete, numbered plan of the "
    "changes you would make — files, functions, the order — and the open "
    "questions. The person will switch to Build mode to have it carried out.\n\n"
)


@dataclass(slots=True)
class CliPlan:
    argv: list[str]
    env: dict[str, str]
    stdin_text: str | None
    shape: str  # "claude" | "codex" | "agy"
    #: Vendor session id we chose up front (claude/grok --session-id), or None
    #: when the CLI assigns one (agy, codex) and we read it from the stream.
    vendor_session: str | None
    #: Keep stdin open after the prompt: the CLI talks back on it (Claude
    #: Code's control protocol — permission prompts answered from the chat).
    #: The pump closes stdin once the turn's ``result`` line arrived.
    keep_stdin: bool = False
    #: The control-protocol handshake, written before the prompt. Claude Code
    #: only asks about tool calls once a client has announced itself this way.
    control_init: str | None = None


def claude_control_init() -> str:
    """The handshake that turns Claude Code's control protocol on.

    Without it, ``--permission-prompt-tool stdio`` is a flag the CLI accepts
    and never uses: it has no client on the other end that it knows can answer,
    so every tool call runs straight through and "Ask before acting" behaves
    exactly like "Auto-accept edits" (maintainer report 2026-08-24). The CLI
    answers this with a ``control_response`` listing its commands; from then on
    a permission prompt arrives as ``control_request {subtype: can_use_tool}``
    and the chat's approval card answers it on stdin.

    ``hooks: null`` says this client registers none of its own — the CLI's
    configured hooks still run.
    """
    return (
        json.dumps(
            {
                "type": "control_request",
                "request_id": "jarvis-init",
                "request": {"subtype": "initialize", "hooks": None},
            },
            ensure_ascii=False,
        )
        + "\n"
    )


def claude_stream_input(prompt: str) -> str:
    """One NDJSON user message for ``--input-format stream-json``."""
    return (
        json.dumps(
            {"type": "user", "message": {"role": "user", "content": prompt}},
            ensure_ascii=False,
        )
        + "\n"
    )


def plan_claude(
    *, prompt: str, cwd: Path, model: str, effort: str, permission_mode: str, resume: str | None
) -> CliPlan:
    mode = normalize_permission("claude-cli", permission_mode)
    argv = [
        *claude_argv_prefix(),
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        # The control protocol: the user message goes in as NDJSON and a
        # permission prompt comes back as a ``control_request`` line that the
        # chat answers on stdin — so "ask" really asks, here, in the chat.
        "--input-format",
        "stream-json",
        "--permission-prompt-tool",
        "stdio",
        "--permission-mode",
        mode,
    ]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    # Jarvis' own tools, and the identity that says when to use them. Both are
    # skipped when the app cannot offer them (no control key, server not bound
    # yet) — the session then behaves exactly as it did before.
    mcp_config = jarvis_harness.mcp_config_json()
    if mcp_config:
        argv += [
            "--mcp-config",
            mcp_config,
            "--append-system-prompt",
            jarvis_harness.SYSTEM_PREAMBLE,
        ]
    if resume:
        argv += ["--resume", resume]
        sid = resume
    else:
        sid = str(uuid.uuid4())
        argv += ["--session-id", sid]
    return CliPlan(
        argv,
        jarvis_harness.apply_env(_account_env("claude")),
        claude_stream_input(prompt),
        "claude",
        sid,
        keep_stdin=True,
        control_init=claude_control_init(),
    )


def plan_grok(
    *, prompt: str, cwd: Path, model: str, effort: str, permission_mode: str, resume: str | None
) -> CliPlan:
    argv = [
        *grok_argv_prefix(),
        "--no-auto-update",
        "--no-alt-screen",
        "--output-format",
        "streaming-messages-json",
        "--include-partial-messages",
        "--permission-mode",
        normalize_permission("grok-cli", permission_mode),
        "--cwd",
        str(cwd),
    ]
    if model:
        argv += ["-m", model]
    if effort:
        argv += ["--reasoning-effort", effort]
    if resume:
        argv += ["-r", resume]
        sid = resume
    else:
        sid = str(uuid.uuid4())
        argv += ["--session-id", sid]
    argv += ["-p", prompt]
    return CliPlan(argv, _account_env("grok-build"), None, "claude", sid)


#: agy's own model ids as of 1.1.19, for a box where ``agy models`` cannot be
#: read (the live list is account-dependent and wins whenever it answers).
AGY_FALLBACK_MODELS: Final[tuple[tuple[str, str, tuple[str, ...]], ...]] = (
    ("gemini-3.7-flash", "Gemini 3.7 Flash", ("low", "medium", "high")),
    ("gemini-3.6-flash", "Gemini 3.6 Flash", ("low", "medium", "high")),
    ("gemini-3.5-flash", "Gemini 3.5 Flash", ("low", "medium", "high")),
    ("gemini-3.1-pro", "Gemini 3.1 Pro", ("low", "high")),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)", ()),
    ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)", ()),
    ("gpt-oss-120b", "GPT-OSS 120B", ("medium",)),
)

_AGY_EFFORT_SUFFIXES: Final[tuple[str, ...]] = ("low", "medium", "high")


def agy_model_catalog(raw_models: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Fold agy's suffixed ids into base models with an effort ladder each.

    ``agy models`` lists ``gemini-3.5-flash-low`` / ``-medium`` / ``-high``
    as three ids; the composer shows ONE model with a three-step effort
    pick, so this folds ``<base>-<effort>`` onto ``<base>`` and records the
    efforts seen. Ids without a suffix (the Claude models, ``gpt-oss-120b``)
    keep an empty ladder = no effort pick. ``None`` (no list readable) gives
    the fallback table.
    """
    if not raw_models:
        return [
            {"id": mid, "label": label, "efforts": list(efforts)}
            for mid, label, efforts in AGY_FALLBACK_MODELS
        ]
    order: list[str] = []
    efforts: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for m in raw_models:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        label = str(m.get("label") or mid)
        base, suffix = mid, ""
        for sfx in _AGY_EFFORT_SUFFIXES:
            if mid.endswith("-" + sfx):
                base, suffix = mid[: -(len(sfx) + 1)], sfx
                break
        if base not in efforts:
            efforts[base] = []
            order.append(base)
            labels[base] = _strip_effort_label(label) if suffix else label
        if suffix and suffix not in efforts[base]:
            efforts[base].append(suffix)
    return [
        {
            "id": base,
            "label": labels[base],
            "efforts": sorted(efforts[base], key=_AGY_EFFORT_SUFFIXES.index),
        }
        for base in order
    ]


def _strip_effort_label(label: str) -> str:
    # "Gemini 3.5 Flash (High)" -> "Gemini 3.5 Flash"
    for sfx in ("(Low)", "(Medium)", "(High)"):
        if label.endswith(sfx):
            return label[: -len(sfx)].rstrip()
    return label


def agy_model_args(
    model: str, effort: str, catalog: list[dict[str, Any]] | None = None
) -> list[str]:
    """The ``--model`` / ``--effort`` pair agy accepts for a base id + effort.

    agy is strict: a base Gemini id REQUIRES ``--effort`` (and only the levels
    that model has — Pro knows low/high), a suffixed id FORBIDS it, and the
    Claude / GPT-OSS ids take none at all. The catalog says which is which;
    without one the fallback table does.
    """
    rows = catalog or agy_model_catalog(None)
    by_id = {r["id"]: r for r in rows}
    if not model:
        # Default model: agy accepts ``--effort`` alone.
        return ["--effort", effort] if effort in _AGY_EFFORT_SUFFIXES else []
    row = by_id.get(model)
    if row is None:
        # A suffixed or unknown id: pass it through untouched.
        return ["--model", model]
    ladder = list(row.get("efforts") or [])
    if not ladder:
        return ["--model", model]
    if ladder == ["medium"] and model.startswith("gpt-oss"):
        # gpt-oss-120b: the bare id runs; ``-medium`` is the only suffix.
        return ["--model", model]
    level = effort if effort in ladder else _nearest_lower(effort, ladder)
    return ["--model", model, "--effort", level]


def _nearest_lower(effort: str, ladder: list[str]) -> str:
    order = list(_AGY_EFFORT_SUFFIXES)
    if effort not in order:
        return ladder[-1] if "high" in ladder else ladder[0]
    idx = order.index(effort)
    lower = [lvl for lvl in ladder if order.index(lvl) <= idx]
    return lower[-1] if lower else ladder[0]


def plan_agy(
    *, prompt: str, cwd: Path, model: str, effort: str, permission_mode: str, resume: str | None
) -> CliPlan:
    mode = normalize_permission("agy-cli", permission_mode)
    argv = [
        *agy_argv_prefix(),
        "--output-format",
        "stream-json",
        "--add-dir",
        str(cwd),
        # agy's own print timeout is 5 min; ours is the long one.
        "--print-timeout",
        f"{int(_TURN_TIMEOUT_S // 60)}m",
    ]
    if mode == "skip-permissions":
        argv += ["--dangerously-skip-permissions"]
    elif mode == "plan":
        argv += ["--mode", "plan"]
    else:
        argv += ["--mode", "accept-edits"]
    argv += agy_model_args(model, effort, _agy_catalog_cached())
    if resume:
        argv += ["--conversation", resume]
    env = dict(os.environ)
    env.setdefault("AGY_CLI_HIDE_LOGO", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Prompt on stdin: no argv length limit, and no ``-p`` that could swallow
    # the next flag as its value.
    return CliPlan(argv, env, prompt, "agy", resume)


def read_codex_models() -> list[dict[str, Any]] | None:
    """Codex's account catalog from ``$CODEX_HOME/models_cache.json``.

    The CLI refreshes that file itself (TTL 5 min, ETag) on every run; it is
    the same list the Codex TUI's model picker shows for this login. Rows:
    ``{id, label, efforts, note}`` for ``visibility: list`` models, ordered
    by the catalog's ``priority``. ``None`` when the file is missing or
    unreadable (the caller falls back to the bundled list).
    """
    env = _account_env("codex")
    home = Path(env.get("CODEX_HOME") or Path.home() / ".codex")
    path = home / "models_cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.debug("agent chat: codex models cache unreadable at %s: %s", path, exc)
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    rows: list[tuple[int, dict[str, Any]]] = []
    for m in models:
        if not isinstance(m, dict):
            continue
        if str(m.get("visibility") or "list") != "list":
            continue
        slug = str(m.get("slug") or m.get("id") or "").strip()
        if not slug:
            continue
        levels = m.get("supported_reasoning_levels") or []
        efforts = [
            str(lvl.get("effort") if isinstance(lvl, dict) else lvl)
            for lvl in levels
            if (lvl.get("effort") if isinstance(lvl, dict) else lvl)
        ]
        note = ""
        upgrade = m.get("upgrade")
        if isinstance(upgrade, dict) and upgrade.get("retirement_at"):
            note = f"retires {str(upgrade['retirement_at'])[:10]}"
        try:
            prio = int(m.get("priority") or 0)
        except (TypeError, ValueError):
            prio = 0
        rows.append(
            (
                prio,
                {
                    "id": slug,
                    "label": str(m.get("display_name") or slug),
                    "efforts": efforts,
                    "note": note,
                },
            )
        )
    rows.sort(key=lambda r: r[0])
    return [r[1] for r in rows] or None


_AGY_CATALOG: dict[str, Any] = {"at": 0.0, "rows": None}
_AGY_CATALOG_TTL_S: Final[float] = 600.0


def _agy_catalog_cached() -> list[dict[str, Any]] | None:
    rows = _AGY_CATALOG.get("rows")
    return rows if isinstance(rows, list) else None


def read_agy_models(timeout_s: float = 8.0) -> list[dict[str, Any]]:
    """``agy --output-format json models`` -> the folded catalog (cached 10 min).

    Blocking (a ~2 s subprocess) — call it off the event loop. Any failure
    yields the fallback table; the cache keeps a failure out of the next
    request for the TTL too.
    """
    import subprocess

    now = time.monotonic()
    if _AGY_CATALOG["rows"] is not None and now - _AGY_CATALOG["at"] < _AGY_CATALOG_TTL_S:
        return list(_AGY_CATALOG["rows"])
    raw: list[dict[str, Any]] | None = None
    try:
        argv = [*agy_argv_prefix(), "--output-format", "json", "models"]
        env = dict(os.environ)
        env.setdefault("AGY_CLI_HIDE_LOGO", "1")
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            argv,
            capture_output=True,
            timeout=timeout_s,
            env=env,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        text = proc.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            obj = json.loads(line)
            data = ((obj.get("command") or {}).get("data") or {}) if isinstance(obj, dict) else {}
            models = data.get("models") if isinstance(data, dict) else None
            if isinstance(models, list):
                raw = [m for m in models if isinstance(m, dict)]
                break
    except (CliUnavailable, OSError, ValueError, subprocess.SubprocessError) as exc:
        log.debug("agent chat: agy models unavailable: %s", exc)
    rows = agy_model_catalog(raw)
    _AGY_CATALOG["rows"] = rows
    _AGY_CATALOG["at"] = now
    return list(rows)


def plan_codex(
    *, prompt: str, cwd: Path, model: str, effort: str, permission_mode: str, resume: str | None
) -> CliPlan:
    mode = normalize_permission("codex-cli", permission_mode)
    base = codex_argv_prefix()
    if resume:
        argv = [*base, "exec", "resume", resume, "--json"]
    else:
        argv = [*base, "exec", "--json", "--cd", str(cwd)]
    argv += ["--skip-git-repo-check"]
    # Jarvis' own tools over streamable-HTTP MCP, mounted for this run only —
    # the person's ~/.codex/config.toml is never touched.
    argv += jarvis_harness.codex_config_args()
    # The TUI's presets, spelled out for ``exec`` (codex 0.149): Read only /
    # Auto (workspace-write) / Full access (``--yolo``), plus "approve for
    # me" — Codex's own reviewer model decides what would have asked you,
    # the one ask-stance a headless run has. ``exec`` never prompts on
    # stdin; the sandbox decides what may happen. Plan is the read-only
    # sandbox plus an instruction to plan instead of act. The sandbox goes
    # through ``-c sandbox_mode`` because ``exec resume`` has no ``-s``.
    if mode == "full-access":
        argv += ["--dangerously-bypass-approvals-and-sandbox"]
    elif mode == "approve-for-me":
        argv += [
            "-c",
            'sandbox_mode="workspace-write"',
            "-c",
            'approvals_reviewer="auto_review"',
            "-c",
            'approval_policy="on-request"',
        ]
    else:
        sandbox = "workspace-write" if mode == "auto" else "read-only"
        argv += ["-c", f'sandbox_mode="{sandbox}"']
    if model:
        # The account's catalog says which levels THIS model takes (5.5 stops
        # at xhigh, terra goes to ultra); an unsupported level is snapped
        # rather than sent for the server to reject.
        for row in read_codex_models() or []:
            if row.get("id") == model and row.get("efforts"):
                effort = snap_to_ladder(effort, list(row["efforts"]))
                break
    if effort:
        argv += ["-c", f"model_reasoning_effort={effort}"]
    # Without a summary setting no ``reasoning`` items appear at all.
    argv += ["-c", "model_reasoning_summary=auto"]
    if model:
        argv += ["--model", model]
    argv += ["-"]  # prompt on stdin
    text = _PLAN_PREAMBLE + prompt if mode == "plan" else prompt
    return CliPlan(argv, jarvis_harness.apply_env(_account_env("codex")), text, "codex", resume)


_PLANNERS: Final[dict[str, Any]] = {
    "claude-cli": plan_claude,
    "grok-cli": plan_grok,
    "agy-cli": plan_agy,
    "codex-cli": plan_codex,
}


def supports_cli_runner(runner: str) -> bool:
    return runner in _PLANNERS


# ------------------------------------------------------------ translation

#: The token fields a Claude CLI message reports. The two cache fields carry
#: the bulk of a real turn — a warm session reads tens of thousands of cached
#: tokens while ``input_tokens`` counts only the handful that were new — so a
#: counter that skips them under-reports by orders of magnitude (BUG-173).
_CLAUDE_USAGE_KEYS: Final[tuple[str, ...]] = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


@dataclass(slots=True)
class _ClaudeState:
    turn_id: str
    vendor_session: str | None = None
    current_message_id: str | None = None
    text_acc: dict[str, str] = field(default_factory=dict)
    thinking_acc: dict[str, str] = field(default_factory=dict)
    thinking_started: dict[str, float] = field(default_factory=dict)
    #: Message ids whose thinking block was announced live (one card each).
    thinking_announced: set[str] = field(default_factory=set)
    #: Per-message usage as the CLI reports it — summed into the live counter.
    usage_by_message: dict[str, dict[str, int]] = field(default_factory=dict)
    emitted_tool_ids: set[str] = field(default_factory=set)
    emitted_text: bool = False
    status: str = "done"
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = None
    result_text: str = ""
    #: The turn's ``result`` line arrived — stdin may close, the CLI exits.
    saw_result: bool = False


def _claude_request_summary(tool_name: str, tool_input: dict[str, Any]) -> str:
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().splitlines()[0][:200]
    return tool_name


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p)
    return "" if content is None else str(content)


def translate_claude_line(obj: dict[str, Any], st: _ClaudeState) -> list[dict[str, Any]]:
    """One Claude-shaped NDJSON object -> zero or more agent-chat events."""
    out: list[dict[str, Any]] = []
    kind = str(obj.get("type") or "")
    sid = obj.get("session_id") or obj.get("conversation_id")
    if sid and not st.vendor_session:
        st.vendor_session = str(sid)

    if kind == "system":
        return out  # init / hooks / status — nothing the timeline shows

    if kind == "stream_event":
        ev = obj.get("event") or {}
        et = ev.get("type")
        if et == "message_start":
            mid = ((ev.get("message") or {}).get("id")) or uuid.uuid4().hex
            st.current_message_id = str(mid)
        elif et == "content_block_start":
            block = ev.get("content_block") or {}
            if block.get("type") == "thinking":
                mid = st.current_message_id or uuid.uuid4().hex
                st.current_message_id = mid
                st.thinking_started.setdefault(mid, time.perf_counter())
                # Claude Code redacts thinking text in its stream, so without
                # this the UI learned of eight seconds of thought only once it
                # was over. Announce the block the moment it opens.
                if mid not in st.thinking_announced:
                    st.thinking_announced.add(mid)
                    out.append(
                        make_event(
                            "reasoning_started",
                            {"turn_id": st.turn_id, "message_id": mid},
                        )
                    )
        elif et == "content_block_delta":
            delta = ev.get("delta") or {}
            mid = st.current_message_id or uuid.uuid4().hex
            st.current_message_id = mid
            if delta.get("type") == "text_delta" and delta.get("text"):
                out.append(
                    make_event(
                        "text_delta",
                        {"turn_id": st.turn_id, "message_id": mid, "text": delta["text"]},
                    )
                )
            elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                st.thinking_started.setdefault(mid, time.perf_counter())
                out.append(
                    make_event(
                        "reasoning_delta",
                        {"turn_id": st.turn_id, "message_id": mid, "text": delta["thinking"]},
                    )
                )
        return out

    if kind == "assistant":
        message = obj.get("message") or {}
        mid = str(message.get("id") or st.current_message_id or uuid.uuid4().hex)
        st.current_message_id = mid
        usage_now = message.get("usage")
        if isinstance(usage_now, dict):
            counted = {
                k: int(usage_now[k])
                for k in _CLAUDE_USAGE_KEYS
                if isinstance(usage_now.get(k), int | float)
            }
            # A message's first usage report is the ``message_start`` snapshot:
            # the input side is already final, ``output_tokens`` is a placeholder
            # that only becomes true later. Keep the largest value seen so a
            # placeholder can never walk a real count back down (BUG-173).
            previous = st.usage_by_message.get(mid)
            if previous:
                counted = {
                    k: max(counted.get(k, 0), previous.get(k, 0)) for k in (*counted, *previous)
                }
            if counted and counted != previous:
                st.usage_by_message[mid] = counted
                totals: dict[str, int] = {}
                for per_message in st.usage_by_message.values():
                    for k, v in per_message.items():
                        totals[k] = totals.get(k, 0) + v
                out.append(
                    make_event(
                        "usage_delta",
                        {"turn_id": st.turn_id, "usage": totals},
                    )
                )
        content = message.get("content") or []
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}]
        text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
        snapshot = len(content) > 1
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                thought = str(block.get("thinking") or "")
                if snapshot:
                    st.thinking_acc[mid] = thought
                else:
                    st.thinking_acc[mid] = st.thinking_acc.get(mid, "") + thought
                started = st.thinking_started.get(mid)
                out.append(
                    make_event(
                        "reasoning",
                        {
                            "turn_id": st.turn_id,
                            "message_id": mid,
                            "text": st.thinking_acc[mid],
                            "duration_ms": int((time.perf_counter() - started) * 1000)
                            if started
                            else None,
                        },
                    )
                )
            elif btype == "tool_use":
                call_id = str(block.get("id") or uuid.uuid4().hex)
                if call_id in st.emitted_tool_ids:
                    continue
                st.emitted_tool_ids.add(call_id)
                name = str(block.get("name") or "tool")
                args = block.get("input") or {}
                out.append(
                    make_event(
                        "tool_call",
                        {
                            "turn_id": st.turn_id,
                            "call_id": call_id,
                            "name": name,
                            "input": args if isinstance(args, dict) else {"input": args},
                            "summary": _cli_tool_summary(name, args),
                        },
                    )
                )
        if text_blocks:
            joined = "".join(str(b.get("text") or "") for b in text_blocks)
            if snapshot:
                st.text_acc[mid] = joined
            else:
                st.text_acc[mid] = st.text_acc.get(mid, "") + joined
            if st.text_acc[mid].strip():
                st.emitted_text = True
                out.append(
                    make_event(
                        "assistant_text",
                        {"turn_id": st.turn_id, "message_id": mid, "text": st.text_acc[mid]},
                    )
                )
        return out

    if kind == "user":
        message = obj.get("message") or {}
        content = message.get("content") or []
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append(
                        make_event(
                            "tool_result",
                            {
                                "turn_id": st.turn_id,
                                "call_id": str(block.get("tool_use_id") or ""),
                                "output": _content_text(block.get("content")),
                                "is_error": bool(block.get("is_error")),
                                "duration_ms": None,
                            },
                        )
                    )
        return out

    if kind == "result":
        st.saw_result = True
        st.result_text = str(obj.get("result") or "")
        if obj.get("is_error"):
            st.status = "error"
            errors = obj.get("errors")
            detail = "; ".join(str(e) for e in errors if e) if isinstance(errors, list) else ""
            st.error = st.result_text or detail or str(obj.get("subtype") or "error")
        usage = obj.get("usage") or {}
        if isinstance(usage, dict):
            for k in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ):
                if isinstance(usage.get(k), int | float):
                    st.usage[k] = int(usage[k])
            details = usage.get("output_tokens_details")
            if isinstance(details, dict) and isinstance(
                details.get("thinking_tokens"), int | float
            ):
                st.usage["thinking_tokens"] = int(details["thinking_tokens"])
        cost = obj.get("total_cost_usd", obj.get("cost_usd"))
        if isinstance(cost, int | float):
            st.cost_usd = float(cost)
        return out

    return out


def _cli_tool_summary(name: str, args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "query", "url", "description"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().splitlines()[0][:200]
    return ""


@dataclass(slots=True)
class _CodexState:
    turn_id: str
    vendor_session: str | None = None
    status: str = "done"
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    emitted_text: bool = False
    #: item id -> (call_id, name) for items that became tool calls
    items: dict[str, tuple[str, str]] = field(default_factory=dict)
    started_at: dict[str, float] = field(default_factory=dict)
    #: The last top-level ``error`` notification (retryable until turn.failed).
    last_error: str | None = None


def translate_codex_line(obj: dict[str, Any], st: _CodexState) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    kind = str(obj.get("type") or "")
    if kind == "thread.started":
        tid = obj.get("thread_id")
        if tid:
            st.vendor_session = str(tid)
        return out
    if kind == "turn.completed":
        usage = obj.get("usage") or {}
        if isinstance(usage, dict):
            for k in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "reasoning_output_tokens",
            ):
                if isinstance(usage.get(k), int | float):
                    st.usage[k] = int(usage[k])
        # A completed turn wins over a retryable error seen on the way.
        st.status = "done"
        st.error = None
        return out
    if kind == "turn.failed":
        st.status = "error"
        err = obj.get("error")
        st.error = (
            str(err.get("message") or err)
            if isinstance(err, dict)
            else str(err or st.last_error or "turn failed")
        )
        return out
    if kind == "error":
        # A server notification — may be retried by the CLI; remembered, and
        # only terminal when no turn.completed follows (exit code decides).
        st.last_error = str(obj.get("message") or "error")
        return out
    if not kind.startswith("item."):
        return out

    item = obj.get("item") or {}
    item_id = str(item.get("id") or uuid.uuid4().hex)
    itype = str(item.get("type") or "")
    phase = kind.split(".", 1)[1]  # started | updated | completed

    if itype == "agent_message":
        text = str(item.get("text") or "")
        if phase == "completed" and text.strip():
            st.emitted_text = True
            out.append(
                make_event(
                    "assistant_text",
                    {"turn_id": st.turn_id, "message_id": item_id, "text": text},
                )
            )
        return out
    if itype == "reasoning":
        text = str(item.get("text") or "")
        if phase == "started":
            out.append(
                make_event(
                    "reasoning_started",
                    {"turn_id": st.turn_id, "message_id": item_id},
                )
            )
            return out
        if phase == "completed" and text.strip():
            out.append(
                make_event(
                    "reasoning",
                    {
                        "turn_id": st.turn_id,
                        "message_id": item_id,
                        "text": text,
                        "duration_ms": None,
                    },
                )
            )
        return out
    if itype in {"command_execution", "file_change", "mcp_tool_call", "web_search"}:
        if itype == "command_execution":
            name, args = "RunCommand", {"command": str(item.get("command") or "")}
            summary = args["command"].splitlines()[0][:200] if args["command"] else ""
        elif itype == "file_change":
            changes = item.get("changes") or []
            paths = [str(c.get("path") or "") for c in changes if isinstance(c, dict)]
            name, args = "Edit", {"changes": changes}
            summary = ", ".join(p for p in paths if p)[:200]
        elif itype == "mcp_tool_call":
            name = str(item.get("tool") or item.get("server") or "mcp")
            args = item.get("arguments") or {}
            summary = str(item.get("server") or "")
        else:
            name, args = "WebSearch", {"query": str(item.get("query") or "")}
            summary = args["query"]
        if item_id not in st.items:
            st.items[item_id] = (item_id, name)
            st.started_at[item_id] = time.perf_counter()
            out.append(
                make_event(
                    "tool_call",
                    {
                        "turn_id": st.turn_id,
                        "call_id": item_id,
                        "name": name,
                        "input": args if isinstance(args, dict) else {"input": args},
                        "summary": summary,
                    },
                )
            )
        if phase == "completed":
            if itype == "command_execution":
                output = str(item.get("aggregated_output") or "")
                code = item.get("exit_code")
                if code is not None:
                    output = (output.rstrip() + f"\n[exit {code}]").strip()
                item_status = str(item.get("status") or "")
                is_error = bool(code) or item_status in {"failed", "declined"}
                if item_status == "declined":
                    output = (output.rstrip() + "\n[declined by the sandbox / policy]").strip()
            elif itype == "file_change":
                output = "\n".join(
                    f"{c.get('kind', 'edit')}: {c.get('path', '')}"
                    for c in (item.get("changes") or [])
                    if isinstance(c, dict)
                )
                is_error = str(item.get("status") or "") == "failed"
            elif itype == "mcp_tool_call":
                output = json.dumps(
                    item.get("result") or item.get("error") or {}, ensure_ascii=False
                )
                is_error = bool(item.get("error"))
            else:
                output = "done"
                is_error = False
            started = st.started_at.get(item_id)
            out.append(
                make_event(
                    "tool_result",
                    {
                        "turn_id": st.turn_id,
                        "call_id": item_id,
                        "output": output,
                        "is_error": is_error,
                        "duration_ms": int((time.perf_counter() - started) * 1000)
                        if started
                        else None,
                    },
                )
            )
        return out
    if itype == "error":
        # NOT a failed turn: Codex files warnings here (skills budget, hook
        # clamps, model reroutes) in every run. Remembered for the error text
        # should the turn fail without a message of its own.
        st.last_error = str(item.get("message") or "error")
    return out


# ------------------------------------------------------------ agy translation


@dataclass(slots=True)
class _AgyState:
    turn_id: str
    vendor_session: str | None = None
    status: str = "done"
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    emitted_text: bool = False
    result_text: str = ""
    #: step_index -> wall-clock start, for tool durations
    started_at: dict[int, float] = field(default_factory=dict)
    #: step_index of tool steps already announced
    tool_steps: set[int] = field(default_factory=set)
    #: text per agent_response step (a DONE step repeats its last delta)
    text_acc: dict[int, str] = field(default_factory=dict)


_AGY_TOOL_SUMMARY_KEYS: Final[tuple[str, ...]] = (
    "CommandLine",
    "AbsolutePath",
    "TargetFile",
    "Pattern",
    "SearchDirectory",
    "DirectoryPath",
    "Query",
    "Url",
)


def _agy_summary(params: Any) -> str:
    if not isinstance(params, dict):
        return ""
    for key in _AGY_TOOL_SUMMARY_KEYS:
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().splitlines()[0][:200]
    first = next((v for v in params.values() if isinstance(v, str) and v.strip()), "")
    return first.splitlines()[0][:200] if first else ""


def translate_agy_line(obj: dict[str, Any], st: _AgyState) -> list[dict[str, Any]]:
    """One ``agy --output-format stream-json`` object -> agent-chat events.

    Shape (agy 1.1.19): ``{"event": "init" | "step_update" | "result", ...}``.
    A ``step_update`` carries ``step_index``, ``state`` (ACTIVE / DONE /
    ERROR / ...), ``step_type`` (``agent_response`` with ``text_delta``;
    ``tool`` with ``tool_name`` + ``tool_info{name, parameters, output?,
    error?}``) and, on DONE, ``duration_seconds`` + ``usage``. The final
    ``result`` carries ``status`` (SUCCESS / ERROR / CANCELED / ...),
    ``response``, ``error`` and the conversation id.
    """
    out: list[dict[str, Any]] = []
    kind = str(obj.get("event") or "")
    if kind == "init":
        cid = obj.get("conversation_id")
        if cid:
            st.vendor_session = str(cid)
        return out

    if kind == "step_update":
        su = obj.get("step_update") or {}
        if not isinstance(su, dict):
            return out
        cid = su.get("conversation_id")
        if cid and not st.vendor_session:
            st.vendor_session = str(cid)
        try:
            idx = int(su.get("step_index") or 0)
        except (TypeError, ValueError):
            idx = 0
        state = str(su.get("state") or "")
        stype = str(su.get("step_type") or "")

        if stype == "agent_response":
            delta = str(su.get("text_delta") or "")
            if delta:
                acc = st.text_acc.get(idx, "")
                # The DONE step repeats the last delta — do not double it.
                if state == "DONE" and acc.endswith(delta):
                    delta = ""
            if delta:
                st.text_acc[idx] = st.text_acc.get(idx, "") + delta
                st.emitted_text = True
                out.append(
                    make_event(
                        "text_delta",
                        {"turn_id": st.turn_id, "message_id": f"agy-{idx}", "text": delta},
                    )
                )
            if state in {"DONE", "ERROR"} and st.text_acc.get(idx, "").strip():
                out.append(
                    make_event(
                        "assistant_text",
                        {
                            "turn_id": st.turn_id,
                            "message_id": f"agy-{idx}",
                            "text": st.text_acc[idx],
                        },
                    )
                )
            return out

        if stype == "tool":
            info = su.get("tool_info") if isinstance(su.get("tool_info"), dict) else {}
            name = str(su.get("tool_name") or info.get("name") or "tool")
            params = info.get("parameters") if isinstance(info, dict) else None
            call_id = f"agy-step-{idx}"
            if idx not in st.tool_steps:
                st.tool_steps.add(idx)
                st.started_at[idx] = time.perf_counter()
                out.append(
                    make_event(
                        "tool_call",
                        {
                            "turn_id": st.turn_id,
                            "call_id": call_id,
                            "name": name,
                            "input": params if isinstance(params, dict) else {"input": params},
                            "summary": _agy_summary(params),
                        },
                    )
                )
            if state in {"DONE", "ERROR", "CANCELED", "INVALID", "HALTED"}:
                err = info.get("error") if isinstance(info, dict) else None
                err_text = (
                    str(err.get("message") or err) if isinstance(err, dict) else str(err or "")
                )
                output = str(info.get("output") or "") if isinstance(info, dict) else ""
                is_error = state != "DONE" or bool(err_text)
                if is_error and err_text:
                    output = (output.rstrip() + ("\n" if output else "") + err_text).strip()
                dur = su.get("duration_seconds")
                started = st.started_at.get(idx)
                duration_ms = (
                    int(float(dur) * 1000)
                    if isinstance(dur, int | float)
                    else int((time.perf_counter() - started) * 1000)
                    if started
                    else None
                )
                out.append(
                    make_event(
                        "tool_result",
                        {
                            "turn_id": st.turn_id,
                            "call_id": call_id,
                            "output": output or ("done" if not is_error else "failed"),
                            "is_error": is_error,
                            "duration_ms": duration_ms,
                        },
                    )
                )
            return out
        return out

    if kind == "result":
        res = obj.get("result") if isinstance(obj.get("result"), dict) else obj
        cid = res.get("conversation_id")
        if cid:
            st.vendor_session = str(cid)
        status = str(res.get("status") or "SUCCESS").upper()
        st.result_text = str(res.get("response") or "")
        usage = res.get("usage") or {}
        if isinstance(usage, dict):
            for k in (
                "input_tokens",
                "output_tokens",
                "thinking_tokens",
                "cache_read_tokens",
                "total_tokens",
            ):
                if isinstance(usage.get(k), int | float):
                    st.usage[k] = int(usage[k])
        if status == "SUCCESS":
            st.status = "done"
        elif status in {"CANCELED", "INTERRUPTED"}:
            st.status = "error"
            st.error = str(res.get("error") or f"agy stopped the turn ({status.lower()})")
        else:
            st.status = "error"
            st.error = str(res.get("error") or f"agy reported {status}")
        return out
    return out


# ------------------------------------------------------------ the turn


@dataclass(slots=True)
class _Outcome:
    status: str
    error: str | None
    usage: dict[str, int]
    cost_usd: float | None
    vendor_session: str | None


# Error text the CLIs print when the conversation we try to resume is gone
# (a fresh install, a pruned session store, a first turn that died before
# the CLI wrote anything). The turn is then re-run once without resume.
_RESUME_LOST_MARKERS: Final[tuple[str, ...]] = (
    "no conversation found",
    "session not found",
    "no session found",
    "could not find session",
    "unknown session",
    "not found: ",
    "does not exist",
)


def _resume_was_lost(error: str | None) -> bool:
    low = (error or "").lower()
    return any(m in low for m in _RESUME_LOST_MARKERS)


async def run_cli_turn(handle: TurnHandle, user_text: str, runner: str) -> str | None:
    """Run one CLI turn. Returns the vendor session id to persist (or None).

    A resume the CLI no longer knows is retried once as a fresh conversation
    (the person keeps typing; only the CLI-side memory is gone, the timeline
    here is intact).
    """
    t0 = time.perf_counter()
    outcome = await _run_cli_once(handle, user_text, runner, handle.session.vendor_session)
    # agy answers a lost conversation id with a NEW conversation and exit 0
    # (only a stderr warning) — the id that came back is the truth.
    if (
        outcome.status == "error"
        and handle.session.vendor_session
        and _resume_was_lost(outcome.error)
        and not handle.cancel.is_set()
    ):
        log.info("agent chat %s: resume lost (%s) — starting fresh", handle.turn_id, outcome.error)
        outcome = await _run_cli_once(handle, user_text, runner, None)
    await handle.emit(
        make_event(
            "turn_finished",
            {
                "turn_id": handle.turn_id,
                "status": outcome.status,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "usage": outcome.usage,
                "error": outcome.error,
                "cost_usd": outcome.cost_usd,
            },
        )
    )
    return outcome.vendor_session


async def _run_cli_once(
    handle: TurnHandle, user_text: str, runner: str, resume: str | None
) -> _Outcome:
    session = handle.session
    cwd = Path(session.cwd or Path.home())
    effort = normalize_effort(session.provider, session.effort)
    planner = _PLANNERS[runner]
    status = "done"
    error_text: str | None = None
    usage: dict[str, int] = {}
    cost_usd: float | None = None
    vendor_session: str | None = None

    try:
        plan: CliPlan = planner(
            prompt=user_text,
            cwd=cwd,
            model=session.model,
            effort=effort,
            permission_mode=session.permission_mode,
            resume=resume,
        )
    except CliUnavailable as exc:
        return _Outcome("error", str(exc), {}, None, None)

    vendor_session = plan.vendor_session
    log.info("agent chat %s: %s argv=%s", handle.turn_id, runner, plan.argv[:6])
    try:
        proc = await asyncio.create_subprocess_exec(
            *plan.argv,
            cwd=str(cwd),
            env=plan.env,
            stdin=asyncio.subprocess.PIPE
            if plan.stdin_text is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            limit=_READLINE_LIMIT,
        )
    except (OSError, ValueError) as exc:
        return _Outcome("error", f"Could not start {runner}: {exc}", {}, None, None)

    claude_state = _ClaudeState(turn_id=handle.turn_id, vendor_session=vendor_session)
    codex_state = _CodexState(turn_id=handle.turn_id, vendor_session=vendor_session)
    agy_state = _AgyState(turn_id=handle.turn_id, vendor_session=vendor_session)
    stderr_tail: list[str] = []

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                stderr_tail.append(text)
                del stderr_tail[:-40]

    async def _pump_stdout() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                # A plain-text line (agy without JSON, a banner): show it.
                await handle.emit(
                    make_event(
                        "text_delta",
                        {
                            "turn_id": handle.turn_id,
                            "message_id": f"plain-{handle.turn_id}",
                            "text": line + "\n",
                        },
                    )
                )
                claude_state.text_acc[f"plain-{handle.turn_id}"] = (
                    claude_state.text_acc.get(f"plain-{handle.turn_id}", "") + line + "\n"
                )
                continue
            if not isinstance(obj, dict):
                continue
            if plan.shape == "codex":
                events = translate_codex_line(obj, codex_state)
            elif plan.shape == "agy":
                events = translate_agy_line(obj, agy_state)
            else:
                if obj.get("type") == "control_request":
                    await _answer_control_request(obj)
                    continue
                if obj.get("type") == "control_response":
                    # The CLI's answer to our handshake (its command list).
                    # Nothing the timeline shows, and not a line to translate.
                    continue
                events = translate_claude_line(obj, claude_state)
            for ev in events:
                await handle.emit(ev)
            if plan.keep_stdin and claude_state.saw_result:
                # The turn is over; closing stdin lets the CLI exit.
                _close_stdin()

    def _close_stdin() -> None:
        if proc.stdin is None or proc.stdin.is_closing():
            return
        try:
            proc.stdin.close()
        except OSError as exc:
            log.debug("agent chat %s: stdin close failed: %s", handle.turn_id, exc)

    async def _write_stdin(text: str) -> None:
        if proc.stdin is None or proc.stdin.is_closing():
            return
        try:
            proc.stdin.write(text.encode("utf-8"))
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            log.debug("agent chat %s: stdin write failed: %s", handle.turn_id, exc)

    async def _answer_control_request(obj: dict[str, Any]) -> None:
        """A Claude Code ``control_request`` — a permission prompt — answered
        from the chat's approval card. Anything that is not ``can_use_tool``
        is declined (the CLI asks nothing else of a stdio prompt tool today)."""
        request_id = str(obj.get("request_id") or "")
        req = obj.get("request") if isinstance(obj.get("request"), dict) else {}
        subtype = str(req.get("subtype") or "")
        decision = "deny"
        message = "Not supported by this chat."
        if subtype == "can_use_tool":
            tool_name = str(req.get("tool_name") or req.get("display_name") or "tool")
            tool_input = req.get("input") if isinstance(req.get("input"), dict) else {}
            call_id = str(req.get("tool_use_id") or uuid.uuid4().hex)
            summary = str(req.get("description") or "") or _claude_request_summary(
                tool_name, tool_input
            )
            # The card sits on the tool row the assistant line announced; a
            # plan's ExitPlanMode has no row yet, so it gets one here.
            if tool_name == "ExitPlanMode" and call_id not in claude_state.emitted_tool_ids:
                claude_state.emitted_tool_ids.add(call_id)
                await handle.emit(
                    make_event(
                        "tool_call",
                        {
                            "turn_id": handle.turn_id,
                            "call_id": call_id,
                            "name": tool_name,
                            "input": tool_input,
                            "summary": "Plan ready — approve to start building",
                        },
                    )
                )
                summary = "Plan ready — approve to start building"
            decision = await handle.request_approval(call_id, tool_name, tool_input, summary)
            message = (
                "The person declined this action."
                if decision == "deny"
                else "The turn was stopped."
            )
        if decision in {"allow", "allow_always"}:
            body: dict[str, Any] = {"behavior": "allow", "updatedInput": req.get("input") or {}}
        else:
            body = {"behavior": "deny", "message": message}
        frame = {
            "type": "control_response",
            "response": {"subtype": "success", "request_id": request_id, "response": body},
        }
        await _write_stdin(json.dumps(frame, ensure_ascii=False) + "\n")

    async def _feed_stdin() -> None:
        if proc.stdin is None:
            return
        try:
            # The control-protocol handshake goes FIRST, on the same stdin, so
            # the CLI knows a client is listening before it needs to ask about
            # the first tool call. Back to back with the prompt on purpose: the
            # CLI reads its input in order, and waiting for the response would
            # add a round-trip to every turn for no gain.
            if plan.control_init is not None:
                proc.stdin.write(plan.control_init.encode("utf-8"))
                await proc.stdin.drain()
            if plan.stdin_text is not None:
                proc.stdin.write(plan.stdin_text.encode("utf-8"))
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            log.debug("agent chat %s: stdin closed early: %s", handle.turn_id, exc)
        finally:
            # A CLI that talks back on stdin keeps it until its result line.
            if not plan.keep_stdin:
                _close_stdin()

    async def _watch_cancel() -> None:
        await handle.cancel.wait()
        _kill(proc)

    pump = asyncio.create_task(_pump_stdout())
    drain = asyncio.create_task(_drain_stderr())
    feeder = asyncio.create_task(_feed_stdin())
    watcher = asyncio.create_task(_watch_cancel())
    try:
        await asyncio.wait_for(asyncio.gather(pump, drain, feeder), timeout=_TURN_TIMEOUT_S)
        await proc.wait()
    except TimeoutError:
        _kill(proc)
        status = "error"
        error_text = f"{runner} did not finish within {int(_TURN_TIMEOUT_S)} s."
    except asyncio.CancelledError:
        _kill(proc)
        raise
    finally:
        watcher.cancel()
        for task in (pump, drain, feeder):
            if not task.done():
                task.cancel()

    shape_state: Any = (
        codex_state if plan.shape == "codex" else agy_state if plan.shape == "agy" else claude_state
    )
    if handle.cancel.is_set():
        status = "cancelled"
    elif status == "done":
        st_status = shape_state.status
        st_error = shape_state.error
        if st_status == "error":
            status, error_text = "error", st_error
        elif proc.returncode not in (0, None):
            status = "error"
            error_text = (
                st_error
                or getattr(shape_state, "last_error", None)
                or "\n".join(stderr_tail[-8:]).strip()
                or f"{runner} exited with code {proc.returncode}."
            )

    if plan.shape == "codex":
        usage = codex_state.usage
        vendor_session = codex_state.vendor_session or vendor_session
        emitted_text = codex_state.emitted_text
        plain_tail = ""
    elif plan.shape == "agy":
        usage = agy_state.usage
        vendor_session = agy_state.vendor_session or vendor_session
        emitted_text = agy_state.emitted_text
        plain_tail = claude_state.text_acc.get(f"plain-{handle.turn_id}", "")
        if not emitted_text and agy_state.result_text.strip():
            emitted_text = True
            await handle.emit(
                make_event(
                    "assistant_text",
                    {
                        "turn_id": handle.turn_id,
                        "message_id": f"result-{handle.turn_id}",
                        "text": agy_state.result_text,
                    },
                )
            )
    else:
        usage = claude_state.usage
        cost_usd = claude_state.cost_usd
        vendor_session = claude_state.vendor_session or vendor_session
        emitted_text = claude_state.emitted_text
        plain_tail = claude_state.text_acc.get(f"plain-{handle.turn_id}", "")
        if not emitted_text and claude_state.result_text.strip():
            emitted_text = True
            await handle.emit(
                make_event(
                    "assistant_text",
                    {
                        "turn_id": handle.turn_id,
                        "message_id": f"result-{handle.turn_id}",
                        "text": claude_state.result_text,
                    },
                )
            )
    if not emitted_text and plain_tail.strip():
        await handle.emit(
            make_event(
                "assistant_text",
                {
                    "turn_id": handle.turn_id,
                    "message_id": f"plain-{handle.turn_id}",
                    "text": plain_tail.strip(),
                },
            )
        )

    return _Outcome(status, error_text, usage, cost_usd, vendor_session)


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except (ProcessLookupError, OSError) as exc:
        log.debug("agent chat: killing the CLI failed: %s", exc)
