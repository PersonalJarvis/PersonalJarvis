"""Central CPU-first compute-device selection policy.

Single source of truth for the question "which compute device may this component
use?". Personal Jarvis is cloud-first: the baseline user is on a headless
``python:3.11-slim`` VPS with no GPU, never the maintainer's CUDA workstation
(CLAUDE.md §3, ``docs/adr/0024-cpu-first-device-selection.md``). So the DEFAULT
is always CPU, and a GPU is used only when a component BOTH (a) is EXPLICITLY
asked for one via config, AND (b) has a VERIFIED capability to run it.

This module owns rule (a) — the *policy*. It deliberately does NOT own rule (b)
— the *capability verdict* — which is injected as the ``cuda_usable`` argument,
because the correct capability probe differs per consumer: the always-on wake
path MUST gate on the strict out-of-process inference probe, never on bare CUDA
presence (AP-25), while a latency-tolerant utterance path may pass a cheaper
verdict or ``None`` and let the backend self-heal. Keeping the capability out of
this module also keeps it a pure, dependency-light, instantly testable decision
function that never imports ``torch`` / ``ctranslate2`` and therefore adds no
boot cost and never sits on the AP-26 critical path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

CPU = "cpu"

# Requested-device spellings that mean "use the GPU". Anything that is neither a
# GPU token nor a CPU/auto token is unrecognized and fails closed to CPU.
_GPU_TOKENS = frozenset({"cuda", "gpu"})
# Empty and "auto" both mean "let the system decide" — and the system decides
# CPU-first unless a GPU is proven usable.
_AUTO_TOKENS = frozenset({"", "auto"})


@dataclass(frozen=True, slots=True)
class DeviceResolution:
    """Outcome of a device-selection decision.

    ``device``   — the string to hand to the backend (``"cpu"`` or the resolved
                   GPU spec, e.g. ``"cuda"`` / ``"cuda:0"``).
    ``requested`` — the original (trimmed) request, for auditing.
    ``fell_back`` — ``True`` iff a GPU was requested but the policy routed to CPU.
    ``reason``    — a short English explanation, fit for a log line or a surfaced
                    status message.
    """

    device: str
    requested: str
    fell_back: bool
    reason: str


def _looks_like_gpu(token: str) -> bool:
    """True for ``cuda`` / ``gpu`` and their indexed forms (``cuda:0``, ``gpu:1``).

    A bare numeric device (e.g. ``"0"``) is intentionally NOT treated as a GPU
    request here — an ambiguous device string is safer resolved to CPU.
    """
    return token.split(":", 1)[0] in _GPU_TOKENS


def resolve_device(
    requested: str | None,
    *,
    cuda_usable: bool | None = None,
    purpose: str = "",
) -> DeviceResolution:
    """Resolve a requested device string to a safe, CPU-first device.

    Policy (cloud-first, ADR-0024):

    * ``"cpu"`` -> ``"cpu"`` (never escalates).
    * ``"auto"`` / ``""`` / ``None`` -> ``"cpu"`` UNLESS ``cuda_usable is True``
      (auto lets the system pick, and the system picks CPU unless a GPU is
      proven usable).
    * an EXPLICIT GPU request (``"cuda"``, ``"cuda:0"``, ``"gpu"`` ...):
        - ``cuda_usable is True``  -> honor it (GPU opt-in, capability verified).
        - ``cuda_usable is False`` -> validated fallback to CPU + WARNING (we
          KNOW the GPU is unusable on this host).
        - ``cuda_usable is None``  -> honor the explicit request (the config IS
          the opt-in) but INFO-log that capability was not pre-verified; the
          backend's own construction-time self-heal is the safety net.
    * anything unrecognized -> ``"cpu"`` (fail-closed to the safe default).

    ``cuda_usable`` is the injected CAPABILITY verdict (AP-21/AP-25): the caller
    supplies whatever probe is correct for its context. ``purpose`` is a short
    label (``"stt-utterance"``, ``"wake"``) used only in log lines.
    """
    raw = (requested or "").strip()
    token = raw.lower()
    tag = f" [{purpose}]" if purpose else ""

    if token == CPU:
        return DeviceResolution(CPU, raw, False, "explicit CPU")

    if token in _AUTO_TOKENS:
        if cuda_usable is True:
            reason = "auto -> verified GPU available, using CUDA"
            logger.info("Device%s: %s", tag, reason)
            return DeviceResolution("cuda", raw, False, reason)
        reason = "auto -> CPU (cloud-first default; no verified GPU)"
        logger.debug("Device%s: %s", tag, reason)
        return DeviceResolution(CPU, raw, False, reason)

    if _looks_like_gpu(token):
        if cuda_usable is True:
            reason = f"explicit GPU request honored (capability verified) -> {raw}"
            logger.info("Device%s: %s", tag, reason)
            return DeviceResolution(raw, raw, False, reason)
        if cuda_usable is False:
            reason = f"GPU requested ({raw!r}) but not usable on this host -> CPU fallback"
            logger.warning("Device%s: %s", tag, reason)
            return DeviceResolution(CPU, raw, True, reason)
        reason = (
            f"explicit GPU request ({raw!r}); capability not pre-verified, "
            "backend self-heals to CPU on failure"
        )
        logger.info("Device%s: %s", tag, reason)
        return DeviceResolution(raw, raw, False, reason)

    reason = f"unrecognized device {raw!r} -> CPU (fail-closed to safe default)"
    logger.warning("Device%s: %s", tag, reason)
    return DeviceResolution(CPU, raw, False, reason)
