"""Telemetry-Layer (Phase 5 Capability 5).

Flight-Recorder (JSONL, tagesrotiert, ADR-0007) + Replay-CLI.
"""
from __future__ import annotations

from .recorder import FlightRecorder

__all__ = ["FlightRecorder"]
