"""Tests for the worktree-scoped tools of the in-process API worker."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from jarvis.missions.workers import api_agent_tools as aat
from jarvis.missions.workers.api_agent_tools import (
    WORKER_TOOL_SPECS,
    execute_worker_tool,
    execute_worker_tool_async,
    tool_writes_file,
)


def test_specs_cover_the_core_tools() -> None:
    names = {t["name"] for t in WORKER_TOOL_SPECS}
    assert names == {"Write", "Read", "Edit", "RunCommand", "Ls"}
    # every spec is OpenAI/Anthropic-translatable
    for t in WORKER_TOOL_SPECS:
        assert t["description"]
        assert t["input_schema"]["type"] == "object"


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    out, err = execute_worker_tool(
        "Write", {"file_path": "a/b.txt", "content": "hi"}, worktree=tmp_path
    )
    assert err is False
    assert (tmp_path / "a" / "b.txt").read_text(encoding="utf-8") == "hi"
    out, err = execute_worker_tool("Read", {"file_path": "a/b.txt"}, worktree=tmp_path)
    assert err is False and out == "hi"


def test_edit_replaces_first_occurrence(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("foo bar foo", encoding="utf-8")
    out, err = execute_worker_tool(
        "Edit", {"file_path": "f.txt", "old_string": "foo", "new_string": "X"}, worktree=tmp_path
    )
    assert err is False
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "X bar foo"


def test_edit_missing_old_string_is_error(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("abc", encoding="utf-8")
    out, err = execute_worker_tool(
        "Edit", {"file_path": "f.txt", "old_string": "zzz", "new_string": "X"}, worktree=tmp_path
    )
    assert err is True
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "abc"  # unchanged


def test_read_missing_file_is_error(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Read", {"file_path": "nope.txt"}, worktree=tmp_path)
    assert err is True


def test_ls_lists_entries(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("1", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    out, err = execute_worker_tool("Ls", {}, worktree=tmp_path)
    assert err is False
    assert "x.txt" in out and "sub/" in out


@pytest.mark.asyncio
async def test_command_runs_directly_in_worktree(tmp_path: Path) -> None:
    (tmp_path / "show_cwd.py").write_text(
        "from pathlib import Path\nprint(Path.cwd().name)\n", encoding="utf-8"
    )
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["show_cwd.py"]},
        worktree=tmp_path,
    )
    assert err is False
    assert tmp_path.name in out


@pytest.mark.asyncio
async def test_command_nonzero_exit_is_error(tmp_path: Path) -> None:
    (tmp_path / "fail.py").write_text(
        "raise SystemExit(3)\n", encoding="utf-8"
    )
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["fail.py"]},
        worktree=tmp_path,
    )
    assert err is True
    assert "exit 3" in out


def test_path_escape_is_rejected_on_write(tmp_path: Path) -> None:
    """The model must never write outside the worktree (../ escape)."""
    out, err = execute_worker_tool(
        "Write", {"file_path": "../escape.txt", "content": "x"}, worktree=tmp_path
    )
    assert err is True
    assert "escape" in out.lower()
    assert not (tmp_path.parent / "escape.txt").exists()


def test_absolute_path_outside_worktree_rejected(tmp_path: Path) -> None:
    out, err = execute_worker_tool(
        "Write", {"file_path": str(tmp_path.parent / "x.txt"), "content": "x"}, worktree=tmp_path
    )
    assert err is True


def test_unknown_tool_is_error(tmp_path: Path) -> None:
    out, err = execute_worker_tool("Frobnicate", {}, worktree=tmp_path)
    assert err is True


def test_tool_writes_file_predicate() -> None:
    assert tool_writes_file("Write") and tool_writes_file("Edit")
    assert not tool_writes_file("Read") and not tool_writes_file("RunCommand")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_input", "message"),
    [
        ({"program": "python", "args": ["../outside.py"]}, "escapes"),
        ({"program": "../python", "args": []}, "executable name"),
        ({"program": "python", "args": ["-c", "print('x')"]}, "inline"),
        ({"program": "py", "args": ["-c", "print('x')"]}, "inline"),
        ({"program": "bash", "args": ["-c", "echo x"]}, "shell"),
        ({"program": "env", "args": ["python", "build.py"]}, "shell"),
        ({"program": "pythonw", "args": ["build.py"]}, "shell"),
        ({"program": "npx", "args": ["vite"]}, "package-exec"),
        ({"program": "npm", "args": ["exec", "vite"]}, "package-exec"),
        ({"program": "git", "args": ["config", "alias.x", "!cmd"]}, "git"),
        ({"program": "git", "args": ["-c", "alias.x=!cmd", "x"]}, "git"),
        ({"program": "curl", "args": ["file:///etc/passwd"]}, "local-file"),
        ({"program": "curl", "args": ["--data", "@../secret"]}, "local-file"),
        ({"program": "python", "args": "script.py"}, "array"),
    ],
)
async def test_command_policy_rejects_unsafe_shapes(
    tmp_path: Path, tool_input: dict[str, object], message: str
) -> None:
    out, err = await execute_worker_tool_async(
        "RunCommand", tool_input, worktree=tmp_path
    )
    assert err is True
    assert message in out.lower()


def test_command_policy_keeps_reviewable_build_and_network_shapes(tmp_path: Path) -> None:
    assert aat._validated_command(
        "git", ["--no-pager", "status"], worktree=tmp_path
    ) == ("git", ("--no-pager", "status"))
    assert aat._validated_command(
        "npm", ["run", "build"], worktree=tmp_path
    ) == ("npm", ("run", "build"))
    assert aat._validated_command(
        "curl", ["https://example.com/status"], worktree=tmp_path
    ) == ("curl", ("https://example.com/status",))


@pytest.mark.asyncio
async def test_command_rejects_absolute_path_outside_worktree(tmp_path: Path) -> None:
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": [str(tmp_path.parent / "outside.py")]},
        worktree=tmp_path,
    )
    assert err is True
    assert "escapes" in out.lower()

    out, err = await execute_worker_tool_async(
        "RunCommand",
        {
            "program": "python",
            "args": [f"--unknown-output={tmp_path.parent / 'outside.txt'}"],
        },
        worktree=tmp_path,
    )
    assert err is True
    assert "escapes" in out.lower()


@pytest.mark.asyncio
async def test_command_environment_does_not_forward_credentials(tmp_path: Path) -> None:
    (tmp_path / "show_env.py").write_text(
        "import os\n"
        "print(os.environ.get('OPENAI_API_KEY', 'missing'))\n"
        "print(os.environ.get('HOME', ''))\n",
        encoding="utf-8",
    )
    runtime_dir = tmp_path / "runtime"
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["show_env.py"]},
        worktree=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "OPENAI_API_KEY": "secret"},
        runtime_dir=runtime_dir,
    )
    assert err is False
    assert out.splitlines()[0] == "missing"
    assert str(runtime_dir / "home") in out


def test_command_environment_keeps_safe_portability_without_proxy_credentials(
    tmp_path: Path,
) -> None:
    ca_bundle = tmp_path / "corporate-ca.pem"
    ca_bundle.write_text("PUBLIC CA\n", encoding="ascii")
    unsafe_bundle = tmp_path / "unsafe-ca.pem"
    unsafe_bundle.write_text("PRIVATE KEY\n", encoding="ascii")
    runtime_dir = tmp_path / "runtime"
    env = aat._command_environment(
        {
            "PATH": os.environ.get("PATH", ""),
            "JAVA_HOME": str(tmp_path / "jdk"),
            "HTTPS_PROXY": "http://proxy.example:8080",
            "HTTP_PROXY": "http://user:password@proxy.example:8080",
            "ALL_PROXY": "http://proxy.example:8080?token=secret",
            "REQUESTS_CA_BUNDLE": str(ca_bundle),
            "CURL_CA_BUNDLE": str(unsafe_bundle),
            "OPENAI_API_KEY": "must-not-cross",
        },
        runtime_dir=runtime_dir,
    )
    assert env["JAVA_HOME"] == str(tmp_path / "jdk")
    assert env["HTTPS_PROXY"] == "http://proxy.example:8080"
    assert "HTTP_PROXY" not in env
    assert "ALL_PROXY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "CURL_CA_BUNDLE" not in env
    staged_ca = Path(env["REQUESTS_CA_BUNDLE"])
    assert staged_ca.parent == runtime_dir / "certificates"
    assert staged_ca.read_text(encoding="ascii") == "PUBLIC CA\n"


def test_frozen_python_never_resolves_to_jarvis_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jarvis_exe = str(Path("C:/Program Files/Jarvis/Jarvis.exe"))
    monkeypatch.setattr(aat.sys, "frozen", True, raising=False)
    monkeypatch.setattr(aat.sys, "executable", jarvis_exe)
    monkeypatch.setattr(aat.shutil, "which", lambda *_args, **_kwargs: jarvis_exe)

    with pytest.raises(aat.CommandPolicyError, match="Jarvis.exe will never"):
        aat._resolve_program("python", {"PATH": "unused"})


def test_frozen_python_uses_a_distinct_path_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jarvis_exe = str(Path("C:/Program Files/Jarvis/Jarvis.exe"))
    real_python = str(Path("C:/Python311/python.exe"))
    monkeypatch.setattr(aat.sys, "frozen", True, raising=False)
    monkeypatch.setattr(aat.sys, "executable", jarvis_exe)

    def _which(candidate: str, **_kwargs: object) -> str | None:
        if candidate == "python":
            return jarvis_exe
        if candidate == "python3":
            return real_python
        return None

    monkeypatch.setattr(aat.shutil, "which", _which)
    assert aat._resolve_program("python", {"PATH": "unused"}) == (real_python, ())


def test_command_timeout_allows_long_builds_but_remains_bounded() -> None:
    assert aat._command_timeout(None) == 120.0
    assert aat._command_timeout(600) == 600.0
    with pytest.raises(aat.CommandPolicyError, match="600"):
        aat._command_timeout(601)


@pytest.mark.asyncio
async def test_command_pid_is_assigned_to_mission_container(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("print('ok')\n", encoding="utf-8")

    class _Job:
        def __init__(self) -> None:
            self.assigned: list[int] = []

        def assign(self, pid: int) -> None:
            self.assigned.append(pid)

    job = _Job()
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["ok.py"]},
        worktree=tmp_path,
        job=job,
    )
    assert err is False and out == "ok"
    assert len(job.assigned) == 1
    assert job.assigned[0] > 0


def _tree_script(tmp_path: Path, *, fast_leader: bool = False) -> Path:
    script = tmp_path / "spawn_tree.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "from pathlib import Path\n"
        "flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)\n"
        "child = subprocess.Popen(\n"
        "    [sys.executable, str(Path(__file__).with_name('sleep.py'))],\n"
        "    creationflags=flags,\n"
        ")\n"
        "Path('child.pid').write_text(str(child.pid), encoding='utf-8')\n"
        + ("pass\n" if fast_leader else "time.sleep(60)\n"),
        encoding="utf-8",
    )
    (tmp_path / "sleep.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    return script


async def _wait_for_pid_file(path: Path) -> int:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if path.is_file():  # noqa: ASYNC240 - tiny test synchronization probe
            return int(
                path.read_text(encoding="utf-8")  # noqa: ASYNC240 - tiny pid file
            )
        await asyncio.sleep(0.02)
    raise AssertionError("child pid file was not created")


async def _assert_pid_gone(pid: int) -> None:
    psutil = pytest.importorskip("psutil")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return
        await asyncio.sleep(0.05)
    with pytest.raises(psutil.NoSuchProcess):
        process = psutil.Process(pid)
        if process.is_running():
            raise AssertionError(f"descendant process {pid} survived command cleanup")


@pytest.mark.asyncio
async def test_command_timeout_reaps_descendant_tree(tmp_path: Path) -> None:
    _tree_script(tmp_path)
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["spawn_tree.py"], "timeout_s": 2.0},
        worktree=tmp_path,
    )
    child_pid = await _wait_for_pid_file(tmp_path / "child.pid")
    assert err is True
    assert "timed out" in out.lower()
    await _assert_pid_gone(child_pid)


@pytest.mark.asyncio
async def test_command_cancellation_reaps_descendant_tree(tmp_path: Path) -> None:
    _tree_script(tmp_path)
    task = asyncio.create_task(
        execute_worker_tool_async(
            "RunCommand",
            {"program": "python", "args": ["spawn_tree.py"]},
            worktree=tmp_path,
        )
    )
    child_pid = await _wait_for_pid_file(tmp_path / "child.pid")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _assert_pid_gone(child_pid)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object guarantee")
@pytest.mark.asyncio
async def test_windows_fast_leader_cannot_leave_descendant(tmp_path: Path) -> None:
    _tree_script(tmp_path, fast_leader=True)
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["spawn_tree.py"]},
        worktree=tmp_path,
    )
    child_pid = await _wait_for_pid_file(tmp_path / "child.pid")
    assert err is False, out
    await _assert_pid_gone(child_pid)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object guarantee")
@pytest.mark.asyncio
async def test_windows_noop_job_fails_before_target_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "started.txt"
    (tmp_path / "target.py").write_text(
        "from pathlib import Path\nPath('started.txt').write_text('bad')\n",
        encoding="utf-8",
    )
    from jarvis.missions.isolation.job_object import AlwaysOpenJobObject

    monkeypatch.setattr(
        aat,
        "WindowsJobObject",
        lambda name, **_kwargs: AlwaysOpenJobObject(name),
    )
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["target.py"]},
        worktree=tmp_path,
    )
    assert err is True
    assert "containment" in out.lower()
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object guarantee")
@pytest.mark.asyncio
async def test_windows_assignment_failure_never_opens_target_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "started.txt"
    (tmp_path / "target.py").write_text(
        "from pathlib import Path\nPath('started.txt').write_text('bad')\n",
        encoding="utf-8",
    )

    class _FailingJob:
        handle = object()
        closed = False

        def assign(self, _pid: int) -> None:
            raise OSError("simulated assignment failure")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(aat, "WindowsJobObject", lambda *_args, **_kwargs: _FailingJob())
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["target.py"]},
        worktree=tmp_path,
    )
    assert err is True
    assert "assignment failed" in out.lower()
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object guarantee")
@pytest.mark.asyncio
async def test_windows_breakaway_retry_is_contained_or_fails_before_target(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The generic worker retry cannot bypass the strict command gate."""
    from jarvis.missions.workers import process_utils

    marker = tmp_path / "started.txt"
    (tmp_path / "target.py").write_text(
        "from pathlib import Path\nPath('started.txt').write_text('ok')\n",
        encoding="utf-8",
    )
    breakaway = 0x01000000
    original_create = asyncio.create_subprocess_exec
    seen_flags: list[int] = []

    async def _deny_first_breakaway(*cmd: str, **kwargs: object):  # noqa: ANN202
        flags = int(kwargs.get("creationflags", 0))
        seen_flags.append(flags)
        if len(seen_flags) == 1 and flags & breakaway:
            raise PermissionError(5, "simulated parent Job breakaway denial")
        return await original_create(*cmd, **kwargs)

    monkeypatch.setattr(process_utils.asyncio, "create_subprocess_exec", _deny_first_breakaway)
    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["target.py"]},
        worktree=tmp_path,
    )

    assert len(seen_flags) == 2
    assert seen_flags[0] & breakaway
    assert not seen_flags[1] & breakaway
    if err:
        # Some ambient Windows jobs reject nested assignment. The trusted
        # launcher still cannot open its target gate in that case.
        assert "assignment failed" in out.lower()
        assert not marker.exists()
    else:
        assert marker.read_text(encoding="utf-8") == "ok"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group guarantee")
@pytest.mark.asyncio
async def test_posix_assignment_failure_never_opens_target_gate(tmp_path: Path) -> None:
    marker = tmp_path / "started.txt"
    (tmp_path / "target.py").write_text(
        "from pathlib import Path\nPath('started.txt').write_text('bad')\n",
        encoding="utf-8",
    )

    class _FailingJob:
        def assign(self, _pid: int) -> None:
            raise OSError("simulated assignment failure")

    out, err = await execute_worker_tool_async(
        "RunCommand",
        {"program": "python", "args": ["target.py"]},
        worktree=tmp_path,
        job=_FailingJob(),
    )
    assert err is True
    assert "assignment failed" in out.lower()
    assert not marker.exists()
