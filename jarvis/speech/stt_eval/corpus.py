"""Manifest and WAV loading for the STT evaluation harness."""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class STTEvalItem:
    """One consented recording and its human transcript.

    ``switch_anchors`` are short phrases crossing an annotated language-change
    boundary. Losing one counts as a switch error independently from aggregate
    WER, which prevents a mostly-correct long recording from hiding a failed
    code switch.
    """

    id: str
    audio_path: Path
    reference: str
    switch_anchors: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


def load_corpus(path: Path | str) -> tuple[STTEvalItem, ...]:
    """Load a JSONL corpus, resolving audio paths beside the manifest."""
    manifest = Path(path)
    items: list[STTEvalItem] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON on corpus line {line_number}: {exc.msg}"
            ) from exc
        item_id = str(row.get("id", "")).strip()
        audio = str(row.get("audio", "")).strip()
        reference = str(row.get("reference", "")).strip()
        if not item_id or not audio or not reference:
            raise ValueError(
                f"Corpus line {line_number} requires id, audio, and reference."
            )
        if item_id in seen:
            raise ValueError(f"Duplicate corpus id {item_id!r}.")
        seen.add(item_id)
        audio_path = Path(audio)
        if not audio_path.is_absolute():
            audio_path = manifest.parent / audio_path
        items.append(
            STTEvalItem(
                id=item_id,
                audio_path=audio_path,
                reference=reference,
                switch_anchors=tuple(
                    str(value).strip()
                    for value in row.get("switch_anchors", ())
                    if str(value).strip()
                ),
                tags=tuple(
                    str(value).strip()
                    for value in row.get("tags", ())
                    if str(value).strip()
                ),
            )
        )
    if not items:
        raise ValueError("The STT evaluation corpus is empty.")
    return tuple(items)


def read_pcm16(item: STTEvalItem) -> tuple[bytes, float]:
    """Read the pipeline's native 16 kHz/mono/int16 WAV format."""
    try:
        with wave.open(str(item.audio_path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
            if (channels, sample_width, sample_rate) != (1, 2, 16_000):
                raise ValueError(
                    f"{item.audio_path} must be 16 kHz mono 16-bit PCM WAV; "
                    f"got {sample_rate} Hz, {channels} channel(s), "
                    f"{sample_width * 8}-bit."
                )
            pcm = wav.readframes(frames)
    except (OSError, wave.Error) as exc:
        raise ValueError(f"Cannot read corpus audio {item.audio_path}: {exc}") from exc
    return pcm, frames / 16_000


__all__ = ["STTEvalItem", "load_corpus", "read_pcm16"]
