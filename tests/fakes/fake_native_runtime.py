"""Injectable process boundary for transactional native model selection."""

from jarvis.realtime.local_runtime.events import NativeAudioError, NativeAudioEvent


class FakeNativeProcess:
    def __init__(self, command, directory, **kwargs):
        self.command = command
        self.running = False
        self.busy = False
        self.discarded = False

    @property
    def usable(self):
        return self.running

    async def start(self):
        if self.command[0] == "broken":
            raise NativeAudioError("Cannot load test model")
        self.running = True

    async def generate(self, **kwargs):
        if self.command[0] != "silent":
            yield NativeAudioEvent(kind="audio", pcm=b"\x00\x01", sample_rate=24000)
        yield NativeAudioEvent(kind="done")

    def discard_context(self):
        self.discarded = True

    async def close(self):
        self.running = False
