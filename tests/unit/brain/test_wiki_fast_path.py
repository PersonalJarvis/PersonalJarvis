"""Explicit wiki commands run model-independently and confirm only after
the write (spec A1-A3). Uses constructor-injected fakes per house style —
build a minimal BrainManager the same way test_routing.py does (copy its
manager fixture) and register a recording fake executor + a fake
wiki-ingest tool.

The confirm-after-write contract is proven by a SHARED timeline list that
BOTH the fake executor and the recording bus append to: the executor writes
``"execute:done"`` when ``execute()`` returns; the bus writes ``"announce"``
when it publishes an ``AnnouncementRequested``. A success turn must have
``execute:done`` strictly before ``announce`` — i.e. the completion is spoken
only after the write actually happened."""
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
    """Fake ToolExecutor that stamps the shared timeline on completion.

    Appends ``"execute:done"`` to ``timeline`` at the instant ``execute()``
    returns the tool result — i.e. the moment the write is finished.
    """

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def execute(self, tool, args, **kwargs):
        if getattr(tool, "delay_s", 0):
            await asyncio.sleep(tool.delay_s)
        tool.calls.append(dict(args))
        self.timeline.append("execute:done")
        return tool.result


class RecordingBus(EventBus):
    """Real EventBus that also records publishes onto the shared timeline.

    Keeps a flat ``published`` list (mirrors the ``_FakeBus``/``bus.published``
    shape used by tests/unit/brain/test_computer_use_offload.py) AND appends
    ``"announce"`` to the shared ``timeline`` whenever an
    ``AnnouncementRequested`` is published, so the confirm-after-write ordering
    is directly assertable against the executor's ``execute:done`` stamp.
    Stays a REAL EventBus so BrainManager's constructor/attach path behaves
    exactly as in production.
    """

    def __init__(self, timeline: list[str]) -> None:
        super().__init__()
        self.timeline = timeline
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:  # noqa: ANN401
        self.published.append(event)
        if type(event).__name__ == "AnnouncementRequested":
            self.timeline.append("announce")
        await super().publish(event)


@pytest.fixture
def manager_factory():
    """Build a minimal BrainManager wired for the wiki fast path.

    Follows the ``tests/unit/brain/test_routing.py`` construction style: the
    real ``BrainManager(config=JarvisConfig(), bus=..., tools=..., tool_executor=...)``
    constructor, no real providers (``readback_composer`` stays unset, so
    ``render_readback`` always returns the deterministic canned phrase — no
    LLM call, no network).

    The executor and the bus share ONE ``timeline`` list (exposed as
    ``bus.timeline`` / ``executor.timeline``, the same object) so a test can
    prove the completion announcement fires strictly after the write.

    Seeds one plausible prior user/assistant exchange into ``_history`` so an
    ANAPHORIC "write THAT to the wiki" command (spec A1) has real content to
    source from — mirroring production, where such a command is never
    literally the first turn of a conversation.
    """

    def _factory(*, tools: dict[str, Any]) -> tuple[BrainManager, RecordingExecutor, RecordingBus]:
        timeline: list[str] = []
        bus = RecordingBus(timeline)
        executor = RecordingExecutor(timeline)
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
        ),
        delay_s=0.02,  # a real gap between "write" and "announce" to order against
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
    # Confirm-after-write: the completion announcement is stamped on the SHARED
    # timeline strictly AFTER the executor finished the write.
    assert "execute:done" in bus.timeline, "the write must be recorded on the timeline"
    assert "announce" in bus.timeline, "the announcement must be recorded on the timeline"
    assert bus.timeline.index("execute:done") < bus.timeline.index("announce"), (
        f"confirm-after-write violated: announce must come after the write; "
        f"timeline={bus.timeline}"
    )


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


async def test_failure_announcement_carries_the_reason(manager_factory):
    """The keyless failure path is honest-with-cause: a real tool error is
    distilled into the spoken failure phrase (wiki_save_failed_reason), and the
    bare success phrase is NEVER emitted on failure."""
    tool = FakeWikiIngestTool(
        ToolResult(success=False, output="", error="wiki integration not bootstrapped")
    )
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    reply = await mgr._run_wiki_ingest_fast_path("write that to the wiki")
    assert reply is not None
    await asyncio.sleep(0.05)
    completed = [e for e in bus.published if type(e).__name__ == "AnnouncementRequested"]
    assert completed
    text = completed[-1].text
    # Never a success phrase on failure (any supported language).
    assert text.strip().lower() not in {
        "saved to the wiki.",
        "im wiki gespeichert.",  # i18n-allow
        "guardado en la wiki.",  # i18n-allow
    }
    assert "not work" in text.lower()  # honest failure phrasing (en)
    assert "wiki integration not bootstrapped" in text  # the cause is carried through


async def test_bare_exit_code_reason_is_not_spoken(manager_factory):
    """An opaque 'exit N'-only error leaves no presentable reason, so the
    failure degrades to the reason-less phrase — a raw exit code is never
    spoken (mirror of cu_failure_readback's guard)."""
    tool = FakeWikiIngestTool(ToolResult(success=False, output="", error="exit 5"))
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    await mgr._run_wiki_ingest_fast_path("write that to the wiki")
    await asyncio.sleep(0.05)
    completed = [e for e in bus.published if type(e).__name__ == "AnnouncementRequested"]
    assert completed
    text = completed[-1].text
    assert "exit 5" not in text.lower()
    assert "exit" not in text.lower()
    assert text == "Saving to the wiki did not work."  # the reason-less phrase


async def test_anaphoric_command_without_usable_content_asks_what_to_write(manager_factory):
    """An anaphoric 'write that to the wiki' with no usable last exchange must
    ask what to write — the wiki_nothing_to_save ack — with NO executor call
    and NO completion announcement (spec A1 content gap)."""
    tool = FakeWikiIngestTool(ToolResult(success=True, output=""))
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    mgr._history.clear()  # no prior exchange to source the anaphor from
    reply = await mgr._run_wiki_ingest_fast_path("write that to the wiki")
    assert reply is not None
    assert "wiki" in reply.lower()
    await asyncio.sleep(0.05)
    assert not tool.calls, "the absent-content path must not call the executor"
    assert not [
        e for e in bus.published if type(e).__name__ == "AnnouncementRequested"
    ], "no write happened, so nothing is announced"


async def test_non_wiki_turn_returns_none(manager_factory):
    tool = FakeWikiIngestTool(ToolResult(success=True, output=""))
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    assert await mgr._run_wiki_ingest_fast_path("wie wird das wetter morgen?") is None  # i18n-allow
    assert not tool.calls
