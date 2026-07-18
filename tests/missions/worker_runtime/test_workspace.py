"""Tests for jarvis.missions.worker_runtime.workspace.

Source of truth: docs/jarvis-agents-bridge.md AD-23 + AP-OC15 +
docs/spike-results-jarvis-agents.md B-9 (system-prompt auto-injection finding).
"""
from __future__ import annotations

from pathlib import Path

import pytest

import subprocess

from jarvis.missions.worker_runtime.workspace import (
    EXPECTED_WORKSPACE_FILES,
    WORKSPACE_SUBDIR,
    _agents_md,
    materialize_worker_contract,
    prepare_workspace,
    verify_injected_files,
)


# --- execution contract (AGENTS.md) ---


def test_agents_contract_forbids_clarifying_questions() -> None:
    """Fix #3 (2026-05-29): a background mission has NO interactive user, so a
    clarifying question is a dead end that wastes the attempt (live: mission
    019e6fea iter0 produced an empty diff because the worker only asked
    questions; one iter ran 630s with no tool use before timing out). The
    contract must tell the worker to adopt a sensible default and execute,
    never ask or wait for input."""
    md = _agents_md("019e0000-1111-2222-3333-444444444444")
    low = md.lower()
    assert "clarifying question" in low, "contract must name the failure mode"
    assert "default" in low, "contract must tell the worker to adopt a default"
    assert "execute" in low
    # Framed as fire-and-forget / no interactive user.
    assert (
        "background" in low or "no interactive" in low or "nobody" in low
    ), "contract must explain there is no one to answer questions"


def test_agents_contract_still_carries_file_write_obligation() -> None:
    """Regression: the no-questions rule must not displace the existing
    file-write obligation (Rule 1)."""
    md = _agents_md("019e0000-1111-2222-3333-444444444444")
    assert "write tool" in md.lower()


def test_agents_contract_sets_quality_bar_no_stub() -> None:
    """Live incident 2026-05-31 (mission 019e7e04): the router brief said
    'Erstelle ein sinnvolles HTML-Grundgerüst', the Opus worker obeyed and  # i18n-allow: quotes the actual German router-brief text from the live incident
    shipped a 12-line stub, and the mission passed. The contract must set a
    quality floor: a complete, production-quality artefact is required and a
    skeleton/stub/placeholder is a FAILURE — even when a hint sounds minimal."""
    md = _agents_md("019e0000-1111-2222-3333-444444444444")
    low = md.lower()
    assert "quality" in low, "contract must state a quality bar"
    assert "skeleton" in low or "stub" in low or "placeholder" in low, (
        "contract must name a stub/skeleton as the failure mode"
    )
    # A minimal hint is a floor, not a ceiling — neutralises a lazy router brief.
    assert "floor" in low and "ceiling" in low


def test_agents_contract_quality_bar_does_not_override_no_features_rule() -> None:
    """Regression: the quality bar must raise execution depth WITHOUT licensing
    unrequested features — Rule 4 (no self-invention) must still hold so the
    worker builds the requested thing fully, not a different/bigger thing."""
    md = _agents_md("019e0000-1111-2222-3333-444444444444")
    assert "unrequested features" in md
    assert "Do not invent" in md or "do not invent" in md.lower()


# --- prepare_workspace ---


def test_prepare_workspace_creates_subdir(tmp_path: Path) -> None:
    workspace = prepare_workspace(tmp_path, mission_id="m-001")
    assert workspace == tmp_path / WORKSPACE_SUBDIR
    assert workspace.is_dir()


def test_prepare_workspace_writes_all_five_stub_files(tmp_path: Path) -> None:
    """B-9: the Jarvis-Agent worker harness injects AGENTS/SOUL/IDENTITY/TOOLS/USER
    — all 5 stubs must be present."""
    workspace = prepare_workspace(tmp_path, mission_id="m-001")
    actual = {f.name for f in workspace.iterdir() if f.is_file()}
    assert actual == EXPECTED_WORKSPACE_FILES


def test_prepare_workspace_files_contain_mission_id(tmp_path: Path) -> None:
    """Audit: each of the 5 stub files contains the mission ID — trace path."""
    workspace = prepare_workspace(tmp_path, mission_id="m-abc-123")
    for name in EXPECTED_WORKSPACE_FILES:
        text = (workspace / name).read_text(encoding="utf-8")
        assert "m-abc-123" in text, f"{name} missing mission_id"


def test_prepare_workspace_files_carry_managed_marker(tmp_path: Path) -> None:
    """Header marker lets later tools recognize whether a file is Personal-Jarvis-managed."""
    workspace = prepare_workspace(tmp_path, mission_id="m-001")
    for name in EXPECTED_WORKSPACE_FILES:
        text = (workspace / name).read_text(encoding="utf-8")
        assert "managed by Personal Jarvis" in text, f"{name} missing managed marker"


def test_prepare_workspace_creates_parents(tmp_path: Path) -> None:
    """state_dir doesn't need to exist — the helper creates parents implicitly."""
    deep = tmp_path / "missions" / "m-deep" / "openclaw_state"
    workspace = prepare_workspace(deep, mission_id="m-deep")
    assert workspace.is_dir()
    assert deep.is_dir()


def test_prepare_workspace_idempotent_overwrites(tmp_path: Path) -> None:
    """A second call overwrites with the same content, no append duplicates."""
    workspace = prepare_workspace(tmp_path, mission_id="m-1")
    first_text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    workspace2 = prepare_workspace(tmp_path, mission_id="m-1")
    second_text = (workspace2 / "AGENTS.md").read_text(encoding="utf-8")
    assert workspace == workspace2
    assert first_text == second_text


def test_prepare_workspace_idempotent_with_new_mission_id(tmp_path: Path) -> None:
    """A second call with a different mission ID replaces the content (no append)."""
    prepare_workspace(tmp_path, mission_id="m-1")
    workspace = prepare_workspace(tmp_path, mission_id="m-2")
    text = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "m-2" in text
    assert "m-1" not in text


def test_prepare_workspace_uses_lf_line_endings(tmp_path: Path) -> None:
    """The worker harness presumably reads the files as text — LF is safely cross-platform."""
    workspace = prepare_workspace(tmp_path, mission_id="m-1")
    raw = (workspace / "AGENTS.md").read_bytes()
    assert b"\r\n" not in raw, "Stubs must use LF line endings, not CRLF"


def test_prepare_workspace_utf8_encoding(tmp_path: Path) -> None:
    """UTF-8 because the worker harness runs cross-platform."""
    workspace = prepare_workspace(tmp_path, mission_id="m-1")
    raw = (workspace / "AGENTS.md").read_bytes()
    # UTF-8 BOM must NOT be present (interferes with the worker harness's Markdown parser)
    assert not raw.startswith(b"\xef\xbb\xbf"), "Files must not start with UTF-8 BOM"


@pytest.mark.parametrize("bad_id", ["", "   ", "\t\n", "  \t  "])
def test_prepare_workspace_rejects_blank_mission_id(tmp_path: Path, bad_id: str) -> None:
    with pytest.raises(ValueError, match="mission_id"):
        prepare_workspace(tmp_path, mission_id=bad_id)


# --- verify_injected_files ---


def test_verify_injected_files_pass_with_expected_only() -> None:
    injected = [
        {"name": "AGENTS.md", "rawChars": 100},
        {"name": "SOUL.md", "rawChars": 50},
        {"name": "IDENTITY.md", "rawChars": 30},
        {"name": "TOOLS.md", "rawChars": 20},
        {"name": "USER.md", "rawChars": 10},
    ]
    assert verify_injected_files(injected) == []


def test_verify_injected_files_returns_unexpected() -> None:
    """B-9 / AP-OC15: an extra file is a persona-leak indicator."""
    injected = [
        {"name": "AGENTS.md"},
        {"name": "SOUL.md"},
        {"name": "CUSTOM_PERSONA.md"},  # leak!
    ]
    assert verify_injected_files(injected) == ["CUSTOM_PERSONA.md"]


def test_verify_injected_files_multiple_unexpected_alphabetical() -> None:
    injected = [
        {"name": "ZZZ.md"},
        {"name": "AAA.md"},
        {"name": "AGENTS.md"},  # erlaubt
        {"name": "MMM.md"},
    ]
    assert verify_injected_files(injected) == ["AAA.md", "MMM.md", "ZZZ.md"]


def test_verify_injected_files_dedups() -> None:
    injected = [
        {"name": "LEAK.md"},
        {"name": "LEAK.md"},
        {"name": "LEAK.md"},
    ]
    assert verify_injected_files(injected) == ["LEAK.md"]


def test_verify_injected_files_empty_list() -> None:
    assert verify_injected_files([]) == []


def test_verify_injected_files_none_treated_as_pass() -> None:
    """If the worker harness delivers no report -> audit counts as green (not a fail)."""
    assert verify_injected_files(None) == []


def test_verify_injected_files_ignores_entries_without_name() -> None:
    """A schema break on the worker harness's side is not a bridge bug."""
    injected = [
        {"name": "AGENTS.md"},
        {"rawChars": 100},
        {"name": 42},
        {"name": "OK.md"},
    ]
    assert verify_injected_files(injected) == ["OK.md"]


def test_verify_injected_files_custom_expected_set() -> None:
    """Override of expected for tests / future use."""
    injected = [{"name": "FOO.md"}, {"name": "BAR.md"}]
    custom = frozenset({"FOO.md"})
    assert verify_injected_files(injected, expected=custom) == ["BAR.md"]


def test_verify_injected_files_consumes_iterator() -> None:
    """Iterable darf Generator sein — Bridge streamt aus JSON-Loaderin."""
    def gen() -> object:
        yield {"name": "AGENTS.md"}
        yield {"name": "LEAK.md"}

    assert verify_injected_files(gen()) == ["LEAK.md"]


# --- Constants Drift-Guard ---


def test_expected_workspace_files_matches_spike_b9() -> None:
    """B-9 hat exakt diese 5 Files identifiziert — Drift-Guard."""
    assert EXPECTED_WORKSPACE_FILES == frozenset({
        "AGENTS.md",
        "SOUL.md",
        "IDENTITY.md",
        "TOOLS.md",
        "USER.md",
    })


def test_workspace_subdir_constant_is_workspace() -> None:
    """`workspace/` matches the Jarvis-Agent worker's systemPromptReport.workspaceDir suffix."""
    assert WORKSPACE_SUBDIR == "workspace"


# --- AGENTS.md execution contract (Plan E, 2026-05-15) -----------------------
# Live repro mission_019e2d35: gemini-3.1-pro-preview worker claimed success
# in reply text without ever invoking file_write. AGENTS.md is the only
# Jarvis-controlled artefact in the worker's system prompt, so the
# file-write obligation must be spelled out here.


def test_agents_md_mandates_write_tool_invocation(tmp_path: Path) -> None:
    """The contract must explicitly say the worker MUST invoke a write tool —
    not just describe the action. Text-only success claims are the main
    Gemini-3.1-Pro tool-skip failure mode."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "MUST invoke a write tool" in agents_md, (
        "AGENTS.md missing mandatory tool-invocation rule"
    )


def test_agents_md_lists_specific_write_tools(tmp_path: Path) -> None:
    """The contract should name `Write`, `Edit`, and `file_write` so the
    LLM cannot claim it didn't know which tool was meant."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    for tool_name in ("Write", "Edit", "file_write"):
        assert tool_name in agents_md, f"AGENTS.md missing tool name {tool_name!r}"


def test_agents_md_references_git_diff_validation(tmp_path: Path) -> None:
    """The worker must know the validation is via `git diff HEAD` against
    the worktree — so it understands why text claims don't count."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "git diff" in agents_md, "AGENTS.md missing git-diff validation hint"


def test_agents_md_forbids_text_only_success_claims(tmp_path: Path) -> None:
    """Both German and English false-success patterns must be explicitly
    called out as not counting. Live repro of both languages in
    mission_019e2c18 / mission_019e2d35."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "I have created the file" in agents_md, (
        "AGENTS.md missing English false-success example"
    )
    assert "Habe die Datei" in agents_md, (
        "AGENTS.md missing German false-success example"
    )


def test_agents_md_demands_cwd_relative_writes(tmp_path: Path) -> None:
    """Worker must not write absolute paths or %SystemDrive%-rooted files —
    those are invisible to the diff-based reviewer. Live repro: empty
    Arbeitsordner/ subdirs created by gemini-pro under mission_019e2bbf."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "current working directory" in agents_md or "cwd" in agents_md.lower(), (
        "AGENTS.md missing cwd-write rule"
    )
    assert "SystemDrive" in agents_md, (
        "AGENTS.md missing %SystemDrive% absolute-path warning"
    )


def test_agents_md_permits_task_mandated_external_paths(tmp_path: Path) -> None:
    """mission_019e7abd (2026-05-30): when the task itself names an absolute
    target outside the worktree (e.g. the user's Desktop\\M\\ folder), the
    worker must NOT refuse, ask, or silently relocate it — the runtime now
    verifies external writes on disk. The contract must carve out this
    exception so the cwd-default no longer contradicts an explicit external
    target (the contradiction that stalled the worker for 3 iterations)."""
    workspace = prepare_workspace(tmp_path, mission_id="m-ext")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    low = agents_md.lower()
    assert "exception" in low, "contract must mark the external-target exception"
    assert "explicitly" in low and "outside the worktree" in low, (
        "contract must allow an explicitly task-mandated external target"
    )
    # The cwd default must still be the preferred behaviour (existing Rule 2).
    assert "current working directory" in agents_md or "cwd" in low


def test_agents_md_requires_output_confirmation_line(tmp_path: Path) -> None:
    """Worker must emit a `Written: ...` line at the end so the reviewer
    can correlate the diff with the worker's intent."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Written:" in agents_md, "AGENTS.md missing Written-confirmation example"
    assert "No file output required" in agents_md, (
        "AGENTS.md missing no-output fallback line"
    )


def test_agents_md_preserves_no_self_invention_rule(tmp_path: Path) -> None:
    """Existing rule against self-invention / reading other stubs must
    survive Plan-E rewrite — it stops Gemini from hallucinating goals
    or trying to follow the empty SOUL.md / IDENTITY.md stubs."""
    workspace = prepare_workspace(tmp_path, mission_id="m-plan-e")
    agents_md = (workspace / "AGENTS.md").read_text(encoding="utf-8")
    assert "Do not invent" in agents_md or "do not invent" in agents_md.lower()
    assert "empty stubs" in agents_md.lower()


# --- materialize_worker_contract (CRIT-2 from 2026-05-17 audit) ----------


def _init_git_repo(repo: Path) -> None:
    """Create an empty git repo at ``repo`` and seed it with one commit
    so subsequent ``git worktree add`` calls succeed. Used to give
    ``materialize_worker_contract`` a real worktree fixture to write
    the .git/info/exclude file into."""
    repo.mkdir(parents=True, exist_ok=True)
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    import os
    env = {**os.environ, **env}
    for cmd in (
        ["git", "init", "-b", "main"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(  # noqa: S603 - controlled
            cmd, cwd=str(repo), check=True,
            capture_output=True, env=env,
        )


def test_materialize_writes_agents_md_in_worktree(tmp_path: Path) -> None:
    """Worker contract must land in the worktree root itself (not in
    MISSION_STATE_DIR/workspace) so claude --print picks it up via
    --add-dir."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    # Use the repo as a fake worktree -- materialize_worker_contract
    # doesn't care whether the directory is a worktree or the main
    # checkout, only that `git rev-parse --git-dir` works.
    out = materialize_worker_contract(repo, mission_id="m-test-001")
    assert out == repo / "AGENTS.md"
    assert out.is_file()
    body = out.read_text(encoding="utf-8")
    assert "m-test-001" in body, "mission id must appear in the contract"
    assert "EXECUTION CONTRACT" in body
    assert "File-write obligation" in body


def test_materialize_adds_agents_md_to_local_exclude(tmp_path: Path) -> None:
    """The contract must not pollute the diff -- it goes into
    .git/info/exclude (local-only, per-worktree)."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    materialize_worker_contract(repo, mission_id="m-test-002")
    exclude = repo / ".git" / "info" / "exclude"
    assert exclude.is_file(), f"exclude not written at {exclude}"
    assert "AGENTS.md" in exclude.read_text(encoding="utf-8")


def test_materialize_is_idempotent_no_duplicate_exclude_entries(
    tmp_path: Path,
) -> None:
    """Calling twice (e.g. resumed mission) must not duplicate the
    exclude entry or pollute the file."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    materialize_worker_contract(repo, mission_id="m-a")
    materialize_worker_contract(repo, mission_id="m-b")
    exclude_text = (repo / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    )
    occurrences = exclude_text.count("AGENTS.md")
    assert occurrences == 1, (
        f"exclude entry must be deduped, got {occurrences} occurrences"
    )
    # Second call overwrote the AGENTS.md with the new mission id.
    body = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "m-b" in body
    assert "m-a" not in body


def test_materialize_preserves_existing_exclude_entries(tmp_path: Path) -> None:
    """User-authored exclude entries (e.g. local IDE droppings) must
    survive a contract materialisation."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("# user note\n*.swp\n", encoding="utf-8")
    materialize_worker_contract(repo, mission_id="m-keep")
    text = exclude.read_text(encoding="utf-8")
    assert "*.swp" in text
    assert "# user note" in text
    assert "AGENTS.md" in text


def test_materialize_rejects_missing_worktree(tmp_path: Path) -> None:
    """Refuses to write into a path that does not exist -- caller is
    misusing the helper."""
    with pytest.raises(FileNotFoundError):
        materialize_worker_contract(tmp_path / "nope", mission_id="m")


def test_materialize_rejects_empty_mission_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    with pytest.raises(ValueError):
        materialize_worker_contract(repo, mission_id="   ")


def test_materialize_survives_non_git_dir(tmp_path: Path) -> None:
    """If `git rev-parse --git-dir` fails (no .git, broken repo), the
    AGENTS.md still gets written -- the gitignore step is best-effort
    and must not propagate failures up to the mission runtime."""
    plain = tmp_path / "plain"
    plain.mkdir()
    out = materialize_worker_contract(plain, mission_id="m-no-git")
    assert out.is_file()
    assert "m-no-git" in out.read_text(encoding="utf-8")
