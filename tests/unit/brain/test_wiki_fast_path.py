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
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainMessage, ToolResult
from jarvis.sessions.store import SessionStore


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


async def test_latest_transcript_falls_back_to_active_persisted_session(
    manager_factory,
    monkeypatch,
    tmp_path,
):
    """An empty Realtime-owned history uses only its active persisted session."""
    from jarvis.core import runtime_refs

    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        for session_id, turn_id, started_ms, user_text, jarvis_text in (
            (
                "active-session",
                "active-turn",
                1_000,
                "The launch code name is Aurora.",
                "I will remember Aurora.",
            ),
            (
                "other-session",
                "other-turn",
                2_000,
                "A different conversation contains private text.",
                "This must not cross session boundaries.",
            ),
        ):
            store.upsert_session(
                session_id=session_id,
                started_ms=started_ms,
                language="en",
            )
            store.upsert_turn(
                turn_id=turn_id,
                session_id=session_id,
                idx=0,
                started_ms=started_ms,
            )
            store.finalize_turn(
                turn_id=turn_id,
                ended_ms=started_ms + 100,
                user_text=user_text,
                user_lang="en",
                jarvis_text=jarvis_text,
                jarvis_lang="en",
                tier="realtime",
                provider="fake",
                model="fake-model",
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_total_ms=100,
                tool_calls=[],
            )

        pipeline = SimpleNamespace(
            voice_engine_status=lambda: {"session_id": "active-session"}
        )
        app = SimpleNamespace(state=SimpleNamespace(session_store=store))
        monkeypatch.setattr(runtime_refs, "get_speech_pipeline", lambda: pipeline)
        monkeypatch.setattr(runtime_refs, "get_web_app", lambda: app)

        tool = FakeWikiIngestTool(ToolResult(success=True, output="stored"))
        mgr, _executor, _bus = manager_factory(tools={"wiki-ingest": tool})
        mgr._history.clear()

        reply = await mgr._run_wiki_ingest_fast_path(
            "Write the latest transcript to the Wiki."
        )

        assert reply is not None
        await asyncio.sleep(0.05)
        assert len(tool.calls) == 1
        saved_text = tool.calls[0]["text"]
        assert "Aurora" in saved_text
        assert "different conversation" not in saved_text
        assert "cross session boundaries" not in saved_text
    finally:
        store.close()


async def test_latest_transcript_never_uses_unscoped_persisted_history(
    manager_factory,
    monkeypatch,
    tmp_path,
):
    """Without a reliable active session id, persisted fallback stays closed."""
    from jarvis.core import runtime_refs

    store = SessionStore(tmp_path / "sessions.db")
    store.open()
    try:
        store.upsert_session(session_id="unrelated", started_ms=1_000, language="en")
        store.upsert_turn(
            turn_id="unrelated-turn",
            session_id="unrelated",
            idx=0,
            started_ms=1_000,
        )
        store.finalize_turn(
            turn_id="unrelated-turn",
            ended_ms=1_100,
            user_text="Never copy this unrelated transcript.",
            user_lang="en",
            jarvis_text="Unrelated answer.",
            jarvis_lang="en",
            tier="realtime",
            provider="fake",
            model="fake-model",
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            latency_total_ms=100,
            tool_calls=[],
        )

        pipeline = SimpleNamespace(voice_engine_status=lambda: {"session_id": ""})
        app = SimpleNamespace(state=SimpleNamespace(session_store=store))
        monkeypatch.setattr(runtime_refs, "get_speech_pipeline", lambda: pipeline)
        monkeypatch.setattr(runtime_refs, "get_web_app", lambda: app)

        tool = FakeWikiIngestTool(ToolResult(success=True, output="stored"))
        mgr, _executor, _bus = manager_factory(tools={"wiki-ingest": tool})
        mgr._history.clear()

        reply = await mgr._run_wiki_ingest_fast_path(
            "Write the latest transcript to the Wiki."
        )

        assert reply is not None
        assert "wiki" in reply.lower()
        await asyncio.sleep(0.05)
        assert not tool.calls
    finally:
        store.close()


async def test_non_wiki_turn_returns_none(manager_factory):
    tool = FakeWikiIngestTool(ToolResult(success=True, output=""))
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    assert await mgr._run_wiki_ingest_fast_path("wie wird das wetter morgen?") is None  # i18n-allow
    assert not tool.calls


async def test_reported_obsidian_follow_up_ingests_inline_facts(manager_factory):
    """An immediate Obsidian context resolves "there" without saving the
    preceding listing question as content."""
    tool = FakeWikiIngestTool(ToolResult(success=True, output="stored"))
    mgr, _executor, _bus = manager_factory(tools={"wiki-ingest": tool})
    mgr._history.append(  # noqa: SLF001 - bounded production-history seam
        BrainMessage(
            role="user",
            content="Was steht in meiner Obsidian-Wiki drin?",  # i18n-allow
        )
    )
    mgr._history.append(  # noqa: SLF001 - mirror the preceding assistant reply
        BrainMessage(role="assistant", content="Dein Vault hat zwÃ¶lf Seiten."),  # i18n-allow
    )

    reply = await mgr._run_wiki_ingest_fast_path(
        "Kannst du bitte einen Eintrag da eintragen, dass ich ziemlich "  # i18n-allow
        "genervt bin und dass ich in San Francisco "  # i18n-allow
        "wohne?"  # i18n-allow: production transcript under test
    )

    assert reply is not None
    await asyncio.sleep(0.05)
    assert len(tool.calls) == 1
    saved = tool.calls[0]["text"].lower()
    assert "genervt" in saved
    assert "san francisco" in saved
    assert "was steht" not in saved


async def test_live_polite_travel_fact_reaches_wiki_before_local_action(
    manager_factory,
    monkeypatch,
):
    """The production utterance must never be reinterpreted as trip booking."""
    tool = FakeWikiIngestTool(
        ToolResult(success=True, output="Wiki ingest done:\n- applied: 1")
    )
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})

    async def fail_if_local_action_runs(*args, **kwargs):
        raise AssertionError("explicit wiki writes must precede local-action routing")

    monkeypatch.setattr(
        BrainManager,
        "_run_local_action_fast_path",
        fail_if_local_action_runs,
    )
    reply = await mgr.generate(
        "Kannst du bitte mein Wiki-System eintragen, dass ich morgen nach "  # i18n-allow
        "San Francisco reisen will?"  # i18n-allow: production transcript under test
    )

    assert reply
    await asyncio.sleep(0.05)
    assert len(tool.calls) == 1
    assert "san francisco" in tool.calls[0]["text"].lower()
