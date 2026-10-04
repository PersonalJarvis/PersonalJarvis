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

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger(__name__)

CapacityWaitReason = Literal["provider_quota", "provider_auth", "provider_unavailable"]

#: Written into ``<mission_dir>/`` when a mission is parked. Its presence also
#: keeps the mission directory out of the age-based cleanup sweep.
CHECKPOINT_NAME = "checkpoint.json"

#: Bumped when the checkpoint layout changes incompatibly.
CHECKPOINT_VERSION = 1


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


__all__ = [
    "CHECKPOINT_NAME",
    "CHECKPOINT_VERSION",
    "CapacityWaitReason",
    "WorkerCapacityUnavailable",
    "pinned_to_subscription",
    "write_checkpoint",
]
