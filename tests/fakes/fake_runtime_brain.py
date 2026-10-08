"""Scripted gateway provider for real runtime transport checks, never inference."""

from jarvis.core.protocols import BrainDelta


class ReplayRuntimeBrain:
    context_window = 128000

    async def complete(self, request):
        assistant = any(message.role == "assistant" for message in request.messages)
        remembered = any("PELICAN" in str(message.content) for message in request.messages)
        answer = ("PELICAN" if remembered else "UNKNOWN") if assistant else "READY"
        yield BrainDelta(content=answer)
        yield BrainDelta(usage={"input_tokens": 10, "output_tokens": 2}, finish_reason="stop")
