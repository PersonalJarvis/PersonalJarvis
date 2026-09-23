"""Select by model and adapter capabilities, without claiming live readiness."""

from __future__ import annotations

from pydantic import Field

from .models import Capability, Device, FrozenModel, LocalModelManifest, OperatingSystem


class RuntimeProbe(FrozenModel):
    """Trusted host-adapter facts, not values read from a custom model manifest.

    Memory is the available budget AFTER host/application headroom. CUDA working
    memory is dedicated GPU memory; Metal and CPU share host memory. Unknown
    values remain None and cannot make a model eligible for an automatic start.
    """

    adapter: str
    os: OperatingSystem
    device: Device
    available: bool
    families: frozenset[str]
    capabilities: frozenset[Capability]
    working_memory_bytes: int | None = Field(default=None, ge=0, strict=True)
    host_memory_bytes: int | None = Field(default=None, ge=0, strict=True)
    reason: str = ""


class VoiceRequirements(FrozenModel):
    language: str
    capabilities: frozenset[Capability] = frozenset(
        {
            "audio_input",
            "audio_output",
            "streaming_output",
            "tool_calls",
            "tool_results",
            "interruption",
            "conversation_context",
        }
    )


class ModelCompatibility(FrozenModel):
    """Eligibility for installation/probing. This is NEVER live wake readiness."""

    model_id: str
    manifest_fingerprint: str
    adapter: str
    device: Device
    eligible: bool
    blockers: tuple[str, ...]
    download_bytes: int


def assess_model(
    model: LocalModelManifest,
    runtime: RuntimeProbe,
    requirements: VoiceRequirements,
) -> ModelCompatibility:
    """Require agreement between model claims, actual adapter and available memory."""
    blockers: list[str] = []
    if not runtime.available:
        blockers.append(runtime.reason or "The inference runtime is unavailable.")
    if runtime.device == "metal" and runtime.os != "macos":
        blockers.append("Metal requires macOS.")
    if runtime.device == "cuda" and runtime.os == "macos":
        blockers.append("This CUDA adapter is not supported on macOS.")
    if model.family not in runtime.families:
        blockers.append("The runtime does not support this model architecture.")
    # An exact tag or an explicitly declared primary-language tag is required.
    # A language-specific model must not quietly become the multilingual default.
    language = requirements.language.replace("_", "-").lower()
    languages = {tag.lower() for tag in model.languages}
    if language not in languages and language.split("-")[0] not in languages:
        blockers.append(f"This model does not declare speech support for {language}.")
    for capability in sorted(requirements.capabilities):
        if capability not in model.capabilities:
            blockers.append(f"The model does not declare {capability}.")
        elif capability not in runtime.capabilities:
            blockers.append(f"The adapter cannot provide {capability}.")
    memory = next((item for item in model.memory if item.device == runtime.device), None)
    if memory is None:
        blockers.append("This model has no memory profile for this device.")
    elif runtime.working_memory_bytes is None or runtime.host_memory_bytes is None:
        blockers.append("Available memory could not be verified.")
    else:
        # CPU/Metal allocations share RAM. Counting each independently would
        # approve a 9 GiB model + 3 GiB host budget in 10 GiB of unified memory.
        working = memory.working_memory_bytes
        host = memory.host_memory_bytes
        if runtime.device in {"cpu", "metal"}:
            if working + host > min(runtime.working_memory_bytes, runtime.host_memory_bytes):
                blockers.append("Insufficient available shared memory for this model.")
        else:
            if working > runtime.working_memory_bytes:
                blockers.append("Insufficient available accelerator memory for this model.")
            if host > runtime.host_memory_bytes:
                blockers.append("Insufficient available system memory for this model.")
    return ModelCompatibility(
        model_id=model.id,
        manifest_fingerprint=model.fingerprint,
        adapter=runtime.adapter,
        device=runtime.device,
        eligible=not blockers,
        blockers=tuple(blockers),
        download_bytes=model.download_bytes,
    )
