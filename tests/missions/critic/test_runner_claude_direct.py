"""Coverage for the ClaudeDirectCritic path (CRIT-1, 2026-05-17).

Audit-Team 10 verdict on 2026-05-17 (Audit-2 RED): the live OpenClaw
critic spawn has been failing 100 % of voice missions since 13:14 today
because OpenClaw 2026.5.7 silently ignores the
``agents.defaults.cliBackends["claude-cli"]`` override our
``_ensure_critic_agent_registered`` helper writes, and falls back to the
``anthropic`` Messages-API backend that needs paid extra-usage credits the
user doesn't have. CRIT-1 mirrors what BUG-023 did for the worker: when
``[brain.sub_jarvis].provider`` resolves to ``claude-api`` we spawn
``claude --print`` directly, bypassing OpenClaw entirely.

These tests pin the contract so the path stays exercised even after the
existing OpenClaw fakes patch the resolver to a non-claude provider.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jarvis.missions.critic.runner import (
    CriticRunner,
    _iter_balanced_json_objects,
    _strip_json_fences,
    _validate_verdict_tolerant,
)
from jarvis.missions.critic.verdict import (
    REQUIRED_AXES,
    CriticVerdict,
    is_approval_valid,
)


def _valid_verdict_json(verdict: str = "approve") -> str:
    """A schema-valid CriticVerdict as the raw text claude --print prints.

    Unlike the OpenClaw path we do NOT wrap this in ``{"payloads": [...]}``
    — claude --print outputs the model's reply verbatim, so the entire
    stdout IS the JSON object the prompt asked for.
    """
    return json.dumps({
        "verdict": verdict,
        "axes": {
            ax: {"status": "pass", "evidence": ["src/x.py:1"]}
            for ax in REQUIRED_AXES
        },
        "issues": [],
        "correction_instruction": "" if verdict == "approve" else "fix x",
        "summary": "ok" if verdict == "approve" else "needs fix",
        "summary_de": "ok" if verdict == "approve" else "muss korrigiert werden",
        "confidence": 0.9,
        "suggested_next_action": "accept" if verdict == "approve" else "retry",
    })


class _FakeStdin:
    """Minimal stdin stub: collects writes, no-op drain/close."""

    def __init__(self) -> None:
        self.written: bytes = b""

    def write(self, data: bytes) -> None:
        self.written += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode
        self.stdin = _FakeStdin()
        self.pid = 4242

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


def _patch_direct(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout: str | bytes,
    returncode: int = 0,
) -> dict[str, Any]:
    """Force the claude-direct branch and capture the spawn call.

    The returned dict gains an ``argv`` and a ``stdin`` key after the
    subprocess is invoked.
    """
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: ("claude-api", "claude-sonnet-4-6"),
    )
    # Pin the binary so the test does not depend on a real `claude` shim
    # being on PATH on the developer machine.
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker."
        "_resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["argv"] = list(args)
        captured["kwargs"] = kwargs
        out = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
        proc = _FakeProc(out, returncode=returncode)
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)
    return captured


@pytest.mark.asyncio
async def test_claude_direct_path_approves_when_resolver_picks_claude_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """When ``provider==claude-api``, the critic must spawn claude --print
    directly instead of routing through OpenClaw."""
    captured = _patch_direct(monkeypatch, stdout=_valid_verdict_json("approve"))

    verdict = await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    assert verdict.verdict == "approve"
    argv = captured["argv"]
    assert argv[0] == "claude", argv
    assert "--print" in argv
    # 2026-05-24: was "plan" — plan-mode made claude --print emit
    # ExitPlanMode meta-prose instead of JSON, failing every critic.
    # bypassPermissions (same as the worker) makes claude answer directly;
    # the critic stays read-only by prompt intent (diff is in the prompt).
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    # Worktree must be exposed so claude can resolve relative paths.
    assert argv[argv.index("--add-dir") + 1] == str(tmp_path)
    # Model from the resolver — must be wired through.
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-6"
    # OpenClaw must NOT have been spawned -- no openclaw.json gets written.
    assert not (tmp_path / "openclaw.json").exists()


@pytest.mark.parametrize("provider,foreign_model", [
    ("grok", "grok-4.3"),
    ("gemini", "gemini-3.1-pro-preview"),
    ("openrouter", "anthropic/claude-sonnet"),
])
@pytest.mark.asyncio
async def test_non_claude_provider_critic_uses_claude_model_not_foreign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    provider: str, foreign_model: str,
) -> None:
    """Regression (2026-06-08): with a non-claude [brain.sub_jarvis].provider the
    critic correctly falls back to the direct claude critic — but it MUST pass a
    claude model to ``claude --model``, never the foreign provider model
    (e.g. ``grok-4.3``), which the claude CLI rejects with returncode=1. That
    failed the critic twice -> ``critic_unavailable`` and the whole mission
    FAILED even though the worker had delivered real work (the sibling of the
    ClaudeDirectWorker provider-refusal bug, same root cause in the critic path).
    """
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: (provider, foreign_model),
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker."
        "_resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        captured["argv"] = list(args)
        return _FakeProc(
            _valid_verdict_json("approve").encode("utf-8"), returncode=0,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    assert verdict.verdict == "approve"
    argv = captured["argv"]
    # The critic must fall back to the direct claude critic, not OpenClaw.
    assert argv[0] == "claude", argv
    # The foreign provider model must NEVER reach `claude --model`.
    assert foreign_model not in argv, f"foreign model leaked into claude argv: {argv}"
    model_arg = argv[argv.index("--model") + 1]
    assert model_arg.startswith("claude") or model_arg in {"sonnet", "opus", "haiku"}, (
        f"--model must be a claude critic model, got {model_arg!r}"
    )


@pytest.mark.asyncio
async def test_claude_direct_pipes_prompt_through_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The rendered critic prompt + JSON schema must reach the subprocess
    on stdin (not as a CLI argument). This is what protects us from
    long-prompt-cutoff bugs on Windows command-line length limits."""
    captured = _patch_direct(monkeypatch, stdout=_valid_verdict_json("approve"))

    await CriticRunner().run(
        mission_prompt="UNIQUE_MISSION_MARKER_X9Q",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    stdin_bytes = captured["proc"].stdin.written
    assert b"UNIQUE_MISSION_MARKER_X9Q" in stdin_bytes
    # The JSON-schema contract must also reach stdin.
    assert b"Output contract" in stdin_bytes
    # Nothing leaked to argv.
    argv = " ".join(captured["argv"])
    assert "UNIQUE_MISSION_MARKER_X9Q" not in argv


@pytest.mark.asyncio
async def test_claude_direct_strips_markdown_fences(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Sonnet sometimes wraps the JSON object in ``` fences despite the
    prompt's 'no markdown' instruction. The parse path must strip them."""
    fenced = "```json\n" + _valid_verdict_json("approve") + "\n```"
    _patch_direct(monkeypatch, stdout=fenced)

    verdict = await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "approve"


@pytest.mark.asyncio
async def test_claude_direct_invalid_json_triggers_adversarial_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """First call returns garbage -> JSON parse fails -> Runner retries
    with the adversarial reframe -> second call succeeds."""
    call_count = {"n": 0}

    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: ("claude-api", "claude-sonnet-4-6"),
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker."
        "_resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeProc(b"not even close to json", returncode=0)
        return _FakeProc(
            _valid_verdict_json("approve").encode("utf-8"), returncode=0,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "approve"
    assert call_count["n"] == 2  # first parse fail, then retry


@pytest.mark.asyncio
async def test_claude_direct_nonzero_exit_returns_none_for_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If the binary itself exits non-zero (e.g. OAuth token expired),
    the runner treats it as a parse failure and triggers the adversarial
    retry path -- consistent with the OpenClaw branch."""
    call_count = {"n": 0}

    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: ("claude-api", "claude-sonnet-4-6"),
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker."
        "_resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeProc(b"", returncode=1)
        return _FakeProc(
            _valid_verdict_json("approve").encode("utf-8"), returncode=0,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "approve"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_claude_direct_recovers_verdict_from_agent_narration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Under ``--permission-mode bypassPermissions`` claude runs as an agent:
    it verifies the worker diff against the on-disk worktree with Read/Glob,
    then narrates its findings *before* emitting the JSON verdict
    ("Issuing the JSON verdict: {...}"). The parse path must recover the
    verdict object from the surrounding prose -- a single sentence ahead of
    the ``{`` previously failed every such mission with CriticSchemaInvalid
    and showed "error" in the Outputs view (live repro mission_019e5966,
    2026-05-24)."""
    narrated = (
        "Direct verification complete. I ran a `Read` of "
        "`outputs_final_proof.md` and a recursive `Glob` for "
        "`**/outputs_final_proof.md` -- the file exists on disk with the "
        "exact content the worker diff claims. Note: the worker also created "
        "an unrelated `{scratch}` marker, ignored. Issuing the JSON verdict:\n"
        + _valid_verdict_json("approve")
    )
    _patch_direct(monkeypatch, stdout=narrated)

    verdict = await CriticRunner().run(
        mission_prompt="Create outputs_final_proof.md",
        worker_diff="+outputs land here",
        worker_log="file_change",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "approve"


@pytest.mark.asyncio
async def test_claude_direct_skips_openclaw_json_materialisation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The direct path must NOT write openclaw.json -- that file only
    exists for the OpenClaw branch. A stray openclaw.json in the
    state-dir was the symptom that misled us into thinking the worker
    was using the right backend on 2026-05-16."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    _patch_direct(monkeypatch, stdout=_valid_verdict_json("approve"))

    await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="log",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={"MISSION_STATE_DIR": str(state_dir)},
    )
    assert not (state_dir / "openclaw.json").exists(), (
        "claude-direct branch must not touch the OpenClaw state dir"
    )


# ---------------------------------------------------------------------------
# Over-long TTS summary regression (mission 019e7f6d, 2026-05-31 21:08/21:09).
#
# The critic returned a fully valid `approve` verdict over a rich 597-line
# HTML deliverable, but `summary` (322 chars) and `summary_de` (322 chars)
# exceeded the `max_length=280` TTS cap on `CriticVerdict`. Pydantic rejected
# the whole object on BOTH the first attempt and the adversarial-reframe
# retry, the runner returned None twice, and the mission was marked
# `critic_unavailable` -- discarding the worker's real work. The richer the
# worker output, the longer the critic summary prose, so this false-negative
# bites MORE often as worker quality improves.
# ---------------------------------------------------------------------------


def _verdict_dict(
    *,
    verdict: str = "approve",
    summary: str = "ok",
    summary_de: str = "ok",
) -> dict:
    """A schema-valid CriticVerdict dict with configurable summary fields."""
    return {
        "verdict": verdict,
        "axes": {
            ax: {"status": "pass", "evidence": ["src/x.py:1"]}
            for ax in REQUIRED_AXES
        },
        "issues": [],
        "correction_instruction": "",
        "summary": summary,
        "summary_de": summary_de,
        "confidence": 0.92,
        "suggested_next_action": "accept",
    }


class TestValidateVerdictTolerant:
    """`_validate_verdict_tolerant` truncates only the over-long TTS summary
    fields and never weakens any other schema check."""

    def test_within_cap_is_unchanged(self) -> None:
        payload = json.dumps(_verdict_dict(summary="short", summary_de="kurz"))
        verdict = _validate_verdict_tolerant(payload)
        assert verdict.verdict == "approve"
        assert verdict.summary == "short"
        assert verdict.summary_de == "kurz"

    def test_over_long_summary_is_truncated_not_rejected(self) -> None:
        long_en = "A" * 322  # the real mission's summary length
        long_de = "B" * 322  # the real mission's summary_de length
        payload = json.dumps(_verdict_dict(summary=long_en, summary_de=long_de))
        verdict = _validate_verdict_tolerant(payload)
        assert verdict.verdict == "approve"
        assert len(verdict.summary) <= 280
        assert len(verdict.summary_de) <= 280
        assert verdict.summary.startswith("AAA")
        assert verdict.summary_de.startswith("BBB")

    def test_only_summary_de_over_long(self) -> None:
        payload = json.dumps(_verdict_dict(summary="fine", summary_de="C" * 400))
        verdict = _validate_verdict_tolerant(payload)
        assert verdict.summary == "fine"
        assert len(verdict.summary_de) <= 280

    def test_other_validation_error_still_raises(self) -> None:
        # Missing the required `confidence` field -- NOT a summary problem.
        bad = _verdict_dict()
        del bad["confidence"]
        bad["summary"] = "Z" * 400  # also over-long, but a real error coexists
        with pytest.raises(ValueError):
            _validate_verdict_tolerant(json.dumps(bad))

    def test_bad_enum_still_raises(self) -> None:
        bad = _verdict_dict()
        bad["suggested_next_action"] = "not_a_real_action"
        bad["summary"] = "Z" * 400
        with pytest.raises(ValueError):
            _validate_verdict_tolerant(json.dumps(bad))

    def test_invalid_json_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            _validate_verdict_tolerant("{ this is not json")


def _parse_like_claude_direct(raw: str) -> CriticVerdict | None:
    """Mirror `_invoke_via_claude_direct`'s fast + recovery parse path."""
    cleaned = _strip_json_fences(raw.strip())
    try:
        return _validate_verdict_tolerant(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass
    for candidate in reversed(_iter_balanced_json_objects(raw)):
        try:
            return _validate_verdict_tolerant(_strip_json_fences(candidate))
        except (json.JSONDecodeError, ValueError):
            continue
    return None


@pytest.mark.asyncio
async def test_claude_direct_accepts_verdict_with_over_long_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """End-to-end reproduction of mission 019e7f6d: a real approve verdict
    whose summary fields exceed the TTS cap must be ACCEPTED (truncated), not
    rejected into critic_unavailable."""
    over_long = json.dumps(
        _verdict_dict(
            summary=(
                "Single self-contained static HTML page on Synthwave with 8 "
                "well-structured sections covering definition, history, "
                "sub-genres, artists, and influence. All content factually "
                "accurate, valid HTML5, no security concerns. Minor: inline "
                "CSS instead of external sheet (acceptable for single-file "
                "deliverable)."
            ),
            summary_de=(
                "Die Datei synthwave.html enthaelt eine vollstaendige, "
                "sachlich korrekte HTML-Seite ueber Synthwave mit acht klar "
                "gegliederten Abschnitten zu Definition, Geschichte, "
                "Sub-Genres, Kuenstlern und Einfluss. Sauberes, valides "
                "HTML5 ohne Sicherheitsprobleme. Kleiner Hinweis: Inline-CSS "
                "statt externer Datei, bei Einzeldatei aber akzeptabel und "
                "nicht blockierend."
            ),
        )
    )
    # Sanity: the fixture must actually exceed the cap (otherwise the test is
    # vacuous and would pass even with the bug present).
    _d = json.loads(over_long)
    assert len(_d["summary"]) > 280
    assert len(_d["summary_de"]) > 280

    _patch_direct(monkeypatch, stdout=over_long)

    verdict = await CriticRunner().run(
        mission_prompt="Create an HTML page about Synthwave",
        worker_diff="+<!DOCTYPE html> ... 597 lines",
        worker_log="file_change synthwave.html",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "approve"
    assert len(verdict.summary) <= 280
    assert len(verdict.summary_de) <= 280


@pytest.mark.asyncio
async def test_claude_direct_recovers_over_long_summary_from_narration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The recovery (balanced-object) path must also tolerate an over-long
    summary when the verdict is preceded by agent narration."""
    over_long_obj = json.dumps(_verdict_dict(summary="D" * 350))
    narrated = (
        "Direct verification complete. Issuing the JSON verdict:\n"
        + over_long_obj
    )
    _patch_direct(monkeypatch, stdout=narrated)

    verdict = await CriticRunner().run(
        mission_prompt="Build X",
        worker_diff="diff",
        worker_log="file_change",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )
    assert verdict.verdict == "approve"
    assert len(verdict.summary) <= 280


@pytest.mark.asyncio
async def test_critic_unavailable_model_retries_without_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Live mission 019ec61b (2026-06-14): the critic model FRONTIER_MODEL=
    claude-fable-5 is approved-access-only; the CLI rejects it. The critic
    must retry without --model (CLI default) rather than fail the mission.

    Tests ``_invoke_via_claude_direct`` directly so the model arg is forced
    verbatim (``run()`` resolves it via ``primary_model or model``)."""
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_argv_prefix",
        lambda: ["claude"],
    )

    spawns: list[list[str]] = []

    async def fake(*args: Any, **kwargs: Any) -> _FakeProc:
        spawns.append(list(args))
        if len(spawns) == 1:
            return _FakeProc(b"Claude Fable 5 is currently unavailable.", returncode=1)
        return _FakeProc(_valid_verdict_json("approve").encode("utf-8"), returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake)

    verdict = await CriticRunner()._invoke_via_claude_direct(
        prompt="grade this", worktree=tmp_path, env={},
        model="claude-fable-5", iteration=0, adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"
    assert len(spawns) == 2, "must retry exactly once on model-unavailable"
    assert "--model" in spawns[0] and "claude-fable-5" in spawns[0]
    assert "--model" not in spawns[1], "retry must omit --model (CLI default)"


# --- conversational / informational task (empty diff, no tools) -------------


@pytest.mark.asyncio
async def test_pure_question_no_files_is_approved_not_revised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Live mission 019ec638 (2026-06-14): "which city would you recommend for
    a trip to Australia?" produced a correct text answer but no file, so the
    empty-diff veto rejected it 3x -> critic_loop_exhausted -> FAILED. A pure
    question's deliverable IS the answer: the pre-gate must approve it
    deterministically, WITHOUT spawning an LLM."""
    def _boom(*_a: Any, **_k: Any):
        raise AssertionError("pre-gate must not spawn a subprocess for a question")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    worker_log = json.dumps({
        "type": "result",
        "result": "I'd recommend Sydney as your first stop, then Melbourne.",
        "subtype": "success",
    })

    verdict = await CriticRunner().run(
        mission_prompt=(
            "Could you please tell me which city you would recommend if I "
            "would like to book a trip to Australia?"
        ),
        worker_diff="",
        worker_log=worker_log,
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    assert verdict.verdict == "approve"
    assert is_approval_valid(verdict), "approval must carry evidence on every axis"


@pytest.mark.asyncio
async def test_advisory_trip_planning_no_files_is_approved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Live missions 019ec66c/019ec674/019ec708 (2026-06-14): "plan/book a trip"
    failed critic_loop_exhausted — an advisory task whose deliverable is a text
    plan, not a file. The pre-gate must approve it deterministically (the plan IS
    the deliverable), exactly like a question."""
    def _boom(*_a: Any, **_k: Any):
        raise AssertionError("advisory pre-gate must not spawn a subprocess")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    worker_log = json.dumps({
        "type": "result",
        "result": (
            "Here's a 9-day London-to-Taiwan itinerary: fly LHR-TPE, 3 nights "
            "Taipei (Taipei 101, Jiufen), 2 nights Tainan, 2 nights Taroko Gorge…"
        ),
        "subtype": "success",
    })

    verdict = await CriticRunner().run(
        mission_prompt=(
            "I would like you to spawn a sub-agent which will help me plan a "
            "trip from London to Taiwan."
        ),
        worker_diff="",
        worker_log=worker_log,
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    assert verdict.verdict == "approve"
    assert is_approval_valid(verdict)


@pytest.mark.asyncio
async def test_do_task_with_no_files_still_revised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The conversational-answer relaxation must NOT reopen the hallucination
    hole: a DO-task ("create a file") that produced no diff and no tool calls —
    only a 'done' claim — is still deterministically revised."""
    def _boom(*_a: Any, **_k: Any):
        raise AssertionError("do-task veto must not spawn a subprocess either")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    worker_log = json.dumps({
        "type": "result",
        "result": "I have created the file report.md.",
        "subtype": "success",
    })

    verdict = await CriticRunner().run(
        mission_prompt="Create a file report.md with the analysis.",
        worker_diff="",
        worker_log=worker_log,
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
    )

    assert verdict.verdict == "revise"
