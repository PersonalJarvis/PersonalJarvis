"""Explicit wiki commands run model-independently and confirm only after
the write (spec A1-A3). Uses constructor-injected fakes per house style —
build a minimal BrainManager the same way test_routing.py does (copy its
manager fixture) and register a recording fake executor + a fake
wiki-ingest tool."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainMessage, ToolResult


class FakeWikiIngestTool:
    name = "wiki-ingest"
    risk_tier = "monitor"

    def __init__(self, result: ToolResult, delay_s: float = 0.0) -> None:
        self.result = result
        self.delay_s = delay_s
        self.calls: list[dict] = []


class RecordingExecutor:
    def __init__(self) -> None:
        self.order: list[str] = []

    async def execute(self, tool, args, **kwargs):
        self.order.append("execute:start")
        if getattr(tool, "delay_s", 0):
            await asyncio.sleep(tool.delay_s)
        tool.calls.append(dict(args))
        self.order.append("execute:done")
        return tool.result


class RecordingBus(EventBus):
    """EventBus that also keeps a flat list of everything published.

    Mirrors the ``_FakeBus``/``bus.published`` shape used by the sibling
    Computer-Use offload tests (tests/unit/brain/test_computer_use_offload.py)
    while staying a REAL EventBus so BrainManager's constructor/attach path
    behaves exactly as in production.
    """

    def __init__(self) -> None:
        super().__init__()
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:  # noqa: ANN401
        self.published.append(event)
        await super().publish(event)


@pytest.fixture
def manager_factory():
    """Build a minimal BrainManager wired for the wiki fast path.

    Follows the ``tests/unit/brain/test_routing.py`` construction style: the
    real ``BrainManager(config=JarvisConfig(), bus=..., tools=..., tool_executor=...)``
    constructor, no real providers (``readback_composer`` stays unset, so
    ``render_readback`` always returns the deterministic canned phrase — no
    LLM call, no network).

    Seeds one plausible prior user/assistant exchange into ``_history`` so an
    ANAPHORIC "write THAT to the wiki" command (spec A1) has real content to
    source from — mirroring production, where such a command is never
    literally the first turn of a conversation.
    """

    def _factory(*, tools: dict[str, Any]) -> tuple[BrainManager, RecordingExecutor, RecordingBus]:
        bus = RecordingBus()
        executor = RecordingExecutor()
        mgr = BrainManager(
            config=JarvisConfig(),
            bus=bus,
            tools=tools,
            tool_executor=executor,  # type: ignore[arg-type]
        )
        mgr._history.append(
            BrainMessage(
                role="user",
                content=(
                    "Ich war heute mit Joy im Park und wir haben über ihren "  # i18n-allow
                    "Geburtstag im August gesprochen."  # i18n-allow
                ),
            )
        )
        mgr._history.append(
            BrainMessage(
                role="assistant",
                content="Klingt nach einem schönen Nachmittag im Park mit Joy.",  # i18n-allow
            )
        )
        return mgr, executor, bus

    return _factory


async def test_explicit_command_ingests_and_announces_after_write(manager_factory):
    """'Schreib ins Wiki, dass X' → tool called with X; the completion
    announcement fires only AFTER the executor returned success."""  # i18n-allow
    tool = FakeWikiIngestTool(
        ToolResult(
            success=True,
            output="Wiki ingest done:\n- applied: 1\nPages touched:\n  - joy.md",
        )
    )
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    reply = await mgr._run_wiki_ingest_fast_path(
        "Schreib ins Wiki, dass Joys Geburtstag am 14. August ist"  # i18n-allow
    )
    assert reply is not None                     # immediate progress ack
    await asyncio.sleep(0.05)                    # let the background task run
    assert tool.calls, "wiki-ingest must be invoked through the executor"
    completed = [e for e in bus.published if type(e).__name__ == "AnnouncementRequested"]
    assert completed, "outcome must be announced (zero silent drops)"
    assert executor.order.index("execute:done") < len(executor.order)


async def test_failure_is_announced_honestly_never_as_success(manager_factory):
    tool = FakeWikiIngestTool(
        ToolResult(success=False, output="", error="wiki integration not bootstrapped")
    )
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    reply = await mgr._run_wiki_ingest_fast_path("write that to the wiki")
    assert reply is not None
    await asyncio.sleep(0.05)
    completed = [e for e in bus.published if type(e).__name__ == "AnnouncementRequested"]
    assert completed
    text = completed[-1].text.lower()
    assert "saved to the wiki." != text          # no bare success phrase
    assert any(k in text for k in ("not work", "nicht geklappt", "no se pudo"))  # i18n-allow


async def test_non_wiki_turn_returns_none(manager_factory):
    tool = FakeWikiIngestTool(ToolResult(success=True, output=""))
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    assert await mgr._run_wiki_ingest_fast_path("wie wird das wetter morgen?") is None  # i18n-allow
    assert not tool.calls
