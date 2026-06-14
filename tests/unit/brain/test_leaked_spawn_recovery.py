"""Robustness net for provider function-calling leaks (2026-05-24).

Some providers (notably Gemini) intermittently emit a ``spawn_worker``
tool_use block as the response *text* instead of executing it. Without the
recovery path the raw JSON reaches TTS as "Es trat ein Fehler auf" and the
delegated Opus-4.7 sub-agent never runs — even though the brain decided to
delegate. These tests pin the detector + the recovery execution.

Live repro: voice mission "erstelle mir eine Datei test-opus.md …" on
2026-05-24 produced ``[{"type":"tool_use","name":"spawn_worker",…}]`` as the
spoken reply and created no mission.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.brain.manager import (
    BrainManager,
    _extract_leaked_spawn_call,
    _looks_like_tool_use_leak,
    _render_recovered_tool_output,
)
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig

# Canonical fragments of the leak-recovery "couldn't execute" fallback. A
# recovered read tool that produced a usable result must NEVER speak these.
_FAILURE_FRAGMENTS = ("konnte sie aber nicht", "couldn't execute")


def test_extract_detects_leaked_spawn_variants() -> None:
    # Exact shape observed in the live log (list of tool_use blocks).
    assert _extract_leaked_spawn_call(
        '[{"type": "tool_use", "id": "gemini_49a94f6c", '
        '"name": "spawn_worker", "input": {"utterance": "x"}}]'
    ) == {"utterance": "x"}
    # Bare object, no input.
    assert _extract_leaked_spawn_call(
        '{"type":"tool_use","name":"spawn_worker","input":{}}'
    ) == {}
    # Markdown-fenced.
    assert _extract_leaked_spawn_call(
        '```json\n[{"type":"tool_use","name":"spawn_worker",'
        '"input":{"action":"y"}}]\n```'
    ) == {"action": "y"}
    # Prose-wrapped.
    assert _extract_leaked_spawn_call(
        'Klar! {"type":"tool_use","name":"spawn_worker",'
        '"input":{"target":"z"}} erledige ich.'
    ) == {"target": "z"}


def test_extract_ignores_normal_text_and_other_tools() -> None:
    assert _extract_leaked_spawn_call("Mach ich, im Hintergrund.") is None
    assert _extract_leaked_spawn_call("") is None
    # A different tool leak must NOT be treated as a spawn.
    assert _extract_leaked_spawn_call(
        '{"type":"tool_use","name":"wiki-recall","input":{}}'
    ) is None


def _manager_with_fake_spawn(captured: dict) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"

    class _FakeExecutor:
        async def execute(self, tool, args, *, user_utterance, trace_id):  # type: ignore[no-untyped-def]
            captured["args"] = args
            captured["tool"] = tool
            captured["user_utterance"] = user_utterance
            return SimpleNamespace(
                success=True,
                output="Mach ich, ich kümmere mich im Hintergrund darum.",
                error=None,
            )

    fake_tool = SimpleNamespace(name="spawn_worker")
    return BrainManager(
        config=cfg,
        bus=EventBus(),
        tools={"spawn_worker": fake_tool},
        tool_executor=_FakeExecutor(),
    )


@pytest.mark.asyncio
async def test_recover_executes_leaked_spawn_and_returns_ack() -> None:
    captured: dict = {}
    mgr = _manager_with_fake_spawn(captured)
    leaked = (
        '[{"type":"tool_use","name":"spawn_worker",'
        '"input":{"utterance":"erstelle mir eine Datei test-opus.md"}}]'
    )
    out = await mgr._recover_leaked_spawn(
        leaked,
        user_text="erstelle mir eine Datei test-opus.md",
        trace_id=uuid4(),
    )
    assert out == "Mach ich, ich kümmere mich im Hintergrund darum."
    # The leaked utterance is forwarded to the spawn tool.
    assert captured["args"]["utterance"] == "erstelle mir eine Datei test-opus.md"
    assert captured["tool"].name == "spawn_worker"


@pytest.mark.asyncio
async def test_recover_returns_none_for_normal_response() -> None:
    captured: dict = {}
    mgr = _manager_with_fake_spawn(captured)
    out = await mgr._recover_leaked_spawn(
        "Alles erledigt, ich habe das im Hintergrund übernommen.",
        user_text="erstelle mir eine Datei",
        trace_id=uuid4(),
    )
    assert out is None
    assert "args" not in captured  # executor never called


# ---------------------------------------------------------------------------
# Leaked READ-tool (search_web) recovery (live repro 2026-06-14).
#
# "Was hältst du von exp.com?" — an opinion question — made Gemini leak a
# ``search_web`` tool_use block as TEXT. The recovery ran the search and got a
# usable eXp-Realty result, but returned ``str(result.output)`` — and
# search_web's output is a dict, so the string started with ``{``. The
# streaming guard ``_looks_like_tool_use_leak`` then mistook that ANSWER for
# another tool-use leak, dropped it, and the user heard the canned
# "Ich habe die Aktion erkannt, konnte sie aber nicht ausführen." A recovered
# read tool must yield a SPEAKABLE answer, never a raw dict and never the
# failure phrase.
# ---------------------------------------------------------------------------


_SEARCH_OUTPUT = {
    "query": "exp.com",
    "results": [
        {
            "title": "eXp Realty",
            "snippet": "eXp Realty is a cloud-based real estate brokerage.",
            "url": "https://exp.com",
        },
    ],
}


def _manager_with_fake_tool(captured: dict, *, name: str, output: object) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"

    class _FakeExecutor:
        async def execute(self, tool, args, *, user_utterance, trace_id):  # type: ignore[no-untyped-def]
            captured["args"] = args
            captured["tool"] = tool
            return SimpleNamespace(success=True, output=output, error=None)

    fake_tool = SimpleNamespace(name=name)
    return BrainManager(
        config=cfg,
        bus=EventBus(),
        tools={name: fake_tool},
        tool_executor=_FakeExecutor(),
    )


def test_render_recovered_tool_output_renders_search_results_speakable() -> None:
    rendered = _render_recovered_tool_output(_SEARCH_OUTPUT)
    assert "realty" in rendered.lower()
    # Never a {/[ -prefixed repr — that false positive is exactly what dropped
    # the answer on the streaming path.
    assert not _looks_like_tool_use_leak(rendered)


def test_render_recovered_tool_output_passes_through_plain_string() -> None:
    # String-output tools (open_app -> "Gestartet: chrome") stay byte-identical.
    assert _render_recovered_tool_output("Gestartet: chrome") == "Gestartet: chrome"


def test_render_recovered_tool_output_empty_results_is_empty_not_repr() -> None:
    # Empty search -> empty string; the caller supplies the localized
    # 'nothing found' sentence (never a "{...}" repr, never the canned phrase).
    rendered = _render_recovered_tool_output({"query": "x", "results": []})
    assert rendered == ""


def test_render_recovered_tool_output_never_emits_brace_prefixed_repr() -> None:
    # A structured dict with no renderable text fields must still never reach
    # TTS as a "{...}" Python repr.
    rendered = _render_recovered_tool_output({"foo": {"bar": 1}})
    assert not _looks_like_tool_use_leak(rendered)


@pytest.mark.asyncio
async def test_recover_leaked_search_web_speaks_answer_not_failure_phrase() -> None:
    captured: dict = {}
    mgr = _manager_with_fake_tool(captured, name="search_web", output=_SEARCH_OUTPUT)
    leaked = (
        '[{"type":"tool_use","id":"gemini_abc",'
        '"name":"search_web","input":{"query":"exp.com"}}]'
    )
    out = await mgr._recover_leaked_tool(
        leaked, user_text="Was hältst du von exp.com?", trace_id=uuid4(),
    )
    # The search actually ran with the leaked query.
    assert captured["args"]["query"] == "exp.com"
    # The answer is spoken — not the canned failure, not a raw dict.
    assert out is not None
    assert not any(frag in out.lower() for frag in _FAILURE_FRAGMENTS)
    assert not _looks_like_tool_use_leak(out)
    assert "realty" in out.lower()


@pytest.mark.asyncio
async def test_recover_leaked_search_web_empty_results_is_spoken_fallback() -> None:
    captured: dict = {}
    mgr = _manager_with_fake_tool(
        captured, name="search_web", output={"query": "zzz", "results": []},
    )
    leaked = '{"type":"tool_use","name":"search_web","input":{"query":"zzz"}}'
    out = await mgr._recover_leaked_tool(
        leaked, user_text="Was hältst du von zzz?", trace_id=uuid4(),
    )
    assert out is not None
    assert not any(frag in out.lower() for frag in _FAILURE_FRAGMENTS)
    assert not _looks_like_tool_use_leak(out)
    assert out.strip()  # a real spoken sentence, never silence
