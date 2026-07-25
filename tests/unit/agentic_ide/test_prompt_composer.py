"""Guards for the spoken-instruction → agent-prompt composer.

The composer's contract is that the instruction ALWAYS gets delivered. A user
with no API key at all, a depleted provider, or a slow one must still see their
words reach the terminal — just phrased less well. So the tests here are mostly
about the unhappy paths: they are the ones that decide whether the feature works
for an arbitrary downloader (§3) or only on a machine like the maintainer's.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from jarvis.agentic_ide import file_index, prompt_composer


@dataclass
class _Profile:
    """Minimal stand-in for ProjectProfile — only what the composer reads."""

    lines: list[str] = field(default_factory=lambda: ["Folder: /work/project"])

    def summary_lines(self) -> list[str]:
        return self.lines


@dataclass
class _Session:
    folder: str
    profile: _Profile = field(default_factory=_Profile)


class _Writer:
    """Stands in for a resolved quality-tier Brain.

    Every test here patches ``_llm_compose``, so this only has to exist — the
    point is that ``compose`` found a model it considers good enough and did
    not take the honest-degradation exit.
    """


@pytest.fixture(autouse=True)
def _writer_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """A quality-tier writer is reachable unless a test says otherwise.

    Without this the composer would resolve the real provider chain, which on a
    machine with no keys returns None and sends every test down the
    deterministic path.
    """
    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: _Writer())


@pytest.fixture()
def workspace(tmp_path: Path) -> _Session:
    (tmp_path / "jarvis" / "plugins" / "wake").mkdir(parents=True)
    (tmp_path / "jarvis" / "plugins" / "wake" / "vosk_kws_provider.py").write_text("x")
    file_index.reset_cache()
    return _Session(folder=str(tmp_path))


def _prime(session: _Session) -> None:
    """Build the index synchronously so the test does not race the thread."""
    file_index._CACHE[str(Path(session.folder))] = file_index.build_index(  # noqa: SLF001
        session.folder
    )


async def test_no_provider_still_delivers_a_cleaned_instruction(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every provider dead the words still reach the agent."""
    _prime(workspace)

    async def _boom(**_kwargs: object) -> str:
        raise RuntimeError("no provider configured")

    monkeypatch.setattr(prompt_composer, "_llm_compose", _boom)

    result = await prompt_composer.compose(
        "kannst du mal ähm bitte den vosk wake provider angucken",  # i18n-allow: German speech input under test
        session=workspace,
        terminal_name="Kai",
    )
    assert result.composed_by == "fallback"
    assert result.text, "the instruction must never be dropped"
    # Speech filler is gone, the task survives.
    assert "ähm" not in result.text  # i18n-allow: the German filler under test
    assert "vosk" in result.text.lower()
    # And the file it pointed at came along.
    assert "@jarvis/plugins/wake/vosk_kws_provider.py" in result.text


async def test_a_timeout_falls_back_rather_than_hanging(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prime(workspace)

    async def _slow(**_kwargs: object) -> str:
        import asyncio

        await asyncio.sleep(60)
        return "never"

    monkeypatch.setattr(prompt_composer, "_llm_compose", _slow)
    monkeypatch.setattr(prompt_composer, "COMPOSE_TIMEOUT_S", 0.05)

    result = await prompt_composer.compose(
        "schau dir den vosk provider an", session=workspace, terminal_name="Kai"
    )
    assert result.composed_by == "fallback"
    assert "timed out" in result.note


async def test_invented_file_references_are_stripped(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hallucinated @path costs the agent a wasted turn — it must not ship."""
    _prime(workspace)

    async def _hallucinate(**_kwargs: object) -> str:
        return (
            "Review the wake word detection. "
            "@jarvis/plugins/wake/vosk_kws_provider.py @jarvis/does/not/exist.py"
        )

    monkeypatch.setattr(prompt_composer, "_llm_compose", _hallucinate)

    result = await prompt_composer.compose(
        "review the wake word detection", session=workspace, terminal_name="Kai"
    )
    assert result.composed_by == "llm"
    assert result.files == ["jarvis/plugins/wake/vosk_kws_provider.py"]
    assert "@jarvis/does/not/exist.py" not in result.text
    # The name survives as plain text — the agent can still read the intent.
    assert "jarvis/does/not/exist.py" in result.text


async def test_a_path_escaping_the_workspace_is_refused(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prime(workspace)

    async def _escape(**_kwargs: object) -> str:
        return "Look at @../../../etc/passwd and @/etc/shadow"

    monkeypatch.setattr(prompt_composer, "_llm_compose", _escape)

    result = await prompt_composer.compose(
        "look at the config", session=workspace, terminal_name="Kai"
    )
    assert result.files == []


async def test_a_fenced_or_quoted_answer_is_unwrapped(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prime(workspace)

    async def _fenced(**_kwargs: object) -> str:
        return "```\nReview the wake word detection.\n```"

    monkeypatch.setattr(prompt_composer, "_llm_compose", _fenced)

    result = await prompt_composer.compose(
        "review the wake word detection", session=workspace, terminal_name="Kai"
    )
    assert result.text == "Review the wake word detection."


async def test_use_llm_false_keeps_the_users_own_wording(workspace: _Session) -> None:
    """The typed prompt bar must not have its wording rewritten behind the user.

    Asserted on the WORDS surviving rather than on the exact string: the prompt
    carries a markdown skeleton around them, and what must not happen is the
    instruction being rephrased, not the skeleton existing.
    """
    _prime(workspace)
    result = await prompt_composer.compose(
        "run the tests", session=workspace, terminal_name="Kai", use_llm=False
    )
    assert result.composed_by == "raw"
    assert "run the tests" in result.text


async def test_an_index_that_is_not_ready_yet_does_not_block(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace whose walk is still running ships without file references."""
    file_index.reset_cache()

    async def _echo(**kwargs: object) -> str:
        assert kwargs["candidates"] == []
        return "Review the wake word detection."

    monkeypatch.setattr(prompt_composer, "_llm_compose", _echo)
    result = await prompt_composer.compose(
        "review the vosk wake provider", session=workspace, terminal_name="Kai"
    )
    # The instruction goes out; with no index there is nothing to attach.
    assert "wake" in result.text.lower()
    assert result.files == []
