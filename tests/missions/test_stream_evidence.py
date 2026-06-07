"""Extract tool-call evidence + final answer from a claude stream.jsonl.

This is the keystone for making read-only / informational missions work:
the critic must SEE the tool calls + answer (today they get truncated out of
the 4000-char log summary), and the orchestrator must surface the worker's
answer to voice. Both consume `extract_stream_evidence`.
"""
from __future__ import annotations

import json

from jarvis.missions.stream_evidence import (
    extract_stream_evidence,
    extract_verified_commands,
)


def _stream(lines: list[dict]) -> str:
    return "\n".join(json.dumps(line) for line in lines)


# ---------------------------------------------------------------------------
# extract_verified_commands — Git/GitHub side-effects that leave no worktree
# diff (commit/push/PR). The tool_result is the REAL subprocess output (the
# git/gh process wrote it), so a non-errored result is ground truth, not a log
# claim. This closes the "commit and push" / "open PRs" critic_loop_exhausted
# false-negative without weakening the anti-hallucination guard.
# ---------------------------------------------------------------------------


def test_credits_successful_git_push() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "To github.com:me/repo.git\n   abc..def  main -> main"}]}},
    ])
    cmds = extract_verified_commands(stream)
    assert len(cmds) == 1
    assert "git push" in cmds[0][0]
    assert "main -> main" in cmds[0][1]


def test_credits_gh_pr_create() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "p1", "name": "Bash",
             "input": {"command": "gh pr create --title 'Add X' --body '...'"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "p1",
             "content": "https://github.com/me/repo/pull/42"}]}},
    ])
    cmds = extract_verified_commands(stream)
    assert len(cmds) == 1
    assert "gh pr create" in cmds[0][0]
    assert "pull/42" in cmds[0][1]


def test_errored_command_not_credited() -> None:
    """A failed command (is_error result) is NOT credited — anti-hearsay."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
             "content": "error: failed to push some refs"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_read_only_command_not_credited() -> None:
    """A read-only command (git status) is not a deliverable — not credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git status"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "clean"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_unmatched_command_result_not_credited() -> None:
    """A command whose result never arrived (truncated stream) is NOT credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "gh pr create --title x"}}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_credits_git_dash_C_push() -> None:
    """`git -C <dir> push` — a worker operating from a different cwd uses this
    constantly. The global `-C <path>` flag before the subcommand must not
    defeat crediting (code-review MAJOR-3)."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git -C /home/me/repo push origin main"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "   abc..def  main -> main"}]}},
    ])
    assert len(extract_verified_commands(stream)) == 1


def test_credits_git_long_flag_before_subcommand() -> None:
    """`git --no-pager commit` — a long global flag before the subcommand."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git --no-pager commit -m 'msg'"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": "[main 1a2b3c4] msg\n 1 file changed"}]}},
    ])
    assert len(extract_verified_commands(stream)) == 1


def test_git_log_grep_push_not_a_false_positive() -> None:
    """`git log --grep=push` is read-only — the word 'push' inside a flag value
    must NOT be credited as a mutating command."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": "git log --grep=push --oneline"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "abc commit"}]}},
    ])
    assert extract_verified_commands(stream) == ()


def test_chained_command_with_commit_and_push_is_credited() -> None:
    """A real-world chained command (add && commit && push) is credited once."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "c1", "name": "Bash",
             "input": {"command": "cd repo && git add -A && git commit -m 'x' && git push"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "c1",
             "content": "[main 1a2b3c4] x\n 1 file changed\n   ab..cd  main -> main"}]}},
    ])
    cmds = extract_verified_commands(stream)
    assert len(cmds) == 1
    assert "git commit" in cmds[0][0] or "git push" in cmds[0][0]


def test_extracts_tool_calls_and_final_answer() -> None:
    stream = _stream([
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},
        {"type": "system", "subtype": "init", "tools": [], "mcp_servers": []},  # hook noise
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Ich frage GitHub ab."},
            {"type": "tool_use", "name": "mcp__github__get_me", "input": {}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": '{"login":"octocat"}'}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__github__search_repositories",
             "input": {"query": "user:octocat"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": [
                {"type": "text", "text": '{"total_count":32}'}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Du hast 32 aktive Repositories."}]}},
        {"type": "result",
         "result": "Du hast 32 aktive Repositories (1 öffentlich, 31 privat).",
         "subtype": "success", "is_error": False},
    ])
    ev = extract_stream_evidence(stream)

    assert "mcp__github__get_me" in ev.tool_calls
    assert "mcp__github__search_repositories" in ev.tool_calls
    assert ev.final_answer == "Du hast 32 aktive Repositories (1 öffentlich, 31 privat)."
    # at least one tool result captured (truncated form is fine)
    assert any("total_count" in r or "32" in r for r in ev.tool_results)


def test_final_answer_falls_back_to_last_assistant_text_without_result() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"path": "x"}}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Die Datei erklärt das Modul X."}]}},
    ])
    ev = extract_stream_evidence(stream)
    assert ev.final_answer == "Die Datei erklärt das Modul X."
    assert "Read" in ev.tool_calls


def test_garbage_and_empty_lines_are_skipped() -> None:
    stream = "not json\n\n" + json.dumps(
        {"type": "result", "result": "ok", "subtype": "success"}
    )
    ev = extract_stream_evidence(stream)
    assert ev.final_answer == "ok"
    assert ev.tool_calls == ()


def test_no_tools_no_answer_is_empty() -> None:
    ev = extract_stream_evidence("")
    assert ev.tool_calls == ()
    assert ev.final_answer == ""
    assert not ev.has_tool_evidence


# --- readonly_answer: the read-only success signal -------------------------
from jarvis.missions.stream_evidence import readonly_answer, summarize_answers  # noqa: E402

_GH_STREAM = _stream([
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__github__search_repositories", "input": {}}]}},
    {"type": "result", "result": "Du hast 32 aktive Repositories.", "subtype": "success"},
])


def test_readonly_answer_returns_answer_on_empty_diff_with_tool_evidence() -> None:
    assert readonly_answer("", _GH_STREAM) == "Du hast 32 aktive Repositories."
    assert readonly_answer("   \n  ", _GH_STREAM) == "Du hast 32 aktive Repositories."


def test_readonly_answer_none_when_diff_present() -> None:
    # a code task that produced a diff is NOT an informational result
    assert readonly_answer("diff --git a/x b/x\n+hello", _GH_STREAM) is None


def test_readonly_answer_none_without_tool_evidence() -> None:
    # anti-hallucination: empty diff + NO tool calls -> not a success
    bare = _stream([{"type": "result", "result": "Habe alles erledigt.", "subtype": "success"}])
    assert readonly_answer("", bare) is None


def test_readonly_answer_none_when_answer_empty() -> None:
    no_answer = _stream([{"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {}}]}}])
    assert readonly_answer("", no_answer) is None


def test_summarize_answers_joins_and_caps() -> None:
    assert summarize_answers([]) == ""
    assert summarize_answers(["A", "B"]) == "A\nB"
    long = "x" * 5000
    assert len(summarize_answers([long], cap=600)) <= 600


# --- extract_write_targets: out-of-worktree deliverable verification --------
#
# Root cause of mission_019e7abd (2026-05-30): the worker wrote a file to an
# absolute path OUTSIDE its git worktree (the user's Desktop\M\). `_capture_diff`
# is worktree-scoped → empty diff → the Critic's GROUND-TRUTH-RULE failed it 3×
# even though the file existed and was correct. `extract_write_targets` is the
# stream-side half of the fix: surface the paths the worker actually wrote with
# a real, non-errored Write/Edit tool_use so the Kontrollierer can verify them
# on disk and present them to the Critic as ground truth.
from jarvis.missions.stream_evidence import extract_write_targets  # noqa: E402


def test_extract_write_targets_returns_successful_write_path() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": "Write",
             "input": {"file_path": r"C:\Users\x\M\hello.html",
                       "content": "<h1>Hi</h1>"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu1",
             "content": [{"type": "text", "text": "File created successfully."}]}]}},
        {"type": "result", "result": "done", "subtype": "success"},
    ])
    assert extract_write_targets(stream) == (r"C:\Users\x\M\hello.html",)


def test_extract_write_targets_excludes_errored_write() -> None:
    # iter1 of the live failure: Write returned `File has not been read yet`.
    # An errored-only path must NOT be credited — the worker did not write it.
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu1", "name": "Write",
             "input": {"file_path": r"C:\x\f.txt", "content": "x"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu1", "is_error": True,
             "content": [{"type": "text",
                          "text": "<tool_use_error>File has not been read yet."
                                  "</tool_use_error>"}]}]}},
    ])
    assert extract_write_targets(stream) == ()


def test_extract_write_targets_edit_tool_counts() -> None:
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "e1", "name": "Edit",
             "input": {"file_path": "/tmp/out/report.md",
                       "old_string": "a", "new_string": "b"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "e1",
             "content": [{"type": "text", "text": "Edit applied."}]}]}},
    ])
    assert extract_write_targets(stream) == ("/tmp/out/report.md",)


def test_extract_write_targets_dedups_path_with_one_success() -> None:
    # Same path written twice: first errored, then succeeded → credited once.
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Write",
             "input": {"file_path": "/tmp/x.txt", "content": "1"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a", "is_error": True,
             "content": [{"type": "text", "text": "<tool_use_error>nope</tool_use_error>"}]}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "b", "name": "Write",
             "input": {"file_path": "/tmp/x.txt", "content": "2"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "b",
             "content": [{"type": "text", "text": "ok"}]}]}},
    ])
    assert extract_write_targets(stream) == ("/tmp/x.txt",)


def test_extract_write_targets_empty_without_write_tools() -> None:
    # Read-only / informational stream (only a search tool) → no write targets.
    assert extract_write_targets(_GH_STREAM) == ()
    assert extract_write_targets("") == ()


def test_extract_write_targets_excludes_idless_write_frame() -> None:
    """A tool_use with no `id` cannot be correlated to a result → it must NOT
    be credited. Closes the hearsay hole for out-of-worktree writes: a malformed
    frame must not let a pre-existing file masquerade as freshly written."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write",
             "input": {"file_path": "/tmp/no_id.txt", "content": "x"}}]}},
    ])
    assert extract_write_targets(stream) == ()


def test_extract_write_targets_excludes_unmatched_write_frame() -> None:
    """A write frame whose tool_result never arrived (truncated stream) is not
    a confirmed success → not credited. Only a matched, non-errored result
    counts as ground truth."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write",
             "input": {"file_path": "/tmp/truncated.txt", "content": "x"}}]}},
        # no tool_result for w1
    ])
    assert extract_write_targets(stream) == ()


def test_readonly_answer_returns_answer_for_external_only_diff() -> None:
    """An external-target-only diff (out-of-worktree deliverable) carries no
    in-worktree `diff --git` hunk, so the worker's final answer — which names
    the delivered file — must still be spoken back, not suppressed as a 'code
    task'. Regression for the mission_019e7abd fix: after the external-write
    augmentation the diff is non-empty, and a naive truthiness gate would
    silence the readback."""
    external_diff = (
        "diff --external-target b/C:/Users/x/M/hello.html\n"
        "# verified-external-write: C:/Users/x/M/hello.html\n"
        "--- /dev/null\n+++ b/C:/Users/x/M/hello.html\n+<h1>Hi</h1>\n"
    )
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "w1", "name": "Write",
             "input": {"file_path": "C:/Users/x/M/hello.html",
                       "content": "<h1>Hi</h1>"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "w1",
             "content": [{"type": "text", "text": "ok"}]}]}},
        {"type": "result",
         "result": "Created hello.html at C:/Users/x/M/hello.html.",
         "subtype": "success"},
    ])
    assert (
        readonly_answer(external_diff, stream)
        == "Created hello.html at C:/Users/x/M/hello.html."
    )


# ---------------------------------------------------------------------------
# extract_verified_desktop_actions — desktop-launch commands that produce NO
# file diff: the deliverable is a running process, not a file change.
# Mirrors the git/gh command-evidence path so a diff-less "open Explorer /
# launch Chrome" mission can be credited as real work instead of being vetoed
# as an empty diff. Anti-hearsay discipline: SAME rules as
# extract_verified_commands (id required; non-errored result required).
# ---------------------------------------------------------------------------
from jarvis.missions.stream_evidence import extract_verified_desktop_actions  # noqa: E402


def test_extract_verified_desktop_actions_credits_silent_launch() -> None:
    """Windows 'start explorer.exe' with an empty (but non-errored) result is
    credited — a silent detached spawn produces no stdout, and that is SUCCESS,
    not missing evidence."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "d1", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "d1",
             "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert "start explorer.exe" in actions[0][0]
    # Silent detached spawn: empty stdout → substitute sentinel text.
    assert actions[0][1] == "(command succeeded; no output captured)"


def test_extract_verified_desktop_actions_skips_errored() -> None:
    """A launch command whose tool_result is errored is NOT credited — the
    process did not start (or the shell reported failure)."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "d2", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "d2", "is_error": True,
             "content": "The system cannot find the file specified."}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_ignores_readonly() -> None:
    """Read-only commands (ls, cat) do NOT match the desktop-launch regex and
    must never be credited (false-positive guard)."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r1", "name": "Bash",
             "input": {"command": "ls -la"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r1",
             "content": "total 0"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "r2", "name": "Bash",
             "input": {"command": "cat foo.txt"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "r2",
             "content": "hello"}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_linux_xdg_open() -> None:
    """Linux: 'xdg-open foo.pdf' is a desktop-launch command and is credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "x1", "name": "Bash",
             "input": {"command": "xdg-open /home/user/document.pdf"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "x1",
             "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert "xdg-open" in actions[0][0]
    assert actions[0][1] == "(command succeeded; no output captured)"


def test_extract_verified_desktop_actions_macos_open() -> None:
    """macOS: 'open -a Safari' is a desktop-launch command and is credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "m1", "name": "Bash",
             "input": {"command": "open -a Safari https://example.com"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "m1",
             "content": ""}]}},
    ])
    actions = extract_verified_desktop_actions(stream)
    assert len(actions) == 1
    assert "open -a" in actions[0][0]
    assert actions[0][1] == "(command succeeded; no output captured)"


def test_extract_verified_desktop_actions_skips_uncorrelated() -> None:
    """A tool_use with no id cannot be correlated to a result and must NOT be
    credited — anti-hearsay gate mirrors extract_verified_commands."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            # No "id" field — cannot correlate to a result.
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
    ])
    assert extract_verified_desktop_actions(stream) == ()


def test_extract_verified_desktop_actions_skips_unmatched_result() -> None:
    """A tool_use whose result never arrived (truncated stream) is NOT credited."""
    stream = _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t99", "name": "Bash",
             "input": {"command": "start explorer.exe"}}]}},
        # no tool_result for t99
    ])
    assert extract_verified_desktop_actions(stream) == ()


# ---------------------------------------------------------------------------
# MAJOR-2 — tightened `start` arm: CLI runs must NOT be credited as desktop
# launches, but real GUI/app launches must still be credited.
# ---------------------------------------------------------------------------


def _desktop_stream(command: str) -> str:
    """Build a minimal successful shell tool_use stream for `command`."""
    return _stream([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Bash",
             "input": {"command": command}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "s1", "content": ""}]}},
    ])


def test_start_explorer_still_credited_after_tightening() -> None:
    """Real GUI launch: 'start explorer.exe' must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream("start explorer.exe"))) == 1


def test_start_chrome_still_credited_after_tightening() -> None:
    """Real GUI launch: 'start chrome' must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream("start chrome"))) == 1


def test_start_calc_still_credited_after_tightening() -> None:
    """Real GUI launch: 'start calc' must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream("start calc"))) == 1


def test_start_quoted_title_chrome_still_credited() -> None:
    """Real GUI launch: 'start \"\" chrome' (quoted-title form) must still be credited."""
    assert len(extract_verified_desktop_actions(_desktop_stream('start "" chrome'))) == 1


def test_start_git_push_not_credited() -> None:
    """MAJOR-2: 'start git push' is a CLI run, NOT a desktop launch — must not be credited."""
    assert extract_verified_desktop_actions(_desktop_stream("start git push")) == ()


def test_start_python_build_not_credited() -> None:
    """MAJOR-2: 'start python build.py' is a CLI run, NOT a desktop launch — must not be credited."""
    assert extract_verified_desktop_actions(_desktop_stream("start python build.py")) == ()


def test_start_slash_B_not_credited() -> None:
    """MAJOR-2: 'start /B git status' uses a Windows flag — NOT a named-app GUI launch."""
    assert extract_verified_desktop_actions(_desktop_stream("start /B git status")) == ()


def test_start_npm_run_build_not_credited() -> None:
    """MAJOR-2: 'start npm run build' is a CLI tool run — must not be credited as a launch."""
    assert extract_verified_desktop_actions(_desktop_stream("start npm run build")) == ()
