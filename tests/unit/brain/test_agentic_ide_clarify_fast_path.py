"""A call-sign the transcript garbled is asked about, then delivered on.

The live failure of 2026-07-27 16:18, end to end: the pane "Ellis" came back
from speech recognition as "Ilies", scoring just under the acting threshold. The
addressed-terminal path returned ``None`` without a word — no prompt was typed,
nothing was said, and the realtime model, which is never told any of this, filled
the silence by announcing that an agent was on it. The maintainer spent the next
two turns being told a lie.

What this file pins is the whole repaired loop, because each half is worthless
alone:

* an uncertain call-sign produces a QUESTION rather than silence, and types
  nothing into any pane while it is unanswered;
* the answer delivers the ORIGINAL sentence — the one that carried the task — so
  clarifying costs the user one word instead of repeating themselves;
* a sentence about the outside world still reaches no pane at all, which is the
  failure the question must not buy at the price of.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agentic_ide import clarify as ide_clarify
from jarvis.agentic_ide import prompt_composer
from jarvis.agentic_ide import session as session_mod
from jarvis.agentic_ide.prompt_composer import ComposedPrompt
from jarvis.agentic_ide.session import Registry
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from tests.fakes.fake_pty_manager import FakePtyManager


@pytest.fixture(autouse=True)
def _isolated_recents(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never rewrite the developer's real recent-workspace list from a test."""
    from jarvis.agentic_ide import recents

    store = tmp_path_factory.mktemp("recents") / "recents.json"
    monkeypatch.setattr(recents, "_store_path", lambda: store)


@pytest.fixture(autouse=True)
def _clean_window() -> None:
    """The clarification window is process-wide; no test may inherit one."""
    ide_clarify.WINDOW.disarm()
    yield
    ide_clarify.WINDOW.disarm()


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> Registry:
    monkeypatch.setattr(session_mod, "agent_argv", lambda name: (f"/usr/bin/{name}",))
    reg = Registry(pty_manager=FakePtyManager())
    monkeypatch.setattr(session_mod, "get_registry", lambda: reg)
    return reg


@pytest.fixture
def manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    # Pinned so the wording assertions never depend on the host's own locale
    # (AP-23: never test against the maintainer's configuration).
    mgr._reply_language = "en"
    return mgr


@pytest.fixture(autouse=True)
def _fake_composer(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A deterministic stand-in for the quality-tier prompt writer."""
    seen: list[str] = []

    async def fake_compose(utterance: str, **kwargs: object) -> ComposedPrompt:
        seen.append(str(kwargs.get("instruction") or utterance))
        return ComposedPrompt(
            text=f"## Task\n{utterance}", files=[], composed_by="llm"
        )

    monkeypatch.setattr(prompt_composer, "compose", fake_compose)
    return seen


async def _noop(_text: str) -> None:
    return None


async def _noop_exit(_code: int) -> None:
    return None


#: Panes here carry CUSTOM call-signs, not the positional ones a workspace
#: hands out by default (T1, T2, …). That is deliberate and it is the whole
#: point of this file: a position is either said or not said, so it cannot be
#: garbled into a near miss — while a name the user gave a pane can be, which
#: is exactly the failure the clarification loop exists for. Testing it against
#: T-numbers would test nothing at all.
NAMED_PANES: tuple[str, ...] = ("Alex", "Blake", "Casey", "Dana", "Ellis")


async def _open(registry: Registry, folder: Path, count: int) -> list[str]:
    """Open ``count`` named panes AND bring their agents live; return the names."""
    await registry.start(
        folder if isinstance(folder, str) else str(folder),
        [{"agent": "claude", "name": name} for name in NAMED_PANES[:count]],
    )
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    return [t.name for t in registry.session.terminals]


async def _open_numbered(registry: Registry, folder: Path, count: int) -> list[str]:
    """The same, with the call-signs a workspace assigns on its own."""
    await registry.start(str(folder), [{"agent": "claude"} for _ in range(count)])
    assert registry.session is not None
    for term in list(registry.session.terminals):
        await registry.attach(term.name, 100, 30, _noop, _noop_exit)
    return [t.name for t in registry.session.terminals]


def _typed(registry: Registry) -> list[str]:
    """Everything the panes were actually sent this test."""
    return list(registry._pty.typed)  # noqa: SLF001 - the fake's recording surface


def _briefs(registry: Registry) -> list[str]:
    """Just the prompts, without the Enter that submits each one."""
    return [text for text in _typed(registry) if text.strip()]


# --------------------------------------------------------------------------- #


async def test_a_garbled_call_sign_asks_instead_of_silently_doing_nothing(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The regression: 'Ilies' must not vanish into a silent ``None``."""
    names = await _open(registry, tmp_path, 5)
    assert "Ellis" in names, "the fifth pane of the pool is the one that was misheard"

    reply = await manager._run_agentic_ide_fast_path(
        "can you have Ilies do a deep dive and find out why the tests fail"
    )

    assert reply is not None, "the turn that started all of this must not be silent"
    assert "Ellis" in reply
    assert "Ilies" in reply, "the user hears what was understood, not just a guess"
    assert ide_clarify.WINDOW.armed


async def test_nothing_is_typed_into_a_pane_while_the_question_is_open(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """Asking must not also act — that would be guessing with extra steps."""
    await _open(registry, tmp_path, 5)

    await manager._run_agentic_ide_fast_path(
        "can you have Ilies do a deep dive and find out why the tests fail"
    )

    assert _typed(registry) == []


async def test_the_answer_delivers_the_original_task(
    manager: BrainManager,
    registry: Registry,
    tmp_path: Path,
    _fake_composer: list[str],
) -> None:
    """"Yes" must do the work the FIRST sentence described, not repeat itself."""
    await _open(registry, tmp_path, 5)
    task = "can you have Ilies do a deep dive and find out why the tests fail"

    question = await manager._run_agentic_ide_fast_path(task)
    assert question is not None

    verdict = await manager._run_agentic_ide_fast_path("yes")

    assert verdict is not None
    assert "Ellis" in verdict
    # The composer saw the original sentence, never the bare confirmation.
    assert any("deep dive" in seen for seen in _fake_composer)
    assert not any(seen.strip().casefold() == "yes" for seen in _fake_composer)
    assert not ide_clarify.WINDOW.armed


async def test_declining_delivers_nothing(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    await _open(registry, tmp_path, 5)
    await manager._run_agentic_ide_fast_path(
        "can you have Ilies do a deep dive and find out why the tests fail"
    )

    assert await manager._run_agentic_ide_fast_path("no") is None
    assert _typed(registry) == []
    assert not ide_clarify.WINDOW.armed


async def test_a_question_about_the_outside_world_neither_asks_nor_types(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The maintainer's counter-example, pinned as a hard negative."""
    await _open(registry, tmp_path, 5)

    reply = await manager._run_agentic_ide_fast_path(
        "can you tell me what Elon Musk is doing right now?"
    )

    assert reply is None
    assert _typed(registry) == []
    assert not ide_clarify.WINDOW.armed


async def test_a_certain_call_sign_still_acts_without_asking(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """Clarifying must not become the new normal for names that ARE clear."""
    names = await _open(registry, tmp_path, 5)

    reply = await manager._run_agentic_ide_fast_path(
        f"tell {names[0]} to fix the failing tests"
    )

    assert reply is not None
    assert not ide_clarify.WINDOW.armed
    assert _typed(registry), "a certain call-sign is delivered, not questioned"


# --------------------------------------------------------------------------- #
# A turn addresses a FLEET (live 2026-07-27 19:07)                             #
# --------------------------------------------------------------------------- #


async def test_two_garbled_call_signs_are_asked_about_together(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """Two mangled names in one list used to produce nothing at all.

    The detector stood down above one uncertain word, so "Alexa und Blaike,
    macht beide ..." reached neither pane and asked nothing — the silent miss
    this whole area exists to end, only for a pair instead of a single pane.
    """
    await _open(registry, tmp_path, 5)

    reply = await manager._run_agentic_ide_fast_path(
        "Alexa and Blaike should both do a deep dive on the failing tests"
    )

    assert reply is not None
    assert "Alex" in reply and "Blake" in reply
    assert _typed(registry) == [], "nothing is typed while the question is open"
    assert ide_clarify.WINDOW.armed


async def test_one_yes_briefs_the_whole_addressed_pair(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """One question, one word, both agents working."""
    await _open(registry, tmp_path, 5)
    task = "Alexa and Blaike should both do a deep dive on the failing tests"
    assert await manager._run_agentic_ide_fast_path(task) is not None

    verdict = await manager._run_agentic_ide_fast_path("yes")

    assert verdict is not None
    assert "Alex" in verdict and "Blake" in verdict
    assert len(_briefs(registry)) == 2, "both addressees are briefed, not the first"
    assert all("deep dive" in text for text in _briefs(registry))


async def test_a_pane_that_was_understood_is_briefed_before_the_question(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """A name past every threshold ("Dave") must not cost the one that worked.

    The live shape: Alex resolves, "Dave" matches no pane at any threshold, and
    the turn said "Alex is on it" while the user was waiting for two agents.
    The count the user stated out loud is what survives that, so the work goes
    out AND the gap is named in the same breath.
    """
    await _open(registry, tmp_path, 5)

    reply = await manager._run_agentic_ide_fast_path(
        "Alex and Dave should both do a deep dive on the failing tests"
    )

    assert reply is not None
    assert "Alex" in reply
    assert len(_briefs(registry)) == 1, "the pane that WAS understood is briefed"
    assert ide_clarify.WINDOW.armed, "and the second addressee is asked about"


async def test_naming_the_missing_pane_delivers_the_original_task(
    manager: BrainManager,
    registry: Registry,
    tmp_path: Path,
    _fake_composer: list[str],
) -> None:
    """"Blake" answers "who else?" with the work the first sentence carried."""
    await _open(registry, tmp_path, 5)
    await manager._run_agentic_ide_fast_path(
        "Alex and Dave should both do a deep dive on the failing tests"
    )

    verdict = await manager._run_agentic_ide_fast_path("Blake")

    assert verdict is not None
    assert "Blake" in verdict
    assert len(_briefs(registry)) == 2
    assert all("deep dive" in text for text in _briefs(registry))


async def test_a_single_addressee_is_never_asked_who_else(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """The plural check must not tax the ordinary one-pane turn."""
    names = await _open(registry, tmp_path, 5)

    reply = await manager._run_agentic_ide_fast_path(
        f"tell {names[0]} to do a deep dive on the failing tests"
    )

    assert reply is not None
    assert not ide_clarify.WINDOW.armed
    assert len(_briefs(registry)) == 1


# ------------------------------------------------------- positional call-signs


async def test_a_spoken_position_is_briefed_without_a_question(
    manager: BrainManager,
    registry: Registry,
    tmp_path: Path,
    _fake_composer: list[str],
) -> None:
    """The default call-signs cannot be garbled, so they cost no clarification.

    This is what the positional scheme buys: "prompt terminal two" carries its
    own certainty — a number is either said or it is not — so the whole
    near-miss loop above simply never runs for it.
    """
    names = await _open_numbered(registry, tmp_path, 4)
    assert names == ["T1", "T2", "T3", "T4"]

    reply = await manager._run_agentic_ide_fast_path(
        "prompt terminal two to do a deep dive on the failing tests"
    )

    assert reply is not None
    assert "T2" in reply
    assert not ide_clarify.WINDOW.armed, "a position is never asked back"
    assert len(_briefs(registry)) == 1
    assert any("deep dive" in seen for seen in _fake_composer)


async def test_a_position_nobody_opened_briefs_no_pane(
    manager: BrainManager, registry: Registry, tmp_path: Path
) -> None:
    """"T7" with four panes open is a wrong number — never the nearest one."""
    await _open_numbered(registry, tmp_path, 4)

    await manager._run_agentic_ide_fast_path(
        "prompt T7 to do a deep dive on the failing tests"
    )

    assert _briefs(registry) == []
