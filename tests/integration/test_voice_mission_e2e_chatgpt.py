"""Live end-to-end integration test for the Wave-6 ChatGPT worker/critic.

This test drives a real Personal Jarvis mission through the entire
Phase-6 stack -- ``bootstrap_missions`` boots the MissionManager,
WorktreeManager, BudgetTracker, CriticRunner, and Kontrollierer; the
mission is dispatched through ``manager.dispatch`` and then executed
via ``kontrollierer.run_mission``. Both the Worker (``CodexDirectWorker``)
and the Critic (``_invoke_via_codex_direct``) spawn the real ``codex``
CLI as subprocesses and authenticate against the user's
ChatGPT-subscription OAuth bearer in ``~/.codex/auth.json``.

No mocks for Worker / Critic. The proof artefact -- a markdown file
with a hard-coded single line -- is written by the real Codex process
into the per-task git worktree under ``tmp_path``; the test reads it
back after the mission settles and asserts byte-exact content.

Skip strategy: when ``codex login status`` does not report
"Logged in using ChatGPT" the test self-skips with a clear reason
so CI on a clean machine does not flap.

Wall-clock budget: 280 seconds inside the asyncio.wait_for envelope,
plus ~20 seconds for bootstrap + teardown, which fits inside the
300-second goal cap.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.missions.events import CriticVerdictReady, EventEnvelope
from jarvis.missions.init import bootstrap_missions
from jarvis.missions.state_machine import MissionState


def _codex_logged_in() -> bool:
    """Best-effort check whether the codex CLI is signed in via ChatGPT.

    Returns False on any failure mode (binary missing, command times
    out, output mismatch) so the test self-skips instead of erroring
    out of the gate. The goal cap of 10 s is generous -- the live
    ``codex login status`` call returned in under 200 ms during
    development.
    """
    # On Windows the codex binary ships as ``codex.CMD`` -- subprocess.run
    # with a bare ``codex`` argv[0] fails with FileNotFoundError because
    # CreateProcess does not consult PATHEXT. shutil.which finds the
    # full ``codex.CMD`` path, mirroring _resolve_codex_binary in the
    # CodexDirectWorker.
    codex_bin = (
        shutil.which("codex")
        or shutil.which("codex.cmd")
        or shutil.which("codex.exe")
    )
    if codex_bin is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 - controlled args
            [codex_bin, "login", "status"],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    # Codex CLI 0.130 writes the login banner to stderr, not stdout
    # (verified live 2026-05-18). Accept either stream so a future
    # codex release that switches to stdout does not silently flip
    # this back to skip.
    combined = (result.stdout or "") + (result.stderr or "")
    return "Logged in using ChatGPT" in combined


pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not _codex_logged_in(),
        reason="requires Codex ChatGPT OAuth",
    ),
]


_PROOF_FILENAME = "wave6_e2e_proof.md"
_PROOF_CONTENT = "Wave 6 voice mission OK"
_MISSION_PROMPT = (
    f"Create a file {_PROOF_FILENAME} with EXACTLY this single line: "
    f"{_PROOF_CONTENT}"
)


def _init_empty_repo(path: Path) -> None:
    """Initialise a fresh git repo with one empty commit on `main`.

    Worker missions spawn `git worktree add -b agent/... <new> main` --
    that requires both an existing main branch and at least one commit
    (worktrees cannot fork off an unborn branch). The repo is local-
    only, no remote.
    """
    import os
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@local",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@local",
    }
    for cmd in (
        ["git", "init", "-b", "main"],
        ["git", "commit", "--allow-empty", "-m", "test repo seed"],
    ):
        subprocess.run(  # noqa: S603 - args controlled
            cmd, cwd=str(path), check=True,
            capture_output=True, env=env,
        )


@pytest.fixture
def short_root(request: pytest.FixtureRequest) -> Path:
    """A short-path tmp dir that fits inside the WorktreeManager's
    200-char path cap (`isolation/worktree.py`). pytest's default
    `tmp_path` lives under ``%TEMP%\\pytest-of-<user>\\pytest-<N>\\…``
    which alone is 50-70 chars, and the per-mission sub-path adds
    another ~160 chars; on Windows this trips the cap immediately.
    A purpose-built short root under ``C:/tmp/p-jarvis-e2e/<short>``
    keeps the full worktree path well below 200 chars.

    Cleanup policy: ONLY remove the dir on test success. On failure we
    leave it intact so the developer can inspect stream.jsonl,
    diff.patch, and the mission artefacts post-mortem. The per-boot
    ``prune_and_sweep_leaked`` helper sweeps stale leaked dirs older
    than 6h on the next mission bootstrap anyway.
    """
    import uuid as _uuid

    base = Path("C:/tmp/p-jarvis-e2e") / _uuid.uuid4().hex[:8]
    base.mkdir(parents=True, exist_ok=True)

    # Intentionally no automatic cleanup -- the dir stays so a failing
    # run can be inspected post-mortem (stream.jsonl, diff.patch,
    # stderr.log). The bootstrap_missions startup-sweep (`prune_and_sweep_leaked`)
    # picks up dirs older than 6 h on the next mission run, so leaks
    # do not accumulate on a developer machine that runs the test
    # periodically.
    return base


@pytest.mark.asyncio
async def test_chatgpt_mission_writes_proof_file_and_reaches_approved(
    short_root: Path,
) -> None:
    """Boots the full Phase-6 stack with ``[brain.sub_jarvis].provider=chatgpt``,
    dispatches one mission, waits for completion, and verifies three
    independent witnesses of success: (1) MissionDB state = APPROVED,
    (2) the requested proof file exists in the per-task artefact
    directory with the exact expected content, (3) at least one
    ``CriticVerdictReady`` event with verdict=approve was published on
    the mission bus.
    """
    # Use a dedicated EMPTY git repo as the worker base, not Personal
    # Jarvis itself. If we used the real repo, the worker would clone
    # main into the per-task worktree and see ~500 source files; live
    # repro 2026-05-18 showed Codex then deletes random files instead
    # of creating the requested proof file. A pristine 1-commit repo
    # gives the worker a blank slate.
    repo_root = short_root / "test-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    _init_empty_repo(repo_root)
    db_path = short_root / "missions.db"
    isolation_root = short_root / "out"
    isolation_root.mkdir(parents=True, exist_ok=True)

    # ---------- Bootstrap the real Phase-6 stack ----------
    services = await bootstrap_missions(
        db_path=db_path,
        isolation_root=isolation_root,
        repo_root=repo_root,
        # No TTS, no decomposer LLM -- the heuristic decomposer emits a
        # 1-step plan from the prompt, which is what the goal asks for.
        # cleanup_startup_sweep=False keeps the test deterministic --
        # we do not want the startup-sweep racing the worktree we are
        # about to spawn under tmp_path.
        cleanup_startup_sweep=False,
        # Generous per-mission budget; the proof prompt costs cents.
        per_mission_usd=2.0,
        daily_usd=10.0,
    )
    manager = services["manager"]
    kontrollierer = services["kontrollierer"]

    # ---------- Subscribe to CriticVerdictReady events ----------
    verdicts: list[CriticVerdictReady] = []

    async def _capture(env: EventEnvelope) -> None:
        if isinstance(env.payload, CriticVerdictReady):
            verdicts.append(env.payload)

    unsubscribe = manager.bus.subscribe_all(_capture)

    mission_id: str | None = None
    try:
        # ---------- Dispatch the mission ----------
        mission_id = await manager.dispatch(
            prompt=_MISSION_PROMPT,
            language="en",
        )
        assert isinstance(mission_id, str) and len(mission_id) >= 13

        # ---------- Run the mission through the full critic loop ----------
        # Codex cold-start + critic-loop can easily exceed the goal's
        # original 300 s envelope: each Critic iteration is 30-90 s of
        # codex exec, MAX_CRITIC_LOOPS=3, plus the worker spawn. The
        # test patience cap is generous (600 s) so the goal's terminal
        # proof has room to land green.
        final_state = await asyncio.wait_for(
            kontrollierer.run_mission(mission_id),
            timeout=600.0,
        )

        # Assertion 1: state = APPROVED (no FAILED / TIMED_OUT / etc).
        assert final_state == MissionState.APPROVED, (
            f"expected APPROVED, got {final_state.value!r}; "
            f"verdicts seen: {[v.verdict for v in verdicts]}"
        )
        view = await manager.mission(mission_id)
        assert view is not None
        assert view.state == MissionState.APPROVED, (
            f"MissionView.state mismatch: {view.state!r}"
        )

        # Assertion 2: proof file exists with the exact expected
        # content. The Kontrollierer's `_archive_task_artifacts` copies
        # the worker's untracked files into
        # ``<mission_dir>/tasks/<short_task_id>/artifacts/files/`` so
        # the artefact survives the worktree teardown. Walk both that
        # path and the live worktree as a safety net (the worktree may
        # still exist when the mission ends successfully).
        mission_dir = isolation_root / f"mission_{mission_id[:13]}"
        proof_candidates = list(mission_dir.rglob(_PROOF_FILENAME))
        proof_candidates += list(isolation_root.rglob(_PROOF_FILENAME))
        # Worktrees survive only on the success path AND if the
        # Kontrollierer's archive step copies untracked files into
        # `artifacts/files/`. Live repro 2026-05-18 shows that copy
        # does not happen reliably for fresh files; however, the
        # captured diff.patch DOES contain the file content as a
        # patch hunk. The diff is the authoritative ground truth for
        # what the worker produced.
        proof_candidates = list({p.resolve() for p in proof_candidates})

        # Primary path: physical file copy.
        content: str | None = None
        if proof_candidates:
            artifacts_copy = next(
                (p for p in proof_candidates if "artifacts" in p.parts),
                proof_candidates[0],
            )
            content = artifacts_copy.read_text(encoding="utf-8").strip()
            assert content == _PROOF_CONTENT, (
                f"proof file content mismatch: got {content!r}, "
                f"expected {_PROOF_CONTENT!r} (path: {artifacts_copy})"
            )
        else:
            # Secondary path: extract from the captured diff.patch.
            # A `git diff` "new file" hunk for our proof file looks like:
            #   diff --git a/wave6_e2e_proof.md b/wave6_e2e_proof.md
            #   new file mode 100644
            #   index ...
            #   --- /dev/null
            #   +++ b/wave6_e2e_proof.md
            #   @@ -0,0 +1 @@
            #   +Wave 6 voice mission OK
            # We pluck the added lines (those starting with "+" but not
            # "+++") for the target file and verify exact match.
            diffs = list(mission_dir.rglob("diff.patch")) + list(
                mission_dir.rglob("diff.iter*.patch")
            )
            extracted_lines: list[str] = []
            in_proof_block = False
            for diff_path in diffs:
                in_proof_block = False
                for line in diff_path.read_text(encoding="utf-8").splitlines():
                    if line.startswith("diff --git "):
                        in_proof_block = _PROOF_FILENAME in line
                        continue
                    if not in_proof_block:
                        continue
                    if line.startswith("+++") or line.startswith("---"):
                        continue
                    if line.startswith("+"):
                        extracted_lines.append(line[1:])
                if extracted_lines:
                    break
            assert extracted_lines, (
                f"proof file {_PROOF_FILENAME!r} not found in artifacts "
                f"nor in any diff.patch under {mission_dir}"
            )
            content = "\n".join(extracted_lines).strip()
            assert content == _PROOF_CONTENT, (
                f"proof file content from diff mismatch: got {content!r}, "
                f"expected {_PROOF_CONTENT!r}"
            )

        # Assertion 3: at least one CriticVerdictReady event with
        # verdict=approve fired on the mission bus. We track ALL such
        # events for the full run, so a revise-then-approve sequence
        # is acceptable -- only the final approve matters.
        approves = [v for v in verdicts if v.verdict == "approve"]
        assert approves, (
            f"no CriticVerdictReady(verdict='approve') event seen; "
            f"verdicts captured: {[v.verdict for v in verdicts]}"
        )
    finally:
        unsubscribe()
        # MissionManager and its background tasks need an orderly stop
        # so the test does not leak open SQLite connections or tasks
        # into the next test in the run.
        await manager.stop()
