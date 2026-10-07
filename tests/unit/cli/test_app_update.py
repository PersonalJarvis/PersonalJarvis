"""`jarvis update` — the terminal twin of the in-app update button."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.cli import app_update
from jarvis.cli.app_update import (
    EXIT_APP_RUNNING,
    EXIT_FAILED,
    EXIT_NOT_UPDATABLE,
    FINALIZE_FLAG,
    PROBE_FLAG,
    run_update,
)
from jarvis.ui.relauncher import UPDATE_RESULT_FILENAME


class FakeChildren:
    """Answers the child processes run_update would spawn."""

    def __init__(self, *, status: dict, stage: dict | None = None, finalize_rc: int = 0,
                 on_finalize=None) -> None:
        self.status = status
        self.stage = stage
        self.finalize_rc = finalize_rc
        self.on_finalize = on_finalize
        self.calls: list[tuple[list[str], float | None, bool]] = []

    def __call__(self, args: list[str], timeout: float | None, detached: bool):
        self.calls.append((args, timeout, detached))
        if args == [PROBE_FLAG, "status"]:
            return 0, "log noise\n" + json.dumps(self.status) + "\n"
        if args == [PROBE_FLAG, "stage"]:
            return 0, json.dumps(self.stage or {}) + "\n"
        if args[0] == FINALIZE_FLAG:
            if self.on_finalize is not None:
                self.on_finalize(Path(args[1]))
            return self.finalize_rc, ""
        raise AssertionError(f"unexpected child {args}")

    def modes(self) -> list[str]:
        return [args[1] if args[0] == PROBE_FLAG else "finalize" for args, _, _ in self.calls]


def _status(**overrides) -> dict:
    base = {
        "managed": True,
        "kind": "managed",
        "current": "2.8.0",
        "latest": "2.9.0",
        "update_available": True,
        "service_pid": None,
    }
    return {**base, **overrides}


def _run(children: FakeChildren, **kwargs) -> int:
    kwargs.setdefault("is_frozen", lambda: False)
    kwargs.setdefault("running_app_pid", lambda: None)
    kwargs.setdefault("interactive", False)
    return run_update(run_child=children, **kwargs)


def _write_result(root: Path, **payload) -> None:
    (root / UPDATE_RESULT_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_frozen_install_points_to_the_in_app_button(capsys):
    children = FakeChildren(status=_status())
    assert _run(children, is_frozen=lambda: True) == EXIT_NOT_UPDATABLE
    assert children.calls == []
    assert "Settings -> Update" in capsys.readouterr().out


def test_dev_checkout_is_never_updated(capsys):
    children = FakeChildren(status=_status(managed=False, kind="dev"))
    assert _run(children) == EXIT_NOT_UPDATABLE
    assert children.modes() == ["status"]
    assert "git pull" in capsys.readouterr().out


def test_already_current_changes_nothing(capsys):
    children = FakeChildren(status=_status(latest="2.8.0", update_available=False))
    assert _run(children) == 0
    assert children.modes() == ["status"]
    assert "already the newest version" in capsys.readouterr().out


def test_check_only_reports_and_stops(capsys):
    children = FakeChildren(status=_status())
    assert _run(children, check_only=True) == 0
    assert children.modes() == ["status"]
    assert "2.9.0 is available" in capsys.readouterr().out


def test_offline_check_fails_honestly(capsys):
    children = FakeChildren(status=_status(latest=None, update_available=False, check_failed=True))
    assert _run(children) == EXIT_FAILED
    assert "Could not reach GitHub" in capsys.readouterr().err


def test_running_app_blocks_the_update(capsys):
    children = FakeChildren(status=_status())
    assert _run(children, running_app_pid=lambda: 4242) == EXIT_APP_RUNNING
    assert children.modes() == ["status"]
    assert "click Update inside the app" in capsys.readouterr().err


def test_background_service_is_asked_to_hand_back(tmp_path):
    stopped: list[int] = []
    children = FakeChildren(
        status=_status(service_pid=777),
        stage={"ok": True, "root": str(tmp_path), "version": "2.9.0"},
        on_finalize=lambda root: _write_result(root, ok=True, rolled_back=False),
    )
    rc = _run(
        children,
        running_app_pid=lambda: 777,
        stop_service=lambda pid: stopped.append(pid) or True,
    )
    assert rc == 0
    assert stopped == [777]
    assert children.modes() == ["status", "stage", "finalize"]


def test_service_that_will_not_stop_blocks_the_update():
    children = FakeChildren(status=_status(service_pid=777))
    rc = _run(children, running_app_pid=lambda: 777, stop_service=lambda pid: False)
    assert rc == EXIT_APP_RUNNING
    assert children.modes() == ["status"]


def test_successful_update_finalizes_detached(tmp_path, capsys):
    children = FakeChildren(
        status=_status(),
        stage={"ok": True, "root": str(tmp_path), "version": "2.9.0"},
        on_finalize=lambda root: _write_result(root, ok=True, rolled_back=False),
    )
    assert _run(children) == 0
    finalize_args, timeout, detached = children.calls[-1]
    assert finalize_args == [FINALIZE_FLAG, str(tmp_path)]
    assert timeout is None and detached is True
    assert "2.9.0 is installed" in capsys.readouterr().out


def test_stage_failure_installs_nothing(capsys):
    children = FakeChildren(
        status=_status(), stage={"ok": False, "error": "git fetch failed: offline"}
    )
    assert _run(children) == EXIT_FAILED
    assert children.modes() == ["status", "stage"]
    err = capsys.readouterr().err
    assert "git fetch failed" in err
    assert "Nothing was changed" in err


def test_failed_install_reports_the_rollback(tmp_path, capsys):
    children = FakeChildren(
        status=_status(),
        stage={"ok": True, "root": str(tmp_path), "version": "2.9.0"},
        finalize_rc=1,
        on_finalize=lambda root: _write_result(root, ok=False, rolled_back=True),
    )
    assert _run(children) == EXIT_FAILED
    assert "went back to 2.8.0" in capsys.readouterr().err


def test_failed_install_without_rollback_names_the_repair(tmp_path, capsys):
    children = FakeChildren(
        status=_status(),
        stage={"ok": True, "root": str(tmp_path), "version": "2.9.0"},
        finalize_rc=1,
        on_finalize=lambda root: _write_result(root, ok=False, rolled_back=False),
    )
    assert _run(children) == EXIT_FAILED
    assert "install/install." in capsys.readouterr().err


def test_unreadable_child_output_is_a_failure(capsys):
    def broken(args, timeout, detached):
        return 1, "Traceback (most recent call last): ..."

    assert run_update(
        run_child=broken, is_frozen=lambda: False, running_app_pid=lambda: None,
        interactive=False,
    ) == EXIT_FAILED


@pytest.mark.parametrize("argv", [[], ["--probe"], ["--probe", "nope"], ["--finalize"]])
def test_child_entry_rejects_unknown_arguments(argv):
    assert app_update._child_main(argv) == 2


def test_last_json_skips_log_lines():
    assert app_update._last_json('INFO x\n{"a": 1}\n\n') == {"a": 1}
    assert app_update._last_json("no json here") is None


def test_jarvis_main_routes_update(monkeypatch):
    import jarvis.__main__ as entry

    seen: list[bool] = []
    monkeypatch.setattr(
        app_update, "run_update", lambda check_only=False: seen.append(check_only) or 0
    )
    assert entry.main(["update", "--check"]) == 0
    assert seen == [True]
