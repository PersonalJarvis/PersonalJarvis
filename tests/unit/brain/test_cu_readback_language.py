"""Computer-use offload outcome readbacks must follow the turn's language.

Live bug 2026-06-15 (voice session 20:58): an all-English computer-use turn
("open Chrome ... use computer use") ended with the German completion readback
"Erledigt." The CU offload runs OFF the LLM and was published as
``AnnouncementRequested(text="Erledigt.", language="de")`` regardless of the
turn language. The language is captured at dispatch and threaded into the
background task (it cannot read ``self._turn_detected_lang`` — a later turn may
have overwritten it by the time the harness finishes).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis.brain.manager import BrainManager


class _FakeBus:
    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event) -> None:  # noqa: ANN001
        self.published.append(event)


class _CUExecutor:
    """tool_executor stand-in: a CU harness run with a configurable outcome."""

    def __init__(self, *, success=True, output="", error=None) -> None:
        self.success = success
        self.output = output
        self.error = error

    async def execute(self, tool, args, *, user_utterance, trace_id):  # noqa: ANN001
        return SimpleNamespace(success=self.success, output=self.output, error=self.error)


def _make_manager(executor, bus):
    mgr = BrainManager.__new__(BrainManager)
    mgr._bus = bus
    mgr._tool_executor = executor
    return mgr


def _completion(bus: _FakeBus):
    comps = [e for e in bus.published if getattr(e, "kind", None) == "completion"]
    assert comps, f"no completion announcement published; got {bus.published}"
    return comps[-1]


@pytest.mark.asyncio
async def test_english_success_readback_is_done(monkeypatch) -> None:
    bus = _FakeBus()
    mgr = _make_manager(_CUExecutor(success=True, output=""), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="open chrome",
        timeout_s=180.0, user_text="please open chrome and use computer use",
        trace_id=uuid4(), lang="en",
    )
    comp = _completion(bus)
    assert comp.text == "Done.", comp.text
    assert comp.language == "en"


@pytest.mark.asyncio
async def test_german_success_readback_unchanged(monkeypatch) -> None:
    bus = _FakeBus()
    mgr = _make_manager(_CUExecutor(success=True, output=""), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="öffne chrome",
        timeout_s=180.0, user_text="öffne mir chrome", trace_id=uuid4(), lang="de",
    )
    comp = _completion(bus)
    assert comp.text == "Erledigt."
    assert comp.language == "de"


@pytest.mark.asyncio
async def test_english_failure_readback_localized(monkeypatch) -> None:
    bus = _FakeBus()
    mgr = _make_manager(_CUExecutor(success=False, error="403 credits"), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="open chrome",
        timeout_s=180.0, user_text="open chrome please", trace_id=uuid4(), lang="en",
    )
    comp = _completion(bus)
    assert "Erledigt" not in comp.text
    assert "403 credits" in comp.text
    assert comp.language == "en"


@pytest.mark.asyncio
async def test_bare_exit_code_never_reaches_readback(monkeypatch) -> None:
    """Live bug (Discord/BridgeMind turn): the user HEARD "That didn't work on
    screen: exit 5" and asked "what is the exit file?". A bare ``exit N`` error
    must be mapped to a plain-language sentence — never spoken verbatim."""
    import re

    bus = _FakeBus()
    # dispatch_to_harness composes error="exit 5" for the model's `fail` action.
    mgr = _make_manager(_CUExecutor(success=False, error="exit 5"), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="open discord",
        timeout_s=180.0, user_text="open discord and check the news", trace_id=uuid4(),
        lang="en",
    )
    comp = _completion(bus)
    assert not re.search(r"\bexit\s*\d+\b", comp.text, re.IGNORECASE), comp.text
    assert "screen" in comp.text.lower()
    assert comp.language == "en"


@pytest.mark.asyncio
async def test_harness_detail_reason_is_surfaced_over_exit_code(monkeypatch) -> None:
    """When the harness output carries the model's real `fail` reason (stderr),
    surface that human sentence instead of the opaque ``exit 5``."""
    import re

    bus = _FakeBus()
    # dispatch_to_harness puts exit_code + stderr in output; error stays "exit 5".
    output = {
        "harness": "screenshot",
        "exit_code": 5,
        "stdout": "",
        "stderr": "[cu] fail at step-4: the BridgeMind server has no news channel",
    }
    mgr = _make_manager(_CUExecutor(success=False, output=output, error="exit 5"), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="open discord",
        timeout_s=180.0, user_text="open discord and check the news", trace_id=uuid4(),
        lang="en",
    )
    comp = _completion(bus)
    assert not re.search(r"\bexit\s*\d+\b", comp.text, re.IGNORECASE), comp.text
    assert "BridgeMind server has no news channel" in comp.text


@pytest.mark.asyncio
async def test_cu_failure_announcement_carries_technical_detail() -> None:
    """The spoken text is humanized (no bare 'exit 5'), but the completion
    announcement also carries an optional technical ``detail`` — the exit code
    plus the raw harness reason — so the Transcription view can show it for
    debugging without the user HEARING a cryptic number (user 2026-06-16)."""
    import re

    bus = _FakeBus()
    output = {
        "harness": "screenshot",
        "exit_code": 5,
        "stdout": "",
        "stderr": "[cu] fail at step-4: 5 guard-blocked actions this mission",
    }
    mgr = _make_manager(_CUExecutor(success=False, output=output, error="exit 5"), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="open discord",
        timeout_s=180.0, user_text="open discord and check the news",
        trace_id=uuid4(), lang="en",
    )
    comp = _completion(bus)
    # Voice stays humanized — no bare exit code spoken.
    assert not re.search(r"\bexit\s*\d+\b", comp.text, re.IGNORECASE), comp.text
    # ...but the technical detail is preserved on the announcement for the log.
    assert comp.detail is not None, "failure announcement carries no technical detail"
    assert "exit 5" in comp.detail
    assert "guard-blocked actions" in comp.detail


@pytest.mark.asyncio
async def test_cu_success_announcement_has_no_detail() -> None:
    """A successful run has no failure diagnostic — ``detail`` stays None so the
    transcript shows only the clean completion line."""
    bus = _FakeBus()
    mgr = _make_manager(_CUExecutor(success=True, output=""), bus)
    await mgr._run_computer_use_background(
        tool=object(), harness_name="screenshot", prompt="open chrome",
        timeout_s=180.0, user_text="open chrome", trace_id=uuid4(), lang="en",
    )
    comp = _completion(bus)
    assert getattr(comp, "detail", None) is None
