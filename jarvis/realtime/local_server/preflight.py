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
import shutil
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

    Delegates to the shared probe in :mod:`jarvis.hardware.detection`, which is
    where this logic now lives: the local-model recommender asks the very same
    question ("how much can this machine actually run?"), and two copies would
    eventually give one box two different verdicts. Behaviour is unchanged —
    largest single NVIDIA device, Apple Silicon unified memory, otherwise 0,
    and 0 is below the floor, which is the honest outcome for a GPU-less host.
    """
    try:
        from jarvis.hardware.detection import usable_accelerator_gb

        return usable_accelerator_gb()
    except Exception:  # pragma: no cover — probe quirks must not crash preflight
        log.debug("preflight: accelerator probe failed", exc_info=True)
        return 0.0, "none"


def _disk_free_gb(root: Path) -> float:
    probe = root
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / (1024**3)
    except OSError:
        # Unreadable filesystem — 0 free is the honest, fail-safe answer.
        return 0.0


def _preferred_brain_model() -> str:
    """The user's configured Ollama brain model, read cheaply and fail-open.

    A plain TOML read (no full config model) because this runs inside a
    read-only probe; any problem answers "" and the resolver simply applies
    its curated preference order.
    """
    try:
        import tomllib

        from jarvis.core.config import resolve_config_path

        path = resolve_config_path()
        if not path.exists():
            return ""
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
        providers = (data.get("brain") or {}).get("providers") or {}
        return str((providers.get("ollama") or {}).get("model") or "").strip()
    except Exception:  # noqa: BLE001 — a probe must never raise
        log.debug("preflight: preferred-brain-model read failed", exc_info=True)
        return ""


def run_preflight(
    install_root: Path, *, preferred_model: str = ""
) -> PreflightReport:
    """The full go/no-go report for a managed install under ``install_root``."""
    usable_gb, source = _usable_accelerator_gb()
    disk_free = _disk_free_gb(install_root)
    tier = pick_tier(usable_gb)
    if tier is None:
        if source == "none":
            # "0 GB accelerator memory" is factually WRONG on a machine with
            # a 24 GB Radeon or an Arc card — the honest statement is that
            # the managed stack does not support that hardware (yet), and
            # the same sentence covers a headless box without nvidia-smi.
            blocker = (
                "No supported accelerator was found: the managed stack "
                "currently needs an NVIDIA GPU (CUDA) or Apple Silicon. "
                "AMD and Intel GPUs are not supported by it yet, and a "
                "missing nvidia-smi hides an NVIDIA card."
            )
        else:
            blocker = (
                f"This machine has {usable_gb:.0f} GB of usable accelerator "
                f"memory — under the {FLOOR_GB:.0f} GB minimum for a good "
                "local realtime experience, so the managed install is not "
                "offered here."
            )
        return PreflightReport(
            ok=False,
            blocker=blocker,
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
    brain = resolve_brain(
        preferred_model=preferred_model or _preferred_brain_model(),
        usable_gb=usable_gb,
    )
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
        # Hardware and disk pass but the BRAIN is blocked: the one blocked
        # state the install can fix ITSELF (install/start Ollama, pull the
        # model). The card keeps the Install button alive on this flag with
        # an honest note about the extra downloads.
        "brain_fixable": (
            not report.ok
            and tier is not None
            and report.brain is not None
            and report.brain.kind == "blocked"
        ),
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
