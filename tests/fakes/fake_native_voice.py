"""In-memory providers for ``NativeLiveVoiceSession`` (Gemini Live and the local engine).

Two shapes share one file because ``jarvis/live/native.py`` serves both, and
every fix there needs a regression test on each:

* :func:`gemini_like_provider` creates its responses itself at the end of the
  user's turn (``creates_responses_automatically=True``), declares no tool
  budget and no refusal sentence of its own, like ``GeminiLiveProvider``.
* :func:`local_voice_like_provider` waits for ``request_response``, declares a
  2K-token tool budget and explains a refusal in ``duplex_unavailable_reason``,
  like ``LocalVoiceProvider``.

No network, audio device, model or worker process is involved.
"""

from __future__ import annotations

import asyncio
from typing import Any

from jarvis.core.protocols import SupervisorToolDescriptor, ToolResult


class FakeNativeConnection:
    """One scripted native session; every call the session makes is recorded in order."""

    def __init__(self, *, creates_responses_automatically: bool, model: str = "") -> None:
        self.creates_responses_automatically = creates_responses_automatically
        self.isolates_response_generations = True
        self.supports_tool_results = True
        self.model = model
        self.calls: list[tuple[str, Any]] = []
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []
        self.tool_result_sent = asyncio.Event()
        self.closed = False
        self._events: asyncio.Queue[Any] = asyncio.Queue()

    def push(self, event: Any) -> None:
        self._events.put_nowait(event)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def receive(self):
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    async def send_audio(self, chunk: Any) -> None:
        self.calls.append(("send_audio", chunk))

    async def send_text(self, text: str) -> None:
        self.calls.append(("send_text", text))

    async def update_session(self, **changes: Any) -> None:
        self.calls.append(("update_session", changes))

    async def request_response(self, *, required_tool: str | None = None) -> None:
        self.calls.append(("request_response", required_tool))

    async def interrupt(self, **kwargs: Any) -> None:
        self.calls.append(("interrupt", kwargs))

    async def truncate(self, audio_end_ms: int) -> None:
        self.calls.append(("truncate", audio_end_ms))

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        self.calls.append(("send_tool_result", call_id))
        self.tool_results.append((call_id, name, result))
        self.tool_result_sent.set()

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._events.put_nowait(None)


class FakeNativeProvider:
    """A native-tool provider that records the session config it was opened with."""

    supports_realtime = True
    native_tool_orchestration = True
    browser_audio = True
    credential_candidates: tuple[tuple[str, str | None], ...] = ()

    def __init__(
        self,
        name: str,
        *,
        creates_responses_automatically: bool,
        input_sample_rate: int,
        output_sample_rate: int = 24_000,
        ready: bool = True,
        refusal_reason: str | None = None,
        probe_delay_s: float = 0.0,
        tool_declaration_budget_tokens: int = 0,
    ) -> None:
        self.name = name
        self.input_sample_rate = input_sample_rate
        self.output_sample_rate = output_sample_rate
        self.tool_declaration_budget_tokens = tool_declaration_budget_tokens
        self._creates = creates_responses_automatically
        self._ready = ready
        self._refusal_reason = refusal_reason
        self._probe_delay_s = probe_delay_s
        self.probes = 0
        self.opened_with: list[Any] = []
        self.connection: FakeNativeConnection | None = None
        if refusal_reason is not None:
            self.duplex_unavailable_reason = ""

    async def can_open_duplex_session(self) -> bool:
        self.probes += 1
        if self._probe_delay_s:
            await asyncio.sleep(self._probe_delay_s)
        if self._refusal_reason is not None:
            self.duplex_unavailable_reason = "" if self._ready else self._refusal_reason
        return self._ready

    async def open_session(self, cfg: Any) -> FakeNativeConnection:
        self.opened_with.append(cfg)
        self.connection = FakeNativeConnection(
            creates_responses_automatically=self._creates, model=str(cfg.model or "")
        )
        return self.connection


def gemini_like_provider(**overrides: Any) -> FakeNativeProvider:
    options: dict[str, Any] = {
        "creates_responses_automatically": True,
        "input_sample_rate": 16_000,
    }
    options.update(overrides)
    return FakeNativeProvider("gemini-live", **options)


def local_voice_like_provider(**overrides: Any) -> FakeNativeProvider:
    options: dict[str, Any] = {
        "creates_responses_automatically": False,
        "input_sample_rate": 16_000,
        "refusal_reason": "The local voice is still loading (stt, 40 %).",
        "tool_declaration_budget_tokens": 2_000,
    }
    options.update(overrides)
    return FakeNativeProvider("local-voice", **options)


class FakeToolGateway:
    """Supervisor tool gateway with a configurable catalog and blockable tools."""

    def __init__(self, descriptors: tuple[SupervisorToolDescriptor, ...] = ()) -> None:
        self._descriptors = descriptors or (
            SupervisorToolDescriptor(
                "search_web",
                "Search the web.",
                {"type": "object", "properties": {"query": {"type": "string"}}},
                "safe",
            ),
        )
        self.gates: dict[str, asyncio.Event] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.finished: list[str] = []

    def catalog(self) -> tuple[SupervisorToolDescriptor, ...]:
        return self._descriptors

    def hold(self, name: str) -> asyncio.Event:
        """Block ``name`` until the returned event is set."""
        self.gates[name] = asyncio.Event()
        return self.gates[name]

    async def execute(self, name: str, arguments: dict[str, Any], request: Any) -> ToolResult:
        del request
        self.calls.append((name, arguments))
        gate = self.gates.get(name)
        if gate is not None:
            await gate.wait()
        self.finished.append(name)
        return ToolResult(True, {"verified": True})

    async def execute_confirmed(self, trace: Any, request: Any) -> ToolResult:
        del trace, request
        return ToolResult(True, {"verified": True})

    async def cancel_pending(self, trace: Any, *, reason: str = "voice_vetoed") -> bool:
        del trace, reason
        return True


def catalog_of(count: int, *, schema_padding: int = 200) -> tuple[SupervisorToolDescriptor, ...]:
    """``count`` generic tools plus the curated names, each with a sizable schema."""
    names = [
        "search_web",
        "workspace-orchestrate",
        "youtube_music",
        "google_calendar",
        *(f"tool-{index:02d}" for index in range(count)),
    ]
    return tuple(
        SupervisorToolDescriptor(
            name,
            f"Does {name}. " + "x" * schema_padding,
            {"type": "object", "properties": {"value": {"type": "string"}}},
            "safe",
        )
        for name in names
    )


__all__ = [
    "FakeNativeConnection",
    "FakeNativeProvider",
    "FakeToolGateway",
    "catalog_of",
    "gemini_like_provider",
    "local_voice_like_provider",
]
