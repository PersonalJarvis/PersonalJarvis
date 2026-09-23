"""Learning receipts survive quiesce without admitting new model reviews."""

import asyncio

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceTurnCompleted
from jarvis.society.roster import LEAD_AGENT_ID
from jarvis.society.runtime import SocietyRuntime
from tests.fakes.learning_shutdown import GatedNotebook, WaitingReviewer


async def test_voice_receipt_drains_after_quiesce_and_subscription_is_owned(tmp_path, monkeypatch):
    bus = EventBus()
    runtime = SocietyRuntime(tmp_path, app_bus=bus, seed_starter_team=False)
    reviewer = WaitingReviewer()
    runtime.turn_reviewer = reviewer
    await runtime.ensure_started()
    experience_for = runtime.experience_for
    notebook = GatedNotebook(runtime.experience_for(LEAD_AGENT_ID))
    monkeypatch.setattr(runtime, "experience_for", lambda agent_id: notebook)
    close = None
    try:
        await runtime.quiesce()
        # A turn admitted earlier may finish while the server drains voice owners.
        await bus.publish(
            VoiceTurnCompleted(
                session_id="draining-call",
                turn_id="final-turn",
                user_text="Prefer concise answers.",
                jarvis_text="Understood.",
            )
        )
        assert await asyncio.to_thread(notebook.entered.wait, 2)
        assert len(runtime.conversations.pending_reviews()) == 1
        assert reviewer.calls == 0
        close = asyncio.create_task(runtime.close())
        await asyncio.sleep(0)
        assert not close.done()
        notebook.release.set()
        await asyncio.wait_for(close, 2)
        assert runtime._closed and not runtime._learning_receipts
        assert runtime._voice_learning not in bus._subscribers[VoiceTurnCompleted]

        monkeypatch.setattr(runtime, "experience_for", experience_for)
        await runtime.ensure_started()
        assert bus._subscribers[VoiceTurnCompleted].count(runtime._voice_learning) == 1
        await asyncio.wait_for(reviewer.started.wait(), 2)
        assert reviewer.calls == 1
        # The persisted review remains pending while its model call is canceled.
        await runtime.quiesce()
        assert len(runtime.conversations.pending_reviews()) == 1
        await runtime.close()
        await bus.publish(
            VoiceTurnCompleted(
                session_id="closed-call",
                turn_id="ignored",
                user_text="This runtime is closed.",
                jarvis_text="Ignored.",
            )
        )
        assert not runtime._learning_receipts
    finally:
        notebook.release.set()
        reviewer.release.set()
        if close is not None:
            await asyncio.gather(close, return_exceptions=True)
        await runtime.close()


async def test_receipt_timeout_keeps_storage_open_and_close_can_retry(tmp_path, monkeypatch):
    from jarvis.society import shutdown

    bus = EventBus()
    runtime = SocietyRuntime(tmp_path, app_bus=bus, seed_starter_team=False)
    runtime.turn_reviewer = WaitingReviewer()
    await runtime.ensure_started()
    await runtime.quiesce()
    notebook = GatedNotebook(runtime.experience_for(LEAD_AGENT_ID))
    monkeypatch.setattr(runtime, "experience_for", lambda agent_id: notebook)
    monkeypatch.setattr(shutdown, "QUIESCE_TIMEOUT_S", 0.02)
    try:
        await bus.publish(
            VoiceTurnCompleted(
                session_id="slow-call",
                turn_id="slow-turn",
                user_text="Use clear words.",
                jarvis_text="Understood.",
            )
        )
        assert await asyncio.to_thread(notebook.entered.wait, 2)
        with pytest.raises(RuntimeError, match="learning receipts did not drain"):
            await runtime.close()
        assert runtime.store._conn is not None
        assert len(runtime.conversations.pending_reviews()) == 1
        assert runtime._learning_receipts
        assert not any(task.cancelled() for task in runtime._learning_receipts)
        notebook.release.set()
        await asyncio.wait_for(asyncio.gather(*runtime._learning_receipts), 2)
        await runtime.close()
        assert runtime.store._conn is None
    finally:
        notebook.release.set()
        await asyncio.gather(*runtime._learning_receipts, return_exceptions=True)
        await runtime.close()
