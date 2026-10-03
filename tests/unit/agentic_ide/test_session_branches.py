"""Ownership requires successful creation in this session's own tool record."""

import json
from dataclasses import replace

import pytest

from jarvis.agentic_ide import agent_transcript
from jarvis.agentic_ide import session_branches as branches


def call(args, identity="call", name="exec_command"):
    return {"kind": "tool_call", "payload": {"call_id": identity, "name": name, "input": args}}


def result(output, identity="call", error=False):
    return {
        "kind": "tool_result",
        "ts_ms": 5000,
        "payload": {"call_id": identity, "output": output, "is_error": error},
    }


@pytest.mark.parametrize(
    "command, expected",
    [
        ("git worktree add -b agent/fix ../fork HEAD", "agent/fix"),
        ("git switch -c feature/one", "feature/one"),
        ("git switch --create feature/one", "feature/one"),
        ("git checkout -b feature/two", "feature/two"),
        ("git branch feature/three", "feature/three"),
        ("git switch main", None),
        ("git checkout existing", None),
        ("git branch -D old", None),
        ("git worktree add ../fork existing", None),
        ("git checkout -B existing", None),
        ("echo 'git switch -c fake'", None),
        ("git status", None),
    ],
)
def test_only_creation_commands_count(command, expected, tmp_path):
    found = branches.created_by_command(command, str(tmp_path))
    assert [entry.branch for entry in found] == ([expected] if expected else [])


def test_success_belongs_to_the_call_not_some_other_session(tmp_path):
    command = call({"cmd": "git switch -c owned"})
    assert (
        branches.created_branches(
            [command, result("fatal: already exists", error=True)], str(tmp_path)
        )
        == []
    )
    assert (
        branches.created_branches(
            [command, result("Switched to a new branch 'owned'", "other")], str(tmp_path)
        )
        == []
    )
    found = branches.created_branches(
        [command, result("Switched to a new branch 'owned'")], str(tmp_path)
    )
    assert found[0].branch == "owned" and found[0].created_at == 5


def test_shell_branch_creation_requires_explicit_success(tmp_path):
    args = {"command": "git branch owned"}
    assert (
        branches.created_branches([call(args, name="Bash"), result("nothing")], str(tmp_path)) == []
    )
    assert (
        branches.created_branches(
            [call(args, name="Bash"), result('{"exit_code":0}')], str(tmp_path)
        )[0].branch
        == "owned"
    )


def test_code_mode_literal_arguments_and_windows_paths(tmp_path):
    cwd = str(tmp_path / "a folder")
    source = (
        'text(await tools.exec_command({cmd: "git worktree add -b codex/fix ../fork HEAD", '
        "workdir: " + json.dumps(cwd) + "}));"
    )
    found = branches.created_branches(
        [
            call({"input": source}, name="exec"),
            result("Preparing worktree (new branch 'codex/fix')"),
        ],
        str(tmp_path),
    )
    assert found[0].folder == cwd and found[0].branch == "codex/fix"
    assert (
        branches.created_branches(
            [
                call({"input": source}, name="read_file"),
                result("Preparing worktree (new branch 'codex/fix')"),
            ],
            str(tmp_path),
        )
        == []
    )


def test_directory_changes_and_git_dash_c(tmp_path):
    found = branches.created_by_command(
        'cd "nested folder"; git -C ../other switch -c owned', str(tmp_path)
    )
    assert found[0].folder == str(tmp_path / "other")


def test_dynamic_code_mode_cwd_is_not_assumed_to_be_the_workspace(tmp_path):
    source = 'await tools.exec_command({cmd: "git switch -c other-repo", workdir: anotherRepo});'
    events = [call({"input": source}, name="exec"), result("Switched to a new branch 'other-repo'")]
    assert branches.created_branches(events, str(tmp_path)) == []


def test_code_mode_accepts_literal_json_object_keys(tmp_path):
    source = (
        "await tools.exec_command("
        + json.dumps({"cmd": "git switch -c own", "workdir": str(tmp_path)})
        + ");"
    )
    events = [call({"input": source}, name="exec"), result("Switched to a new branch 'own'")]
    assert branches.created_branches(events, str(tmp_path))[0].branch == "own"


def test_workspace_branch_alone_never_gives_ownership(monkeypatch, tmp_path):
    record = branches.PaneBranchRecord("t1", "history", str(tmp_path))
    assert branches.owned_branch(record) is None
    explicit = replace(record, created_branch="fork/owned")
    assert branches.owned_branch(explicit).branch == "fork/owned"


def test_native_fork_does_not_inherit_the_parent_branch(tmp_path):
    event = call({"cmd": "git switch -c parent"})
    event["ts_ms"] = 1000
    events = [event, result("Switched to a new branch 'parent'")]
    assert branches.created_branches(events, str(tmp_path), not_before=6) == []
    own = call({"cmd": "git switch -c child"}, identity="child")
    own["ts_ms"] = 7000
    outcome = result("Switched to a new branch 'child'", identity="child")
    outcome["ts_ms"] = 8000
    assert [
        item.branch
        for item in branches.created_branches([*events, own, outcome], str(tmp_path), not_before=6)
    ] == ["child"]


def test_other_sessions_and_unrelated_repositories_are_isolated(monkeypatch, tmp_path):
    branches._cache.clear()
    monkeypatch.setattr(agent_transcript, "can_read", lambda _: True)

    def read(_agent, session, **_):
        if session == "author":
            return [
                call({"cmd": "git switch -c owned"}),
                result("Switched to a new branch 'owned'"),
            ]
        if session == "unrelated":
            return [
                call({"cmd": "git -C /elsewhere switch -c unrelated"}),
                result("Switched to a new branch 'unrelated'"),
            ]
        return []

    monkeypatch.setattr(agent_transcript, "read_events", read)
    monkeypatch.setattr(
        branches, "_common", lambda folder: "same" if folder == str(tmp_path) else "other"
    )
    record = branches.PaneBranchRecord("t1", "history", str(tmp_path), "codex", "author")
    assert branches.owned_branch(record).branch == "owned"
    assert branches.owned_branch(replace(record, session_id="reader")) is None
    assert branches.owned_branch(replace(record, session_id="unrelated")) is None


def test_codex_item_ids_do_not_break_call_result_matching(monkeypatch, tmp_path):
    path = tmp_path / "record.jsonl"
    rows = [
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "id": "ctc_item",
                "call_id": "shared",
                "name": "exec_command",
                "input": json.dumps({"cmd": "git switch -c owned"}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "id": "ctco_item",
                "call_id": "shared",
                "output": "Switched to a new branch 'owned'",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    monkeypatch.setattr(agent_transcript, "_codex_file", lambda *_: path)
    events = agent_transcript.read_events("codex", "session")
    paired = [
        event["payload"]["call_id"]
        for event in events
        if event["kind"] in {"tool_call", "tool_result"}
    ]
    assert paired == ["shared", "shared"]
    assert branches.created_branches(events, str(tmp_path))[0].branch == "owned"
