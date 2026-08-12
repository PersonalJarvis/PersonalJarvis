"""Boot progress, boot statistics and crash forensics for the managed server.

Why this exists (live 2026-08-10 20:16): a deliberate model switch replaced a
healthy idle server, the new generation needed ~65 seconds to load its speech
models, and every call attempt inside that window failed with the static
sentence "try again in about a minute". The card showed the equally static
"Server starting — loading voice models…". Nothing told the user WHICH model
was loading or HOW LONG was actually left, and a silent native crash of the
previous generation (live 2026-08-10 18:41) left zero forensic trace because
the log tail was pure ``/v1/pool`` polling noise.

Three read-only-safe capabilities, all pure functions over explicit inputs so
they are unit-testable without a live server:

- **Stage parsing**: the pinned server logs a stable module prefix per boot
  phase (``speech_to_speech.STT.* Loading``, ``speech_to_speech.TTS.*
  Loading``, …). Matching on the MODULE rather than the model name keeps the
  parse honest across engines and tiers (Parakeet vs Whisper, Qwen3-TTS vs
  others). Lines from earlier generations in the shared append-only log are
  excluded by their timestamps.
- **Boot statistics**: recent completed boot durations (median → honest ETA)
  plus a consecutive readiness-timeout streak, persisted as one small JSON.
  Concurrent writers can race read-modify-write; the cost is one lost sample,
  never corruption (atomic replace).
- **Crash tail**: the last substantive server-log lines with the readiness
  polling noise removed, bounded, for the desktop log when the monitor sees
  the owned process exit.

This module must not import the supervisor (the supervisor imports it); every
path arrives as a parameter.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)


class BootStats(TypedDict):
    """Validated shape of the persisted boot-statistics file."""

    durations_s: list[float]
    failed_streak: int
    ready_token: str
    timeout_token: str

#: How much of the shared append-only log one parse reads. Big enough to span
#: a whole boot transcript (~30 KB observed) plus call noise, small enough to
#: stay cheap on every status poll.
TAIL_READ_BYTES = 128 * 1024

#: Bounds for the crash tail that lands in the desktop log.
CRASH_TAIL_MAX_LINES = 25
CRASH_TAIL_MAX_LINE_CHARS = 300

#: How many completed boot durations feed the median ETA.
MAX_RECORDED_BOOTS = 5

#: The readiness poll requests that make a raw tail useless as forensics.
_POOL_POLL_NOISE = re.compile(r'"GET /v1/pool HTTP/1\.1" 200')

_LINE_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

#: Ordered boot phases. Module-prefixed patterns, never model names: the same
#: stage line must match whichever STT/TTS engine the user's tier selected.
#: ``label`` is a backend English sentence fragment (rendered verbatim by the
#: UI per the managed-card doctrine: substance from the server, chrome
#: localized).
_STAGE_MARKERS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"speech_to_speech\.VAD\.\S+ - INFO - Load"),
        "speech-detection",
        "preparing speech detection",
    ),
    (
        re.compile(r"speech_to_speech\.STT\.\S+ - INFO - Loading"),
        "listening-model",
        "loading the listening model",
    ),
    (
        re.compile(r"speech_to_speech\.STT\.\S+ - INFO - Warming up"),
        "listening-model",
        "warming up the listening model",
    ),
    (
        re.compile(r"speech_to_speech\.LLM\.\S+ - INFO - Warming up"),
        "brain",
        "warming up the language model",
    ),
    (
        re.compile(r"speech_to_speech\.TTS\.\S+ - INFO - Loading"),
        "voice-model",
        "loading the speaking voice",
    ),
    (
        re.compile(r"speech_to_speech\.TTS\.\S+ - INFO - Warming up"),
        "voice-warmup",
        "warming up the speaking voice",
    ),
    (
        re.compile(r"speech_to_speech\.api\.\S+ - INFO - OpenAI Realtime API starting"),
        "opening",
        "opening the call endpoint",
    ),
)


def read_log_tail(path: Path, *, max_bytes: int = TAIL_READ_BYTES) -> list[str]:
    """The last ``max_bytes`` of one log file as text lines, never raising.

    The child writes UTF-8 (``PYTHONIOENCODING`` is set at spawn); a seek into
    the middle of a multibyte character is healed by ``errors="replace"``.
    """
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max(0, max_bytes)))
            raw = handle.read(max(0, max_bytes))
    except OSError:
        # Log file gone or unreadable mid-boot — an empty tail, not a crash.
        return []
    return raw.decode("utf-8", errors="replace").splitlines()


def _line_epoch(line: str) -> float | None:
    """Local-time epoch of a timestamped server-log line, else ``None``."""
    match = _LINE_TIMESTAMP.match(line)
    if match is None:
        return None
    try:
        return time.mktime(time.strptime(match.group(1), "%Y-%m-%d %H:%M:%S"))
    except (OverflowError, ValueError):
        # Unparseable timestamp — treat the line as untimestamped, not fatal.
        return None


def parse_boot_stage(lines: list[str], *, spawned_at: float) -> tuple[str, str] | None:
    """``(stage_key, stage_label)`` of the newest boot marker after the spawn.

    The server log is append-only across generations, so every marker is
    gated on its line timestamp: only lines stamped at or after ``spawned_at``
    (with a small clock-skew allowance) belong to the current boot.
    Untimestamped lines (uvicorn, bare prints) inherit the verdict of the last
    timestamped line above them.
    """
    threshold = spawned_at - 5.0
    in_current_boot = False
    best: tuple[int, str, str] | None = None
    for line in lines:
        stamp = _line_epoch(line)
        if stamp is not None:
            in_current_boot = stamp >= threshold
        if not in_current_boot:
            continue
        for ordinal, (pattern, key, label) in enumerate(_STAGE_MARKERS):
            if pattern.search(line) and (best is None or ordinal >= best[0]):
                best = (ordinal, key, label)
    if best is None:
        return None
    return best[1], best[2]


def crash_tail(lines: list[str]) -> list[str]:
    """The substantive end of the server log, bounded for the desktop log.

    Readiness polling produces hundreds of identical ``GET /v1/pool`` lines
    that would drown the one traceback worth keeping (a real crashed
    generation's tail was 100% polling noise, live 2026-08-10 18:41).
    """
    kept = [
        line[:CRASH_TAIL_MAX_LINE_CHARS]
        for line in lines
        if line.strip() and _POOL_POLL_NOISE.search(line) is None
    ]
    return kept[-CRASH_TAIL_MAX_LINES:]


# ── Boot statistics ──────────────────────────────────────────────────────


def _atomic_write_json(path: Path, payload: dict[str, object]) -> bool:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return True
    except OSError:
        log.debug("boot stats: atomic write failed", exc_info=True)
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            log.debug("boot stats: temporary cleanup failed", exc_info=True)


def load_stats(path: Path) -> BootStats:
    """The persisted boot statistics, empty-but-valid when absent/corrupt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Absent or corrupt file — falls through to the empty-but-valid default below.
        data = None
    if not isinstance(data, dict):
        data = {}
    durations = data.get("durations_s")
    cleaned: list[float] = []
    if isinstance(durations, list):
        for value in durations:
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                cleaned.append(float(value))
    streak = data.get("failed_streak")
    if isinstance(streak, bool) or not isinstance(streak, int) or streak < 0:
        streak = 0
    ready_token = data.get("ready_token")
    timeout_token = data.get("timeout_token")
    return {
        "durations_s": cleaned[-MAX_RECORDED_BOOTS:],
        "failed_streak": streak,
        "ready_token": ready_token if isinstance(ready_token, str) else "",
        "timeout_token": timeout_token if isinstance(timeout_token, str) else "",
    }


def expected_boot_s(stats: BootStats) -> float | None:
    """Median of the recorded boot durations; ``None`` without history."""
    durations = stats["durations_s"]
    if not durations:
        return None
    ordered = sorted(durations)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def record_ready(path: Path, *, token: str, duration_s: float) -> bool:
    """Record one completed boot exactly once per spawn generation.

    Also clears the consecutive-failure streak: a server that just proved it
    can boot is not crash-looping. Returns ``False`` when this generation was
    already recorded (idempotent — status polls, the monitor and the warm
    task all race to be the first observer).
    """
    if not token or duration_s <= 0:
        return False
    stats = load_stats(path)
    if stats["ready_token"] == token:
        return False
    durations = list(stats["durations_s"])
    durations.append(round(float(duration_s), 1))
    return _atomic_write_json(
        path,
        {
            "durations_s": durations[-MAX_RECORDED_BOOTS:],
            "failed_streak": 0,
            "ready_token": token,
            "timeout_token": stats["timeout_token"],
            "updated_at": time.time(),
        },
    )


def record_timeout(path: Path, *, token: str) -> bool:
    """Count one readiness timeout, once per spawn generation."""
    if not token:
        return False
    stats = load_stats(path)
    if stats["timeout_token"] == token:
        return False
    return _atomic_write_json(
        path,
        {
            "durations_s": stats["durations_s"],
            "failed_streak": stats["failed_streak"] + 1,
            "ready_token": stats["ready_token"],
            "timeout_token": token,
            "updated_at": time.time(),
        },
    )
