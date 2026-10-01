"""Current per-agent review receipts survive shutdown and a fresh runtime owner."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from jarvis.society.runtime import SocietyRuntime, SocietyRuntimeClosed
from tests.fakes.learning_shutdown import WaitingReviewer


def completion(turn_id):
    return SimpleNamespace(
        events_json=json.dumps(
            [
                {"seq": 1, "kind": "user_message", "payload": {"text": "Compare these sources."}},
                {"seq": 2, "kind": "assistant_text", "payload": {"text": "Comparison complete."}},
                {"seq": 3, "kind": "turn_finished", "payload": {"status": "done"}},
            ]
        ),
        turn=SimpleNamespace(turn_id=turn_id, direct_user=True),
    )


@pytest.mark.parametrize("quiesce_first", [False, True])
async def test_review_receipt_survives_shutdown_and_recovers_once(tmp_path, quiesce_first):
    runtime = SocietyRuntime(tmp_path, seed_starter_team=False)
    reviewer = WaitingReviewer()
    runtime.turn_reviewer = reviewer
    await runtime.ensure_started()
    agent, _ = await runtime.roster.create(name="Receipt scout")
    recovered = None
    try:
        if quiesce_first:
            await runtime.quiesce()
        await runtime.turn_completed(
            SimpleNamespace(session_id=agent.session_id), completion("last-admitted-turn")
        )
        if quiesce_first:
            assert reviewer.calls == 0
        else:
            await asyncio.wait_for(reviewer.started.wait(), 2)
        await runtime.quiesce()
        assert len(runtime.conversations.pending_reviews()) == 1
        assert not runtime._producers
        await runtime.close()
        with pytest.raises(SocietyRuntimeClosed):
            await runtime.ensure_started()

        recovered = SocietyRuntime(tmp_path, seed_starter_team=False)
        recovered_reviewer = WaitingReviewer()
        recovered_reviewer.release.set()
        recovered.turn_reviewer = recovered_reviewer
        await recovered.ensure_started()
        await recovered.recover_reviews()
        assert recovered_reviewer.calls == 1
        assert recovered.conversations.review_counts(agent.agent_id) == {"pending": 0, "done": 1}
        await recovered.recover_reviews()
        assert recovered_reviewer.calls == 1
    finally:
        reviewer.release.set()
        await runtime.close()
        if recovered is not None:
            await recovered.close()
