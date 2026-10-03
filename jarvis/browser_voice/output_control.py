"""Session-scoped output policy for every browser voice transport."""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.events import VoiceSpeakerMuteChanged


class BrowserOutputControl:
    """Serialize speaker snapshots with PCM; never queue speech across a mute."""

    def __init__(self, *, bus: Any, pipeline: Any, send_json: Any,
                 send_binary: Any, volume: float = 1.0) -> None:
        self._bus = bus
        self._pipeline = pipeline
        self._json = send_json
        self._binary = send_binary
        self._lock = asyncio.Lock()
        self._state = {"muted": volume <= 0, "volume": volume, "revision": 0}
        self._watching = False

    def snapshot(self) -> dict[str, Any]:
        getter = getattr(self._pipeline, "speaker_output_state", None)
        return getter() if callable(getter) else dict(self._state)

    async def start(self) -> None:
        if self._bus is not None:
            self._bus.subscribe(VoiceSpeakerMuteChanged, self._changed)
            self._watching = True
        async with self._lock:
            await self._json({"type": "output_state", **self.snapshot()})

    def close(self) -> None:
        if self._watching:
            self._bus.unsubscribe(VoiceSpeakerMuteChanged, self._changed)
            self._watching = False

    async def _changed(self, event: VoiceSpeakerMuteChanged) -> None:
        self._state = {"muted": event.muted, "volume": event.volume,
                       "revision": event.revision}
        async with self._lock:
            if self._watching:
                await self._json({"type": "output_state", **self.snapshot()})

    async def send_binary(self, data: bytes) -> None:
        before = self.snapshot()
        async with self._lock:
            current = self.snapshot()
            if before["muted"] or current["muted"] or before["revision"] != current["revision"]:
                return
            await self._binary(data)

    async def send_json(self, message: dict[str, Any]) -> None:
        async with self._lock:
            if message.get("type") in {"audio_ready", "audio_transport"}:
                state = self.snapshot()
                message = {**message, "output_muted": state["muted"],
                           "output_volume": state["volume"],
                           "output_revision": state["revision"]}
            await self._json(message)
