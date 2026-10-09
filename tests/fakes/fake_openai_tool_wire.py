"""OpenAI SDK stream with controllable tool JSON and termination reason."""

import json

import httpx


class OpenAIToolWire:
    def __init__(self, arguments: str, finish: str) -> None:
        self.arguments = arguments
        self.finish = finish

    def handle(self, request: httpx.Request) -> httpx.Response:
        deltas = [
            ({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                "function": {"name": "read_file", "arguments": self.arguments}}]}, None),
            ({}, self.finish),
        ]
        events = [{"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1,
                   "model": "test", "choices": [{"index": 0, "delta": delta,
                                                   "finish_reason": finish}]}
                  for delta, finish in deltas]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
