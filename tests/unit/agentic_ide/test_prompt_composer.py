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
    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: (_Writer(), "test"))


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
            "## Task\nReview the wake word detection.\n\n"
            "## Key files\n"
            "- `@jarvis/plugins/wake/vosk_kws_provider.py` - the provider\n"
            "- `@jarvis/does/not/exist.py` - invented by the model"
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

    # A BRIEF inside the fence, not a bare sentence: the composer rejects output
    # with no heading as debris (``looks_like_brief``), so a one-line payload
    # would take the fallback and this would be testing that instead of the
    # unwrapping it is named for.
    brief = "## Task\nReview the wake word detection."

    async def _fenced(**_kwargs: object) -> str:
        return f"```\n{brief}\n```"

    monkeypatch.setattr(prompt_composer, "_llm_compose", _fenced)

    result = await prompt_composer.compose(
        "review the wake word detection", session=workspace, terminal_name="Kai"
    )
    assert result.text == brief
    assert result.composed_by == "llm"


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
        # With no index there are no candidates, and the writer is told so
        # rather than left to guess a path.
        assert "no candidate files matched" in str(kwargs["user_block"])
        return "Review the wake word detection."

    monkeypatch.setattr(prompt_composer, "_llm_compose", _echo)
    result = await prompt_composer.compose(
        "review the vosk wake provider", session=workspace, terminal_name="Kai"
    )
    # The instruction goes out; with no index there is nothing to attach.
    assert "wake" in result.text.lower()
    assert result.files == []


# ------------------------------------------------------- structured output
async def test_the_composed_prompt_is_markdown(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prime(workspace)

    async def _brief(**_kwargs: object) -> str:
        return (
            "## Task\nReview the wake provider.\n\n"
            "## Key files\n- `@jarvis/plugins/wake/vosk_kws_provider.py` - the provider"
        )

    monkeypatch.setattr(prompt_composer, "_llm_compose", _brief)

    result = await prompt_composer.compose(
        "review the vosk wake provider", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "llm"
    assert result.text.startswith("## Task")
    assert "\n" in result.text, "the line structure must survive sanitisation"


async def test_the_writer_gets_the_rules_for_the_detected_kind(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _prime(workspace)
    seen: dict[str, str] = {}

    async def _capture(**kwargs: object) -> str:
        seen["system"] = str(kwargs["system_prompt"])
        return "## Task\nDo it."

    monkeypatch.setattr(prompt_composer, "_llm_compose", _capture)

    await prompt_composer.compose(
        "review the wake provider", session=workspace, terminal_name="Kai"
    )

    assert "REVIEW task" in seen["system"]
    assert "IMPLEMENTATION task" not in seen["system"]


async def test_the_writer_is_shown_an_outline_of_the_candidate_files(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the outline the prompt can only describe code, not name it."""
    provider = (
        Path(workspace.folder) / "jarvis" / "plugins" / "wake" / "vosk_kws_provider.py"
    )
    provider.write_text(
        '"""Vosk keyword spotting."""\n\n\n'
        "def candidate_shape_ok(span: float) -> bool:\n    return True\n",
        encoding="utf-8",
    )
    _prime(workspace)
    seen: dict[str, str] = {}

    async def _capture(**kwargs: object) -> str:
        seen["user"] = str(kwargs["user_block"])
        return "## Task\nDo it."

    monkeypatch.setattr(prompt_composer, "_llm_compose", _capture)

    await prompt_composer.compose(
        "review the vosk wake provider", session=workspace, terminal_name="Kai"
    )

    assert "candidate_shape_ok" in seen["user"]
    assert "Vosk keyword spotting." in seen["user"]


async def test_no_quality_writer_degrades_openly(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rather than silently demoting to whatever small model is left."""
    _prime(workspace)
    monkeypatch.setattr(prompt_composer, "_resolve_writer", lambda: (None, ""))

    result = await prompt_composer.compose(
        "review the vosk wake provider", session=workspace, terminal_name="Kai"
    )

    assert result.composed_by == "fallback"
    assert "quality-tier" in result.note
    assert result.text.startswith("## Task")


async def test_a_prompt_ending_on_a_reference_is_repaired(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trailing @path holds the completion popup open and never submits."""
    _prime(workspace)

    async def _trailing(**_kwargs: object) -> str:
        return (
            "## Task\nReview it.\n\n"
            "## Key files\n- `@jarvis/plugins/wake/vosk_kws_provider.py`"
        )

    monkeypatch.setattr(prompt_composer, "_llm_compose", _trailing)

    result = await prompt_composer.compose(
        "review the vosk wake provider", session=workspace, terminal_name="Kai"
    )

    from jarvis.agentic_ide.prompt_blueprint import ends_on_reference

    assert not ends_on_reference(result.text)


async def test_an_explicitly_pinned_brain_is_used(
    workspace: _Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parameter still works for a caller that means it."""
    _prime(workspace)
    pinned = object()
    seen: dict[str, object] = {}

    async def _capture(**kwargs: object) -> str:
        seen["brain"] = kwargs["brain"]
        return "## Task\nDo it."

    monkeypatch.setattr(prompt_composer, "_llm_compose", _capture)
    monkeypatch.setattr(
        prompt_composer, "_resolve_writer", lambda: pytest.fail("should not resolve")
    )

    await prompt_composer.compose(
        "review it", session=workspace, terminal_name="Kai", brain=pinned
    )

    assert seen["brain"] is pinned


# --------------------------------------------------------------------------
# The deterministic filler pass may not eat words that carry the instruction
# --------------------------------------------------------------------------
#
# `_FILLER_RE` runs language-BLIND — one pattern over German, English and
# Spanish alike — so an entry that is a hesitation sound in one locale and a
# content word in another does not merely risk damage, it guarantees it. Same
# admission rule as `jarvis.dictation.cleanup`: an entry qualifies only if it is
# meaningless in EVERY locale served (2026-07-30).


@pytest.mark.parametrize(
    ("said", "must_survive"),
    [
        # i18n-allow: spoken instructions under test (§1 list #4)
        ("Kümmere dich um den Bug im Login", "um"),
        ("Es geht um die Tests für den Router", "um"),
        ("Also add the missing tests", "Also"),
        ("Make it like the other panel", "like"),
        ("What kind of test should run here", "kind of"),
        ("Der Fehler ist halt im Cache", "halt"),
        ("Mach das Layout eben und ruhig", "eben"),
        ("Abre este archivo y revisa el router", "este"),
    ],
)
def test_a_content_word_is_never_filtered_out(said: str, must_survive: str) -> None:
    assert must_survive in prompt_composer._clean_speech(said)


def test_real_hesitation_sounds_are_still_removed() -> None:
    cleaned = prompt_composer._clean_speech(
        "ähm mach mal die Tests grün"  # i18n-allow: German speech input under test
    )
    assert "ähm" not in cleaned  # i18n-allow: the German filler under test
    assert "Tests" in cleaned
