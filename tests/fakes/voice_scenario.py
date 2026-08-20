"""Play a whole spoken turn through the real session and report what happened.

Why this exists: the maintainer had to phone Jarvis to find out whether a turn
behaved (2026-08-20 — three live calls to discover that a failed calendar step
silently killed a four-part request). A scenario says, in plain data, what the
user asked and which tools fail, refuse, or succeed; this runner drives the
REAL :class:`RealtimeVoiceSession` over a scripted wire and reports the lines
that would have been spoken.

What it does cover: everything the session decides on its own — which result
becomes speech, whether a cause is named, whether a gated call is mistaken for
a failure, and whether the turn is asked to finish its remaining work.

What it does NOT cover: whether a real model obeys the prompt. No model runs
here. That question needs ``scripts/voice_scenario_run.py --live``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from jarvis.realtime.protocol import RealtimeEvent
from jarvis.realtime.session import RealtimeVoiceSession

#: Outcomes a scenario may script for a tool call. ``blocked`` is a POLICY
#: refusal (a guard said no), which is deliberately NOT the same as ``failed``
#: — conflating the two is the 2026-08-20 13:41 bug.
OUTCOMES = ("succeeded", "failed", "blocked", "confirm")


@dataclass
class ScenarioTool:
    """One scripted tool call inside a turn."""

    name: str
    outcome: str = "succeeded"
    error: str = ""
    spoken_reply: str = ""
    args: dict[str, Any] = field(default_factory=dict)

    def result(self) -> dict[str, Any]:
        if self.outcome not in OUTCOMES:
            raise ValueError(
                f"{self.name}: outcome {self.outcome!r} is not one of {OUTCOMES}"
            )
        if self.outcome == "succeeded":
            payload: dict[str, Any] = {"success": True, "output": "done", "error": None}
            if self.spoken_reply:
                payload["spoken_reply"] = self.spoken_reply
            return payload
        if self.outcome == "confirm":
            return {
                "success": False,
                "confirmation_required": True,
                "message": self.spoken_reply or "Should I really do that?",
            }
        payload = {"success": False, "output": None, "error": self.error or None}
        if self.outcome == "blocked":
            payload["blocked"] = True
        return payload


@dataclass
class Scenario:
    """One spoken turn, its tool outcomes, and what is expected of the reply."""

    name: str
    user: str
    tools: list[ScenarioTool] = field(default_factory=list)
    language: str = "de"
    why: str = ""
    #: Whether the provider renders speech of its own after the tools. The
    #: interesting case is False — the live model going quiet, which is what
    #: hands the turn to the session's recovery.
    provider_speaks: bool = False
    expect: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Scenario:
        tools = [ScenarioTool(**tool) for tool in raw.get("tools", ())]
        known = {"name", "user", "language", "why", "provider_speaks", "expect"}
        unknown = set(raw) - known - {"tools"}
        if unknown:
            # Fail loudly: a typo in a scenario key must never read as a
            # passing check on behaviour nobody actually asserted.
            raise ValueError(f"{raw.get('name')}: unknown scenario keys {sorted(unknown)}")
        return cls(tools=tools, **{k: v for k, v in raw.items() if k in known})


@dataclass
class ScenarioResult:
    """What the session did with the turn."""

    scenario: Scenario
    spoken: list[str]
    prompts: list[str]
    tool_calls: list[str]

    @property
    def spoken_text(self) -> str:
        return " ".join(self.spoken)

    @property
    def asked_to_finish(self) -> bool:
        """True when the session ordered the model to finish the remaining work."""
        return any("is NOT over" in prompt for prompt in self.prompts)

    def failures(self) -> list[str]:
        """Every expectation this run did not meet, in plain words."""
        expect = self.scenario.expect
        said = self.spoken_text
        problems: list[str] = []
        for needle in expect.get("spoken_contains", ()):
            if needle not in said:
                problems.append(f"missing from the reply: {needle!r}")
        for needle in expect.get("spoken_excludes", ()):
            if needle in said:
                problems.append(f"must not be said: {needle!r}")
        if "continues_turn" in expect:
            want = bool(expect["continues_turn"])
            if self.asked_to_finish is not want:
                problems.append(
                    "the turn should have been continued"
                    if want
                    else "the turn was continued but should not have been"
                )
        if "spoken_equals" in expect and said != expect["spoken_equals"]:
            problems.append(f"reply is {said!r}, expected {expect['spoken_equals']!r}")
        return problems


class _ScenarioBridge:
    """Tool bridge that answers exactly what the scenario scripted."""

    def __init__(self, tools: list[ScenarioTool]) -> None:
        self._by_name = {tool.name: tool for tool in tools}
        self.declarations = tuple(
            {
                "name": tool.name,
                "description": f"Scenario tool {tool.name}.",
                "parameters": {"type": "object", "properties": {}},
            }
            for tool in tools
        )
        self.calls: list[str] = []
        self.closed = False

    def set_language(self, language: str) -> None:
        del language

    async def handle_user_transcript(self, text: str) -> None:
        del text

    async def execute(
        self, *, wire_name: str, arguments: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        del arguments
        self.calls.append(wire_name)
        tool = self._by_name.get(wire_name)
        if tool is None:
            return wire_name, {
                "success": False,
                "error": "Tool is not available in this session.",
            }
        return wire_name, tool.result()

    async def close(self) -> None:
        self.closed = True


class _ScenarioWire:
    """Scripted duplex wire: the user speaks, tools fire, the model goes quiet."""

    session_id = "scenario"
    supports_tool_updates = True
    creates_responses_automatically = False
    isolates_response_generations = True

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self._tool_results_in = asyncio.Event()
        self._expected_results = len(scenario.tools)
        self._results_seen = 0
        self.sent_text: list[str] = []
        self.tool_results: list[tuple[str, str, dict[str, Any]]] = []
        self.session_updates: list[dict[str, Any]] = []
        self.sent_audio: list[Any] = []
        self.response_requests = 0
        self.required_tools: list[str | None] = []
        self.truncated: list[int] = []
        self.interrupts = 0
        self.closed = False

    async def receive(self):
        yield RealtimeEvent(
            type="input_transcript",
            text=self._scenario.user,
            is_final=True,
        )
        for index, tool in enumerate(self._scenario.tools):
            yield RealtimeEvent(
                type="tool_call",
                call_id=f"scenario-{index}",
                tool_name=tool.name,
                tool_args=dict(tool.args),
            )
        if self._expected_results:
            # Do not close the turn before the session has answered every call;
            # a boundary that overtakes the results would test a race, not the
            # behaviour under study.
            try:
                await asyncio.wait_for(self._tool_results_in.wait(), timeout=5)
            except TimeoutError:  # pragma: no cover - a stuck bridge
                pass
        if self._scenario.provider_speaks:
            yield RealtimeEvent(
                type="output_transcript_delta",
                text="Done.",
            )
        yield RealtimeEvent(type="turn_complete")
        # The session may now inject a recovery prompt. Give it room to be
        # recorded, then leave the turn silent — the live failure mode.
        await asyncio.sleep(0.05)
        yield RealtimeEvent(type="turn_complete")

    async def send_audio(self, chunk: Any) -> None:
        self.sent_audio.append(chunk)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_tool_result(self, call_id: str, name: str, result: dict) -> None:
        self.tool_results.append((call_id, name, result))
        self._results_seen += 1
        if self._results_seen >= self._expected_results:
            self._tool_results_in.set()

    async def update_session(self, *, instructions=None, language=None, tools=None):
        self.session_updates.append(
            {"instructions": instructions, "language": language, "tools": tools}
        )

    async def request_response(self, *, required_tool: str | None = None) -> None:
        self.response_requests += 1
        self.required_tools.append(required_tool)

    async def truncate(self, audio_end_ms: int) -> None:
        self.truncated.append(audio_end_ms)

    async def interrupt(self, **_kwargs: Any) -> None:
        self.interrupts += 1

    async def close(self) -> None:
        self.closed = True


class _ScenarioProvider:
    name = "scenario"
    supports_realtime = True
    input_sample_rate = 16_000
    output_sample_rate = 24_000
    #: Declared capability (AP-21), not a hack: the shared default budget is
    #: sized for a network handshake, and a COLD run — first import of
    #: jarvis.realtime, no warm bytecode, a loaded CI box — spends it before
    #: this in-memory provider is even asked to open. A scenario that fails on
    #: machine speed instead of behaviour is worse than no scenario at all.
    handshake_budget_s = 60.0

    def __init__(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self.session: _ScenarioWire | None = None

    async def can_open_duplex_session(self) -> bool:
        return True

    async def open_session(self, config: Any) -> _ScenarioWire:
        del config
        self.session = _ScenarioWire(self._scenario)
        return self.session


def _config(language: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        brain=SimpleNamespace(reply_language=language, providers={}),
        stt=SimpleNamespace(language=language),
        voice=SimpleNamespace(mode="realtime", realtime_tool_mode="direct"),
        latency=SimpleNamespace(enabled=False),
    )


async def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Drive one scenario through the real session and collect what was said."""
    jsons: list[dict[str, Any]] = []
    provider = _ScenarioProvider(scenario)
    bridge = _ScenarioBridge(scenario.tools)
    session = RealtimeVoiceSession(
        session_id="scenario",
        send_binary=lambda _data: asyncio.sleep(0),
        send_json=lambda message: jsons.append(message) or asyncio.sleep(0),
        provider=provider,
        config=_config(scenario.language),
        bus=None,
        brain=None,
        tool_bridge=bridge,
    )
    await session.handle_control({"type": "audio_start", "sample_rate": 16_000})
    try:
        await asyncio.wait_for(session.wait_finished(), timeout=10)
    except TimeoutError:  # pragma: no cover - a hung scenario still reports
        pass
    finally:
        await session.end(reason="scenario")

    spoken = [
        str(item.get("text") or "")
        for item in jsons
        if item.get("type") == "error_spoken" and str(item.get("text") or "").strip()
    ]
    wire = provider.session
    return ScenarioResult(
        scenario=scenario,
        spoken=spoken,
        prompts=list(wire.sent_text) if wire is not None else [],
        tool_calls=list(bridge.calls),
    )


def load_scenarios(path: Any) -> list[Scenario]:
    """Read the scenario file (YAML) into :class:`Scenario` objects."""
    import yaml

    raw = yaml.safe_load(open(path, encoding="utf-8")) or {}
    return [Scenario.from_dict(entry) for entry in raw.get("scenarios", ())]


__all__ = [
    "OUTCOMES",
    "Scenario",
    "ScenarioResult",
    "ScenarioTool",
    "load_scenarios",
    "run_scenario",
]
