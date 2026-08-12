"""Reconstruct a run's step timeline from its archived worker streams.

The Visualization section draws a run as a node graph: what was asked, which
tools the worker actually invoked (with their real success/failure), and what
came out. The ground truth for that already exists on disk — every mission
archives its worker transcript at ``tasks/<task_id>/logs/stream.jsonl`` — so
this module is a READ over the archive, never a second store. A run that
predates the feature gets its timeline for free, and there is nothing to
migrate or keep in sync.

Provider-agnosticism is inherited, not re-implemented: the raw stream may be
claude ``stream-json``, codex ``exec --json`` or gemini plain text, and
:func:`jarvis.missions.stream_evidence.normalize_worker_stream` rewrites all of
them into the canonical claude shape the walker below understands. That is the
same canonicalizer the Critic grades through, so the graph can never show a
different story than the one the mission pipeline judged.

Anti-hearsay discipline carries over too: a ``tool_use`` becomes a ``done`` or
``failed`` step only when its correlated ``tool_result`` frame was observed;
a call whose result never arrived (truncated stream, killed worker) is
reported as ``skipped`` rather than silently trusted.

Steps carry a ``kind`` so the graph can draw the run the way it actually
unfolded, not just as a chain of tool calls:

* ``"reasoning"`` — the worker's own thinking/commentary between actions.
  Consecutive thinking/text blocks collapse into ONE step per gap (a run with
  fifty thoughts stays readable), and trailing text is never emitted as a
  step — it IS the final answer, not a step toward it.
* ``"spawn"`` — a tool call that launches a sub-agent (Task/Agent/…), worth
  its own visual grammar in the graph.
* ``"tool"`` — every other tool call.

``task_key`` names the worker (task dir) a step belongs to, so a mission that
ran several workers in parallel is drawn as parallel branches, not one
falsely-linear chain.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Final

from jarvis.missions.stream_evidence import normalize_worker_stream

logger = logging.getLogger(__name__)

# Bounded work per request: the endpoint re-reads the archive on demand, so a
# pathological multi-hundred-MB transcript must not stall the event loop's
# worker thread or balloon the response. Caps are stated in the payload
# (``truncated`` / ``dropped_steps``) so a shortened timeline reads as
# "shortened", never as "the run did less".
MAX_STREAM_BYTES: Final[int] = 24 * 1_048_576
MAX_STEPS: Final[int] = 200
_DETAIL_CHARS: Final[int] = 160
_OUTPUT_CHARS: Final[int] = 400

# tool_use.input keys that carry the one line a node label wants, tried in
# order per tool family. Everything else falls back to the first short string
# value of the input dict.
_PATH_KEYS: Final[tuple[str, ...]] = (
    "file_path",
    "path",
    "notebook_path",
    "filePath",
)
_DETAIL_KEYS: Final[tuple[str, ...]] = (
    "command",
    "cmd",
    "script",
    *_PATH_KEYS,
    "pattern",
    "query",
    "url",
    "prompt",
    "description",
)

# Tool names that materialise a file — their target path is surfaced on the
# step (``writes``) so the frontend can connect an artifact node to the exact
# step that produced it. Mirrors ``stream_evidence._WRITE_TOOL_NAMES``.
_WRITE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        "file_write",
        "write_file",
        "create_file",
        # gemini-cli's edit tool ("replace") — params carry file_path.
        "replace",
    }
)

# Tool names (lowercased) that launch a sub-agent rather than acting directly.
# They become ``kind: "spawn"`` steps — the graph gives them branch grammar.
_SPAWN_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {"task", "agent", "subagent", "spawn_worker", "spawn_subagent", "dispatch_agent"}
)


def _clip(text: str, cap: int) -> str:
    text = " ".join(text.split())
    if len(text) <= cap:
        return text
    return text[: cap - 1].rstrip() + "…"


def _tool_detail(name: str, tool_input: dict[str, Any]) -> str:
    """The one-line label for a tool call — its command, path or query."""
    for key in _DETAIL_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value.strip(), _DETAIL_CHARS)
    for value in tool_input.values():
        if isinstance(value, str) and value.strip():
            return _clip(value.strip(), _DETAIL_CHARS)
    return ""


def _result_text(content: Any) -> str:
    """Flatten a tool_result ``content`` (str | list[block] | dict) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(str(blk.get("text", "")))
            else:
                parts.append(str(blk))
        return " ".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text", ""))
    return str(content)


def _result_is_error(blk: dict[str, Any]) -> bool:
    if blk.get("is_error") is True:
        return True
    return "tool_use_error" in _result_text(blk.get("content", ""))


def _read_stream(path: Path) -> tuple[str, bool]:
    """Read a stream file up to :data:`MAX_STREAM_BYTES`. Returns (text, truncated)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            raw = fh.read(MAX_STREAM_BYTES)
    except OSError as exc:
        logger.debug("run_plan: unreadable stream %s: %s", path, exc)
        return "", False
    return raw.decode("utf-8", errors="replace"), size > MAX_STREAM_BYTES


def _walk_stream(stream_text: str, task_key: str) -> tuple[list[dict[str, Any]], str]:
    """Turn one canonical claude stream into ordered steps + the final answer."""
    steps: list[dict[str, Any]] = []
    # tool_use id -> step (awaiting its correlated result frame).
    pending: dict[str, dict[str, Any]] = {}
    final_answer = ""
    last_assistant_text = ""
    seq = 0
    # Thinking/commentary blocks observed since the last action, in order.
    # Each entry is (is_thinking, text): the flag decides whether a TRAILING
    # entry may still become a step (thinking yes, text no — trailing text is
    # the final answer, and must not appear twice).
    reasoning: list[tuple[bool, str]] = []

    def _flush_reasoning(*, thinking_only: bool = False) -> None:
        nonlocal seq
        kept = [t for is_thinking, t in reasoning if is_thinking or not thinking_only]
        reasoning.clear()
        text = " ".join(kept).strip()
        if not text:
            return
        steps.append(
            {
                "step_id": f"{task_key}:{seq}",
                "name": _clip(text, _DETAIL_CHARS),
                "tool_name": None,
                "kind": "reasoning",
                "task_key": task_key,
                # Reasoning has no result frame to wait for — it was observed
                # verbatim in the stream, so it is simply "done".
                "status": "done",
                "output": _clip(text, _OUTPUT_CHARS),
                "error": None,
                "writes": [],
            }
        )
        seq += 1

    for raw in stream_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # A malformed line is skipped, not fatal — the rest of the stream still parses.
            continue
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")

        if otype == "assistant":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_use":
                    # The thoughts that led to this action become ONE step in
                    # front of it — the graph reads Think → Act, never a wall
                    # of fifty thought cards.
                    _flush_reasoning()
                    name = str(blk.get("name", "")).strip() or "tool"
                    tool_input = blk.get("input") or {}
                    if not isinstance(tool_input, dict):
                        tool_input = {}
                    step: dict[str, Any] = {
                        "step_id": f"{task_key}:{seq}",
                        "name": _tool_detail(name, tool_input) or name,
                        "tool_name": name,
                        "kind": "spawn" if name.lower() in _SPAWN_TOOL_NAMES else "tool",
                        "task_key": task_key,
                        # Anti-hearsay default — upgraded to done/failed only
                        # by a correlated result frame below.
                        "status": "skipped",
                        "output": None,
                        "error": None,
                        "writes": [],
                    }
                    if name in _WRITE_TOOL_NAMES:
                        for key in _PATH_KEYS:
                            value = tool_input.get(key)
                            if isinstance(value, str) and value.strip():
                                step["writes"] = [value.strip()]
                                break
                    seq += 1
                    steps.append(step)
                    tid = str(blk.get("id", "")).strip()
                    if tid:
                        pending[tid] = step
                elif blk.get("type") == "thinking":
                    txt = str(blk.get("thinking", "")).strip()
                    if txt:
                        reasoning.append((True, txt))
                elif blk.get("type") == "text":
                    txt = str(blk.get("text", "")).strip()
                    if txt:
                        last_assistant_text = txt
                        reasoning.append((False, txt))
        elif otype == "user":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                tid = str(blk.get("tool_use_id", "")).strip()
                if tid not in pending:
                    continue
                step = pending.pop(tid)
                excerpt = _clip(_result_text(blk.get("content", "")).strip(), _OUTPUT_CHARS)
                if _result_is_error(blk):
                    step["status"] = "failed"
                    step["error"] = excerpt or "tool call failed"
                else:
                    step["status"] = "done"
                    step["output"] = excerpt or None
        elif otype == "result":
            res = obj.get("result")
            if isinstance(res, str) and res.strip():
                final_answer = res.strip()

    # Trailing thoughts (a run that reasoned its way straight to the answer,
    # or thought after its last action) still become a step — but trailing
    # TEXT stays out: it is the final answer, not a step toward it.
    _flush_reasoning(thinking_only=True)

    if not final_answer:
        final_answer = last_assistant_text
    return steps, final_answer


def build_run_plan(session_dir: Path, *, utterance: str | None = None) -> dict[str, Any]:
    """The Visualization/Outputs read model for one archived run.

    Returns the ``{plan, steps}`` shape ``usePlanForOutput`` already types,
    plus honesty fields (``truncated``, ``dropped_steps``, ``final_answer``).
    A run with no readable stream returns ``{plan: None, steps: []}`` — the
    exact contract of the former stub, so every consumer of "no plan
    available" keeps working unchanged.
    """
    steps: list[dict[str, Any]] = []
    final_answer = ""
    truncated = False

    try:
        stream_paths = sorted(session_dir.glob("tasks/*/logs/stream.jsonl"))
    except OSError as exc:
        logger.debug("run_plan: cannot list %s: %s", session_dir, exc)
        stream_paths = []

    for stream_path in stream_paths:
        raw, was_cut = _read_stream(stream_path)
        truncated = truncated or was_cut
        if not raw.strip():
            continue
        task_key = stream_path.parent.parent.name
        task_steps, answer = _walk_stream(normalize_worker_stream(raw), task_key)
        steps.extend(task_steps)
        if answer:
            final_answer = answer

    dropped = max(0, len(steps) - MAX_STEPS)
    if dropped:
        steps = steps[:MAX_STEPS]

    if not steps and not final_answer:
        return {"plan": None, "steps": []}

    # Reasoning steps are always "done" and must not dilute the verdict —
    # "every action failed" is judged over the actions alone.
    actions = [s for s in steps if s.get("kind") != "reasoning"]
    failed = sum(1 for s in actions if s["status"] == "failed")
    plan = {
        "plan_id": session_dir.name,
        "vision": utterance or "",
        "status": "failed" if failed and failed == len(actions) else "complete",
        "total_steps": len(steps) + dropped,
    }
    return {
        "plan": plan,
        "steps": steps,
        "final_answer": final_answer or None,
        "truncated": truncated,
        "dropped_steps": dropped,
    }
