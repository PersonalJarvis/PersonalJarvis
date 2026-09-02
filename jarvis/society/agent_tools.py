"""The two hands only a society agent has: messaging a teammate, and writing
into its own corner of the wiki.

``society_message_agent`` (agent-definition §4.1) is the ONE send path
between agents. It does exactly one thing — append a typed envelope for ONE
teammate — and never spawns or runs a turn; turning envelopes into activity
is the scheduler's monopoly. Two gates: the schema exists only in a society
session's tool set (built per session by ``jarvis/society/surface.py``), and
execution re-checks that the caller is a live active roster row, the target
resolves, and the kill switch is off. The tool runs under
``ToolExecutor.execute()`` like every other (AP-3).

``society_wiki_note`` (agent-definition §5) writes a page under the agent's
namespace ``society/<agent_id>/`` in the Obsidian vault — never anywhere
else, by construction (there is no path argument) — with provenance
frontmatter, and records the page in the knowledge staging table as
unreviewed. The agent's durable notes live in ``memory.md`` of the same
folder.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Final

from jarvis.core.protocols import ToolResult

from .events import MsgType, now_ms
from .failure_reasons import FailureReason, retry_action
from .roster import AgentState, slugify

log = logging.getLogger(__name__)

__all__ = ["MESSAGE_TOOL_NAME", "WIKI_NOTE_TOOL_NAME", "MessageAgentTool", "WikiNoteTool"]

MESSAGE_TOOL_NAME: Final[str] = "society_message_agent"
WIKI_NOTE_TOOL_NAME: Final[str] = "society_wiki_note"
_KINDS: Final[dict[str, MsgType]] = {
    "say": MsgType.SAY,
    "query": MsgType.QUERY,
    "answer": MsgType.ANSWER,
    "propose": MsgType.PROPOSE,
}
_MAX_TEXT: Final[int] = 8_000
_MAX_NOTE: Final[int] = 40_000


def _failure(reason: FailureReason, detail: str) -> ToolResult:
    return ToolResult(
        success=False,
        output={"reason": str(reason), "retry": str(retry_action(reason))},
        error=f"{reason}: {detail}",
    )


class MessageAgentTool:
    """Send one message to ONE teammate. Fire-and-forget."""

    name: str = MESSAGE_TOOL_NAME
    risk_tier: str = "monitor"
    description: str = (
        "Send a message to ONE teammate in your agent society. Compose the message "
        "yourself — never forward another message verbatim — and address the one "
        "teammate whose role fits; do not fan out to several. Use kind 'query' when "
        "you need an answer, 'propose' to suggest a plan, 'answer' when replying to a "
        "query, else 'say'. The teammate reads it in their own chat and may reply "
        "later; this call returns at once. It never assigns work — ask Jarvis or an "
        "orchestrator to assign."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "The teammate's name or id, exactly as listed under Teammates.",
            },
            "text": {"type": "string", "description": "Your message, in your own words."},
            "kind": {
                "type": "string",
                "enum": sorted(_KINDS),
                "description": "say (default) | query | answer | propose",
            },
            "refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional pointers the teammate should open: wiki:..., file:...",
            },
        },
        "required": ["target", "text"],
    }
    is_action_tool: bool = True

    def __init__(self, runtime: Any, agent_id: str) -> None:
        self._runtime = runtime
        self._agent_id = agent_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        rt = self._runtime
        if await rt.store.kill_switch():
            return _failure(FailureReason.KILL_SWITCH, "the society is halted")
        caller = await rt.roster.get(self._agent_id)
        if caller is None or caller.state is not AgentState.ACTIVE:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "caller is not an active agent")
        target_key = str(args.get("target", "")).strip()
        text = str(args.get("text", "")).strip()[:_MAX_TEXT]
        if not target_key or not text:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "target and text are required")
        target = await rt.roster.resolve(target_key)
        if target is None or target.state is AgentState.ARCHIVED:
            return _failure(FailureReason.TARGET_UNKNOWN, f"no teammate named {target_key!r}")
        if target.agent_id == caller.agent_id:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "you cannot message yourself")
        if target.state is AgentState.PAUSED:
            return _failure(FailureReason.TARGET_PAUSED, f"{target.name} is paused")
        kind = str(args.get("kind") or "say").strip().lower()
        msg_type = _KINDS.get(kind)
        if msg_type is None:
            return _failure(FailureReason.BLOCKED_BY_POLICY, f"unknown kind {kind!r}")
        refs = args.get("refs")
        payload: dict[str, Any] = {}
        if isinstance(refs, list) and refs:
            payload["refs"] = [str(r) for r in refs][:20]
        trace_id = f"chat:{caller.agent_id}:{target.agent_id}"
        env = await rt.say(
            from_agent=caller.agent_id,
            to_agent=target.agent_id,
            text=text,
            trace_id=trace_id,
            msg_type=msg_type,
            payload=payload,
        )
        return ToolResult(
            success=True,
            output={
                "delivered_to": target.name,
                "kind": kind,
                "seq": env.seq,
                "trace_id": env.trace_id,
            },
        )


_FRONTMATTER_SAFE = re.compile(r"[\r\n\"]")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".society-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class WikiNoteTool:
    """Write into ``society/<agent_id>/`` of the vault — the agent's memory."""

    name: str = WIKI_NOTE_TOOL_NAME
    risk_tier: str = "monitor"
    description: str = (
        "Write a note into YOUR folder of the shared wiki (society/<you>/). Use kind "
        "'note' for a work note about something you found or produced (one page per "
        "topic, give it a title), or kind 'memory' to append a durable fact to your "
        "memory page. Pages are marked unreviewed until the user promotes them; write "
        "conclusions and facts, not chat transcripts. Reading the wiki is wiki-recall "
        "and wiki-page-read."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short page title (kind 'note')."},
            "text": {"type": "string", "description": "Markdown body."},
            "kind": {"type": "string", "enum": ["note", "memory"], "description": "note | memory"},
            "origin": {
                "type": "string",
                "enum": ["tool", "web", "agent", "user"],
                "description": "Where the knowledge came from (default 'agent').",
            },
        },
        "required": ["text"],
    }
    is_action_tool: bool = True

    def __init__(self, runtime: Any, agent_id: str, *, vault_root: Path) -> None:
        self._runtime = runtime
        self._agent_id = agent_id
        self._vault_root = Path(vault_root)

    @property
    def namespace(self) -> Path:
        return self._vault_root / "society" / self._agent_id

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        rt = self._runtime
        caller = await rt.roster.get(self._agent_id)
        if caller is None or caller.state is not AgentState.ACTIVE:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "caller is not an active agent")
        text = str(args.get("text", "")).strip()[:_MAX_NOTE]
        if not text:
            return _failure(FailureReason.BLOCKED_BY_POLICY, "text is required")
        kind = str(args.get("kind") or "note").strip().lower()
        origin = str(args.get("origin") or "agent").strip().lower()
        if origin not in ("tool", "web", "agent", "user"):
            origin = "agent"
        trace = str(getattr(ctx, "trace_id", "") or "")
        today = _dt.datetime.now(tz=_dt.UTC).date().isoformat()
        if kind == "memory":
            path = self.namespace / "memory.md"
            existing = path.read_text(encoding="utf-8") if path.is_file() else ""
            if not existing:
                existing = self._frontmatter(f"{caller.name} — memory", origin, trace) + "\n"
            body = existing.rstrip("\n") + f"\n\n## {today}\n\n{text}\n"
            _atomic_write(path, body)
            summary = text[:280]
        else:
            title = _FRONTMATTER_SAFE.sub(" ", str(args.get("title") or text[:60])).strip()
            if not title:
                title = "note"
            slug = slugify(title)[:60]
            path = self.namespace / f"{today}-{slug}.md"
            n = 2
            while path.exists():
                path = self.namespace / f"{today}-{slug}-{n}.md"
                n += 1
            page = self._frontmatter(title, origin, trace) + f"\n# {title}\n\n{text}\n"
            _atomic_write(path, page)
            summary = f"{title}: {text[:220]}"
        rel = path.relative_to(self._vault_root).as_posix()
        try:
            await rt.store.insert_knowledge(
                {
                    "agent_id": caller.agent_id,
                    "wiki_path": rel,
                    "origin": origin,
                    "source_event": None,
                    "trace_id": trace or None,
                    "reviewed": 0,
                    "summary": summary,
                    "created_ms": now_ms(),
                }
            )
        except Exception:  # noqa: BLE001 — the page is written; the staging row is bookkeeping
            log.warning("society wiki note: knowledge row not recorded for %s", rel, exc_info=True)
        return ToolResult(success=True, output={"path": rel, "kind": kind, "reviewed": False})

    def _frontmatter(self, title: str, origin: str, trace: str) -> str:
        safe_title = _FRONTMATTER_SAFE.sub(" ", title)
        return (
            "---\n"
            f'title: "{safe_title}"\n'
            f"author: agent:{self._agent_id}\n"
            f"origin: {origin}\n"
            f"trace: {trace or 'none'}\n"
            "reviewed: false\n"
            "---\n"
        )
