"""Trusted native launch plans derived from verified model data and runner bytes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .events import NativeAudioError
from .models import LocalModelManifest
from .packages import verify_package
from .paths import native_data_path


@dataclass(frozen=True, slots=True)
class NativeRunner:
    """Host-installed executable identity; never supplied by a model manifest."""

    executable: Path
    sha256: str
    revision: str


@dataclass(frozen=True, slots=True)
class LfmBindings:
    """Artifact-relative filenames, allowing own weights without renaming them."""

    model: str
    audio_encoder: str
    audio_tokenizer: str
    vocoder: str


@dataclass(frozen=True, slots=True)
class NativeLaunchPlan:
    model_id: str
    manifest_fingerprint: str
    runner_sha256: str
    revision: str
    command: tuple[str, ...]

    @property
    def identity(self) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "manifest": self.manifest_fingerprint,
                    "runner": self.runner_sha256,
                    "command": self.command,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()


def prepare_lfm_launch(
    manifest: LocalModelManifest,
    weights: Path,
    runner: NativeRunner,
    bindings: LfmBindings,
    *,
    context_tokens: int = 32768,
    threads: int = 4,
) -> NativeLaunchPlan:
    """Verify before changing a running selection. Call off the audio/request loop.

    This initial runner profile is CPU-only. CUDA/Metal must gain a verified
    runner profile before an adapter is allowed to claim acceleration.
    """
    if manifest.family != "lfm2-audio-gguf":
        raise NativeAudioError("This runtime does not understand the selected model architecture.")
    if type(context_tokens) is not int or not 512 <= context_tokens <= 32768:
        raise ValueError("context_tokens must fit this model's context window")
    if type(threads) is not int or not 1 <= threads <= 256:
        raise ValueError("threads must be a positive CPU thread count")
    bound = (bindings.model, bindings.audio_encoder, bindings.audio_tokenizer, bindings.vocoder)
    artifacts = {artifact.path for artifact in manifest.artifacts}
    if len(set(bound)) != 4 or not set(bound) <= artifacts:
        raise NativeAudioError("Assign the four required model components from this package.")
    if any(not name.lower().endswith(".gguf") for name in bound):
        raise NativeAudioError("The native LFM runtime requires GGUF model components.")
    if not verify_package(manifest, weights).verified:
        raise NativeAudioError("The model files failed verification; repair the download first.")
    with runner.executable.open("rb") as executable:
        digest = hashlib.file_digest(executable, "sha256").hexdigest()
    if digest != runner.sha256:
        raise NativeAudioError("The native runtime failed verification; reinstall the runtime.")
    paths = [native_data_path(weights / name) for name in bound]
    return NativeLaunchPlan(
        model_id=manifest.id,
        manifest_fingerprint=manifest.fingerprint,
        runner_sha256=digest,
        revision=runner.revision,
        command=(
            str(runner.executable.resolve()),
            "-m",
            paths[0],
            "-mm",
            paths[1],
            "--tts-speaker-file",
            paths[2],
            "-mv",
            paths[3],
            "-t",
            str(threads),
            "-ngl",
            "0",
            "-c",
            str(context_tokens),
        ),
    )
