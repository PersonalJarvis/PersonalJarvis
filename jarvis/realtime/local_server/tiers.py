"""The hardware tier ladder for the managed local realtime server (AD-10).

Five tiers spanning ~12 GB to 128 GB+ of usable accelerator memory, with a
HARD floor at 12 GB: below it the managed install is refused with an honest
blocker instead of shipping a degraded experience (maintainer mandate
2026-08-07).

Bake-off honesty: a tier may only pin a model stack that has been MEASURED
(recorded audio + numbers), never datasheet-picked. Today only the bottom
stack is measured (the dev-box configuration: Parakeet TDT CPU STT +
qwen2.5:7b via Ollama + Qwen3-TTS CustomVoice on GPU, speech-end → first
audio ~2.4 s warm). Higher tiers therefore install the SAME measured stack
and say so; their target classes are documented here and flip to pinned
only when the Chunk-6 bake-off records a winner.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Managed installs are refused below this much usable accelerator memory.
FLOOR_GB = 12.0


@dataclass(frozen=True, slots=True)
class Tier:
    key: str
    label: str
    #: Minimum usable accelerator memory (dedicated VRAM, or unified memory
    #: on Apple Silicon) in GiB.
    min_usable_gb: float
    #: The brain model the managed install configures today.
    brain_model: str
    #: The class of model this tier is MEANT to run once the bake-off has
    #: measured a winner. Documentation, never something the installer acts on.
    target_class: str
    #: Whether the installed stack for this tier is bake-off measured.
    measured: bool
    #: Approximate one-time download volume for the managed stack, in GiB
    #: (venv wheels + first-boot model fetches), stated in the preflight.
    download_gb: float
    #: Expected speech-end → first-audio answer latency, warm.
    expected_latency: str


#: Ordered bottom-up; :func:`pick_tier` returns the highest fitting tier.
TIERS: tuple[Tier, ...] = (
    Tier(
        key="t0-12gb",
        label="12 GB — baseline",
        min_usable_gb=12.0,
        brain_model="qwen2.5:7b",
        target_class="measured dev-box stack (the only recorded configuration)",
        measured=True,
        download_gb=14.0,
        expected_latency="~2-3 s",
    ),
    Tier(
        key="t1-16gb",
        label="16 GB",
        min_usable_gb=16.0,
        brain_model="qwen2.5:7b",
        target_class="~30B-class brain (pending bake-off)",
        measured=False,
        download_gb=14.0,
        expected_latency="~2-3 s",
    ),
    Tier(
        key="t2-32gb",
        label="32 GB",
        min_usable_gb=32.0,
        brain_model="qwen2.5:7b",
        target_class="~70B-class brain, quantized (pending bake-off)",
        measured=False,
        download_gb=14.0,
        expected_latency="~2-3 s",
    ),
    Tier(
        key="t3-64gb",
        label="64 GB",
        min_usable_gb=64.0,
        brain_model="qwen2.5:7b",
        target_class="~70B-class brain, full precision / large MoE (pending bake-off)",
        measured=False,
        download_gb=14.0,
        expected_latency="~2-3 s",
    ),
    Tier(
        key="t4-128gb",
        label="128 GB+",
        min_usable_gb=128.0,
        brain_model="qwen2.5:7b",
        target_class="frontier open-weights class, ~120B+ MoE (pending bake-off)",
        measured=False,
        download_gb=14.0,
        expected_latency="~2-3 s",
    ),
)


def pick_tier(usable_gb: float) -> Tier | None:
    """The highest tier this much usable memory supports, or ``None``.

    ``None`` means the machine is under the hard floor — the caller must
    surface the honest blocker, never a degraded install.
    """
    best: Tier | None = None
    for tier in TIERS:
        if usable_gb >= tier.min_usable_gb:
            best = tier
    return best


def describe_stack(tier: Tier) -> str:
    """One honest sentence about what this tier actually installs today."""
    if tier.measured:
        return (
            f"Installs the measured baseline stack (brain {tier.brain_model}, local STT + GPU TTS)."
        )
    return (
        f"Installs the measured baseline stack (brain {tier.brain_model}) for "
        f"now; this tier targets a {tier.target_class} and upgrades once the "
        "bake-off has recorded a winner."
    )
