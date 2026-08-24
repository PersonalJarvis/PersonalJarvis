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
def test_worker_prompt_and_stripper_share_the_directive_wording() -> None:
    """The force-spawn directive is ONE string, referenced by both sides.

    It used to be an inline literal in the builder and a set of guessed
    phrases in the stripper. When the wording changed, the stripper silently
    stopped matching and every "real request" surface — report titles,
    generated filenames, the agent board's task column — started showing
    "Carry out the underlying user request directly…" instead of the ask.
    """
    assert spawn_worker.FORCE_SPAWN_DIRECTIVE is stream_evidence.FORCE_SPAWN_DIRECTIVE
    assert spawn_worker.UNDERLYING_REQUEST_LEAD is stream_evidence.UNDERLYING_REQUEST_LEAD


def test_clean_request_body_removes_everything_the_builder_prepends() -> None:
    """Round trip: what the builder stacks on, the stripper takes back off.

    This is the assertion the original drift would have failed. It does not
    name any directive wording, so rewording either layer keeps it honest.
    """
    ask = "build me a complete social section with friend stats"
    built = spawn_worker._build_mission_prompt(
        utterance=f"spawn an agent to {ask}",
        action="",
        target="",
        context_hints=[],
    )

    cleaned = stream_evidence.clean_request_body(built)

    assert ask in cleaned
    # No fragment of either directive layer survives.
    assert "production-quality" not in cleaned.lower()
    assert "carry out the underlying user request" not in cleaned.lower()
    assert stream_evidence.UNDERLYING_REQUEST_LEAD not in cleaned


def test_clean_request_body_leaves_an_undirected_prompt_alone() -> None:
    """A prompt carrying neither layer is returned whole."""
    assert stream_evidence.clean_request_body("just do the thing") == "just do the thing"
