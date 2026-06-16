"""Anti-drift parity guard for spawn-meta stripping.

The recurring "voice sub-agent missions fail" bug (2026-06-16) came back every
time because the spawn/routing meta-clause was cleaned in TWO places that drifted
apart: the critic classifier (``is_informational_request``) stripped it, but the
worker-prompt builder (``spawn_worker._build_mission_prompt``) did not — so the
worker received "spawn a sub-agent …" as its own task. The fix makes both call
the SAME ``strip_spawn_meta`` function. This test makes a future duplicated regex
impossible to merge: both modules must reference the identical object.
"""
from __future__ import annotations

from jarvis.missions import stream_evidence
from jarvis.plugins.tool import spawn_worker


def test_worker_prompt_and_critic_share_one_strip_function() -> None:
    """The worker-prompt builder and the critic classifier must strip spawn-meta
    via the exact same function object — single source of truth, no drift."""
    assert spawn_worker.strip_spawn_meta is stream_evidence.strip_spawn_meta


def test_strip_spawn_meta_is_public_on_stream_evidence() -> None:
    """``strip_spawn_meta`` is the public, shared entry point."""
    assert callable(stream_evidence.strip_spawn_meta)
    assert "strip_spawn_meta" in stream_evidence.__all__
