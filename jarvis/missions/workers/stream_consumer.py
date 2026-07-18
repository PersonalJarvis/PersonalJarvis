"""NDJSON stream consumer for OpenClaw and Codex CLIs.

Both CLIs emit NDJSON over stdout (one Pydantic-round-trip-capable JSON
line per event). We parse line-buffered, optionally tee to disk, and yield
typed Pydantic events as an AsyncIterator.

Pydantic v2 discriminator strategy:

Claude `--output-format stream-json` uses `type` as the top-level
discriminator (`system`, `assistant`, `user`, `stream_event`, `result`).
Complication: `system` has a second sub-discriminator `subtype`
(`init`, `api_retry`, ...). Solution: `parse_claude_stream_json` reads
the (type, subtype) pair manually and dispatches to the correct concrete
class. Other top-level types are passed through directly.

Codex `--json` is simpler: a flat `type` discriminator
(`thread.started`, `turn.started`, `turn.completed`, `turn.failed`,
`item.*`, `error`). The `item.*` family is collapsed under a single
`CodexItem` class (the CLI format itself has no final specification yet).

Frozen + extra='ignore': CLI output may add fields without us crashing —
we only remember what we know.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import (
    Any,
    Literal,
    TypeVar,
    Union,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Claude Stream-Json Event-Models
# ---------------------------------------------------------------------------


class _ClaudeBase(BaseModel):
    """Frozen + extra='ignore' — CLI may add new fields without breaking us."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class ClaudeSystemInit(_ClaudeBase):
    """`system` event with `subtype=init`. First event of every run."""

    type: Literal["system"] = "system"
    subtype: Literal["init"] = "init"
    session_id: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    cwd: str | None = None
    external_capabilities: dict[str, Any] = Field(default_factory=dict)


class ClaudeApiRetry(_ClaudeBase):
    """`system` event with `subtype=api_retry`. Legitimate silence!

    The supervisor extends its idle timer by `retry_delay_ms`; otherwise
    the normal idle heuristic would incorrectly classify the worker as
    'stuck' while Anthropic plays out backoffs for 429 rate limits.
    """

    type: Literal["system"] = "system"
    subtype: Literal["api_retry"] = "api_retry"
    retry_delay_ms: int | None = None
    attempt: int | None = None


class ClaudeAssistantMessage(_ClaudeBase):
    """`assistant` event — worker produces a reply (text/tool_use blocks)."""

    type: Literal["assistant"] = "assistant"
    message: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ClaudeUserMessage(_ClaudeBase):
    """`user` event — typically tool_result blocks being returned."""

    type: Literal["user"] = "user"
    message: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ClaudeStreamDelta(_ClaudeBase):
    """`stream_event` — token delta within an assistant message.

    For the supervisor this is primarily a heartbeat ('worker is still
    typing'), not a semantic event. We collapse all stream_event sub-types
    (`message_start`, `content_block_delta`, ...) under one model.
    """

    type: Literal["stream_event"] = "stream_event"
    event: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None


class ClaudeResult(_ClaudeBase):
    """`result` — terminal event of every run.

    `is_error=True` combined with `subtype` from
    {`error_max_turns`, `error_during_execution`, ...} marks failure.
    Success = `subtype="success"`, `is_error=False`. `cost_usd` and
    `num_turns` are the primary cost trackers for Risk-#1 mitigation.
    """

    type: Literal["result"] = "result"
    subtype: str | None = None
    is_error: bool = False
    cost_usd: float | None = None
    num_turns: int | None = None
    session_id: str | None = None
    duration_ms: int | None = None
    result: str | None = None
    # True when this terminal result was synthesized after a wall-clock or
    # first-output TIMEOUT (not a crash/auth error). A structured signal so the
    # orchestrator can recognise a timeout WITHOUT string-matching the result
    # text — the codex/gemini timeout result strings used to omit "timeout",
    # so a real deliverable was discarded as task_error (mission 019eacb8).
    # Defaulted False → backward-compatible with old stream-json result lines.
    timed_out: bool = False


# Type alias for all Claude stream event variants (NO Pydantic discriminator
# because `system/init` and `system/api_retry` both have `type='system'` —
# resolution is done by `parse_claude_stream_json` via (type, subtype).
ClaudeStreamEvent = Union[
    ClaudeSystemInit,
    ClaudeApiRetry,
    ClaudeAssistantMessage,
    ClaudeUserMessage,
    ClaudeStreamDelta,
    ClaudeResult,
]


# ---------------------------------------------------------------------------
# Codex --json Event-Models
# ---------------------------------------------------------------------------


class _CodexBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class CodexThreadStarted(_CodexBase):
    type: Literal["thread.started"] = "thread.started"
    thread_id: str | None = None


class CodexTurnStarted(_CodexBase):
    type: Literal["turn.started"] = "turn.started"
    turn_id: str | None = None


class CodexTurnCompleted(_CodexBase):
    type: Literal["turn.completed"] = "turn.completed"
    turn_id: str | None = None
    cost_usd: float | None = None
    tokens_used: int | None = None


class CodexTurnFailed(_CodexBase):
    type: Literal["turn.failed"] = "turn.failed"
    turn_id: str | None = None
    error: str | None = None


class CodexItem(_CodexBase):
    """Generic `item.*` event (item.created, item.delta, ...).

    The Codex CLI schema for item sub-types is not strictly documented;
    we treat all uniformly and pack the original data into `payload`.
    """

    type: Literal[
        "item.created",
        "item.completed",
        "item.delta",
        "item.failed",
        "item",
    ] = "item"
    payload: dict[str, Any] = Field(default_factory=dict)


class CodexError(_CodexBase):
    type: Literal["error"] = "error"
    message: str | None = None
    code: str | None = None


# Codex has a clean top-level discriminator — Tagged-Union works directly.
# But we do NOT use it for parser resolution (we match manually);
# the type alias serves as an annotation aid for consumers.
CodexStreamEvent = Union[
    CodexThreadStarted,
    CodexTurnStarted,
    CodexTurnCompleted,
    CodexTurnFailed,
    CodexItem,
    CodexError,
]


# ---------------------------------------------------------------------------
# Parser-Funktionen
# ---------------------------------------------------------------------------


def parse_claude_stream_json(line: str) -> Any | None:
    """Parse one NDJSON line from `openclaw agent --output-format stream-json`.

    Returns:
        One of ClaudeSystemInit / ClaudeApiRetry / ClaudeAssistantMessage /
        ClaudeUserMessage / ClaudeStreamDelta / ClaudeResult, or None for
        an invalid line (empty, non-JSON, unknown type).

    Strategy: read top-level `type`; for `system` additionally consult
    `subtype`; then validate against the correct concrete class. This
    avoids the nested-discriminator edge case in Pydantic v2.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        logger.debug("parse_claude_stream_json: non-JSON line discarded")
        return None
    if not isinstance(data, dict):
        return None

    type_ = data.get("type")
    try:
        if type_ == "system":
            subtype = data.get("subtype")
            if subtype == "init":
                return ClaudeSystemInit.model_validate(data)
            if subtype == "api_retry":
                return ClaudeApiRetry.model_validate(data)
            return None  # unknown system subtype
        if type_ == "assistant":
            return ClaudeAssistantMessage.model_validate(data)
        if type_ == "user":
            return ClaudeUserMessage.model_validate(data)
        if type_ == "stream_event":
            return ClaudeStreamDelta.model_validate(data)
        if type_ == "result":
            return ClaudeResult.model_validate(data)
    except ValidationError as exc:
        logger.debug("parse_claude_stream_json: validation fail: %s", exc)
        return None
    return None


def parse_codex_stream_json(line: str) -> Any | None:
    """Parse one NDJSON line from `codex exec --json`.

    Returns:
        One of CodexThreadStarted/CodexTurnStarted/CodexTurnCompleted/
        CodexTurnFailed/CodexItem/CodexError, or None for an invalid line.

    The `item.*` family is collapsed under `CodexItem`. An unknown type
    is passed through as CodexItem with `type='item'` and the original
    data in `payload` — this is defensive against CLI schema drift.
    """
    stripped = line.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        logger.debug("parse_codex_stream_json: non-JSON line discarded")
        return None
    if not isinstance(data, dict):
        return None

    type_ = data.get("type")
    try:
        if type_ == "thread.started":
            return CodexThreadStarted.model_validate(data)
        if type_ == "turn.started":
            return CodexTurnStarted.model_validate(data)
        if type_ == "turn.completed":
            return CodexTurnCompleted.model_validate(data)
        if type_ == "turn.failed":
            return CodexTurnFailed.model_validate(data)
        if type_ == "error":
            return CodexError.model_validate(data)
        if isinstance(type_, str) and type_.startswith("item"):
            # Collapse all item.* sub-variants under CodexItem.
            return CodexItem.model_validate(
                {
                    "type": type_
                    if type_ in CodexItem.model_fields["type"].annotation.__args__
                    else "item",  # type: ignore[union-attr]
                    "payload": {k: v for k, v in data.items() if k != "type"},
                }
            )
    except ValidationError as exc:
        logger.debug("parse_codex_stream_json: validation fail: %s", exc)
        return None
    return None


# ---------------------------------------------------------------------------
# Async NDJSON-Reader (line-buffered + tee-faehig)
# ---------------------------------------------------------------------------


T = TypeVar("T")


async def read_ndjson_stream(
    stream: asyncio.StreamReader,
    *,
    parser: Callable[[str], T | None],
    tee_path: Path | None = None,
) -> AsyncIterator[T]:
    """Async generator over NDJSON events from a StreamReader.

    - Line-buffered: `readline()` returns one line (including the newline) or
      an empty bytes object at EOF.
    - Tee: writes every raw line (with newline) to `tee_path` (binary-append),
      so `stream.jsonl` serves as a forensics/replay source.
    - Robustness: invalid lines are resolved to None by the parser and
      simply skipped — the stream only stops at EOF.

    Args:
        stream: stdout StreamReader of the subprocess.
        parser: one of the `parse_*_stream_json` functions.
        tee_path: optional; if set, raw bytes are written here.
            The parent directory is created if needed.

    Yields:
        T instances (ClaudeStreamEvent or CodexStreamEvent), never None.
    """
    tee_handle = None
    if tee_path is not None:
        tee_path.parent.mkdir(parents=True, exist_ok=True)
        tee_handle = tee_path.open("ab")

    try:
        while True:
            try:
                raw = await stream.readline()
            except asyncio.LimitOverrunError:
                # An extremely long line blew past the default buffer (64 KB).
                # We can't safely keep reading it — warn and abort.
                logger.warning("read_ndjson_stream: line buffer exceeded; stopping")
                break
            if not raw:
                # EOF — the subprocess closed stdout.
                break
            if tee_handle is not None:
                tee_handle.write(raw)
                tee_handle.flush()
            try:
                line = raw.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 — defensiv
                continue
            event = parser(line)
            if event is None:
                continue
            yield event
    finally:
        if tee_handle is not None:
            tee_handle.close()
