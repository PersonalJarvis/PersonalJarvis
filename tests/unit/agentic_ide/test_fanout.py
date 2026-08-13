"""Guards for delivering ONE spoken order to SEVERAL terminals.

Live failure this exists for (voice session 2026-07-26 09:18): "Iris und Bruno
beide in Deep Dive geben" briefed Iris, and the answer claimed both agents were
working. Two properties have to hold at once, and they are easy to get wrong in
opposite directions:

* **Every addressee is actually served.** A fan-out that stops at the first
  failure leaves the user with a partially-briefed fleet and no way to tell.
* **The result says exactly who got what.** A pane that was dead, or whose
  prompt could not be written, must come back named — silence there is what
  turned a one-of-two delivery into a spoken lie.

Concurrency is a correctness property here, not a nicety: composing one prompt
takes the quality tier 10-21 s, and a voice turn is abandoned after 20 s. Eight
panes composed one after another cannot be delivered inside any turn at all, so
the peak-concurrency guard below pins the behaviour the feature depends on.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from jarvis.agentic_ide import fanout
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import PendingPromptAttachmentBatch


@dataclass
class FakeTerminal:
    name: str
    agent: str = "claude"
    status: str = "live"
    pty_id: str | None = "pty-1"
    pending_prompt_attachment_batches: list[PendingPromptAttachmentBatch] = field(
        default_factory=list
    )
    pending_prompt_attachment_reservations: set[str] = field(default_factory=set)
    pending_prompt_attachment_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class FakeSession:
    terminals: list[FakeTerminal]
    folder: str = "/repo"

    def find(self, wanted: str) -> FakeTerminal | None:
        for term in self.terminals:
            if term.name.casefold() == (wanted or "").casefold():
                return term
        return None


@dataclass
class Recorder:
    """Collects what was composed and what was typed into which pane."""

    sent: dict[str, str] = field(default_factory=dict)
    composed_for: list[str] = field(default_factory=list)
    attachments_for: dict[str, tuple[object, ...]] = field(default_factory=dict)
    active: int = 0
    peak: int = 0
    fail_compose_for: tuple[str, ...] = ()
    fail_send_for: tuple[str, ...] = ()
    empty_compose_for: tuple[str, ...] = ()
    delay: float = 0.02

    async def compose(self, utterance: str, **kwargs) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        self.composed_for.append(name)
        self.attachments_for[name] = tuple(kwargs.get("attachments") or ())
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            if name in self.fail_compose_for:
                raise RuntimeError(f"composer exploded for {name}")
            if name in self.empty_compose_for:
                return ComposedPrompt(text="")
            instruction = kwargs.get("instruction") or utterance
            return ComposedPrompt(
                text=f"## Task for {name}\n{instruction}",
                files=["jarvis/core/bus.py"],
                composed_by="llm",
            )
        finally:
            self.active -= 1

    not_submitted_for: tuple[str, ...] = ()

    async def send(self, name: str, text: str) -> SimpleNamespace:
        if name in self.fail_send_for:
            raise RuntimeError(f"pty write failed for {name}")
        self.sent[name] = text
        # Mirrors Registry.send_prompt, which returns the Terminal carrying the
        # submitted flag.
        return SimpleNamespace(name=name, submitted=name not in self.not_submitted_for)


def _session(*names: str) -> FakeSession:
    return FakeSession(terminals=[FakeTerminal(name=n) for n in names])


async def test_every_addressed_terminal_receives_a_prompt() -> None:
    rec = Recorder()
    result = await fanout.deliver(
        session=_session("Iris", "Bruno"),
        terminals=["Iris", "Bruno"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert sorted(rec.sent) == ["Bruno", "Iris"]
    assert result.all_delivered is True
    assert [d.terminal for d in result.delivered] == ["Iris", "Bruno"]


async def test_voice_orb_attachments_reach_exactly_the_next_spoken_prompt() -> None:
    session = _session("Iris")
    attachment = SimpleNamespace(name="layout.png")
    session.terminals[0].pending_prompt_attachment_batches.append(
        PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    )
    rec = Recorder()

    await fanout.deliver(
        session=session,
        terminals=["Iris"],
        utterance="fix what the screenshot shows",
        include_pending_attachments=True,
        compose=rec.compose,
        send=rec.send,
    )

    assert rec.attachments_for["Iris"] == (attachment,)
    assert session.terminals[0].pending_prompt_attachment_batches == []


async def test_manual_fanout_does_not_consume_a_voice_orb_drop() -> None:
    session = _session("Iris")
    attachment = SimpleNamespace(name="layout.png")
    batch = PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    session.terminals[0].pending_prompt_attachment_batches.append(batch)
    rec = Recorder()

    await fanout.deliver(
        session=session,
        terminals=["Iris"],
        utterance="an unrelated manual prompt",
        compose=rec.compose,
        send=rec.send,
    )

    assert rec.attachments_for["Iris"] == ()
    assert session.terminals[0].pending_prompt_attachment_batches == [batch]


async def test_voice_orb_attachments_survive_a_failed_delivery() -> None:
    session = _session("Iris")
    attachment = SimpleNamespace(name="layout.png")
    batch = PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    session.terminals[0].pending_prompt_attachment_batches.append(batch)
    rec = Recorder(fail_send_for=("Iris",))

    await fanout.deliver(
        session=session,
        terminals=["Iris"],
        utterance="fix what the screenshot shows",
        include_pending_attachments=True,
        compose=rec.compose,
        send=rec.send,
    )

    assert session.terminals[0].pending_prompt_attachment_batches == [batch]
    assert session.terminals[0].pending_prompt_attachment_reservations == set()


async def test_overlapping_deliveries_reserve_batches_by_identity() -> None:
    session = _session("Iris")
    first = SimpleNamespace(name="first.png")
    second = SimpleNamespace(name="second.png")
    term = session.terminals[0]
    term.pending_prompt_attachment_batches.append(
        PendingPromptAttachmentBatch("batch-a", (first,), ("first.png",))
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    composed_attachments: list[tuple[object, ...]] = []

    async def compose(utterance: str, **kwargs) -> ComposedPrompt:
        attachments = tuple(kwargs.get("attachments") or ())
        composed_attachments.append(attachments)
        if attachments == (first,):
            first_started.set()
            await release_first.wait()
        return ComposedPrompt(text=utterance)

    async def send(_name: str, _text: str) -> SimpleNamespace:
        return SimpleNamespace(submitted=True)

    delivery_a = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["Iris"],
            utterance="first prompt",
            include_pending_attachments=True,
            compose=compose,
            send=send,
        )
    )
    await first_started.wait()
    async with term.pending_prompt_attachment_lock:
        term.pending_prompt_attachment_batches.append(
            PendingPromptAttachmentBatch("batch-b", (second,), ("second.png",))
        )
    delivery_b = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["Iris"],
            utterance="second prompt",
            include_pending_attachments=True,
            compose=compose,
            send=send,
        )
    )
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(delivery_a, delivery_b)

    assert composed_attachments == [(first,), (second,)]
    assert term.pending_prompt_attachment_batches == []


def _pending_detached() -> asyncio.Future:
    """The one still-running fan-out a cancelled caller left behind.

    Filters on ``done()`` because the module registry may briefly hold settled
    futures from earlier fan-outs — the discard runs in a done-callback, one
    loop tick after completion.
    """
    pending = [w for w in fanout._DETACHED_DELIVERIES if not w.done()]
    assert len(pending) == 1
    return pending[0]


async def _settle_detached() -> None:
    """Wait for every detached fan-out of THIS loop to finish."""
    pending = [w for w in fanout._DETACHED_DELIVERIES if not w.done()]
    if pending:
        await asyncio.wait(pending, timeout=5.0)


async def test_a_cancelled_reader_still_briefs_the_pane() -> None:
    """Cancelling a REST/CLI caller detaches the delivery instead of killing it.

    The live 2026-08-06 failure: "prompt terminal T1 …" reached the composer,
    the composer's writer needed 15-20 s, the caller was cancelled at 13 s, and
    that killed the delivery mid-compose — nothing was ever typed while the user
    believed T1 was working. A client that goes away has lost the ANSWER, not
    withdrawn the order: the brief must still land, and its receipt must still
    be written.

    The SPOKEN path is the deliberate exception (``cancel_on_hangup``) — see
    ``test_a_hangup_abandons_a_spoken_brief``: there the caller IS the person who
    gave the order.
    """
    session = _session("Iris")
    attachment = SimpleNamespace(name="layout.png")
    term = session.terminals[0]
    batch = PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    term.pending_prompt_attachment_batches.append(batch)
    composing = asyncio.Event()
    release = asyncio.Event()
    sent: dict[str, str] = {}

    async def compose(_utterance: str, **_kwargs) -> ComposedPrompt:
        composing.set()
        await release.wait()
        return ComposedPrompt(text="the finished brief")

    async def send(name: str, text: str) -> SimpleNamespace:
        sent[name] = text
        return SimpleNamespace(submitted=True)

    task = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["Iris"],
            utterance="fix the layout",
            include_pending_attachments=True,
            compose=compose,
            send=send,
        )
    )
    await composing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The caller is gone; the composition is still running detached.
    work = _pending_detached()
    assert sent == {}
    release.set()
    await work

    assert sent == {"Iris": "the finished brief"}
    assert term.pending_prompt_attachment_batches == []
    assert term.pending_prompt_attachment_reservations == set()


async def test_a_cancelled_detached_delivery_releases_its_reservation() -> None:
    """Killing the detached WORK itself (loop shutdown) must not leak a batch.

    The caller-cancel path above finishes the brief; this is the other exit —
    the process is going down and the detached fan-out is cancelled for real.
    The staged attachment then stays claimable for the next spoken prompt.
    """
    session = _session("Iris")
    attachment = SimpleNamespace(name="layout.png")
    term = session.terminals[0]
    batch = PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    term.pending_prompt_attachment_batches.append(batch)
    composing = asyncio.Event()

    async def compose(_utterance: str, **_kwargs) -> ComposedPrompt:
        composing.set()
        await asyncio.Event().wait()
        return ComposedPrompt(text="unreachable")

    task = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["Iris"],
            utterance="fix the layout",
            include_pending_attachments=True,
            compose=compose,
        )
    )
    await composing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    work = _pending_detached()
    work.cancel()
    with pytest.raises(asyncio.CancelledError):
        await work

    assert term.pending_prompt_attachment_batches == [batch]
    assert term.pending_prompt_attachment_reservations == set()


async def test_cancellation_during_commit_still_consumes_a_sent_batch() -> None:
    session = _session("Iris")
    term = session.terminals[0]
    attachment = SimpleNamespace(name="layout.png")
    term.pending_prompt_attachment_batches.append(
        PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    )
    send_returned = asyncio.Event()

    async def compose(utterance: str, **_kwargs) -> ComposedPrompt:
        return ComposedPrompt(text=utterance)

    async def send(_name: str, _text: str) -> SimpleNamespace:
        await term.pending_prompt_attachment_lock.acquire()
        send_returned.set()
        return SimpleNamespace(submitted=True)

    task = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["Iris"],
            utterance="fix the layout",
            include_pending_attachments=True,
            compose=compose,
            send=send,
        )
    )
    await send_returned.wait()
    await asyncio.sleep(0)
    task.cancel()
    term.pending_prompt_attachment_lock.release()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The commit finishes inside the detached fan-out, not the dead caller.
    await _settle_detached()

    assert term.pending_prompt_attachment_batches == []
    assert term.pending_prompt_attachment_reservations == set()


async def test_a_hangup_abandons_a_spoken_brief() -> None:
    """Hanging up ends the ORDER for a pane, not just the conversation.

    Maintainer decision 2026-08-13, from the session that produced it: the user
    hung up at 11:19:43 and T5 was typed into at 11:20:03 — twenty seconds after
    they had stopped waiting, on a screen they were watching, followed by a
    verdict spoken into an empty room. Composition is the whole 20-30 s window,
    and the PTY write is its LAST step, so abandoning it leaves the pane exactly
    as it was: no text, no receipt, nothing to discover later.
    """
    session = _session("Iris")
    term = session.terminals[0]
    attachment = SimpleNamespace(name="layout.png")
    batch = PendingPromptAttachmentBatch("batch-a", (attachment,), ("layout.png",))
    term.pending_prompt_attachment_batches.append(batch)
    composing = asyncio.Event()
    sent: dict[str, str] = {}

    async def compose(_utterance: str, **_kwargs) -> ComposedPrompt:
        composing.set()
        await asyncio.Event().wait()  # the writer is still thinking
        return ComposedPrompt(text="unreachable")

    async def send(name: str, text: str) -> SimpleNamespace:
        sent[name] = text
        return SimpleNamespace(submitted=True)

    task = asyncio.create_task(
        fanout.deliver(
            session=session,
            terminals=["Iris"],
            utterance="fix the layout",
            include_pending_attachments=True,
            compose=compose,
            send=send,
            cancel_on_hangup=True,
        )
    )
    await composing.wait()

    assert fanout.cancel_spoken_deliveries(reason="the call ended (hotkey)") == 1
    with pytest.raises(asyncio.CancelledError):
        await task

    # Nothing typed, no brief left running, and the staged drop stays claimable
    # for the next spoken prompt rather than being consumed by a dead order.
    assert sent == {}
    assert [w for w in fanout._SPOKEN_DELIVERIES if not w.done()] == []
    assert term.pending_prompt_attachment_batches == [batch]
    assert term.pending_prompt_attachment_reservations == set()
    # Idempotent: teardown may run through both the realtime session and the
    # pipeline for the same call.
    assert fanout.cancel_spoken_deliveries() == 0


async def test_a_spoken_delivery_narrates_and_reports_its_arrival() -> None:
    """The spoken path gets the same two UI signals the typed prompt bar has.

    Until 2026-08-13 both were wired to the REST route only, so speaking to a
    pane left the app looking untouched for the 10-30 s a brief takes to write —
    "the bar didn't indicate any thinking, so you think it didn't work".
    """
    beats: list[str] = []
    arrived: list[str] = []

    async def compose(utterance: str, **kwargs) -> ComposedPrompt:
        kwargs["on_progress"](
            SimpleNamespace(
                stage="drafting", message="writing for Iris", terminal="Iris", kind=""
            )
        )
        return ComposedPrompt(text=utterance)

    async def send(name: str, _text: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, submitted=True)

    async def on_delivered(term) -> None:  # noqa: ANN001 - the fake Terminal
        arrived.append(term.name)

    result = await fanout.deliver(
        session=_session("Iris"),
        terminals=["Iris"],
        utterance="analyse the run",
        compose=compose,
        send=send,
        on_progress=lambda notice: beats.append(notice.stage),
        on_delivered=on_delivered,
    )

    assert beats == ["drafting"]
    assert arrived == ["Iris"]
    assert result.all_delivered is True


async def test_an_injected_composer_needs_no_progress_keyword() -> None:
    """No sink, no keyword: the composer's own stdout notice stays the default."""
    seen: list[str] = []

    async def compose(utterance: str, **kwargs) -> ComposedPrompt:
        seen.append(",".join(sorted(kwargs)))
        return ComposedPrompt(text=utterance)

    async def send(name: str, _text: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, submitted=True)

    await fanout.deliver(
        session=_session("Iris"),
        terminals=["Iris"],
        utterance="analyse the run",
        compose=compose,
        send=send,
    )
    assert "on_progress" not in seen[0]


async def test_prompts_are_composed_concurrently() -> None:
    """Composed one after another, eight panes cannot fit in a voice turn."""
    rec = Recorder()
    await fanout.deliver(
        session=_session("Iris", "Bruno", "Casey"),
        terminals=["Iris", "Bruno", "Casey"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert rec.peak >= 2, "compositions ran sequentially"


async def test_concurrency_stays_within_the_limit() -> None:
    """A fleet of twenty must not fire twenty provider calls at once."""
    rec = Recorder()
    names = [f"Pane{i}" for i in range(8)]
    await fanout.deliver(
        session=_session(*names),
        terminals=names,
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
        limit=3,
    )
    assert rec.peak <= 3
    assert len(rec.sent) == 8


async def test_a_dead_pane_is_reported_by_name() -> None:
    session = _session("Iris", "Bruno")
    session.terminals[1].status = "exited"
    session.terminals[1].pty_id = None
    rec = Recorder()

    result = await fanout.deliver(
        session=session,
        terminals=["Iris", "Bruno"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert list(rec.sent) == ["Iris"]
    assert [d.terminal for d in result.undelivered] == ["Bruno"]
    assert result.all_delivered is False
    assert "exited" in result.undelivered[0].reason


async def test_an_unknown_call_sign_is_reported_not_dropped() -> None:
    rec = Recorder()
    result = await fanout.deliver(
        session=_session("Iris"),
        terminals=["Iris", "Ghost"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert [d.terminal for d in result.undelivered] == ["Ghost"]


async def test_one_failure_does_not_stop_the_others() -> None:
    rec = Recorder(fail_send_for=("Bruno",))
    result = await fanout.deliver(
        session=_session("Iris", "Bruno", "Casey"),
        terminals=["Iris", "Bruno", "Casey"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert sorted(rec.sent) == ["Casey", "Iris"]
    assert [d.terminal for d in result.undelivered] == ["Bruno"]
    assert result.partial is True


async def test_a_composer_crash_is_contained_to_its_pane() -> None:
    rec = Recorder(fail_compose_for=("Iris",))
    result = await fanout.deliver(
        session=_session("Iris", "Bruno"),
        terminals=["Iris", "Bruno"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert list(rec.sent) == ["Bruno"]
    assert [d.terminal for d in result.undelivered] == ["Iris"]


async def test_an_empty_composition_is_never_typed() -> None:
    """Typing an empty prompt would submit a bare Enter into the agent."""
    rec = Recorder(empty_compose_for=("Bruno",))
    result = await fanout.deliver(
        session=_session("Iris", "Bruno"),
        terminals=["Iris", "Bruno"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert "Bruno" not in rec.sent
    assert [d.terminal for d in result.undelivered] == ["Bruno"]


async def test_per_terminal_assignments_beat_the_shared_instruction() -> None:
    """The hook the work splitter delivers through: one brief per pane."""
    rec = Recorder()
    await fanout.deliver(
        session=_session("Iris", "Bruno"),
        terminals=["Iris", "Bruno"],
        utterance="split the analysis between you",
        assignments={"Iris": "audit the wake path", "Bruno": "audit the UI"},
        compose=rec.compose,
        send=rec.send,
    )
    assert "audit the wake path" in rec.sent["Iris"]
    assert "audit the UI" in rec.sent["Bruno"]


async def test_no_terminals_is_an_empty_result_not_a_crash() -> None:
    result = await fanout.deliver(
        session=_session("Iris"),
        terminals=[],
        utterance="analyse the codebase",
        compose=Recorder().compose,
        send=Recorder().send,
    )
    assert result.deliveries == ()
    assert result.all_delivered is False


async def test_the_same_pane_named_twice_is_briefed_once() -> None:
    """A transcript that repeats a call-sign must not double-submit."""
    rec = Recorder()
    await fanout.deliver(
        session=_session("Iris"),
        terminals=["Iris", "Iris"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert rec.composed_for == ["Iris"]


async def test_typed_but_not_started_is_its_own_verdict() -> None:
    """A prompt sitting in the input box is not a running task (2026-07-25)."""
    rec = Recorder(not_submitted_for=("Bruno",))
    result = await fanout.deliver(
        session=_session("Iris", "Bruno"),
        terminals=["Iris", "Bruno"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    # It WAS delivered — the text reached the pane — but it did not start.
    assert result.all_delivered is True
    assert [d.terminal for d in result.typed_but_not_started] == ["Bruno"]


async def test_a_failure_carries_a_machine_readable_code() -> None:
    """The spoken layer localizes from the code, never from the English reason."""
    session = _session("Iris")
    session.terminals[0].status = "exited"
    session.terminals[0].pty_id = None

    result = await fanout.deliver(
        session=session,
        terminals=["Iris"],
        utterance="analyse the codebase",
        compose=Recorder().compose,
        send=Recorder().send,
    )
    assert result.undelivered[0].reason_code == "not_running"
    assert result.undelivered[0].status == "exited"


@pytest.mark.parametrize("status", ["pending", "starting", "exited", "failed"])
async def test_only_a_live_pane_with_a_pty_is_written_to(status: str) -> None:
    session = _session("Iris")
    session.terminals[0].status = status
    rec = Recorder()
    result = await fanout.deliver(
        session=session,
        terminals=["Iris"],
        utterance="analyse the codebase",
        compose=rec.compose,
        send=rec.send,
    )
    assert rec.sent == {}
    assert result.undelivered[0].terminal == "Iris"


# ------------------------------------------------------- the duplicate memory
# Live failure 2026-08-11 16:38: the same spoken order was fanned out twice,
# 37 s apart, because the second caller (the router brain) could not see that
# the first fleet was already composing. The memory below is what the /fanout
# route consults before opening a second fleet for the same order.

ORDER = "fix all bugs on macOS and check the git history on GitHub for anomalies"


@pytest.fixture(autouse=True)
def _fresh_duplicate_memory() -> None:
    fanout._RECENT_FANOUTS.clear()
    yield
    fanout._RECENT_FANOUTS.clear()


def _workspace(workspace_id: str, *names: str) -> FakeSession:
    session = _session(*names)
    session.id = workspace_id  # FakeSession carries no id by default
    return session


async def test_a_delivery_is_visible_to_the_guard_while_still_composing() -> None:
    """The record is written at delivery START — the live duplicate arrived
    mid-composition, when a completion-time record would not have existed."""
    session = _workspace("ide_1", "T5", "T6")
    rec = Recorder(delay=0.05)
    task = asyncio.ensure_future(
        fanout.deliver(
            session=session,
            terminals=["T5", "T6"],
            utterance=ORDER,
            compose=rec.compose,
            send=rec.send,
        )
    )
    await asyncio.sleep(0.01)  # composing, nowhere near done
    try:
        duplicate = fanout.find_duplicate_fanout(session, ORDER)
        assert duplicate is not None
        assert duplicate.terminals == ("T5", "T6")
    finally:
        await task


async def test_the_match_survives_a_rephrased_word() -> None:
    session = _workspace("ide_1", "T5")
    fanout.record_fanout(session, ORDER.replace("GitHub", "GitHuib"), ["T5"])
    assert fanout.find_duplicate_fanout(session, ORDER) is not None


async def test_a_different_task_is_no_duplicate() -> None:
    session = _workspace("ide_1", "T5")
    fanout.record_fanout(session, ORDER, ["T5"])
    other = "write end-to-end tests for the wake-word pipeline and fix the flaky ones"
    assert fanout.find_duplicate_fanout(session, other) is None


async def test_the_memory_is_bound_to_its_workspace() -> None:
    """Names are grid positions: another workspace's T5 is a different pane."""
    first = _workspace("ide_1", "T5")
    second = _workspace("ide_2", "T5")
    fanout.record_fanout(first, ORDER, ["T5"])
    assert fanout.find_duplicate_fanout(second, ORDER) is None


async def test_an_old_order_no_longer_shadows_a_new_one() -> None:
    session = _workspace("ide_1", "T5")
    fanout.record_fanout(session, ORDER, ["T5"], now=1000.0)
    late = 1000.0 + fanout.DUPLICATE_WINDOW_S + 1.0
    assert fanout.find_duplicate_fanout(session, ORDER, now=late) is None


async def test_closed_panes_make_the_rerun_legitimate() -> None:
    session = _workspace("ide_1", "T5", "T6")
    fanout.record_fanout(session, ORDER, ["T5", "T6"])
    session.terminals = [t for t in session.terminals if t.name == "T6"]
    duplicate = fanout.find_duplicate_fanout(session, ORDER)
    assert duplicate is not None and duplicate.terminals == ("T6",)
    session.terminals = []
    assert fanout.find_duplicate_fanout(session, ORDER) is None


async def test_a_sessionless_delivery_records_nothing() -> None:
    """FakeSession has no id here — exactly like a caller outside a workspace."""
    session = _session("T5")
    fanout.record_fanout(session, ORDER, ["T5"])
    assert not fanout._RECENT_FANOUTS
