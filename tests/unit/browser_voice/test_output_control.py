"""The browser output policy covers native Live, WebRTC and classic TTS."""

import asyncio

from jarvis.browser_voice.output_control import BrowserOutputControl
from jarvis.core.bus import EventBus
from jarvis.core.events import VoiceSpeakerMuteChanged


class Pipeline:
    def __init__(self):
        self.muted = True
        self.revision = 1

    def speaker_output_state(self):
        return {"muted": self.muted, "volume": 0.6, "revision": self.revision}


async def test_initial_state_reconnect_audio_transport_and_unsubscribe():
    bus = EventBus()
    pipeline = Pipeline()
    messages, audio = [], []

    async def send_json(msg):
        messages.append(msg)

    async def send_binary(data):
        audio.append(data)

    control = BrowserOutputControl(bus=bus, pipeline=pipeline, send_json=send_json,
                                   send_binary=send_binary)
    await control.start()
    assert messages[-1] == {"type": "output_state", **pipeline.speaker_output_state()}
    await control.send_binary(b"muted")
    assert audio == []
    for kind in ("audio_transport", "audio_ready"):
        await control.send_json({"type": kind})
        assert messages[-1]["output_muted"] is True
        assert messages[-1]["output_volume"] == 0.6
    pipeline.muted = False
    pipeline.revision = 2
    await bus.publish(VoiceSpeakerMuteChanged(muted=False, revision=2))
    assert messages[-1]["muted"] is False
    await control.send_binary(b"fresh")
    assert audio == [b"fresh"]
    control.close()
    count = len(messages)
    await bus.publish(VoiceSpeakerMuteChanged(muted=True, revision=3))
    assert len(messages) == count


async def test_pcm_waiting_for_transport_does_not_cross_a_mute_cycle():
    pipeline = Pipeline()
    pipeline.muted = False
    heard = []

    async def send_binary(data):
        heard.append(data)

    async def send_json(msg):
        pass  # This test only exercises the binary send ordering.

    control = BrowserOutputControl(bus=None, pipeline=pipeline, send_json=send_json,
                                   send_binary=send_binary)
    async with control._lock:
        pending = asyncio.create_task(control.send_binary(b"stale"))
        await asyncio.sleep(0)
        pipeline.revision += 2
    await pending
    assert heard == []
    await control.send_binary(b"fresh")
    assert heard == [b"fresh"]
