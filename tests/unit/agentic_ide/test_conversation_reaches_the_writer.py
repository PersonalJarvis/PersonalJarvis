"""The turns an order came out of must actually reach the writing model.

The rule that a back-reference has to be resolved is worthless if the thing it
resolves against never arrives. This walks the path the live 2026-07-29 failure
took — brain manager → fan-out → composer → the one model call — and pins that
the conversation survives every hop.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.agentic_ide import fanout, prompt_composer

# The live turns this bug was measured on, kept verbatim: they ARE the input
# under test, and a translated back-reference would not reproduce it.
_TURNS = (
    # i18n-allow: spoken input under test
    ("user", "wie können wir die Transkription schärfen?"),
    (
        # i18n-allow: spoken input under test
        "assistant",
        "Zweitens die VAD-Schwellenwerte optimieren, drittens ein "
        "Post-Processing-Modul nach dem STT.",
    ),
)


class _CapturingBrain:
    """Answers with a valid brief and keeps what it was asked."""

    def __init__(self) -> None:
        self.system = ""
        self.user = ""

    async def complete(self, request: object):  # noqa: ANN202 - test double
        from jarvis.core.protocols import BrainDelta

        self.system = str(getattr(request, "system", "") or "")
        self.user = "\n".join(
            str(getattr(m, "content", "") or "")
            for m in getattr(request, "messages", ())
        )
        yield BrainDelta(content="## Task\nSharpen the VAD thresholds.\n")


def _session(tmp_path) -> object:  # noqa: ANN001 - pytest tmp_path
    return SimpleNamespace(
        folder=str(tmp_path),
        profile=SimpleNamespace(
            instruction_files=[], summary_lines=lambda: ["Test workspace"]
        ),
    )


async def test_the_composer_hands_the_conversation_to_the_writer(tmp_path) -> None:  # noqa: ANN001
    brain = _CapturingBrain()

    result = await prompt_composer.compose(
        # i18n-allow: spoken input under test
        "mach für T2 den Prompt, vor allem Punkt zwei und drei",
        session=_session(tmp_path),
        terminal_name="T2",
        brain=brain,
        conversation=_TURNS,
    )

    assert result.composed_by == "llm"
    assert "VAD-Schwellenwerte" in brain.user
    assert "Post-Processing-Modul" in brain.user
    # And the rule that tells the writer what to do with it travels along.
    assert "The brief is ALL the agent gets" in " ".join(brain.system.split())


async def test_a_composition_without_conversation_still_works(tmp_path) -> None:  # noqa: ANN001
    """The first turn of a session has nothing to carry, and that is normal."""
    brain = _CapturingBrain()

    result = await prompt_composer.compose(
        "look at the wake word",
        session=_session(tmp_path),
        terminal_name="T2",
        brain=brain,
    )

    assert result.composed_by == "llm"
    assert "THE CONVERSATION" not in brain.user


async def test_the_fan_out_gives_every_pane_the_same_conversation() -> None:
    """They were all addressed by one sentence, so they all get its context."""
    seen: dict[str, tuple] = {}

    async def compose(utterance: str, **kwargs) -> prompt_composer.ComposedPrompt:
        seen[kwargs["terminal_name"]] = tuple(kwargs.get("conversation") or ())
        return prompt_composer.ComposedPrompt(text=f"## Task\n{utterance}")

    async def send(name: str, text: str) -> SimpleNamespace:  # noqa: ARG001
        return SimpleNamespace(submitted=True)

    terminals = [
        SimpleNamespace(name=name, agent="claude", status="live", pty_id="pty")
        for name in ("T2", "T3")
    ]
    session = SimpleNamespace(
        terminals=terminals,
        folder="/repo",
        find=lambda wanted: next(
            (t for t in terminals if t.name.casefold() == wanted.casefold()), None
        ),
    )

    result = await fanout.deliver(
        session=session,
        terminals=["T2", "T3"],
        utterance="prompt T2 and T3, above all points two and three",
        conversation=_TURNS,
        compose=compose,
        send=send,
    )

    assert result.all_delivered
    assert seen == {"T2": _TURNS, "T3": _TURNS}
