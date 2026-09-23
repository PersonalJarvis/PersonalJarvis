"""Normalized native audio events, independent of an engine transport."""

from dataclasses import dataclass
from typing import Literal


class NativeAudioError(RuntimeError):
    """The native engine did not complete a verified protocol operation."""


@dataclass(frozen=True, slots=True)
class NativeAudioEvent:
    kind: Literal["text", "audio", "done", "cancelled"]
    text: str = ""
    pcm: bytes = b""
    sample_rate: int = 0
