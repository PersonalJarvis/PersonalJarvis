"""Honest hardware preflight for the managed local realtime install (AD-5).

Runs BEFORE the first byte downloads and reports, for THIS machine: usable
accelerator memory (dedicated VRAM via the existing hardware detection, or
unified memory on Apple Silicon), free disk against the tier's stated
download volume, the picked tier, and the resolved brain endpoint. Below
the 12 GB floor (AD-10) the report carries the honest blocker and a pointer
to the cloud realtime cards — never a degraded install.

Read-only and side-effect free: probing must not download, create, or start
anything (the same doctrine as ``jarvis/speech/local_models.py``).
"""

from __future__ import annotations

import logging
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.realtime.local_server.brain_link import BrainResolution, resolve_brain
from jarvis.realtime.local_server.tiers import FLOOR_GB, Tier, describe_stack, pick_tier

log = logging.getLogger(__name__)

#: Headroom demanded beyond the tier's stated download volume, in GiB, so an
#: install can never land a machine on a full disk.
_DISK_HEADROOM_GB = 5.0


@dataclass(frozen=True, slots=True)
class PreflightReport:
    ok: bool
    #: Set when ``ok`` is False: one honest sentence on why.
    blocker: str = ""
    #: Concrete fixing actions when blocked, best first.
    actions: tuple[str, ...] = field(default_factory=tuple)
    usable_gb: float = 0.0
    #: Where the memory figure came from: "nvidia-smi" | "apple-unified" | "none".
    memory_source: str = "none"
    disk_free_gb: float = 0.0
    tier: Tier | None = None
    #: What this install would actually set up, in one sentence.
    stack_sentence: str = ""
    brain: BrainResolution | None = None


def _usable_accelerator_gb() -> tuple[float, str]:
    """Usable accelerator memory in GiB and the source of that figure.

    Dedicated NVIDIA VRAM counts as-is. On Apple Silicon the GPU shares the
    unified memory, so total RAM is the honest figure (the probe measures
    what is THERE; whether the OS grants it is the smoke boot's job).
    Anything else reports 0 — and 0 is below the floor, which is exactly
    the honest outcome for a GPU-less host.
    """
    try:
        from jarvis.hardware.detection import _detect_nvidia_gpus  # read-only probe

        gpus = _detect_nvidia_gpus()
    except Exception:  # pragma: no cover — nvidia-smi quirks must not crash preflight
        log.debug("preflight: NVIDIA probe failed", exc_info=True)
        gpus = []
    # The LARGEST single device, never the fleet sum: the whole stack runs
    # on one GPU (--num_pipelines 1, one TTS device), so two 8 GB cards are
    # an 8 GB machine for this feature — summing them would defeat the
    # 12 GB floor and OOM at first call (review 2026-08-07).
    vram_mb = max((g.vram_mb for g in gpus), default=0)
    if vram_mb > 0:
        return vram_mb / 1024.0, "nvidia-smi"
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            from jarvis.hardware.detection import _detect_ram

            total_mb, _available = _detect_ram()
        except Exception:  # pragma: no cover
            log.debug("preflight: RAM probe failed", exc_info=True)
            total_mb = 0
        if total_mb > 0:
            return total_mb / 1024.0, "apple-unified"
    return 0.0, "none"


def _disk_free_gb(root: Path) -> float:
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / (1024**3)
    except OSError:
        return 0.0


def run_preflight(install_root: Path) -> PreflightReport:
    """The full go/no-go report for a managed install under ``install_root``."""
    usable_gb, source = _usable_accelerator_gb()
    disk_free = _disk_free_gb(install_root)
    tier = pick_tier(usable_gb)
    if tier is None:
        return PreflightReport(
            ok=False,
            blocker=(
                f"This machine has {usable_gb:.0f} GB of usable accelerator "
                f"memory — under the {FLOOR_GB:.0f} GB minimum for a good "
                "local realtime experience, so the managed install is not "
                "offered here."
            ),
            actions=(
                "Use a cloud realtime provider on this machine (same card list).",
                "Or point this card at a self-hosted server on a stronger machine.",
            ),
            usable_gb=usable_gb,
            memory_source=source,
            disk_free_gb=disk_free,
        )
    needed = tier.download_gb + _DISK_HEADROOM_GB
    if disk_free < needed:
        return PreflightReport(
            ok=False,
            blocker=(
                f"Not enough free disk: the {tier.label} stack needs about "
                f"{needed:.0f} GB free, this disk has {disk_free:.0f} GB."
            ),
            actions=(f"Free up at least {needed - disk_free:.0f} GB and retry.",),
            usable_gb=usable_gb,
            memory_source=source,
            disk_free_gb=disk_free,
            tier=tier,
        )
    brain = resolve_brain()
    if not brain.ok:
        return PreflightReport(
            ok=False,
            blocker=brain.note,
            actions=brain.actions,
            usable_gb=usable_gb,
            memory_source=source,
            disk_free_gb=disk_free,
            tier=tier,
            brain=brain,
        )
    return PreflightReport(
        ok=True,
        usable_gb=usable_gb,
        memory_source=source,
        disk_free_gb=disk_free,
        tier=tier,
        stack_sentence=describe_stack(tier),
        brain=brain,
    )


def report_payload(report: PreflightReport) -> dict[str, object]:
    """JSON-shaped view of a report for the REST route / CLI."""
    tier = report.tier
    return {
        "ok": report.ok,
        "blocker": report.blocker,
        "actions": list(report.actions),
        "usable_gb": round(report.usable_gb, 1),
        "memory_source": report.memory_source,
        "disk_free_gb": round(report.disk_free_gb, 1),
        "tier": None
        if tier is None
        else {
            "key": tier.key,
            "label": tier.label,
            "measured": tier.measured,
            "target_class": tier.target_class,
            "download_gb": tier.download_gb,
            "expected_latency": tier.expected_latency,
        },
        "stack_sentence": report.stack_sentence,
        "brain": None
        if report.brain is None
        else {
            "kind": report.brain.kind,
            "model": report.brain.model,
            "note": report.brain.note,
        },
    }
