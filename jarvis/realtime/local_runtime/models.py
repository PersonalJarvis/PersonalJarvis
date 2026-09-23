"""Portable, data-only manifests for native local realtime models.

A package describes weights, not executable installation instructions. Model
claims are deliberately separate from adapter capabilities and live readiness.
No manifest can opt a runtime into executing remote Python or a shell command.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Capability = Literal[
    "audio_input",
    "audio_output",
    "streaming_output",
    "full_duplex",
    "tool_calls",
    "tool_results",
    "interruption",
    "conversation_context",
]
Device = Literal["cpu", "cuda", "metal"]
OperatingSystem = Literal["windows", "macos", "linux"]

_RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", re.I)
_SAFE_PART = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_. -]*")


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def portable_relative_path(value: str) -> str:
    """Require the same safe relative filename on all three supported OSes."""
    parts = value.split("/")
    if (
        not value
        or len(value) > 240
        or any(
            part in {"", ".", ".."}
            or not _SAFE_PART.fullmatch(part)
            or part.endswith((".", " "))
            or _RESERVED.fullmatch(part)
            for part in parts
        )
    ):
        raise ValueError("artifact paths must be portable relative paths")
    return value


class ModelArtifact(FrozenModel):
    """A weight/tokenizer file at an immutable Hugging Face revision."""

    path: str = Field(min_length=1, max_length=240)
    size_bytes: int = Field(gt=0, strict=True)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = portable_relative_path(value)
        if PurePosixPath(value).suffix.lower() not in {".gguf", ".safetensors", ".onnx", ".json"}:
            raise ValueError("model packages may contain data files only")
        return value


class MemoryRequirement(FrozenModel):
    """Conservative per-adapter estimates, never a latency or readiness claim."""

    device: Device
    working_memory_bytes: int = Field(gt=0, strict=True)
    host_memory_bytes: int = Field(ge=0, strict=True)


class HuggingFaceSource(FrozenModel):
    kind: Literal["huggingface"] = "huggingface"
    repository: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*$")
    revision: str = Field(pattern=r"^[a-f0-9]{40}$")


class LocalSource(FrozenModel):
    # The user-selected directory travels separately, never into a shareable
    # manifest. Own/fine-tuned weights do not require a remote repository.
    kind: Literal["local"] = "local"


class LocalModelManifest(FrozenModel):
    schema_version: Literal[1] = 1
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,95}$")
    label: str = Field(min_length=1, max_length=160)
    # This identifies a registered adapter's data format, never a Python path.
    family: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    source: HuggingFaceSource | LocalSource = Field(discriminator="kind")
    license: str = Field(min_length=1, max_length=120)
    languages: tuple[str, ...] = Field(min_length=1, max_length=256)
    capabilities: frozenset[Capability]
    artifacts: tuple[ModelArtifact, ...] = Field(min_length=1, max_length=128)
    memory: tuple[MemoryRequirement, ...] = Field(default=(), max_length=3)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return portable_relative_path(value)

    @field_validator("languages")
    @classmethod
    def validate_languages(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", v) for v in values):
            raise ValueError("languages must be explicit language tags")
        if len(set(values)) != len(values):
            raise ValueError("duplicate languages")
        return values

    @model_validator(mode="after")
    def validate_package(self) -> LocalModelManifest:
        paths = [item.path.casefold() for item in self.artifacts]
        if len(set(paths)) != len(paths):
            raise ValueError("artifact paths must be unique on case-insensitive filesystems")
        for path in paths:
            if any(other.startswith(path + "/") for other in paths):
                raise ValueError("an artifact cannot also be a directory")
        if len({entry.device for entry in self.memory}) != len(self.memory):
            raise ValueError("duplicate device memory requirements")
        if not {"audio_input", "audio_output"} <= self.capabilities:
            raise ValueError("a native realtime model must accept and produce audio")
        return self

    @property
    def download_bytes(self) -> int:
        return sum(item.size_bytes for item in self.artifacts)

    @property
    def fingerprint(self) -> str:
        """Content identity for install/qualification receipts, independent of set order."""
        import json

        payload = self.model_dump(mode="json")
        payload["capabilities"] = sorted(self.capabilities)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def artifact_url(self, artifact: ModelArtifact) -> str:
        if artifact not in self.artifacts:
            raise ValueError("artifact is not part of this model package")
        if self.source.kind != "huggingface":
            raise ValueError("local model packages have no download URL")
        return (
            f"https://huggingface.co/{self.source.repository}/resolve/{self.source.revision}/"
            f"{quote(artifact.path, safe='/')}"
        )
