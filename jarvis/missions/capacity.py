"""Capacity policy for mission workers: wait, never switch to something that bills.

When a mission's worker has no capacity left — the subscription's usage window
is spent, its login expired, or it cannot run at all — the mission is parked in
``MissionState.WAITING_CAPACITY`` with a checkpoint of the work done so far. It
is NOT failed, and it is NOT moved to another subscription or to a per-token
API key: the user decided (2026-10-04) that paid API use happens only after an
explicit approval for that one mission, and that a subscription install never
silently changes vendor.

Who is "pinned":

- a mission whose configured worker is itself a subscription CLI (the ``claude``
  CLI, codex over the ChatGPT login, agy, grok build), or
- any install that has connected a subscription recently — the same sticky
  signal :mod:`jarvis.brain.background_policy` uses for background work.

An install that never connected a subscription keeps its cross-family fallback
chain, so a single-key download still works (AGENTS.md "any single key must
work", AP-22).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

CapacityWaitReason = Literal["provider_quota", "provider_auth", "provider_unavailable"]

#: Written into ``<mission_dir>/`` when a mission is parked. Its presence also
#: keeps the mission directory out of the age-based cleanup sweep.
CHECKPOINT_NAME = "checkpoint.json"

#: Bumped when the checkpoint layout changes incompatibly.
CHECKPOINT_VERSION = 1

#: How often parked missions are re-checked for returned capacity. The check
#: itself is offline (quota cooldowns, login state), so it costs nothing; a
#: subscription window resets on a scale of minutes to hours.
RESUME_INTERVAL_S = 300.0

#: Random extra delay per tick so instances and windows never fire in lockstep
#: (AP-33).
RESUME_JITTER_S = 60.0


class WorkerCapacityUnavailable(RuntimeError):
    """Raised by the worker factory instead of picking a worker that would bill
    another subscription or an API key."""

    def __init__(
        self,
        reason: CapacityWaitReason,
        provider: str,
        detail: str = "",
    ) -> None:
        super().__init__(f"{provider}: {reason}" + (f" ({detail})" if detail else ""))
        self.reason: CapacityWaitReason = reason
        self.provider = provider
        self.detail = detail


def pinned_to_subscription(*, configured_is_subscription: bool) -> bool:
    """Whether this mission must stay on its configured worker.

    Fails closed: if the subscription signal cannot be read, the mission waits
    rather than risk billing a key nobody approved.
    """
    if configured_is_subscription:
        return True
    try:
        from jarvis.brain.background_policy import subscription_mode

        return subscription_mode()
    except Exception:  # noqa: BLE001 - unreadable signal: waiting is the safe answer
        log.warning(
            "capacity: subscription signal unreadable — pinning the mission "
            "to its configured worker",
            exc_info=True,
        )
        return True


def worker_family(worker: object) -> str:
    """The provider family a worker bills — the identity a parked mission is
    pinned to. Every built-in worker declares ``family``; anything else falls
    back to its ``provider``/``cli`` attribute."""
    for attr in ("family", "provider", "cli"):
        value = getattr(worker, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return type(worker).__name__


def write_checkpoint(mission_dir: Path, data: dict[str, Any]) -> Path:
    """Atomically write the checkpoint and return its path."""
    mission_dir.mkdir(parents=True, exist_ok=True)
    path = mission_dir / CHECKPOINT_NAME
    payload = {"version": CHECKPOINT_VERSION, **data}
    fd, tmp = tempfile.mkstemp(dir=mission_dir, prefix=".checkpoint.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_checkpoint(mission_dir: Path) -> dict[str, Any] | None:
    """The stored checkpoint, or None when it is missing or unreadable."""
    path = mission_dir / CHECKPOINT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("capacity: unreadable checkpoint %s", path, exc_info=True)
        return None
    return data if isinstance(data, dict) else None


async def capacity_resume_loop(
    resume_pass: Callable[[], Awaitable[list[str]]],
    *,
    interval_s: float = RESUME_INTERVAL_S,
    jitter_s: float = RESUME_JITTER_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float, float], float] = random.uniform,
) -> None:
    """Run ``resume_pass`` on a jittered timer until cancelled.

    The first pass waits one interval: nothing here belongs on the boot
    critical path (AP-26). A failing pass is logged and the loop carries on —
    one bad tick must not stop every later resume.
    """
    while True:
        await sleep(interval_s + jitter(0.0, jitter_s))
        try:
            resumed = await resume_pass()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - logged; the next tick retries
            log.exception("capacity: resume pass failed")
            continue
        if resumed:
            log.info("capacity: resumed %d parked mission(s): %s", len(resumed), resumed)


__all__ = [
    "CHECKPOINT_NAME",
    "CHECKPOINT_VERSION",
    "RESUME_INTERVAL_S",
    "RESUME_JITTER_S",
    "CapacityWaitReason",
    "WorkerCapacityUnavailable",
    "capacity_resume_loop",
    "pinned_to_subscription",
    "read_checkpoint",
    "worker_family",
    "write_checkpoint",
]
