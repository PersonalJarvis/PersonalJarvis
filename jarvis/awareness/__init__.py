"""Awareness Layer (Phase A0+) — continuous context for the main Jarvis.

Four-layer architecture (Plan §1):
    L1 Live Frame   — RAM, seconds                      (Phase A1)
    L2 Story        — RAM ring buffer + SQLite          (Phase A2)
    L3 Session      — FTS5 search over episodes         (Phase A3)
    L4 Long-Term    — Curator -> MEMORY.md (existing)

A0 scope: data models + privacy filter + config schema. No watchers,
no captures, no bus subscriptions — those come in A1.
"""
from __future__ import annotations

from jarvis.awareness.config import (
    AwarenessConfig,
    AwarenessPrivacyConfig,
    AwarenessProbesConfig,
    AwarenessQuotasConfig,
    AwarenessWatchersConfig,
)
from jarvis.awareness.context import Context, resolve_context
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.privacy import PrivacyFilter
from jarvis.awareness.probes import FileSystemProbe, GitProbe, Probe
from jarvis.awareness.quotas import StorageQuota
from jarvis.awareness.state import AwarenessState, FrameSnapshot
from jarvis.awareness.working_set import WorkingSet

__all__ = [
    "AwarenessConfig",
    "AwarenessManager",
    "AwarenessPrivacyConfig",
    "AwarenessProbesConfig",
    "AwarenessQuotasConfig",
    "AwarenessState",
    "AwarenessWatchersConfig",
    "Context",
    "FileSystemProbe",
    "FrameSnapshot",
    "GitProbe",
    "PrivacyFilter",
    "Probe",
    "StorageQuota",
    "WorkingSet",
    "resolve_context",
]
