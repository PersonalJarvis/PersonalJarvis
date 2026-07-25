"""Guards for the unelevated relaunch used to recover input reachability.

The contract that matters here is HONESTY: when privileges cannot be dropped,
the caller must learn that instead of quietly relaunching elevated again. A
"repaired" app that still ignores the user's dictation software — with a button
that reported success — is the worst possible outcome.
"""

from __future__ import annotations

from jarvis.platform.deescalate import (
    DeescalationResult,
    environment_block,
    spawn_unelevated,
)


class TestEnvironmentBlock:
    def test_pairs_are_nul_separated_and_double_nul_terminated(self):
        block = environment_block({"A": "1", "B": "2"})
        assert block == "A=1\0B=2\0\0"

    def test_names_are_sorted_case_insensitively(self):
        """Windows documents the block as case-insensitively sorted; an
        unsorted one is 'undefined behaviour' on the restart path."""
        block = environment_block({"beta": "2", "Alpha": "1", "GAMMA": "3"})
        assert block == "Alpha=1\0beta=2\0GAMMA=3\0\0"

    def test_empty_environment_still_terminates(self):
        assert environment_block({}) == "\0"

    def test_values_containing_separators_survive_verbatim(self):
        block = environment_block({"PATH": "C:\\a;C:\\b", "Q": "x=y"})
        assert "PATH=C:\\a;C:\\b\0" in block
        assert "Q=x=y\0" in block


class TestPlatformGating:
    def test_posix_reports_an_actionable_refusal_rather_than_pretending(self):
        result = spawn_unelevated([], cwd=".", env={}, _platform="linux")
        assert result.ok is False
        assert result.pid is None
        assert "normal user account" in result.detail

    def test_windows_path_is_delegated_with_the_caller_arguments(self):
        seen: dict[str, object] = {}

        def fake(argv, *, cwd, env, creationflags):
            seen.update(argv=argv, cwd=cwd, env=env, creationflags=creationflags)
            return DeescalationResult(True, 4242, "ok")

        result = spawn_unelevated(
            ["py", "-m", "x"],
            cwd="C:\\repo",
            env={"A": "1"},
            creationflags=8,
            _platform="win32",
            _spawn=fake,
        )
        assert result.ok is True
        assert result.pid == 4242
        assert seen == {
            "argv": ["py", "-m", "x"],
            "cwd": "C:\\repo",
            "env": {"A": "1"},
            "creationflags": 8,
        }


class TestFailureContainment:
    def test_a_raising_spawn_becomes_a_reported_failure_not_a_crash(self):
        """This runs while the app is mid-restart; an exception escaping here
        would take down a live desktop session."""

        def boom(argv, *, cwd, env, creationflags):
            raise OSError("token duplication denied")

        result = spawn_unelevated(
            ["py"], cwd=".", env={}, _platform="win32", _spawn=boom
        )
        assert result.ok is False
        assert result.pid is None
        assert "token duplication denied" in result.detail

    def test_failure_never_reports_a_pid(self):
        def refuse(argv, *, cwd, env, creationflags):
            return DeescalationResult(False, None, "no linked token")

        assert spawn_unelevated(
            ["py"], cwd=".", env={}, _platform="win32", _spawn=refuse
        ).pid is None
