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

from jarvis.missions.critic.runner import CriticRunner
from jarvis.missions.critic.verdict import REQUIRED_AXES


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
