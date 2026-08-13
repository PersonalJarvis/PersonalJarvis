"""One spoken brief for one existing pane must never open a new one.

The live 2026-08-13 failure, end to end. The maintainer said, of a workspace
holding five Claude panes and one Codex pane:

    "prompten, bitte Terminal TR1, also ein Deep Dive machen soll und mal
     gucken, wo wir aktuell mit dem Jarvis Marketplace stehen"

T1 sat idle. A sixth pane opened, running CODEX, and the composed brief was
typed into it. Three independent layers each failed open in the same direction,
and it took all three to produce that outcome:

1. **The call-sign was unreadable.** Speech recognition wrote "TR1" for "T1".
   The 2026-08-12 repair for exactly this garble required a SPACE between the
   consonant debris and the number ("terminal tft zwei"), so the glued form
   matched nothing and no pane was addressed.
2. **The article became the fleet size.** With no pane addressed, the spawn
   path read the first number-ish word in the clause — "also **ein** Deep Dive"
   — as a request for one terminal. ``names`` refuses the indefinite article as
   a number and says why; the spawn parser did not.
3. **The new pane copied the wrong CLI.** No CLI was named, so the pane
   inherited the agent of the LAST pane in reading order — a Codex pane opened
   minutes earlier for an unrelated errand.

Each layer is pinned separately below, because each one is independently
wrong and any of them could regress on its own.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.intent import detect, detect_spawn
from jarvis.agentic_ide.session import Registry
from tests.fakes.fake_pty_manager import FakePtyManager

#: The workspace as it stood: T5 and T7 had been closed, so the free position
#: the new pane took was T5 — the numbers are not contiguous, which is normal.
PANES = ["T1", "T2", "T3", "T4", "T6", "T8"]

#: The transcript as recorded, trimmed to the sentence that carried the order.
LIVE = (
    # i18n-allow: verbatim spoken transcript under test
    "prompten, bitte Terminal TR1, also ein Deep Dive machen soll und mal "
    "gucken, wo wir aktuell mit dem Jarvis Marketplace stehen"
)


class TestTheGarbledCallSignStillAddressesItsPane:
    """Layer 1: "TR1" is T1, however tightly the debris is glued on."""

    def test_the_live_utterance_addresses_t1(self) -> None:
        found = detect(LIVE, names=PANES)
        assert found is not None
        assert found.terminal == "T1"
        assert found.kind == "prompt"

    @pytest.mark.parametrize(
        "utterance",
        [
            "prompte Terminal TR1, mach einen Deep Dive",  # i18n-allow: input vocab under test
            "prompte Terminal TF1, mach einen Deep Dive",  # i18n-allow: input vocab under test
            "prompte Terminal TT1, mach einen Deep Dive",  # i18n-allow: input vocab under test
            "prompt terminal tr1 to do a deep dive",
        ],
    )
    def test_every_glued_garble_reaches_the_same_pane(self, utterance: str) -> None:
        found = detect(utterance, names=PANES)
        assert found is not None
        assert found.terminal == "T1"


class TestABriefIsNotAFleetOrder:
    """Layer 2: the sentence asks one pane for work, so nothing may open."""

    def test_the_live_utterance_opens_nothing(self) -> None:
        assert detect_spawn(LIVE, names=PANES) is None

    @pytest.mark.parametrize(
        "utterance",
        [
            "prompte Terminal TR1, mach einen Deep Dive",  # i18n-allow: input vocab under test
            "prompte Terminal T1, mach einen Deep Dive",  # i18n-allow: input vocab under test
            "prompte Terminal tft zwei, mach einen Deep Dive",  # i18n-allow: input vocab under test
        ],
    )
    def test_a_briefed_pane_never_opens_a_fleet(self, utterance: str) -> None:
        assert detect_spawn(utterance, names=PANES) is None


class TestTheArticleMustSizeSomething:
    """Layer 2, the half that survives an unreadable call-sign.

    An article is the number one only in front of the thing it sizes. Both
    directions matter: dropping it entirely would cost the user the pane they
    asked for in the most ordinary phrasing there is.
    """

    @pytest.mark.parametrize(
        ("utterance", "expected"),
        [
            ("mach noch ein Terminal auf", 1),  # i18n-allow: input vocab under test
            ("öffne ein weiteres Terminal", 1),  # i18n-allow: input vocab under test
            ("mach noch einen Codex auf", 1),  # i18n-allow: input vocab under test
            ("open a terminal", 1),
            ("abre una terminal", 1),
            # No number at all still means one pane — the default stands.
            ("mach das Terminal auf", 1),  # i18n-allow: input vocab under test
            # Counts that are real numbers are untouched by any of this.
            ("öffne zwei Terminals", 2),  # i18n-allow: input vocab under test
            ("spawn 5 Codex terminals", 5),
            # i18n-allow: input vocab under test
            ("öffne drei Claude Code Terminals und zwei Codex", 5),
        ],
    )
    def test_a_sizing_article_still_counts(self, utterance: str, expected: int) -> None:
        request = detect_spawn(utterance, names=PANES)
        assert request is not None
        assert request.count == expected

    def test_an_article_in_the_task_half_is_not_a_count(self) -> None:
        """"… und mach einen Deep Dive" sizes nothing and must add no pane."""
        request = detect_spawn(
            # i18n-allow: input vocab under test
            "öffne zwei Terminals und mach einen Deep Dive",
            names=PANES,
        )
        assert request is not None
        assert request.count == 2


class TestANewPaneRunsTheWorkspacesCli:
    """Layer 3: an anchor-less pane follows the majority, not the last pane."""

    @pytest.fixture(autouse=True)
    def _isolated_recents(
        self,
        tmp_path_factory: pytest.TempPathFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keep the recents file out of the developer's real data directory."""
        from jarvis.agentic_ide import recents

        store = tmp_path_factory.mktemp("recents") / "recents.json"
        monkeypatch.setattr(recents, "_store_path", lambda: store)

    @pytest.fixture
    def registry(self, monkeypatch: pytest.MonkeyPatch) -> Registry:
        monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
        return Registry(pty_manager=FakePtyManager())

    async def test_the_odd_pane_at_the_end_does_not_decide(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        """The live grid: five Claude panes, then one Codex pane, then a batch."""
        await registry.start(
            str(tmp_path),
            [{"agent": "claude"} for _ in range(5)] + [{"agent": "codex"}],
        )
        created, _capped = await registry.add_terminals(1)
        assert [t.agent for t in created] == ["claude"]

    async def test_a_named_cli_still_wins(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        """Asking for Codex in a Claude workspace opens Codex."""
        await registry.start(str(tmp_path), [{"agent": "claude"} for _ in range(3)])
        created, _capped = await registry.add_terminals(2, agent="codex")
        assert [t.agent for t in created] == ["codex", "codex"]

    async def test_a_split_still_inherits_its_anchor(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        """Splitting a pane means "another one of these", majority or not."""
        session = await registry.start(
            str(tmp_path),
            [{"agent": "claude"} for _ in range(3)] + [{"agent": "codex"}],
        )
        codex_pane = next(t for t in session.terminals if t.agent == "codex")
        split = await registry.add_terminal(anchor=codex_pane.name, direction="down")
        assert split.agent == "codex"

    async def test_a_uniform_workspace_is_unchanged(
        self, registry: Registry, tmp_path: Path
    ) -> None:
        """The common case — every pane the same CLI — behaves as it always did."""
        await registry.start(str(tmp_path), [{"agent": "codex"} for _ in range(2)])
        created, _capped = await registry.add_terminals(2)
        assert [t.agent for t in created] == ["codex", "codex"]
