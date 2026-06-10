"""WindowsAutostart scheduled-task path — pure script builders + decision logic.

The Windows autostart was upgraded from a throttled ``shell:startup`` ``.lnk`` to
a **per-user logon scheduled task** (Task Scheduler is not subject to the Windows
11 startup-app throttle, so Jarvis starts within seconds of login instead of the
4-8 minutes the .lnk took on a machine with many startup programs).

Registering a task needs a one-time elevation (UAC); reading its state does not.
So:

  * the **script builders** are pure → CI-provable on any OS (here),
  * the **install/uninstall decision logic** is exercised with injected fakes
    (no real Task Scheduler, no real UAC), and
  * the real elevated ``Start-Process -Verb RunAs`` + ``.lnk`` I/O are live-only
    (Windows sign-off).

The ``.lnk`` remains the no-elevation **fallback** when the user declines UAC, so
autostart still works (just possibly delayed) — covered by ``test_windows_scripts``.
"""

from __future__ import annotations

from jarvis.autostart.protocol import LaunchSpec
from jarvis.autostart.windows import (
    TASK_NAME,
    WindowsAutostart,
    _TaskInfo,
    build_query_task_script,
    build_register_task_script,
    build_unregister_task_script,
    parse_task_query,
)

_SPEC = LaunchSpec(
    program=r"C:\Python\pythonw.exe",
    args=("-m", "jarvis.ui.web.launcher"),
    working_dir=r"C:\Users\u\Personal Jarvis",
)
_MATCHING_INFO = _TaskInfo(
    execute=_SPEC.program,
    arguments="-m jarvis.ui.web.launcher",
    working_dir=_SPEC.working_dir,
)


# --------------------------------------------------------------------------- #
# Pure script builders                                                         #
# --------------------------------------------------------------------------- #


def test_register_script_has_action_trigger_principal() -> None:
    script = build_register_task_script(TASK_NAME, _SPEC, user_id=r"DOM\u")
    assert "Register-ScheduledTask" in script
    assert "New-ScheduledTaskAction" in script
    assert r"-Execute 'C:\Python\pythonw.exe'" in script
    assert "-m jarvis.ui.web.launcher" in script
    assert r"C:\Users\u\Personal Jarvis" in script
    assert "AtLogOn" in script
    assert TASK_NAME in script


def test_register_script_runs_nonelevated_as_the_login_user() -> None:
    # RunLevel Limited = the launched Jarvis is NOT elevated (mic access, AP-17),
    # and the principal is the *captured* login user (not whoever approves UAC).
    script = build_register_task_script(TASK_NAME, _SPEC, user_id=r"DOM\alice")
    assert "Limited" in script
    assert r"DOM\alice" in script


def test_register_script_bakes_logon_delay() -> None:
    script = build_register_task_script(TASK_NAME, _SPEC, user_id="u", delay_seconds=20)
    assert "PT20S" in script


def test_unregister_script_is_idempotent() -> None:
    script = build_unregister_task_script(TASK_NAME)
    assert "Unregister-ScheduledTask" in script
    assert TASK_NAME in script
    assert "SilentlyContinue" in script  # absent task must not error


def test_query_script_emits_sentinel_lines() -> None:
    script = build_query_task_script(TASK_NAME)
    assert TASK_NAME in script
    assert script.count("Write-Output") >= 3


def test_parse_task_query_roundtrips_register_fields() -> None:
    out = parse_task_query(build_fake_query_output())
    assert out is not None
    assert out.execute == r"C:\Python\pythonw.exe"
    assert out.arguments == "-m jarvis.ui.web.launcher"
    assert out.working_dir == r"C:\Users\u\Personal Jarvis"


def test_parse_task_query_returns_none_when_absent() -> None:
    assert parse_task_query("") is None


def build_fake_query_output() -> str:
    from jarvis.autostart.windows import _QUERY_SENTINEL

    return "\n".join(
        [
            _QUERY_SENTINEL + r"C:\Python\pythonw.exe",
            _QUERY_SENTINEL + "-m jarvis.ui.web.launcher",
            _QUERY_SENTINEL + r"C:\Users\u\Personal Jarvis",
        ]
    )


# --------------------------------------------------------------------------- #
# Decision logic (injected fakes — no real Task Scheduler / UAC / .lnk)        #
# --------------------------------------------------------------------------- #


def _mk(
    *,
    task_info: _TaskInfo | None = None,
    elevate_ok: bool = True,
    shortcut_present: bool = False,
    shortcut_matches: bool = False,
) -> tuple[WindowsAutostart, list[str]]:
    calls: list[str] = []
    state = {"task": task_info, "shortcut": shortcut_present}

    def task_probe() -> _TaskInfo | None:
        return state["task"]

    def run_elevated(script: str) -> bool:
        if "Register-ScheduledTask" in script:
            calls.append("elevate_register")
            if elevate_ok:
                state["task"] = _MATCHING_INFO
        elif "Unregister-ScheduledTask" in script:
            calls.append("elevate_unregister")
            if elevate_ok:
                state["task"] = None
        return elevate_ok

    def sc_present() -> bool:
        return bool(state["shortcut"])

    def sc_matches(spec: LaunchSpec) -> bool:  # noqa: ARG001
        return shortcut_matches

    def write_sc(spec: LaunchSpec) -> None:  # noqa: ARG001
        calls.append("write_shortcut")
        state["shortcut"] = True

    def remove_sc() -> None:
        calls.append("remove_shortcut")
        state["shortcut"] = False

    mgr = WindowsAutostart(
        task_probe=task_probe,
        run_elevated=run_elevated,
        shortcut_present=sc_present,
        shortcut_matches=sc_matches,
        write_shortcut=write_sc,
        remove_shortcut=remove_sc,
    )
    return mgr, calls


def test_status_reports_scheduled_task_when_present_and_matching() -> None:
    mgr, _ = _mk(task_info=_MATCHING_INFO)
    st = mgr.status(_SPEC)
    assert st.installed is True
    assert st.matches_spec is True
    assert "scheduled task" in st.detail.lower()


def test_status_reports_drift_when_task_points_elsewhere() -> None:
    other = _TaskInfo(
        execute=r"C:\Old\pythonw.exe",
        arguments="-m jarvis.ui.web.launcher",
        working_dir=r"C:\Old",
    )
    mgr, _ = _mk(task_info=other)
    st = mgr.status(_SPEC)
    assert st.installed is True
    assert st.matches_spec is False


def test_status_falls_back_to_shortcut_when_no_task() -> None:
    mgr, _ = _mk(task_info=None, shortcut_present=True, shortcut_matches=True)
    st = mgr.status(_SPEC)
    assert st.installed is True
    assert st.matches_spec is True
    assert "shortcut" in st.detail.lower()


def test_status_not_installed_when_neither_present() -> None:
    mgr, _ = _mk(task_info=None, shortcut_present=False)
    st = mgr.status(_SPEC)
    assert st.installed is False


def test_install_interactive_registers_task_and_drops_shortcut() -> None:
    # User-initiated enable: prompt for UAC, register the task, and remove the
    # throttled fallback shortcut so Jarvis never double-starts.
    mgr, calls = _mk(task_info=None, elevate_ok=True, shortcut_present=True)
    st = mgr.install(_SPEC, interactive=True)
    assert "elevate_register" in calls
    assert "remove_shortcut" in calls
    assert "write_shortcut" not in calls
    assert st.matches_spec is True


def test_install_interactive_falls_back_to_shortcut_when_uac_declined() -> None:
    # User clicks "No" on UAC → no task, but autostart must still work → .lnk.
    mgr, calls = _mk(task_info=None, elevate_ok=False, shortcut_present=False)
    mgr.install(_SPEC, interactive=True)
    assert "elevate_register" in calls
    assert "write_shortcut" in calls


def test_install_noninteractive_writes_shortcut_without_prompting() -> None:
    # Boot reconcile must NEVER pop UAC; it ensures the no-elevation fallback.
    mgr, calls = _mk(task_info=None)
    mgr.install(_SPEC, interactive=False)
    assert "elevate_register" not in calls
    assert "write_shortcut" in calls


def test_install_noninteractive_keeps_fallback_when_task_is_stale() -> None:
    # Boot reconcile finds a task pointing at an old install (path drift, BUG-006
    # class). It must NOT elevate (no UAC at boot) — it ensures the shortcut
    # fallback so autostart still works until the user re-enables instant start.
    stale = _TaskInfo(
        execute=r"C:\Old\pythonw.exe",
        arguments="-m jarvis.ui.web.launcher",
        working_dir=r"C:\Old",
    )
    mgr, calls = _mk(task_info=stale)
    mgr.install(_SPEC, interactive=False)
    assert "elevate_register" not in calls
    assert "write_shortcut" in calls


def test_install_noninteractive_is_noop_when_task_already_matches() -> None:
    # Common boot: the task is already there and correct → do nothing at all.
    mgr, calls = _mk(task_info=_MATCHING_INFO)
    mgr.install(_SPEC, interactive=False)
    assert calls == []


def test_uninstall_interactive_removes_task_and_shortcut() -> None:
    mgr, calls = _mk(task_info=_MATCHING_INFO, shortcut_present=True)
    mgr.uninstall(interactive=True)
    assert "remove_shortcut" in calls
    assert "elevate_unregister" in calls


def test_uninstall_noninteractive_never_elevates() -> None:
    mgr, calls = _mk(task_info=_MATCHING_INFO, shortcut_present=True)
    mgr.uninstall(interactive=False)
    assert "remove_shortcut" in calls
    assert "elevate_unregister" not in calls
