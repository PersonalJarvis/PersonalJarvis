"""Recorded Anthropic SSE shapes served locally through an SDK transport."""

import json
from importlib import import_module
from typing import Any


class AnthropicWire:
    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.intermediate_usage = False
        self.tool_arguments: str | None = None
        self.thinking = False

    def client(self) -> Any:
        from anthropic import DefaultAsyncHttpxClient

        # SDK releases may migrate their HTTP dependency. Use its own public
        # client and matching transport rather than assuming one package name.
        for base in DefaultAsyncHttpxClient.__mro__:
            module = import_module(base.__module__.split(".")[0])
            if callable(getattr(module, "MockTransport", None)):
                self._http = module
                return DefaultAsyncHttpxClient(transport=module.MockTransport(self.handle))
        raise RuntimeError("The Anthropic SDK HTTP client has no compatible test transport")

    def handle(self, request: Any) -> Any:
        self.requests.append(request)
        events = [
            {"type": "message_start", "message": {
                "id": "msg_test", "type": "message", "role": "assistant",
                "model": "claude-sonnet-4-6", "content": [], "stop_reason": None,
                "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1,
                    "cache_read_input_tokens": 20, "cache_creation_input_tokens": 5},
            }},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": "OK"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {
                "stop_reason": "end_turn", "stop_sequence": None,
            }, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        if self.tool_arguments is not None:
            events[1]["content_block"] = {
                "type": "tool_use", "id": "call_test", "name": "read_file", "input": {},
            }
            events[2]["delta"] = {"type": "input_json_delta", "partial_json": self.tool_arguments}
            events[4]["delta"]["stop_reason"] = "tool_use"
        if self.intermediate_usage:
            events.insert(4, {"type": "message_delta", "delta": {
                "stop_reason": None, "stop_sequence": None,
            }, "usage": {"output_tokens": 1}})
        if self.thinking:
            for event in events:
                if "index" in event:
                    event["index"] += 1
            events[1:1] = [
                {"type": "content_block_start", "index": 0, "content_block": {
                    "type": "thinking", "thinking": "", "signature": "",
                }},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "thinking_delta", "thinking": "private test reasoning"}},
                {"type": "content_block_delta", "index": 0,
                 "delta": {"type": "signature_delta", "signature": "opaque-test-signature"}},
                {"type": "content_block_stop", "index": 0},
            ]
        body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
        return self._http.Response(200, text=body, headers={"content-type": "text/event-stream"})
