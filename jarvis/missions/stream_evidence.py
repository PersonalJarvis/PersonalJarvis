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
import re
from dataclasses import dataclass
from typing import Final


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


# --- multi-provider stream normalisation ------------------------------------
#
# Every extractor below was written for claude `stream-json` (assistant/user/
# result frames with tool_use/tool_result blocks). Two other worker backends
# write a DIFFERENT shape to the SAME on-disk stream.jsonl the Critic reads:
#   * Codex `exec --json`: real actions are `item.completed` frames whose
#     `item.type` is agent_message / file_change / command_execution /
#     mcp_tool_call / web_search. (``codex_direct_worker._translate`` maps these
#     to claude shapes for the LIVE event stream, but the on-disk log the Critic
#     grades stays RAW codex — live mission 019ec761, 2026-06-15: a codex
#     informational answer was invisible -> readonly_answer None -> empty-diff
#     veto fired 3x -> critic_loop_exhausted.)
#   * Gemini CLI (`--output-format text`): plain text, no JSON frames at all.
# To keep every gate PROVIDER-AGNOSTIC (the maintainer's multi-provider mandate)
# we rewrite both shapes into the canonical claude shape ONCE, up front, so the
# battle-tested extractors are unchanged. A pure-claude stream round-trips
# unchanged (claude is the canonical internal shape).

_CLAUDE_FRAME_TYPES: frozenset[str] = frozenset(
    {"assistant", "user", "result", "system", "stream_event"}
)

# Codex command output embedded into a synthetic tool_result is capped here so a
# multi-hundred-KB `aggregated_output` line cannot bloat the rewritten stream
# (the extractors truncate again to ``max_result_chars`` downstream).
_CODEX_OUTPUT_CAP: int = 4000


def _codex_item_to_claude_lines(
    item: dict, counter: int
) -> tuple[list[str], int]:  # noqa: ANN001 — tolerant of arbitrary item dicts
    """Map one codex ``item.completed`` ``item`` to claude-shaped NDJSON lines.

    Returns ``(lines, counter)``. file_change / command_execution / tool items
    emit an assistant ``tool_use`` + a correlated user ``tool_result`` (so the
    anti-hearsay id-matching in :func:`extract_write_targets` /
    :func:`extract_verified_commands` credits them); ``agent_message`` emits
    assistant text. ``counter`` seeds unique synthetic tool_use ids so multiple
    items never collide.
    """
    itype = item.get("type")
    lines: list[str] = []

    def _tool_use(name: str, tool_input: dict, tid: str) -> str:
        return json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": tid, "name": name, "input": tool_input},
            ]},
        })

    def _tool_result(tid: str, content: str, *, is_error: bool) -> str:
        blk: dict = {"type": "tool_result", "tool_use_id": tid, "content": content}
        if is_error:
            blk["is_error"] = True
        return json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [blk]},
        })

    if itype == "agent_message":
        txt = str(item.get("text", "") or "").strip()
        if txt:
            lines.append(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant", "content": [
                    {"type": "text", "text": txt},
                ]},
            }))
        return lines, counter

    if itype == "file_change":
        changes = item.get("changes")
        if not isinstance(changes, list) or not changes:
            changes = [{}]
        for change in changes:
            if not isinstance(change, dict):
                continue
            counter += 1
            tid = f"codex_fc_{counter}"
            lines.append(_tool_use("Write", change, tid))
            lines.append(_tool_result(tid, "(file change applied)", is_error=False))
        return lines, counter

    if itype == "command_execution":
        counter += 1
        tid = f"codex_cmd_{counter}"
        command = str(item.get("command", "") or "")
        output = str(item.get("aggregated_output", item.get("output", "")) or "")
        exit_code = item.get("exit_code")
        # codex emits item.completed only after the command finished; a missing
        # exit_code is treated as success, a present non-"0" code as failure.
        is_error = exit_code is not None and str(exit_code).strip() not in ("0", "")
        lines.append(_tool_use("Bash", {"command": command}, tid))
        lines.append(_tool_result(tid, output[:_CODEX_OUTPUT_CAP], is_error=is_error))
        return lines, counter

    if itype in ("mcp_tool_call", "web_search"):
        counter += 1
        tid = f"codex_{itype}_{counter}"
        if itype == "web_search":
            name = "web_search"
        else:
            server = str(item.get("server") or "mcp").strip()
            tool = str(item.get("tool") or "tool").strip()
            name = f"mcp__{server}__{tool}"
        excerpt = str(
            item.get("result") or item.get("query") or item.get("output") or ""
        )[:_CODEX_OUTPUT_CAP]
        lines.append(_tool_use(name, {}, tid))
        status = str(item.get("status") or "completed").strip().lower()
        is_error = status in {"failed", "error", "cancelled", "canceled"}
        lines.append(
            _tool_result(tid, excerpt or "(tool completed)", is_error=is_error)
        )
        return lines, counter

    return lines, counter


def _normalize_worker_stream(stream_text: str) -> str:
    """Rewrite codex / gemini worker streams into the canonical claude shape.

    See the module note above. A pure-claude stream returns unchanged; codex
    ``item.completed`` frames become assistant/user pairs; a gemini plain-text
    transcript (no JSON frames at all) becomes a single ``result`` frame so the
    spoken answer is recoverable.
    """
    if not stream_text:
        return stream_text
    out: list[str] = []
    plain_text: list[str] = []
    saw_json = False
    counter = 0

    for raw in stream_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            plain_text.append(line)
            continue
        if not isinstance(obj, dict):
            plain_text.append(line)
            continue
        saw_json = True
        otype = obj.get("type")
        if otype in _CLAUDE_FRAME_TYPES:
            out.append(line)  # claude frame — passed through unchanged
            continue
        if otype == "item.completed":
            item = obj.get("item")
            if isinstance(item, dict):
                mapped, counter = _codex_item_to_claude_lines(item, counter)
                out.extend(mapped)
            continue
        # Any other codex frame (thread.started / turn.* / error / item.created
        # / item.delta) carries no deliverable evidence — drop it.

    # Gemini `--output-format text`: the whole transcript is plain text with no
    # JSON frames. Treat it as the worker's final answer so the spoken answer is
    # recoverable. Gated on NOTHING having parsed as JSON, so a stray non-JSON
    # line inside a claude/codex stream is never misread as the answer.
    if not saw_json and plain_text:
        joined = "\n".join(plain_text).strip()
        if joined:
            out.append(json.dumps({"type": "result", "result": joined}))

    return "\n".join(out)


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
    stream_text = _normalize_worker_stream(stream_text)
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
    stream_text = _normalize_worker_stream(stream_text)
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


# Shell-execution tool names across the worker backends (claude-direct `Bash`,
# OpenClaw / codex / generic variants). Matched case-sensitively against
# `tool_use.name`.
_SHELL_TOOL_NAMES: frozenset[str] = frozenset({
    "Bash", "shell", "run_command", "exec", "execute", "run_shell_command",
})

# Keys under `tool_use.input` that carry the shell command string.
_COMMAND_INPUT_KEYS: tuple[str, ...] = ("command", "cmd", "script", "shell")

# State-CHANGING git / GitHub-CLI operations. A successful one of these is a
# real deliverable for a "commit and push" / "open a PR" task that leaves NO
# worktree diff — the work happens via the shell, not a file write. Read-only
# ops (status, log, diff, fetch) are deliberately excluded: they are not a
# deliverable and must not satisfy an empty-diff veto.
# The git arm allows global options BEFORE the mutating subcommand so common
# real-world forms still match: ``git -C <dir> push`` (worker running from a
# different cwd — very common), ``git --no-pager commit``, ``git -c k=v push``.
# Each option is either a value-bearing global (``-C <path>``, ``--git-dir=…``)
# or a plain flag (``-q`` / ``--force``). The subcommand must be the first
# NON-option word, so ``git log --grep=push`` does NOT match (``log`` is not a
# mutating subcommand and the ``push`` inside the flag value is never in
# subcommand position).
_MUTATING_CMD_RE = re.compile(
    r"\bgit\b(?:\s+(?:-[cC]\s+\S+|--(?:git-dir|work-tree|namespace)(?:=|\s+)\S+"
    r"|--?\S+))*\s+(?:push|commit|merge|tag|cherry-pick|revert|am)\b"
    r"|\bgh\s+(?:pr|issue|release|repo|gist)\s+"
    r"(?:create|merge|close|edit|comment|review|delete|reopen)\b",
    re.IGNORECASE,
)


def extract_verified_commands(
    stream_text: str, *, max_result_chars: int = 400
) -> tuple[tuple[str, str], ...]:
    """State-changing git/GitHub shell commands the worker ran successfully.

    Returns ``(command, result_excerpt)`` tuples for every shell ``tool_use``
    whose command matches a recognised mutating git/``gh`` operation (push,
    commit, merge, ``gh pr create``, …) AND whose correlated ``tool_result`` is
    present and did NOT error.

    Why this is ground truth, not hearsay: the ``tool_result`` is the REAL
    subprocess output — the line ``main -> main`` was written by the actual
    ``git`` process, not asserted by the LLM. A "commit and push" / "open a PR"
    task legitimately produces an EMPTY worktree diff (the change is a commit or
    a remote ref update, not a working-tree file), so the empty-diff
    GROUND-TRUTH-RULE would fail it 3× → ``critic_loop_exhausted``. Crediting a
    verified mutating command closes that false-negative.

    Anti-hearsay discipline (mirrors :func:`extract_write_targets`): a command
    is credited only when its result frame is observed AND successful. A
    ``tool_use`` with no ``id`` (cannot be correlated) or whose result never
    arrived (truncated stream) is NOT credited. Read-only commands (``git
    status``, ``git log``) never match the mutating pattern, so they cannot
    satisfy an empty diff. The Critic still grades the command + its output as
    the final judge — this only lets it SEE the evidence instead of vetoing
    blind.
    """
    stream_text = _normalize_worker_stream(stream_text)
    pending: dict[str, str] = {}          # tool_use_id -> command string
    credited: list[tuple[str, str]] = []
    seen_commands: set[str] = set()

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
                if str(blk.get("name", "")).strip() not in _SHELL_TOOL_NAMES:
                    continue
                tool_input = blk.get("input") or {}
                if not isinstance(tool_input, dict):
                    continue
                command = next(
                    (str(tool_input[k]).strip() for k in _COMMAND_INPUT_KEYS
                     if isinstance(tool_input.get(k), str) and tool_input[k].strip()),
                    "",
                )
                tid = str(blk.get("id", "")).strip()
                # An id is required to correlate the result; a mutating command
                # with no confirmable result is not credited (anti-hearsay).
                if command and tid and _MUTATING_CMD_RE.search(command):
                    pending[tid] = command
        elif otype == "user":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                tid = str(blk.get("tool_use_id", "")).strip()
                command = pending.pop(tid, None)
                if command is None or _result_is_error(blk):
                    continue
                if command in seen_commands:
                    continue
                seen_commands.add(command)
                result_excerpt = _result_text(blk.get("content", "")).strip()
                credited.append((command, result_excerpt[:max_result_chars]))

    # Commands left in `pending` had no matching result (truncated stream) —
    # intentionally NOT credited.
    return tuple(credited)


def extract_verified_external_actions(
    stream_text: str, *, max_result_chars: int = 800
) -> tuple[tuple[str, str], ...]:
    """Successful MCP actions with a correlated, non-errored result frame.

    A remote action can legitimately leave no filesystem diff. It is credited
    only when the worker stream contains both a namespaced MCP ``tool_use`` and
    its matching successful ``tool_result``. A bare tool call, prose claim, or
    truncated stream is never evidence. Codex ``mcp_tool_call`` items are first
    normalised into the same paired representation as Claude workers.
    """
    stream_text = _normalize_worker_stream(stream_text)
    pending: dict[str, str] = {}
    credited: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

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
                name = str(blk.get("name", "")).strip()
                tid = str(blk.get("id", "")).strip()
                is_mcp = name.startswith("mcp__") or "/" in name
                if is_mcp and tid:
                    pending[tid] = name
        elif otype == "user":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                tid = str(blk.get("tool_use_id", "")).strip()
                name = pending.pop(tid, None)
                if name is None or _result_is_error(blk):
                    continue
                result = _result_text(blk.get("content", "")).strip()
                if not result:
                    result = "(tool completed successfully; no output captured)"
                evidence = (name, result[:max_result_chars])
                if evidence not in seen:
                    seen.add(evidence)
                    credited.append(evidence)

    return tuple(credited)


# Desktop/process-LAUNCH commands that produce NO file diff: the deliverable is
# a running process, not a file change. Cross-platform (Win/mac/Linux). Mirrors
# the git/gh command-evidence path so a diff-less "open Explorer / launch Chrome"
# mission can be credited as real work instead of vetoed as an empty diff.
_DESKTOP_ACTION_CMD_RE = re.compile(
    # Windows: start explorer.exe / start "" chrome / start calc
    # Tightened: exclude flag-style runs (/B, /WAIT → (?!/|-)) and common
    # CLI/dev tools (git, python, npm, etc.) that workers run async via
    # `start <tool>` — those are CLI runs, not GUI launches, and must not
    # satisfy the deterministic empty-diff veto for non-launch tasks.
    r"\bstart\s+(?!/|-)(?!git\b|python\b|py\b|pip\b|npm\b|npx\b|node\b|cargo\b"
    r"|go\b|dotnet\b|mvn\b|gradle\b|ruby\b|java\b|deno\b|bun\b"
    r"|pwsh\b|powershell\b|cmd\b|bash\b|sh\b)\S"
    r"|\bexplorer(?:\.exe)?\b"             # bare explorer.exe
    r"|\bStart-Process\b"                  # PowerShell Start-Process
    r"|\bcmd\b.*/[cC]\s+start\b"          # cmd /c start ...
    r"|\bxdg-open\b"                       # Linux
    r"|\bgio\s+open\b"                     # Linux (modern)
    r"|\bopen\s+-[aAbnegtWR]"             # macOS: open -a AppName
    , re.IGNORECASE,
)


def extract_verified_desktop_actions(
    stream_text: str, *, max_result_chars: int = 400
) -> tuple[tuple[str, str], ...]:
    """Desktop-launch shell commands the worker ran successfully.

    Returns ``(command, result_excerpt)`` tuples for every shell ``tool_use``
    whose command matches a recognised desktop/process-launch operation (Windows
    ``start``, PowerShell ``Start-Process``, Linux ``xdg-open``/``gio open``,
    macOS ``open -a``) AND whose correlated ``tool_result`` is present and did
    NOT error.

    Why this is ground truth, not hearsay: the ``tool_result`` is the REAL
    subprocess output from the shell. An "open Explorer" / "launch Chrome" /
    "start the calculator" task legitimately produces an EMPTY worktree diff
    (the deliverable is a running process, not a file change), so the
    empty-diff GROUND-TRUTH-RULE would fail it 3× → ``critic_loop_exhausted``.
    Crediting a verified desktop-launch command closes that false-negative.

    Silent launch handling: a successful ``start explorer.exe`` typically has
    EMPTY stdout because the process is detached. When the non-errored result
    excerpt is empty, we substitute the literal
    ``"(command succeeded; no output captured)"`` — a silent detached spawn IS
    success, not missing evidence.

    Anti-hearsay discipline (mirrors :func:`extract_verified_commands`): a
    command is credited only when its result frame is observed AND successful.
    A ``tool_use`` with no ``id`` (cannot be correlated) or whose result never
    arrived (truncated stream) is NOT credited. Read-only commands (``ls``,
    ``cat``) never match the launch pattern, so they cannot satisfy an empty
    diff. The Critic still grades the command + its output as the final judge —
    this only lets it SEE the evidence instead of vetoing blind.
    """
    stream_text = _normalize_worker_stream(stream_text)
    pending: dict[str, str] = {}          # tool_use_id -> command string
    credited: list[tuple[str, str]] = []
    seen_commands: set[str] = set()

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
                if str(blk.get("name", "")).strip() not in _SHELL_TOOL_NAMES:
                    continue
                tool_input = blk.get("input") or {}
                if not isinstance(tool_input, dict):
                    continue
                command = next(
                    (str(tool_input[k]).strip() for k in _COMMAND_INPUT_KEYS
                     if isinstance(tool_input.get(k), str) and tool_input[k].strip()),
                    "",
                )
                tid = str(blk.get("id", "")).strip()
                # An id is required to correlate the result; a launch command
                # with no confirmable result is not credited (anti-hearsay).
                if command and tid and _DESKTOP_ACTION_CMD_RE.search(command):
                    pending[tid] = command
        elif otype == "user":
            for blk in (obj.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                    continue
                tid = str(blk.get("tool_use_id", "")).strip()
                command = pending.pop(tid, None)
                if command is None or _result_is_error(blk):
                    continue
                if command in seen_commands:
                    continue
                seen_commands.add(command)
                result_excerpt = _result_text(blk.get("content", "")).strip()
                # A silent detached spawn produces no stdout — substitute a
                # clear sentinel rather than leaving an empty evidence string
                # that the Critic might misread as "no output = not run".
                if not result_excerpt:
                    result_excerpt = "(command succeeded; no output captured)"
                credited.append((command, result_excerpt[:max_result_chars]))

    # Commands left in `pending` had no matching result (truncated stream) —
    # intentionally NOT credited.
    return tuple(credited)


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


# Every diff-block prefix the pipeline uses to record a real, ground-truth
# worker action: an in-worktree file change (``diff --git``), an out-of-worktree
# deliverable (``diff --external-target``, Kontrollierer-augmented), a verified
# state-changing git/gh command (``diff --command-evidence``), or a verified
# desktop launch (``diff --desktop-action-evidence``). Each one is observable
# ground truth that the worker DID something — the same standard the empty-diff
# GROUND-TRUTH-RULE applies.
_DIFF_ACTION_PREFIXES: tuple[str, ...] = (
    "diff --git ",
    "diff --external-target",
    "diff --command-evidence",
    "diff --desktop-action-evidence",
    "diff --external-action-evidence",
)


def diff_has_action_evidence(diff_text: str) -> bool:
    """True if the diff proves a real worker action (file change or augmented op).

    This is the ground-truth counterpart to :func:`_extract_tool_call_evidence`
    for workers that perform real work but emit NO machine-readable tool_use
    frame: the Antigravity ``agy`` CLI (PTY prose) and the Gemini CLI
    (``--yolo`` plain text) genuinely write files into the worktree, yet their
    transcript is narrative ("I will create index.html…"), so the frame-based
    extractor finds nothing. The git diff is where those writes ARE observable,
    so it must count as evidence — otherwise the capability-honesty gate
    overrides every such mission to failure (live mission 019eefda, 2026-06-22:
    agy wrote an 80 KB index.html, the gate still said "made no tool call" and
    burned all three critic loops). Crediting the diff is consistent with the
    GROUND-TRUTH-RULE, not a weakening of it: a prose-only claim with an EMPTY
    diff still yields False, so the anti-hallucination contract is intact.
    """
    if not diff_text or not diff_text.strip():
        return False
    return any(
        ln.startswith(_DIFF_ACTION_PREFIXES) for ln in diff_text.splitlines()
    )


# --- informational / question request detection ----------------------------
#
# A pure question ("which city would you recommend for Australia?", "explain
# how X works") has NO file deliverable — the spoken answer IS the result. The
# Worker-Critic empty-diff veto would otherwise reject it as "no work done"
# (live mission 019ec638, 2026-06-14: a travel question failed
# critic_loop_exhausted). We classify the REQUEST, never the worker's claim, so
# a do-task that produced nothing is still vetoed (the hallucination guard).

# Action / artefact verbs (EN + DE) — their presence means the task demanded a
# FILE or a SIDE EFFECT, so its deliverable is an artefact, NOT a text answer.
# This is THE guard: a request with one of these verbs and an empty diff is a
# hallucination ("I created the file" with no Write), so it stays vetoed. A
# request WITHOUT any of these verbs has no artefact to fake — its deliverable
# is the text itself (a trip plan, an answer, a recommendation), so a no-diff
# text answer is a valid completion (live missions 019ec66c/019ec674/019ec708:
# "plan / book a trip" failed critic_loop_exhausted because the deliverable is a
# plan, not a file). Keep this list comprehensive; advisory verbs
# (plan/book/recommend/suggest/research/…) deliberately stay OUT.
_ACTION_VERB_RE = re.compile(
    r"\b("
    r"creat|writ|wrote|build|buil|made|make|implement|generat|"
    r"saved|save|refactor|fix|fixe|install|open|run|ran|launch|start|"
    r"deploy|push|commit|delete|remov|send|sent|post|email|download|"
    r"click|draft|"
    # noun-form do-tasks the question-mark heuristic used to miss
    # (code review 2026-06-14: "a PDF export of …?", "a ZIP archive of …?").
    r"export|convert|conversion|render|compil|transform|packag|archiv|produc|extract|"
    r"migrat|updat|upgrad|backup|sync|compress|zip|scrape|plot|"
    r"erstell|schreib|geschrieben|baue|bau|mach|implementier|generier|"
    r"speicher|installier|öffne|oeffne|starte|lösch|loesch|sende|programmier"
    r")\w*",
    re.IGNORECASE,
)

# File / artefact markers — a named file or a real extension means a deliverable.
_ARTEFACT_RE = re.compile(
    r"(file named|datei|\.(md|py|txt|html?|json|csv|js|ts|tsx|jsx|css|"
    r"toml|ya?ml|xml|sh|ps1|pdf|docx?|xlsx?|png|svg))\b",
    re.IGNORECASE,
)

# Informational TRIGGER words (EN + DE): interrogatives AND advisory verbs/nouns
# whose deliverable is text, not a file. A request must carry one of these to be
# treated as informational. This positive requirement is what separates an
# advisory task whose deliverable IS text ("PLAN a trip", "SUGGEST restaurants",
# "RESEARCH laptops") from an impossible real-world transaction the worker cannot
# perform ("BOOK me a trip", "BUY me X") — the latter has no trigger, so it falls
# through to the capability-refusal path (one-shot honest reject), never an
# approve. It also avoids the "book"-the-noun collision ("recommend a book").
_INFO_TRIGGER_RE = re.compile(
    r"\b("
    r"which|what|whats|how|who|whom|whose|why|where|when|"
    r"recommend|recommendation|recommendations|suggest|suggestion|suggestions|"
    r"advise|advice|explain|describe|summarize|summarise|summary|"
    r"compare|comparison|research|plan|plans|outline|brainstorm|"
    r"itinerary|itineraries|overview|guide|idea|ideas|tip|tips|option|options|"
    r"welche|welcher|welches|was|wie|warum|wieso|weshalb|wer|wem|wen|wo|wann|"
    r"erkläre|erklär|erklaere|beschreibe|beschreib|vergleiche|vergleich|"
    r"empfiehl|empfehl|empfehlung|nenne|plane|schlage|recherchiere|"
    r"vorschlag|übersicht|idee|tipp|reiseplan|reiseroute"
    r")\b",
    re.IGNORECASE,
)


# Spawn / routing meta-language. The user describes HOW the assistant should
# run the task, e.g. "starte eine Sub-Edge-Mission …" / "start a worker  # i18n-allow
# that …" / "lass einen Sub-Agenten …" — the mission runtime IS that worker, so
# the phrase is routing meta, NOT part of the deliverable — exactly the
# META-PHRASE-RULE the LLM critic already obeys (critic/prompts.py). It matters
# here because the launch verbs ``start``/``starte``/``launch`` ALSO live in
# ``_ACTION_VERB_RE`` (a legit "open/launch an app" action), so an unstripped
# "Mission starten" masks a genuine research request as a do-task and routes it
# to the adversarial code-critic (live mission 019ecb56, 2026-06-15:
# critic_loop_exhausted on a working AI-news report). Stripping the spawn-meta
# clause can only REMOVE text, never add an info trigger — so a real do-task
# whose deliverable verb sits OUTSIDE the meta clause ("start a worker that
# CREATES index.html") stays a do-task.
_SPAWN_VERB = (
    r"(?:spawn\w*|start\w*|launch\w*|delegate\w*|delegier\w*|dispatch\w*|"
    r"lass\w*|kick[\s-]?off)"
)
# Routing nouns. German inflections (``Sub-Agenten``, ``Missionen``) are covered
# by the ``(?:en|s)?`` suffix — a ``\b`` after bare ``agents?`` would otherwise
# miss the dative/accusative ``Agenten`` and leak the meta-clause through (live
# regression 2026-06-16: a German "Spawne einen Sub-Agenten, der …" was not
# stripped because "Agenten" did not match).
_SPAWN_NOUN = (
    r"(?:sub-?edge-?mission(?:en|s)?|sub-?agent(?:en|s)?|subagent(?:en|s)?|"
    r"agent(?:en|s)?|workers?|mission(?:en|s)?)"
)
# Creation verbs (EN + DE). Unlike the strong spawn verbs above, these are weak:
# "create"/"build"/"make" routinely govern a genuine deliverable ("create a
# file", "build an app"). They count as routing meta ONLY when they directly
# govern a JARVIS routing noun ("create a sub-agent", "mach einen Worker") — so
# the creation-verb alternative below is restricted to ``_ROUTING_NOUN`` (no bare
# "agent"), which keeps real deliverables intact.
_CREATE_VERB = (
    r"(?:creat\w*|mak\w*|build\w*|bau\w*|generat\w*|generier\w*|"
    r"erstell\w*|erzeug\w*|mach\w*)"
)
_ROUTING_NOUN = (
    r"(?:sub-?edge-?mission(?:en|s)?|sub-?agent(?:en|s)?|subagent(?:en|s)?|"
    r"workers?|mission(?:en|s)?)"
)
_SPAWN_META_RE = re.compile(
    rf"\b{_SPAWN_VERB}\b[^.?!\n]{{0,40}}?\b{_SPAWN_NOUN}\b"
    rf"|\b{_SPAWN_NOUN}\b[^.?!\n]{{0,40}}?\b{_SPAWN_VERB}\b"
    # Third alternative: a creation verb governing a routing noun, optionally
    # chained through "(and|und) <spawn-verb>" ("create and spawn a sub-agent").
    # Narrow on purpose — only ``\s`` + fixed tokens between verb and noun, no
    # ``[^.?!]{0,40}`` wildcard, so it cannot reach across a clause and eat a
    # real deliverable.
    rf"|\b{_CREATE_VERB}\s+(?:(?:and|und)\s+{_SPAWN_VERB}\s+)?"
    rf"(?:a|an|the|ein|eine|einen|der|die|das)?\s*{_ROUTING_NOUN}\b",
    re.IGNORECASE,
)
_MAKE_RESEARCH_IDIOM_RE = re.compile(
    r"\bmake\s+(?:me|us)\s+(?:(?:a|an)\s+)?"
    r"(?:(?:deep|detailed|comprehensive|thorough|full)\s+){0,3}"
    r"research(?:\s+(?:report|brief|analysis|overview))?\b"
    r"(?=\s*(?:$|[.?!,;:]|\b(?:about|on|regarding|for|with|into|from|how|what|where|which)\b))",
    re.IGNORECASE,
)


def _strip_spawn_meta(text: str) -> str:
    """Remove spawn/routing meta-clauses so the classifier sees the real task."""
    return _SPAWN_META_RE.sub(" ", text)


def strip_spawn_meta(text: str) -> str:
    """Public, shared spawn/routing meta-clause remover.

    Single source of truth for two consumers that MUST stay in lock-step:
    (1) the critic classifier here (``is_informational_request``), and (2) the
    worker-prompt builder (``jarvis.plugins.tool.spawn_worker._build_mission_prompt``).
    Before this was shared, only the classifier stripped the meta-clause, so the
    worker received "spawn a sub-agent that …" as its OWN task — which it cannot
    do (no spawn tool, AP-5) — and the mission died ``critic_loop_exhausted``
    (live regression 2026-06-16). Both callers route through this function so the
    prompt the worker runs and the prompt the critic classifies can never drift
    apart again (parity test: ``tests/missions/test_spawn_meta_parity.py``).
    """
    return _strip_spawn_meta(text)


def _normalize_informational_idioms(text: str) -> str:
    """Rewrite colloquial research idioms that otherwise hit generic do-verbs."""
    return _MAKE_RESEARCH_IDIOM_RE.sub("research", text)


def _request_body(prompt: str) -> str:
    """Strip the standing quality directive that spawn_worker prepends so the
    classifier sees only the real request.

    ``spawn_worker._build_mission_prompt`` joins
    ``f"{_QUALITY_DIRECTIVE}\\n\\n{body}"``; the directive is recognised by its
    stable phrasing. When no directive is present (tests / other callers) the
    whole prompt is classified.
    """
    head, sep, tail = prompt.partition("\n\n")
    low = head.lower()
    if sep and tail.strip() and (
        "production-quality" in low
        or "never ship one" in low
        or "inhalt folgt" in low
    ):
        return tail
    return prompt


def clean_request_body(prompt: str) -> str:
    """Public: the user's real request with the standing quality directive removed.

    ``spawn_worker._build_mission_prompt`` prepends a fixed quality directive
    (``f"{_QUALITY_DIRECTIVE}\\n\\n{body}"``). Consumers that want to show the
    user's actual request — report titles/filenames, UI previews — must strip it
    so they don't surface "Deliver a complete, polished, production-quality …"
    instead of the real ask. Single source of truth shared with the critic
    classifier (both route through ``_request_body``).
    """
    return _request_body(prompt or "")


def is_informational_request(prompt: str) -> bool:
    """True when the request's deliverable is a spoken/written ANSWER, not a file.

    The rule keys off the REQUEST, never the worker's claim. Two conditions:
    (1) NO action/artefact verb and NO named file — a request that says
    create/write/build/export/send/… or names a file demanded an artefact, so an
    empty diff for it is a hallucination and stays vetoed. (2) a real
    informational TRIGGER word (which/what/how/recommend/plan/suggest/research/…).

    The trigger requirement is deliberate: it covers questions AND doable advisory
    imperatives ("plan a trip from London to Taiwan", "suggest restaurants") whose
    deliverable IS text, while leaving an impossible real-world transaction
    ("book me a trip", "buy me X") as NON-informational — that has no trigger and
    falls through to the capability-refusal path (one-shot honest reject), never
    a wrongful approve. Hallucination guard intact: "create a file report.md" →
    `creat` + `.md` → vetoed; "send an email" → `send` → vetoed.
    """
    body = _normalize_informational_idioms(
        _strip_spawn_meta(_request_body(prompt or ""))
    ).strip()
    if not body:
        return False
    if _ACTION_VERB_RE.search(body) or _ARTEFACT_RE.search(body):
        return False
    return bool(_INFO_TRIGGER_RE.search(body))


def readonly_answer(
    diff_text: str, stream_text: str, *, prompt: str | None = None
) -> str | None:
    """Return the worker's answer iff this was a genuine read-only / external
    result, OR a pure informational request answered in text.

    Speak-back applies to three shapes: (a) a read-only / informational task
    that invoked tools (empty git diff + tool evidence), (b) an out-of-worktree
    deliverable whose diff contains only ``diff --external-target`` blocks (no
    in-worktree ``diff --git`` hunk), and (c) — when ``prompt`` is supplied and
    classifies as a question/informational request — a pure conversational
    answer with NO tool calls (the spoken answer IS the deliverable; live
    mission 019ec638, 2026-06-14).

    The anti-hallucination contract is intact: shape (c) is gated on the
    *request* being informational (``is_informational_request``), never on the
    worker's claim — so an empty diff + no tool calls for a DO-task ("I created
    the file" with no Write) returns None and the empty-diff veto still applies.

    An in-worktree code change (a real ``diff --git`` hunk) returns None — those
    keep the diff/delivered-files summary instead of a spoken answer.
    """
    if _has_inworktree_hunk(diff_text):
        return None  # in-worktree code change -> not informational
    ev = extract_stream_evidence(stream_text)
    if not ev.has_tool_evidence and not (
        prompt and is_informational_request(prompt)
    ):
        # No tool evidence AND not a question -> let the empty-diff veto handle
        # it (anti-hallucination: a bare "done" claim is not a success).
        return None
    answer = ev.final_answer.strip()
    if len(answer) < 3:
        return None
    return answer


# --- informational request answered as a prose document --------------------
#
# A research / advisory request ("recherchiere AI-News", "research laptops",
# "plan a trip") has a TEXT deliverable. When the worker writes that text into a
# prose document (.md/.txt/…) instead of speaking it, the non-empty diff routed
# the mission to the adversarial CODE-critic, which graded a German news essay
# with a code rubric (correctness/security/side_effects) and demanded reachable
# web citations a web-less worker cannot produce -> 3x revise ->
# critic_loop_exhausted (live mission 019ecb56, 2026-06-15). When the WHOLE
# deliverable is substantive prose for an informational request, the document IS
# the answer: grade it as prose, not as code. The anti-hallucination contract is
# intact — this keys off the REQUEST being informational AND a real, non-stub
# document existing on disk (a named-file/code do-task is never informational,
# and a stub document fails the substance gate and falls through to the critic).

# Prose / document extensions whose deliverable is text, not executable code.
_PROSE_EXTENSIONS: frozenset[str] = frozenset(
    {".md", ".markdown", ".txt", ".text", ".rst", ".org", ".adoc", ".rtf"}
)
# A real document needs real prose. Below this the "report" is a stub/skeleton —
# let the critic see it rather than blanket-approve an empty shell.
_MIN_PROSE_CHARS: Final[int] = 300
_STUB_MARKERS: tuple[str, ...] = (
    "inhalt folgt", "content follows", "coming soon", "to be done",
    "to be written", "placeholder", "lorem ipsum", "tbd",
)
_DIFF_GIT_PATH_RE = re.compile(r"^diff --git a/.+ b/(.+?)\s*$", re.MULTILINE)


def _diff_git_paths(diff_text: str) -> list[str]:
    """The ``b/`` target paths of every in-worktree ``diff --git`` block."""
    return _DIFF_GIT_PATH_RE.findall(diff_text or "")


def _is_prose_only_diff(diff_text: str) -> bool:
    """True iff the diff touches ONLY prose/document files (no code)."""
    paths = _diff_git_paths(diff_text)
    if not paths:
        return False
    return all(
        ("." + path.rsplit(".", 1)[1].lower()) in _PROSE_EXTENSIONS
        if "." in path.rsplit("/", 1)[-1]
        else False
        for path in paths
    )


def _added_document_text(diff_text: str) -> str:
    """Concatenated added (``+``) content of the diff, sans diff headers."""
    out: list[str] = []
    for line in (diff_text or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out).strip()


def _looks_like_stub_document(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in _STUB_MARKERS)


def informational_file_answer(diff_text: str, *, prompt: str) -> str | None:
    """Return the document content iff an informational request was answered by a
    substantive prose document — else None.

    Conditions (all required):
    1. The REQUEST is informational (``is_informational_request``) — keys off the
       request shape, never the worker's claim, so do-tasks stay vetoed.
    2. The diff touches ONLY prose/document files (no code) — a code change keeps
       the adversarial code-critic.
    3. The added prose is substantive and not a stub — an empty shell falls
       through to the critic instead of being blanket-approved.
    """
    if not is_informational_request(prompt):
        return None
    if not _is_prose_only_diff(diff_text):
        return None
    content = _added_document_text(diff_text)
    if len(content) < _MIN_PROSE_CHARS or _looks_like_stub_document(content):
        return None
    return content


# Capability-refusal phrases (EN + DE). When the worker invoked NO tools, wrote
# NOTHING, and its final answer says it cannot do the task, retrying is futile —
# the worker already decided it lacks the capability. Surfacing the honest
# refusal once beats burning three critic loops into ``critic_loop_exhausted``
# (live mission 019ec674, 2026-06-14: "book me a trip from Melbourne to Tokyo").
# Anchored to an explicit inability + object. Bare substrings like "i can't",
# "i cannot", "i can not", "no access to", "that's outside" were REMOVED — they
# false-match success-with-caveat answers ("I implemented it; I can't guarantee
# every edge case") and constructions like "I can not only X but also Y",
# turning a recoverable revise into a terminal one-shot reject and discarding
# real work (the honesty-gate-discards-work bug class). Prefer false negatives
# (fall through to the empty-diff veto, the safe direction) over false positives.
_REFUSAL_PHRASES: tuple[str, ...] = (
    # EN — anchored inability/capability phrases
    "i'm not able to", "i am not able to", "i'm unable to", "i am unable to",
    "i'm not able to access", "not able to access",
    "i can't access", "i cannot access",
    "i can't do that", "i cannot do that",
    "i can't help with", "i cannot help with",
    "i do not have access", "i don't have access", "i dont have access",
    "do not have the ability", "don't have the ability", "dont have the ability",
    "outside what i can do", "outside of what i can do",
    "beyond my capabilities", "not something i can do",
    # DE
    "ich kann das nicht", "das kann ich nicht", "ich kann dir nicht",
    "ich habe keinen zugriff", "keinen zugriff",
    "ich bin nicht in der lage", "nicht in der lage",
    "liegt außerhalb", "liegt ausserhalb", "außerhalb dessen", "ausserhalb dessen",
)


def capability_refusal_answer(
    stream_text: str, *, prompt: str | None = None
) -> str | None:
    """Return the worker's refusal text iff it honestly reported it CANNOT do the task.

    Fires only on the unambiguous "impossible task" shape: the worker invoked
    NO tools, produced a substantive final answer, and that answer expresses an
    inability/capability limit. The caller (the critic empty-diff pre-gate) uses
    this to return a one-shot ``reject`` instead of three deterministic
    ``revise`` loops — a re-prompt cannot grant a capability the worker lacks.

    Anti-hallucination contract (BUG-LIVE-02) preserved on two axes:
      * Informational requests (``which/what/how/...``) are NOT refusals — they
        are answered and approved by :func:`readonly_answer` upstream; this
        returns None for them so the two paths never fight.
      * A "done!" success claim is NOT a refusal — it returns None and stays
        subject to the empty-diff veto (a bare success claim with no tools and
        no diff is the classic hallucination this whole gate defends against).
      * Any tool evidence at all returns None: the worker attempted real work,
        so re-prompting may yet succeed — defer to the Critic LLM.
    """
    if prompt and is_informational_request(prompt):
        return None
    ev = extract_stream_evidence(stream_text or "")
    if ev.has_tool_evidence:
        return None
    answer = ev.final_answer.strip()
    if len(answer) < 10:
        return None
    low = answer.lower()
    if any(phrase in low for phrase in _REFUSAL_PHRASES):
        return answer
    return None


_SENTENCE_ENDERS: str = ".!?…"


def summarize_answers(answers: list[str], *, cap: int = 600) -> str:
    """Join per-task answers into a single mission summary, capped.

    When the joined text overflows ``cap`` this is spoken back to the user via
    TTS, so a hard ``[:cap-1]`` slice ended the readback mid-word — the voice
    cut off inside a token and the user heard it as Jarvis "hanging up
    mid-sentence" (live forensic 2026-06-19, session 514cddc0: a 2486-char
    answer became "…eine schlechtere Auswander…"). Truncate on the last
    sentence boundary in the back half of the budget instead; failing that, on
    the last word boundary. The trailing ``…`` always fits inside ``cap``.
    """
    joined = "\n".join(a.strip() for a in answers if a and a.strip())
    if len(joined) <= cap:
        return joined
    # Reserve two chars for the " …" continuation marker so the result never
    # exceeds ``cap``.
    window = joined[: cap - 2]
    sentence_end = max(
        (i for i, ch in enumerate(window) if ch in _SENTENCE_ENDERS or ch == "\n"),
        default=-1,
    )
    if sentence_end >= len(window) // 2:
        head = window[: sentence_end + 1].rstrip()
    else:
        stripped = window.rstrip()
        space = stripped.rfind(" ")
        head = stripped[:space].rstrip() if space > 0 else stripped
    return f"{head} …"


__all__ = [
    "StreamEvidence",
    "clean_request_body",
    "diff_has_action_evidence",
    "extract_stream_evidence",
    "extract_verified_commands",
    "extract_verified_desktop_actions",
    "extract_verified_external_actions",
    "extract_write_targets",
    "informational_file_answer",
    "is_informational_request",
    "readonly_answer",
    "strip_spawn_meta",
    "summarize_answers",
]
