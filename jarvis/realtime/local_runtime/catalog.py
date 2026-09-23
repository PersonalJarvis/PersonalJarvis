"""Pinned native model candidates; listing never downloads or loads weights.

Catalog presence is not Jarvis runtime qualification or a recommendation. This
initial candidate permits exercising the native-package path without inventing
German support, structured tools, full duplex, or hardware measurements.
"""

from __future__ import annotations

from .models import HuggingFaceSource, LocalModelManifest, ModelArtifact


def native_model_catalog() -> tuple[LocalModelManifest, ...]:
    """Official LFM2.5 artifacts, checked against Hub metadata on 2026-09-23."""
    return (
        LocalModelManifest(
            id="lfm2.5-audio-1.5b-q4",
            label="LFM2.5 Audio 1.5B Q4 (English; runtime qualification pending)",
            family="lfm2-audio-gguf",
            source=HuggingFaceSource(
                repository="LiquidAI/LFM2.5-Audio-1.5B-GGUF",
                revision="7d525f883a077e20afb782f2ff618edcae0e39e4",
            ),
            license="LFM Open License v1.0",
            languages=("en",),
            capabilities=frozenset(
                {
                    "audio_input",
                    "audio_output",
                    "streaming_output",
                    "conversation_context",
                }
            ),
            artifacts=(
                ModelArtifact(
                    path="LFM2.5-Audio-1.5B-Q4_0.gguf",
                    size_bytes=695750880,
                    sha256="3583bee853be20331ca342b0593fefd8acc43fb61a41ec6f1a1dc7465823e0d8",
                ),
                ModelArtifact(
                    path="mmproj-LFM2.5-Audio-1.5B-Q4_0.gguf",
                    size_bytes=219511136,
                    sha256="6b483682c263b100f8cc8022d61507e446b1d320b9febc328e7960f72d03f7ea",
                ),
                ModelArtifact(
                    path="tokenizer-LFM2.5-Audio-1.5B-Q4_0.gguf",
                    size_bytes=50546112,
                    sha256="01ec6afe4578bb1e02a4d43d87e7e5827d6b3d94d2d36912ee931b9c3050f1c1",
                ),
                ModelArtifact(
                    path="vocoder-LFM2.5-Audio-1.5B-Q4_0.gguf",
                    size_bytes=108986560,
                    sha256="423cfcb054f41b69a5706226c243abc96d2531c3aff1121f7a2ed17149b79c95",
                ),
            ),
        ),
    )


def catalog_model(model_id: str) -> LocalModelManifest | None:
    return next((model for model in native_model_catalog() if model.id == model_id), None)
