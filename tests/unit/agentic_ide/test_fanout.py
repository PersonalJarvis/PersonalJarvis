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


@dataclass
class FakeTerminal:
    name: str
    agent: str = "claude"
    status: str = "live"
    pty_id: str | None = "pty-1"


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
    active: int = 0
    peak: int = 0
    fail_compose_for: tuple[str, ...] = ()
    fail_send_for: tuple[str, ...] = ()
    empty_compose_for: tuple[str, ...] = ()
    delay: float = 0.02

    async def compose(self, utterance: str, **kwargs) -> ComposedPrompt:
        name = kwargs["terminal_name"]
        self.composed_for.append(name)
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
        return SimpleNamespace(
            name=name, submitted=name not in self.not_submitted_for
        )


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
