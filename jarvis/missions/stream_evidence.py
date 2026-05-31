"""Extract tool-call evidence + final answer from a claude `stream.jsonl`.

Shared keystone between the Critic (which must SEE that real tools ran, even
when the rich tool_result frames fall outside the 4000-char log summary) and
the Kontrollierer (which must surface the worker's actual answer to voice for
read-only / informational missions).

The parser is deliberately tolerant: every line is best-effort JSON, anything
unparseable is skipped, so a half-flushed stream never raises.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StreamEvidence:
    """What a worker actually did, distilled from its claude stream."""

    tool_calls: tuple[str, ...]      # tool_use names, in first-seen order
    tool_results: tuple[str, ...]    # truncated tool_result payloads
    final_answer: str                # the worker's terminal reply text

    @property
    def has_tool_evidence(self) -> bool:
        return bool(self.tool_calls)


def _result_text(content) -> str:  # noqa: ANN001 — tolerant of str | list | dict
    """Flatten a tool_result `content` (str | list[block] | dict) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(str(blk.get("text", "")) or json.dumps(blk))
            else:
                parts.append(str(blk))
        return " ".join(p for p in parts if p)
    if isinstance(content, dict):
        return str(content.get("text", "")) or json.dumps(content)
    return str(content)


def extract_stream_evidence(
    stream_text: str,
    *,
    max_result_chars: int = 400,
) -> StreamEvidence:
    """Parse a claude `stream.jsonl` into tool evidence + final answer.

    Args:
        stream_text: Raw NDJSON content of the worker's claude stream.
        max_result_chars: Per tool_result truncation cap.
    """
    tool_calls: list[str] = []
    tool_results: list[str] = []
    final_answer = ""
    last_assistant_text = ""

    for raw in stream_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")

        if otype == "assistant":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict):
                    continue
                if blk.get("type") == "tool_use":
                    name = str(blk.get("name", "")).strip()
                    if name and name not in tool_calls:
                        tool_calls.append(name)
                elif blk.get("type") == "text":
                    txt = str(blk.get("text", "")).strip()
                    if txt:
                        last_assistant_text = txt
        elif otype == "user":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                    txt = _result_text(blk.get("content", "")).strip()
                    if txt:
                        tool_results.append(txt[:max_result_chars])
        elif otype == "result":
            res = obj.get("result")
            if isinstance(res, str) and res.strip():
                final_answer = res.strip()

    if not final_answer:
        final_answer = last_assistant_text

    return StreamEvidence(
        tool_calls=tuple(tool_calls),
        tool_results=tuple(tool_results),
        final_answer=final_answer,
    )


# Tool names that materialise a file on disk. Covers the claude-direct worker
# (`Write`/`Edit`/`MultiEdit`/`NotebookEdit`) and the OpenClaw / generic
# variants (`file_write`/`write_file`/`create_file`). Matched case-sensitively
# against the stream's `tool_use.name`.
_WRITE_TOOL_NAMES: frozenset[str] = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "file_write", "write_file", "create_file",
})

# Keys under `tool_use.input` that carry the target path, in priority order.
_PATH_INPUT_KEYS: tuple[str, ...] = ("file_path", "path", "notebook_path", "filePath")


def _result_is_error(blk: dict) -> bool:  # noqa: ANN001 — tolerant
    """True if a tool_result block signals failure.

    Claude marks failures either with an explicit ``is_error: true`` flag or by
    embedding a ``<tool_use_error>`` marker in the result text (the form the
    live mission_019e7abd iter1 produced: *File has not been read yet*).
    """
    if blk.get("is_error") is True:
        return True
    return "tool_use_error" in _result_text(blk.get("content", ""))


def extract_write_targets(stream_text: str) -> tuple[str, ...]:
    """Paths the worker wrote with a real, non-errored write tool_use.

    Returns the file paths (verbatim, as the worker passed them to the tool)
    of every ``Write``/``Edit``/… tool_use whose matching ``tool_result`` is
    present AND did NOT error. A path is returned at most once, in
    first-confirmed order; a path is included if AT LEAST ONE of its write
    attempts had a matched, non-errored result (so an errored retry followed by
    a successful one still counts).

    Ground-truth discipline (anti-hearsay): a write is only credited when its
    result frame is observed and successful. A tool_use with no ``id`` (cannot
    be correlated to a result) or whose result never arrived (truncated stream)
    is NOT credited — otherwise a malformed frame could let a pre-existing file
    masquerade as freshly written and re-open the false-APPROVE vector the
    GROUND-TRUTH-RULE exists to close (BUG-LIVE-05, mission_019e2c18). The
    Kontrollierer additionally pairs each returned path with an on-disk
    existence check (:meth:`Kontrollierer._augment_diff_with_external_writes`),
    so a confirmed write whose file does not exist is still rejected downstream.

    Tolerant by design: unparseable lines are skipped; a tool_use with no
    resolvable path key is ignored.
    """
    # tool_use_id -> path (write tool_use seen, awaiting its result)
    pending: dict[str, str] = {}
    # path -> True if observed to error; downgraded to False on any success.
    # Only populated from a matched tool_result — never from a bare tool_use.
    errored_by_path: dict[str, bool] = {}
    order: list[str] = []

    def _note(path: str, *, errored: bool) -> None:
        if path not in errored_by_path:
            order.append(path)
            errored_by_path[path] = errored
        elif not errored:
            # Any non-errored write clears a prior error verdict for the path.
            errored_by_path[path] = False

    for raw in stream_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")

        if otype == "assistant":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                if str(blk.get("name", "")).strip() not in _WRITE_TOOL_NAMES:
                    continue
                tool_input = blk.get("input") or {}
                if not isinstance(tool_input, dict):
                    continue
                path = next(
                    (str(tool_input[k]).strip() for k in _PATH_INPUT_KEYS
                     if isinstance(tool_input.get(k), str) and tool_input[k].strip()),
                    "",
                )
                tid = str(blk.get("id", "")).strip()
                # An id is required to correlate the result. An id-less frame
                # cannot be confirmed → drop it (do not credit on hearsay).
                if path and tid:
                    pending[tid] = path
        elif otype == "user":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                tid = str(blk.get("tool_use_id", "")).strip()
                path = pending.pop(tid, None)
                if path is None:
                    continue
                _note(path, errored=_result_is_error(blk))

    # Note: paths left in `pending` had a write frame but no matching result
    # (truncated stream). They are intentionally NOT credited — only a confirmed,
    # non-errored result counts as ground truth.
    return tuple(p for p in order if not errored_by_path[p])


def _has_inworktree_hunk(diff_text: str) -> bool:
    """True if the diff carries a real in-worktree change (a ``diff --git`` hunk).

    An external-target-only diff — out-of-worktree deliverables surfaced by the
    Kontrollierer as ``diff --external-target`` blocks — is NOT an in-worktree
    code change, so the worker's spoken answer (which names the external file)
    should still be read back rather than suppressed as a "code task".
    """
    if not diff_text or not diff_text.strip():
        return False
    return any(ln.startswith("diff --git ") for ln in diff_text.splitlines())


def readonly_answer(diff_text: str, stream_text: str) -> str | None:
    """Return the worker's answer iff this was a genuine read-only / external
    result.

    Speak-back applies to two shapes: (a) a read-only / informational task
    (empty git diff), and (b) an out-of-worktree deliverable whose diff contains
    only ``diff --external-target`` blocks (no in-worktree ``diff --git`` hunk).
    Both need REAL tool-call evidence + a substantive final answer. The
    tool-evidence requirement is the anti-hallucination guard — an empty diff
    with no tool calls (the worker just claimed "done") is NOT a success and
    returns None, so the existing empty-diff veto in the critic still applies.

    An in-worktree code change (a real ``diff --git`` hunk) returns None — those
    keep the diff/delivered-files summary instead of a spoken answer.
    """
    if _has_inworktree_hunk(diff_text):
        return None  # in-worktree code change -> not informational
    ev = extract_stream_evidence(stream_text)
    if not ev.has_tool_evidence:
        return None  # no real work -> let the empty-diff veto handle it
    answer = ev.final_answer.strip()
    if len(answer) < 3:
        return None
    return answer


def summarize_answers(answers: list[str], *, cap: int = 600) -> str:
    """Join per-task answers into a single mission summary, capped."""
    joined = "\n".join(a.strip() for a in answers if a and a.strip())
    if len(joined) > cap:
        return joined[: cap - 1].rstrip() + "…"
    return joined


__all__ = [
    "StreamEvidence",
    "extract_stream_evidence",
    "extract_write_targets",
    "readonly_answer",
    "summarize_answers",
]
