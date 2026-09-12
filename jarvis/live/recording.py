"""Archive continuous transcripts without inventing provider turn boundaries."""

from __future__ import annotations

import asyncio

from jarvis.core.events import VoiceSessionEnded, VoiceTurnCompleted


async def archive_session(session, reason: str) -> None:
    """One archive group at close; timestamped fragments remain the source of truth."""
    if session._ledger is None or session._bus is None:
        return
    fragments = await asyncio.to_thread(session._ledger.transcript, session.session_id)
    text = {
        role: "".join(f["delta"] for f in fragments if f["role"] == role)
        for role in ("user", "assistant")
    }
    if any(text.values()):
        await session._bus.publish(
            VoiceTurnCompleted(
                session_id=session.session_id,
                turn_id=session._archive_turn_id,
                user_text=text["user"],
                jarvis_text=text["assistant"],
                user_lang=session._language,
                jarvis_lang=session._language,
                provider=session.active_provider,
                model=session._active_model,
                tier="realtime",
            )
        )
    await session._bus.publish(
        VoiceSessionEnded(
            session_id=session.session_id,
            hangup_reason=reason,
            duration_s=session._voice_seconds,
        )
    )
