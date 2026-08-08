from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from jarvis.agentic_ide.fleet_actions import (
    close_agent_terminals,
    wait_for_prompt_ready,
)
from jarvis.agentic_ide.transcript import Transcript


@dataclass
class _Term:
    name: str
    agent: str
    status: str = "live"
    pty_id: str | None = "pty"
    transcript: Transcript = field(default_factory=Transcript)


class _Session:
    def __init__(self, terms: list[_Term]) -> None:
        self.terminals = terms

    def find(self, name: str) -> _Term | None:
        return next((term for term in self.terminals if term.name == name), None)


@pytest.mark.asyncio
async def test_codex_waits_for_its_input_line_but_claude_does_not() -> None:
    codex = _Term("Cody", "codex")
    claude = _Term("Clara", "claude")
    session = _Session([codex, claude])

    assert await wait_for_prompt_ready(session, ["Cody", "Clara"], timeout_s=0) == (
        "Clara",
    )

    codex.transcript.feed(
        "\x1b[2J\x1b[HOpenAI Codex\r\n"
        "› Input disabled.\x1b[2;3H\x1b[?25l"
    )
    assert await wait_for_prompt_ready(session, ["Cody", "Clara"], timeout_s=0) == (
        "Clara",
    )

    codex.transcript.feed(
        "\x1b[2J\x1b[HOpenAI Codex\r\n"
        "› Ask Codex anything\x1b[2;3H\x1b[?25h"
    )
    assert await wait_for_prompt_ready(session, ["Cody", "Clara"], timeout_s=0) == (
        "Cody",
        "Clara",
    )


@pytest.mark.asyncio
async def test_close_agent_terminals_only_closes_the_requested_cli() -> None:
    class _Registry:
        def __init__(self) -> None:
            self.session = _Session(
                [_Term("Cody", "codex"), _Term("Clara", "claude"), _Term("Cole", "codex")]
            )

        async def close_terminal(self, name: str) -> _Term:
            term = self.session.find(name)
            assert term is not None
            self.session.terminals.remove(term)
            return term

    registry = _Registry()

    closed = await close_agent_terminals(registry, "codex")

    assert [term.name for term in closed] == ["Cody", "Cole"]
    assert [term.name for term in registry.session.terminals] == ["Clara"]
