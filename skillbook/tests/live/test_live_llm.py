"""Live API smoke test — provider-agnostic Reflector + LLM pipeline.

Skipped by default. ``default_llm()`` resolves to whichever brain provider
is configured (explicit ``provider=`` arg, ``SKB_BRAIN_PROVIDER`` env, or
auto-detected from whatever API key is present). At least ONE of these must
be set for the test to run:

  - ``ANTHROPIC_API_KEY`` + ``anthropic`` SDK
  - ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` + ``google-genai`` SDK
  - ``XAI_API_KEY`` / ``GROK_API_KEY`` + ``openai`` SDK (Grok uses
    OpenAI-compatible API)
  - ``OPENAI_API_KEY`` + ``openai`` SDK

If none are available, both tests skip. This validates that the production
LLM adapter + Reflector subprocess pipeline survives real, variable LLM
output — independent of which provider is active.

Invoke:

    pytest skillbook/tests/live/ -v -m live
"""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

from skillbook.memory_layer.models import TraceStep
from skillbook.memory_layer.store import SQLiteMemoryStore


def _has_module(name: str) -> bool:
    """Safe wrapper for ``importlib.util.find_spec`` that returns False if
    any parent package in the dotted name is missing."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _any_provider_available() -> tuple[bool, str]:
    """Return (available, reason). Available means at least one provider has
    both its env key and its SDK installed."""
    checks = [
        (
            "claude",
            bool(os.environ.get("ANTHROPIC_API_KEY")),
            _has_module("anthropic"),
        ),
        (
            "gemini",
            bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")),
            _has_module("google.genai"),
        ),
        (
            "grok",
            bool(os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")),
            _has_module("openai"),
        ),
        (
            "openai",
            bool(os.environ.get("OPENAI_API_KEY")),
            _has_module("openai"),
        ),
    ]
    for name, has_key, has_sdk in checks:
        if has_key and has_sdk:
            return True, f"using {name}"
    parts = [f"{n}(key={k},sdk={s})" for n, k, s in checks]
    return False, "no provider available: " + ", ".join(parts)


_AVAILABLE, _REASON = _any_provider_available()


@pytest.mark.live
@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
async def test_default_llm_round_trips_with_active_provider() -> None:
    """Real API call against whichever provider is configured."""
    from skillbook.ace_core.llm import default_llm

    llm = default_llm()
    response = await llm.complete(
        "Reply with exactly the single word OK and nothing else.",
        max_tokens=10,
    )
    assert isinstance(response, str)
    assert len(response) > 0, "active provider returned an empty response"
    assert "OK" in response.upper(), (
        f"expected 'OK' marker in response; got {response!r}"
    )


@pytest.mark.live
@pytest.mark.skipif(not _AVAILABLE, reason=_REASON)
async def test_reflector_with_real_llm_produces_valid_verdict(tmp_path: Path) -> None:
    """End-to-end: real LLM generates Python analysis code, the Reflector
    sandbox executes it, the parent parses a Verdict. Proves the production
    pipeline survives non-deterministic LLM output regardless of provider."""
    from skillbook.ace_core.llm import default_llm
    from skillbook.ace_core.reflector import RecursiveReflector

    mem = SQLiteMemoryStore(db_path=tmp_path / "live.db")
    await mem.open()
    try:
        await mem.put_trace_step(
            TraceStep(
                task_id="t_live",
                step_idx=0,
                actor="ip_symcon_dimmer_42",
                params={"intensity": 0.7},
                result={"error": "timeout"},
                status="BLOCKED_BY_GUARDRAIL",
                ts_ns=time.time_ns(),
            )
        )

        reflector = RecursiveReflector(memory=mem, llm=default_llm(), timeout_s=30.0)
        verdict = await reflector.reflect(task_id="t_live")

        assert verdict.outcome in ("failure", "no_action"), (
            f"LLM-generated code produced unexpected outcome: {verdict.outcome!r}"
        )
        assert isinstance(verdict.evidence, str)
        if verdict.outcome == "failure":
            assert verdict.rule is not None, (
                "failure verdict must carry a rule per the schema"
            )
            assert "trigger" in verdict.rule
            assert "strategy" in verdict.rule
    finally:
        await mem.close()
