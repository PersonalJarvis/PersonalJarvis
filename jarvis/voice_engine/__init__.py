"""Jarvis-owned local voice engine (ADR-0037).

This package runs inside the engine's OWN Python environment, not the app's.
It therefore imports nothing from ``jarvis.*`` and only standard-library
modules at import time; every model runtime (onnxruntime, sherpa-onnx, torch,
mlx) is imported lazily by the module that needs it. The app talks to the
engine through a thin provider adapter (``docs/local-live-voice-rebuild.md``).

Phase P0 ships the measurement bench (``python -m jarvis.voice_engine.bench``)
on top of the same component modules the worker will use.
"""

ENGINE_VERSION = "0.1.0"
