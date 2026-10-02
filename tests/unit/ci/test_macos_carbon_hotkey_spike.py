"""``scripts/ci/macos_carbon_hotkey_spike.py`` - the evidence tool for a future Carbon backend.

Nothing here runs Carbon, AppKit or a macOS runner, and nothing here is evidence
about macOS behaviour. These tests pin the parts that CAN be checked on Linux:

* the experiment catalogue (names and order are the report contract),
* signal / outcome classification, tails, JSON assembly and the summary tables,
* the evidence rule (a granted preflight voids a run),
* the shape of the ctypes surface (``restype``/``argtypes`` of every function,
  struct sizes, four-character codes) against an in-memory fake Carbon, and the
  sequencing of every scenario against that same fake,
* the REAL ctypes marshalling against a tiny C library that exports the Carbon
  prototypes (skipped without a C compiler): a truncated 64-bit pointer or a
  by-pointer ``EventHotKeyID`` makes the C side return an error,
* the process-group handling of the parent against real subprocesses with a
  sleeping grandchild,
* the workflow file (dispatch-only, read-only token, pinned actions, summary text).

What a fake or a Linux C library cannot show is whether the real Carbon calls
behave the same on a real Mac; that stays unverified until the workflow has run there.
"""

from __future__ import annotations

import ast
import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci import macos_carbon_hotkey_spike as spike

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "macos-hotkey-spike.yml"

EXPECTED_NAMES = (
    "control_signal_capture",
    "tcc_context",
    "carbon_symbols",
    "nsapp_register_unregister",
    "register_unregister_cycles",
    "no_nsapp_register",
    "unregister_off_main_thread",
    "duplicate_register",
    "register_under_secure_input",
    "install_handler_twice",
    "appshot_option_state_read",
)


@pytest.fixture(autouse=True)
def _fresh_child_state():
    spike.reset_child_state()
    yield
    spike.reset_child_state()


# --- catalogue --------------------------------------------------------------------


def test_catalogue_names_and_order_are_pinned() -> None:
    assert spike.experiment_names() == EXPECTED_NAMES


def test_catalogue_entries_are_well_formed() -> None:
    names = spike.experiment_names()
    assert len(set(names)) == len(names)
    for experiment in spike.EXPERIMENTS:
        assert experiment.kind in ("control", "variant", "probe")
        assert experiment.timeout_s > 0
        assert experiment.title and experiment.question
    # Only the signal control and the TCC context (which reports "unsupported" off macOS) run
    # everywhere; every Carbon experiment needs macOS.
    assert [e.name for e in spike.EXPERIMENTS if not e.requires_macos] == [
        "control_signal_capture",
        "tcc_context",
    ]
    # Only controls carry an expectation; variants are measurements.
    assert {e.name: e.expected_outcome for e in spike.EXPERIMENTS if e.expected_outcome} == {
        "control_signal_capture": "crash",
        "carbon_symbols": "ok",
    }


def test_every_catalogued_experiment_has_a_child_scenario() -> None:
    assert set(spike._SCENARIOS) == set(spike.experiment_names())


def test_select_experiments() -> None:
    assert spike.select_experiments("") == spike.EXPERIMENTS
    picked = spike.select_experiments("duplicate_register, carbon_symbols")
    assert [e.name for e in picked] == ["carbon_symbols", "duplicate_register"]
    with pytest.raises(ValueError, match="bogus"):
        spike.select_experiments("carbon_symbols,bogus")


def test_the_tcc_context_always_runs_first_whatever_only_selects() -> None:
    # The report's TCC block comes from this child: `--only` must not be able to drop it.
    planned = spike.plan_experiments("duplicate_register")
    assert [e.name for e in planned] == ["tcc_context", "duplicate_register"]
    assert spike.plan_experiments("") == spike.EXPERIMENTS
    assert [e.name for e in spike.plan_experiments("tcc_context")] == ["tcc_context"]
    with pytest.raises(ValueError, match="bogus"):
        spike.plan_experiments("bogus")
    # It runs before every Carbon variant (the signal control, which loads nothing, is earlier).
    names = list(spike.experiment_names())
    assert names.index("tcc_context") < names.index("carbon_symbols")


# --- classification ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [
        (-int(signal.SIGSEGV), "SIGSEGV"),
        (-int(signal.SIGABRT), "SIGABRT"),
        (-int(signal.SIGILL), "SIGILL"),
        (-999, "SIG999"),
        (0, None),
        (1, None),
        (None, None),
    ],
)
def test_signal_name(returncode: int | None, expected: str | None) -> None:
    assert spike.signal_name(returncode) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"returncode": None, "timed_out": True, "child_ok": None}, "timeout"),
        ({"returncode": -11, "timed_out": True, "child_ok": None}, "timeout"),
        ({"returncode": -11, "timed_out": False, "child_ok": None}, "crash"),
        ({"returncode": -6, "timed_out": False, "child_ok": True}, "crash"),
        ({"returncode": 0, "timed_out": False, "child_ok": True}, "ok"),
        ({"returncode": 0, "timed_out": False, "child_ok": False}, "error"),
        ({"returncode": 0, "timed_out": False, "child_ok": None}, "error"),
        ({"returncode": 1, "timed_out": False, "child_ok": True}, "error"),
        ({"returncode": None, "timed_out": False, "child_ok": None}, "error"),
    ],
)
def test_classify_outcome(kwargs: dict[str, Any], expected: str) -> None:
    assert spike.classify_outcome(**kwargs) == expected
    assert expected in spike.OUTCOMES


def test_tail_text() -> None:
    assert spike.tail_text(None) == ""
    assert spike.tail_text(b"abc\x00def") == "abcdef"
    assert spike.tail_text("x" * 5000, limit=10) == "x" * 10
    assert spike.tail_text(b"\xff\xfeok").endswith("ok")  # undecodable bytes never raise


def test_summarize_argv_drops_machine_specific_directories() -> None:
    argv = ["/opt/hostedtoolcache/Python/3.12/bin/python3", "/Users/runner/w/s.py", "--child", "x"]
    assert spike.summarize_argv(argv) == ["python3", "s.py", "--child", "x"]
    assert spike.summarize_argv([]) == []


def test_parse_child_result_takes_the_last_result_line() -> None:
    out = "STEP a=1\n" + spike.RESULT_PREFIX + '{"ok": false}\nnoise\n'
    out += spike.RESULT_PREFIX + '{"ok": true, "n": 2}\n'
    assert spike.parse_child_result(out) == {"ok": True, "n": 2}
    assert spike.parse_child_result("no result here\n") is None
    broken = spike.parse_child_result(spike.RESULT_PREFIX + "{nope\n")
    assert broken is not None and "ok" not in broken and "_parse_error" in broken
    assert "_parse_error" in (spike.parse_child_result(spike.RESULT_PREFIX + "[1]\n") or {})


def test_last_breadcrumb_locates_a_crash() -> None:
    out = "STEP install_handler=0\nSTEP register=0\nFatal Python error: Segmentation fault\n"
    assert spike.last_breadcrumb(out) == "register=0"
    assert spike.last_breadcrumb("nothing") is None


def test_child_environment_sets_utf8_and_faulthandler_without_dropping_the_base() -> None:
    env = spike.child_environment({"PATH": "/bin", "HOME": "/h"})
    assert env["PATH"] == "/bin" and env["HOME"] == "/h"
    assert env["PYTHONFAULTHANDLER"] == "1" and env["PYTHONUTF8"] == "1"
    assert env["PYTHONUNBUFFERED"] == "1"


def test_probe_records_failures_and_prefers_the_first_working_source() -> None:
    def broken() -> bool:
        raise ImportError("no pyobjc")

    result = spike.probe("x", [("pyobjc", broken), ("ctypes", lambda: True)])
    assert result["value"] is True and result["source"] == "ctypes" and result["error"] is None
    assert result["attempts"] == [{"source": "pyobjc", "error": "ImportError: no pyobjc"}]

    failed = spike.probe("x", [("pyobjc", broken), ("ctypes", broken)])
    assert failed["value"] is None and failed["source"] is None
    assert "pyobjc: ImportError" in failed["error"] and len(failed["attempts"]) == 2

    assert spike.probe("x", [])["error"] == "no source available"


# --- evidence rule -----------------------------------------------------------------


def _tcc(listen: Any = False, trusted: Any = False, iohid: Any = 1) -> dict[str, Any]:
    def entry(value: Any) -> dict[str, Any]:
        return {"value": value, "error": None if value is not None else "unreadable"}

    return {
        "supported": True,
        "probes": {
            "cg_preflight_listen_event": entry(listen),
            "ax_is_process_trusted": entry(trusted),
            "iohid_check_access_listen": entry(iohid),
        },
    }


def _nsapp_row(
    *,
    pressed: int,
    requested: bool = True,
    tcc: Any = None,
    register: int = 0,
    pressed_after_unregister: int = 0,
    injection_seen: bool | None = True,
    outcome: str = "ok",
) -> dict[str, Any]:
    """A ``nsapp_register_unregister`` row as the parent assembles it (steps + counts)."""
    steps = [
        {"step": "register", "value": register},
        {"step": "pressed_while_registered", "value": pressed},
        {"step": "injection_seen_while_registered", "value": injection_seen},
        {"step": "pressed_after_unregister", "value": pressed_after_unregister},
    ]
    total = pressed + pressed_after_unregister
    return {
        "name": "nsapp_register_unregister",
        "outcome": outcome,
        "observations": {
            "tcc": tcc,
            "steps": steps,
            "event_counts": {"pressed": total, "released": total},
            "synthetic_key_requested": requested,
        },
    }


def test_evidence_is_usable_only_with_false_preflights_and_a_delivered_event() -> None:
    verdict = spike.evaluate_evidence(_tcc(), [_nsapp_row(pressed=1, tcc=_tcc())])
    assert verdict["preflights_all_false"] is True
    assert verdict["carbon_event_delivered"] is True
    assert verdict["delivery_conclusion"] == "delivered"
    assert verdict["usable_as_evidence"] is True
    assert verdict["tcc_source"] == "child"
    assert spike.EVIDENCE_RULE == verdict["rule"]


@pytest.mark.parametrize(
    "tcc",
    [_tcc(listen=True), _tcc(trusted=True), _tcc(iohid=0)],
    ids=["listen-granted", "ax-trusted", "iohid-granted"],
)
def test_a_granted_preflight_voids_the_run(tcc: dict[str, Any]) -> None:
    verdict = spike.evaluate_evidence(tcc, [_nsapp_row(pressed=1)])
    assert verdict["preflights_all_false"] is False
    assert verdict["usable_as_evidence"] is False
    assert any("discard" in note for note in verdict["notes"])


def test_the_childs_own_tcc_beats_the_parents() -> None:
    verdict = spike.evaluate_evidence(_tcc(), [_nsapp_row(pressed=1, tcc=_tcc(listen=True))])
    assert verdict["tcc_source"] == "child"
    assert verdict["preflights_all_false"] is False


def test_unreadable_preflight_is_not_claimable() -> None:
    verdict = spike.evaluate_evidence(_tcc(trusted=None), [_nsapp_row(pressed=1)])
    assert verdict["preflights_all_false"] is None
    assert verdict["usable_as_evidence"] is False


def test_delivery_unknown_without_a_synthetic_key_and_false_without_an_event() -> None:
    off = spike.evaluate_evidence(_tcc(), [_nsapp_row(pressed=0, requested=False)])
    assert off["carbon_event_delivered"] is None and off["usable_as_evidence"] is False
    none_arrived = spike.evaluate_evidence(_tcc(), [_nsapp_row(pressed=0)])
    assert none_arrived["carbon_event_delivered"] is False
    assert spike.evaluate_evidence(_tcc(), [])["carbon_event_delivered"] is None
    unsupported = spike.evaluate_evidence({"supported": False, "probes": {}}, [])
    assert unsupported["preflights_all_false"] is None
    assert unsupported["tcc_source"] == "tcc_context"  # the separate tcc_context child
    assert unsupported["delivery_conclusion"] == "unknown"


def test_a_press_after_the_unregister_is_not_a_delivery_while_registered() -> None:
    # The regression: nothing arrived while registered, but one Pressed arrived in the
    # 'after_unregister' window. The whole-scenario total (1) used to make this usable evidence
    # that Carbon needs no grant; the delivery is judged on the registered window alone.
    row = _nsapp_row(pressed=0, pressed_after_unregister=1)
    assert row["observations"]["event_counts"]["pressed"] == 1
    verdict = spike.evaluate_evidence(_tcc(), [row])
    assert verdict["preflights_all_false"] is True
    assert verdict["carbon_event_delivered"] is False
    assert verdict["usable_as_evidence"] is False


def test_a_failed_registration_never_counts_as_a_delivery() -> None:
    row = _nsapp_row(pressed=1, register=spike.EVENT_HOT_KEY_EXISTS_ERR, outcome="error")
    verdict = spike.evaluate_evidence(_tcc(), [row])
    assert verdict["carbon_event_delivered"] is False
    assert verdict["delivery_conclusion"] == "registration_failed"
    assert verdict["usable_as_evidence"] is False
    assert any(f"OSStatus {spike.EVENT_HOT_KEY_EXISTS_ERR}" in note for note in verdict["notes"])


@pytest.mark.parametrize("outcome", ["error", "crash", "timeout"])
def test_a_variant_that_did_not_end_ok_is_not_clean_evidence(outcome: str) -> None:
    # A delivered Pressed with an unexpected status elsewhere (e.g. the unregister did not
    # take effect) is a real observation but not usable evidence.
    verdict = spike.evaluate_evidence(_tcc(), [_nsapp_row(pressed=1, outcome=outcome)])
    assert verdict["carbon_event_delivered"] is True and verdict["variant_ok"] is False
    assert verdict["usable_as_evidence"] is False
    assert any("not clean evidence" in note for note in verdict["notes"])


@pytest.mark.parametrize(
    ("injection_seen", "conclusion"),
    [(True, "not_delivered"), (False, "inconclusive"), (None, "inconclusive")],
)
def test_no_event_is_only_a_carbon_failure_when_the_injection_was_seen_independently(
    injection_seen: bool | None, conclusion: str
) -> None:
    # Zero delivered events looks the same whether Carbon is silent or osascript never sent a
    # key (no Automation/Accessibility grant). The key-down counter is the control.
    row = _nsapp_row(pressed=0, injection_seen=injection_seen)
    verdict = spike.evaluate_evidence(_tcc(), [row])
    assert verdict["carbon_event_delivered"] is False
    assert verdict["delivery_conclusion"] == conclusion
    assert verdict["injection_seen_independently"] is injection_seen
    assert verdict["usable_as_evidence"] is False
    if conclusion == "inconclusive":
        assert any("inconclusive" in note for note in verdict["notes"])


def test_injection_seen_compares_the_key_down_counters() -> None:
    assert spike.injection_seen(
        {"combined_session": 3, "hid_system": 0}, {"combined_session": 4, "hid_system": 0}
    )
    assert spike.injection_seen({"a": 1}, {"a": 1}) is False
    assert spike.injection_seen(None, {"a": 1}) is None
    assert spike.injection_seen({"a": 1}, None) is None


def test_step_value_reads_the_recorded_step_or_none() -> None:
    observations = {"steps": [{"step": "register", "value": 0}, {"step": "x", "value": None}]}
    assert spike.step_value(observations, "register") == 0
    assert spike.step_value(observations, "missing") is None
    assert spike.step_value({}, "register") is None
    assert spike.step_value({"steps": "register"}, "register") is None


# --- appshot read ------------------------------------------------------------------


def test_compare_option_reads_with_no_key_held() -> None:
    reads = {
        "combined_session": {"key_left": False, "key_right": False, "flags_raw": 0x100},
        "hid_system": {"key_left": False, "key_right": False, "flags_raw": 0},
    }
    result = spike.compare_option_reads(reads)
    assert result["as_expected_no_key_held"] is True and not result["any_pressed_reported"]
    assert result["states"]["combined_session"]["flags_raw_hex"] == "0x100"
    assert all(state["agree"] for state in result["states"].values())
    assert "cannot show whether KeyState needs Input Monitoring" in result["limitation"]


def test_compare_option_reads_flags_a_disagreement_between_the_two_apis() -> None:
    alt = spike.K_CG_EVENT_FLAG_MASK_ALTERNATE | spike.NX_DEVICE_RIGHT_ALT_KEY_MASK
    reads = {"combined_session": {"key_left": False, "key_right": False, "flags_raw": alt}}
    state = spike.compare_option_reads(reads)["states"]["combined_session"]
    assert state["flags_alternate"] is True and state["flags_right_device_bit"] is True
    assert state["flags_left_device_bit"] is False and state["agree"] is False
    assert spike.compare_option_reads(reads)["as_expected_no_key_held"] is False


def _read(left: bool = False, right: bool = False, flags: int = 0) -> dict[str, Any]:
    return {"key_left": left, "key_right": right, "flags_raw": flags}


def test_merge_option_samples_keeps_the_peak_of_the_window() -> None:
    # The key is down for only part of the window: the peak must not depend on timing.
    samples = [
        {"hid_system": _read(), "combined_session": _read(flags=0x100)},
        {"hid_system": _read(left=True), "combined_session": _read(flags=0x80000 | 0x20)},
        {"hid_system": _read(), "combined_session": _read(flags=0x100)},
    ]
    merged = spike.merge_option_samples(samples)
    assert merged["hid_system"] == {"key_left": True, "key_right": False, "flags_raw": 0}
    assert merged["combined_session"]["flags_raw"] == 0x100 | 0x80000 | 0x20
    assert spike.merge_option_samples([]) == {}


def _phase(reads: dict[str, Any], exit_status: int | None = 0) -> dict[str, Any]:
    return {"reads": reads, "injection_exit": exit_status, "samples": 7}


def test_option_hold_verdict_without_an_injection_says_only_the_baseline_was_read() -> None:
    baseline = {"hid_system": _read(), "combined_session": _read()}
    verdict = spike.option_hold_verdict(baseline, {})
    assert verdict["phases"] == {} and verdict["any_api_saw_option"] is False
    assert verdict["baseline"]["as_expected_no_key_held"] is True
    assert any("only the no-key baseline" in note for note in verdict["notes"])
    assert "not decided here" in verdict["limitation"]


def test_option_hold_verdict_separates_keystate_from_flagsstate_per_source_state() -> None:
    baseline = {"hid_system": _read(), "combined_session": _read()}
    alt = spike.K_CG_EVENT_FLAG_MASK_ALTERNATE | spike.NX_DEVICE_RIGHT_ALT_KEY_MASK
    phases = {
        # FlagsState (combined) saw the right Option; KeyState saw nothing anywhere.
        "cgevent_right_option": _phase(
            {"hid_system": _read(), "combined_session": _read(flags=alt)}
        ),
        # KeyState (HID) saw the left Option; FlagsState saw nothing.
        "cgevent_left_option": _phase(
            {"hid_system": _read(left=True), "combined_session": _read()}
        ),
    }
    verdict = spike.option_hold_verdict(baseline, phases)
    right = verdict["phases"]["cgevent_right_option"]["states"]["combined_session"]
    assert right["flagsstate_saw_option"] is True and right["keystate_saw_option"] is False
    assert right["sides_seen"] == ["right"]
    left = verdict["phases"]["cgevent_left_option"]["states"]["hid_system"]
    assert left["keystate_saw_option"] is True and left["flagsstate_saw_option"] is False
    assert left["sides_seen"] == ["left"]
    assert verdict["any_api_saw_option"] is True and verdict["injected_routes_exit_0"] == 2
    assert any("FlagsState saw the key, KeyState did not" in n for n in verdict["notes"])
    assert any("KeyState saw the key, FlagsState did not" in n for n in verdict["notes"])


def test_option_hold_verdict_is_inconclusive_when_no_api_saw_a_held_key() -> None:
    # The exact failure the old no-key read could not tell apart: everything false.
    baseline = {"hid_system": _read(), "combined_session": _read()}
    nothing = {"hid_system": _read(), "combined_session": _read(flags=0x100)}
    verdict = spike.option_hold_verdict(baseline, {"system_events_option": _phase(nothing)})
    assert verdict["any_api_saw_option"] is False
    assert any("inconclusive" in note for note in verdict["notes"])
    # A failed injection route is its own conclusion: nothing can be said about the key.
    failed = spike.option_hold_verdict(baseline, {"system_events_option": _phase(nothing, 1)})
    assert failed["injected_routes_exit_0"] == 0
    assert any("no injection route exited 0" in note for note in failed["notes"])


def test_option_hold_script_holds_then_always_releases() -> None:
    by_name = {phase.name: phase for phase in spike.OPTION_HOLD_PHASES}
    assert list(by_name) == ["system_events_option", "cgevent_left_option", "cgevent_right_option"]
    events = spike.option_hold_script(by_name["system_events_option"], 2.5)
    assert (
        events.index("key down option") < events.index("delay 2.5") < events.index("key up option")
    )
    assert events.count("key up option") == 2  # normal path and the error path
    assert "error errorMessage number errorNumber" in events  # a failure is re-raised, not hidden
    left = spike.option_hold_script(by_name["cgevent_left_option"], 2.5)
    right = spike.option_hold_script(by_name["cgevent_right_option"], 2.5)
    assert "CGEventCreateKeyboardEvent(null, 58, down)" in left
    assert "CGEventCreateKeyboardEvent(null, 61, down)" in right
    assert "finally" in left and "post(false)" in left.split("finally")[1]
    with pytest.raises(ValueError, match="unsupported"):
        spike.option_hold_script(spike.OptionHoldPhase("x", "javascript", None, "no key code"))


def test_option_holder_builds_the_osascript_command_line_per_language() -> None:
    by_name = {phase.name: phase for phase in spike.OPTION_HOLD_PHASES}
    apple = spike.OptionHolder(by_name["system_events_option"], run=lambda *a, **k: None)
    assert apple.argv()[:2] == ["osascript", "-e"] and "key down option" in apple.argv()[2]
    js = spike.OptionHolder(by_name["cgevent_right_option"], run=lambda *a, **k: None)
    assert js.argv()[:3] == ["osascript", "-l", "JavaScript"] and js.argv()[3] == "-e"


class _FakeHolder:
    """A holder that finishes after a fixed number of samples, like osascript does."""

    def __init__(self, phase: Any, *, exit_status: int | None = 0, samples_needed: int = 3) -> None:
        self.phase = phase
        self.timeout_s = 1.0
        self.done = threading.Event()
        self.result: dict[str, Any] = {"returncode": exit_status}
        self.samples_needed = samples_needed

    def start(self) -> None:
        pass


def test_hold_and_sample_takes_the_peak_and_one_read_after_the_release() -> None:
    phase = spike.OPTION_HOLD_PHASES[1]
    reads = iter(
        [
            {"hid_system": _read()},
            {"hid_system": _read(left=True)},  # the key is down for the middle sample only
            {"hid_system": _read()},
            {"hid_system": _read(right=True)},  # a stuck key after the release
        ]
    )
    holder = _FakeHolder(phase)

    def read() -> Any:
        value = next(reads)
        if len(holder_samples) >= 2:
            holder.done.set()
        holder_samples.append(value)
        return value

    holder_samples: list[Any] = []
    result = spike.hold_and_sample(
        phase, read, holder_factory=lambda _phase: holder, sleep=lambda _s: None
    )
    assert result["samples"] == 3 and result["finished"] is True
    assert result["reads"]["hid_system"]["key_left"] is True
    assert result["reads_after_release"]["hid_system"]["key_right"] is True
    assert result["injection_exit"] == 0


def test_hold_and_sample_gives_up_at_the_deadline_when_osascript_never_finishes() -> None:
    clock_values = iter([0.0, 1.0, 2.0, 3.0, 100.0, 101.0])
    holder = _FakeHolder(spike.OPTION_HOLD_PHASES[0], exit_status=None)
    result = spike.hold_and_sample(
        spike.OPTION_HOLD_PHASES[0],
        lambda: {"hid_system": _read()},
        holder_factory=lambda _phase: holder,
        sleep=lambda _s: None,
        clock=lambda: next(clock_values),
    )
    assert result["finished"] is False and result["injection_exit"] is None
    assert result["samples"] >= 1


class _FakeCoreGraphics:
    """CGEventSource*State stand-ins: the Option key reads down while ``held`` is set."""

    def __init__(self) -> None:
        self.held: set[int] = set()

    def bind(self, _frameworks: Any, symbol: str, _restype: Any, _argtypes: Any) -> Any:
        if symbol == "CGEventSourceKeyState":
            return lambda _state, key: int(key in self.held)
        if symbol == "CGEventSourceFlagsState":
            return lambda _state: spike.K_CG_EVENT_FLAG_MASK_ALTERNATE if self.held else 0
        raise OSError(symbol)


def test_the_appshot_scenario_reads_a_held_key_and_reports_a_verdict(monkeypatch) -> None:
    graphics = _FakeCoreGraphics()
    monkeypatch.setattr(spike, "_bind_symbol", graphics.bind)

    class Holder(_FakeHolder):
        """Only the left-Option route 'works': it holds key 58 down, then releases it."""

        def start(self) -> None:
            graphics.held = {spike.VK_OPTION} if self.phase.key_code == spike.VK_OPTION else set()

            def release() -> None:
                graphics.held = set()
                self.done.set()

            threading.Timer(0.15, release).start()

    monkeypatch.setattr(spike, "OptionHolder", Holder)
    spike._STATE.inject = True
    result = spike._scenario_appshot_option_state_read()
    steps = _steps()
    assert steps["combined_session_keystate_left"] is False  # the no-key baseline
    assert steps["cgevent_left_option_osascript_exit"] == 0
    assert steps["cgevent_left_option_any_api_saw_option"] is True
    assert steps["cgevent_right_option_any_api_saw_option"] is False
    assert steps["system_events_option_any_api_saw_option"] is False
    assert steps["cgevent_left_option_stuck_after_release"] is False
    assert result["verdict"]["injected_routes_exit_0"] == 3
    json.dumps(result)


def test_the_appshot_scenario_without_injection_records_that_no_key_was_held(monkeypatch) -> None:
    monkeypatch.setattr(spike, "_bind_symbol", _FakeCoreGraphics().bind)
    result = spike._scenario_appshot_option_state_read()
    assert _steps()["hold_phases"] == "not_requested"
    assert result["phases"] == {} and result["comparison"]["as_expected_no_key_held"] is True


# --- child result assembly ---------------------------------------------------------


def test_count_events_and_child_ok_and_headline() -> None:
    events = [
        {"kind": "pressed", "on_main_thread": True},
        {"kind": "released", "on_main_thread": False},
        {"kind": "kind_9", "on_main_thread": True},
    ]
    counts = spike.count_events(events)
    assert counts == {"pressed": 1, "released": 1, "other": 1, "off_main_thread": 1}
    steps = [
        {"step": "register", "value": 0, "expected": 0, "ok": True},
        {"step": "unregister", "value": -9879, "expected": 0, "ok": False},
        {"step": "note", "value": "short"},
        {"step": "long", "value": "x" * 40},
        {"step": "detail", "value": None},
    ]
    assert spike.child_ok(steps[:1], []) is True
    assert spike.child_ok(steps, []) is False
    assert spike.child_ok(steps[:1], ["boom"]) is False
    headline = spike.build_headline(steps, counts)
    assert headline == "register=0 unregister=-9879 note=short pressed=1 released=1"


def test_assemble_child_result_forces_ok_false_and_is_json_serialisable() -> None:
    steps = [{"step": "register", "value": 0, "expected": 0, "ok": True}]
    result = spike.assemble_child_result(
        "x", {"ok": False, "k": 1}, steps=steps, events=[], errors=[], tcc=None, inject=True
    )
    assert result["ok"] is False and result["k"] == 1 and result["synthetic_key_requested"] is True
    json.dumps(result)
    fine = spike.assemble_child_result(
        "x", {}, steps=steps, events=[], errors=[], tcc={"supported": True}, inject=False
    )
    assert fine["ok"] is True and fine["tcc"] == {"supported": True}


# --- run_experiment (the subprocess is a recording fake) ---------------------------


class _Done:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runner:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> Any:
        self.calls.append((argv, kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _experiment(name: str = "duplicate_register") -> spike.Experiment:
    return spike.experiment_by_name(name)


def _run(outcome: Any, name: str = "duplicate_register", **kwargs: Any):
    runner = _Runner(outcome)
    row = spike.run_experiment(
        _experiment(name),
        script_path=Path("/x/spike.py"),
        python="/py/python3",
        run=runner,
        clock=iter([10.0, 12.5]).__next__,
        platform_name="darwin",
        environ={"PATH": "/bin"},
        **kwargs,
    )
    return row, runner


def test_run_experiment_records_a_signal_death_as_a_crash_with_the_last_step() -> None:
    stdout = b"STEP install_handler=0\nSTEP register=0\n"
    segv = -int(signal.SIGSEGV)  # the host signal table decides the name
    row, runner = _run(_Done(segv, stdout, b"Fatal Python error: Segmentation fault\n"))
    assert row["outcome"] == "crash" and row["signal"] == "SIGSEGV" and row["returncode"] == segv
    assert row["last_step"] == "register=0"
    assert "Segmentation fault" in row["stderr_tail"]
    assert row["duration_s"] == 2.5
    argv, kwargs = runner.calls[0]
    assert argv == ["/py/python3", str(Path("/x/spike.py")), "--child", "duplicate_register"]
    assert kwargs["timeout"] == _experiment().timeout_s
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["env"]["PYTHONFAULTHANDLER"] == "1"
    # The runner captures and kills the group itself: no subprocess.run-only options leak in.
    assert set(kwargs) == {"timeout", "stdin", "env", "creationflags"}
    assert row["argv"] == ["python3", "spike.py", "--child", "duplicate_register"]
    assert row["group_killed"] is False and "cleanup_error" not in row


def test_run_experiment_passes_the_injection_flag_only_when_asked() -> None:
    _row, plain = _run(_Done(0))
    assert "--inject-synthetic-key" not in plain.calls[0][0]
    _row, injected = _run(_Done(0), inject=True)
    assert injected.calls[0][0][-1] == "--inject-synthetic-key"


def test_run_experiment_records_a_timeout_with_partial_output() -> None:
    timeout = subprocess.TimeoutExpired(["x"], 1.0, output=b"STEP unregister_off_main=\n")
    row, _runner = _run(timeout)
    assert row["outcome"] == "timeout" and row["returncode"] is None and row["signal"] is None
    assert row["last_step"] == "unregister_off_main="


def test_run_experiment_ok_error_and_protocol_violation() -> None:
    ok = spike.RESULT_PREFIX + json.dumps({"ok": True, "headline": "register=0"})
    row, _ = _run(_Done(0, ok.encode()))
    assert row["outcome"] == "ok" and row["observations"]["headline"] == "register=0"
    assert row["as_expected"] is None  # a variant has no expectation

    failed = spike.RESULT_PREFIX + json.dumps({"ok": False})
    assert _run(_Done(0, failed.encode()))[0]["outcome"] == "error"
    assert _run(_Done(0, b"no result line"))[0]["outcome"] == "error"
    assert _run(_Done(3, ok.encode()))[0]["outcome"] == "error"


def test_run_experiment_records_the_group_kill_and_a_cleanup_error() -> None:
    ok = spike.RESULT_PREFIX + json.dumps({"ok": True})
    leftover = spike.ChildRun(0, ok.encode(), b"", group_killed=True, cleanup_error="boom")
    row, _ = _run(leftover)
    assert row["outcome"] == "ok" and row["group_killed"] is True
    assert row["cleanup_error"] == "boom"
    table = spike._experiment_row(row)
    assert "leftover child processes killed" in table[1]
    timeout = spike.ChildTimeout(
        ["x"], 1.0, output=b"STEP a=1\n", stderr=b"", group_killed=True, cleanup_error=None
    )
    row, _ = _run(timeout)
    assert row["outcome"] == "timeout" and row["group_killed"] is True
    assert "leftover" not in spike._experiment_row(row)[1]  # a timeout says timeout, once


def test_run_experiment_records_a_spawn_failure_as_an_error() -> None:
    row, _ = _run(FileNotFoundError("no python"))
    assert row["outcome"] == "error" and "FileNotFoundError" in row["spawn_error"]


# --- process-group handling (REAL subprocesses; POSIX only) ------------------------

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")

#: A child that starts a grandchild sleeping 300 s (it inherits the stdout pipe), prints the
#: grandchild's pid, then does what ``MODE`` says. Written to a temp file by the tests.
_GROUP_CHILD = textwrap.dedent(
    """
    import json, subprocess, sys, time
    mode = sys.argv[sys.argv.index("--child") + 1] if "--child" in sys.argv else sys.argv[1]
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    print("GRANDCHILD", grandchild.pid, flush=True)
    if mode == "hang":
        time.sleep(300)
    elif mode == "ok_with_stray":
        print("SPIKE_RESULT " + json.dumps({"ok": True, "name": "x"}), flush=True)
        sys.exit(0)
    elif mode == "crash":
        import os, signal
        os.kill(os.getpid(), signal.SIGABRT)
    else:
        sys.exit(0)
    """
)


def _grandchild_pid(stdout: bytes | str) -> int:
    text = stdout.decode() if isinstance(stdout, bytes) else stdout
    line = next(line for line in text.splitlines() if line.startswith("GRANDCHILD"))
    return int(line.split()[1])


def _process_is_gone(pid: int, timeout_s: float = 5.0) -> bool:
    """True when ``pid`` no longer runs (a zombie nobody reaped counts as gone)."""
    has_proc = Path("/proc/self/stat").exists()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if has_proc:
            try:
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            except (FileNotFoundError, ProcessLookupError):
                # ESRCH (not ENOENT) is raised while the killed task is still exiting.
                return True
            if stat.rsplit(") ", 1)[1].split()[0] == "Z":
                return True
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
        time.sleep(0.05)
    return False


def _real_run(tmp_path: Path, mode: str, timeout_s: float) -> spike.ChildRun:
    script = tmp_path / "child.py"
    script.write_text(_GROUP_CHILD, encoding="utf-8")
    return spike.run_in_process_group(
        [sys.executable, str(script), mode],
        timeout=timeout_s,
        stdin=subprocess.DEVNULL,
        env=dict(os.environ),
    )


@posix_only
def test_a_timeout_kills_the_whole_process_group_not_just_the_child(tmp_path: Path) -> None:
    # The finding: only the direct child was SIGKILLed, so its descendants (the injector's
    # osascript) lived on and could press keys into the next experiment.
    with pytest.raises(spike.ChildTimeout) as raised:
        _real_run(tmp_path, "hang", timeout_s=2.0)
    assert raised.value.group_killed is True and raised.value.cleanup_error is None
    grandchild = _grandchild_pid(raised.value.stdout)
    assert _process_is_gone(grandchild), "the grandchild survived the timeout"


@posix_only
def test_a_child_that_exited_ok_keeps_its_real_exit_status_when_a_stray_holds_the_pipe(
    tmp_path: Path,
) -> None:
    # The finding's second probe: the child printed its ok result and exited 0, but a grandchild
    # inherited the stdout pipe, so it was reported as timeout with returncode None. The leader's
    # exit is noticed within EXIT_POLL_S: the 20 s timeout must NOT be waited out.
    started = time.monotonic()
    result = _real_run(tmp_path, "ok_with_stray", timeout_s=20.0)
    assert result.returncode == 0 and result.group_killed is True
    assert b"SPIKE_RESULT" in result.stdout
    assert time.monotonic() - started < 10, "waited for the timeout instead of noticing the exit"
    assert _process_is_gone(_grandchild_pid(result.stdout))


@posix_only
def test_a_normal_exit_with_no_leftovers_kills_nothing(tmp_path: Path) -> None:
    script = tmp_path / "quiet.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    result = spike.run_in_process_group(
        [sys.executable, str(script)], timeout=30.0, stdin=subprocess.DEVNULL, env=dict(os.environ)
    )
    assert result.returncode == 0 and result.stdout == b"hi\n"
    assert result.group_killed is False and result.cleanup_error is None


@posix_only
def test_a_crashing_child_is_reported_as_its_signal_and_its_group_is_reaped(
    tmp_path: Path,
) -> None:
    started = time.monotonic()
    result = _real_run(tmp_path, "crash", timeout_s=20.0)
    assert result.returncode == -int(signal.SIGABRT) and result.group_killed is True
    assert time.monotonic() - started < 10, "waited for the timeout instead of noticing the crash"
    assert _process_is_gone(_grandchild_pid(result.stdout))


@posix_only
def test_an_interrupt_of_the_parent_kills_the_group_and_is_not_swallowed(
    tmp_path: Path, monkeypatch
) -> None:
    script = tmp_path / "child.py"
    script.write_text(_GROUP_CHILD, encoding="utf-8")
    captured: dict[str, Any] = {}
    original_popen = subprocess.Popen

    class InterruptedPopen(original_popen):
        def communicate(self, input=None, timeout=None):  # noqa: A002
            # Wait until the child has started its grandchild, then interrupt like Ctrl-C would.
            captured["grandchild"] = _grandchild_pid(self.stdout.readline())
            captured["leader"] = self
            raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "Popen", InterruptedPopen)
    with pytest.raises(KeyboardInterrupt):
        spike.run_in_process_group(
            [sys.executable, str(script), "hang"],
            timeout=60.0,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ),
        )
    assert _process_is_gone(captured["grandchild"]), "the grandchild survived the interrupt"
    assert captured["leader"].returncode == -int(signal.SIGKILL)  # killed AND reaped (no zombie)


def test_kill_process_group_reports_an_empty_group_as_nothing_killed_and_an_error_as_text() -> None:
    class Leader:
        def kill(self) -> None:
            raise PermissionError("no")

    killed, error = spike.kill_process_group(None, leader=Leader())
    assert killed is False and error == "PermissionError: no"
    if os.name == "posix":
        # A pid that cannot be a group: reported as an empty group, not an exception.
        assert spike.kill_process_group(2**22 + 12345, leader=Leader()) == (False, None)


@posix_only
def test_the_real_runner_drives_run_experiment_end_to_end_with_a_timeout(tmp_path: Path) -> None:
    script = tmp_path / "child.py"
    script.write_text(_GROUP_CHILD, encoding="utf-8")
    hanging = spike.Experiment(
        name="hang", title="t", kind="variant", question="q", timeout_s=2.0, requires_macos=False
    )
    row = spike.run_experiment(
        hanging, script_path=script, python=sys.executable, platform_name="darwin"
    )
    assert row["outcome"] == "timeout" and row["returncode"] is None
    assert row["group_killed"] is True
    assert _process_is_gone(_grandchild_pid(row["stdout_tail"]))


def test_run_experiment_control_expectation() -> None:
    row, _ = _run(_Done(-int(signal.SIGABRT)), name="control_signal_capture")
    assert row["outcome"] == "crash" and row["signal"] == "SIGABRT"
    assert row["as_expected"] is True
    row, _ = _run(_Done(0, b""), name="control_signal_capture")
    assert row["as_expected"] is False


def test_run_experiment_skips_macos_experiments_elsewhere_without_spawning() -> None:
    runner = _Runner(_Done(0))
    row = spike.run_experiment(
        _experiment("carbon_symbols"),
        script_path=Path("/x/spike.py"),
        run=runner,
        platform_name="linux",
    )
    assert row["outcome"] == "skipped" and "requires macOS" in row["skip_reason"]
    assert runner.calls == []


# --- report assembly and rendering -------------------------------------------------


def _report(experiments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    runner = {
        "system": "Darwin",
        "mac_ver": "15.5",
        "macos_build": "24F74",
        "machine": "arm64",
        "python_version": "3.12.9",
    }
    tcc = {
        "supported": True,
        "probes": {
            "cg_preflight_listen_event": {"value": False},
            "iohid_check_access_listen": {"value": 1, "label": "denied"},
        },
        "window_server": {"cg_session_dictionary_present": True, "launchctl_managername": "Aqua"},
    }
    rows = experiments or [
        {
            "name": "control_signal_capture",
            "outcome": "crash",
            "returncode": -6,
            "signal": "SIGABRT",
            "duration_s": 0.2,
            "as_expected": True,
            "observations": None,
        },
        {
            "name": "duplicate_register",
            "outcome": "error",
            "returncode": 0,
            "signal": None,
            "duration_s": 3.14159,
            "observations": {"headline": "register_second=-9878 | pipe\nnewline"},
        },
        {
            "name": "no_nsapp_register",
            "outcome": "skipped",
            "returncode": None,
            "signal": None,
            "duration_s": 0.0,
            "skip_reason": "requires macOS (running on linux)",
            "observations": None,
        },
    ]
    return spike.build_report(
        runner=runner,
        tcc=tcc,
        experiments=rows,
        started_at="2026-10-01T00:00:00Z",
        finished_at="2026-10-01T00:01:00Z",
        options={"inject_synthetic_key": True},
    )


def test_build_report_shape_and_json_round_trip() -> None:
    report = _report()
    assert set(report) == {
        "schema_version",
        "tool",
        "disclaimer",
        "complete",
        "started_at",
        "finished_at",
        "options",
        "runner",
        "tcc",
        "experiments",
        "evidence",
        "not_covered",
    }
    assert report["complete"] is True
    assert report["schema_version"] == spike.SCHEMA_VERSION == 1
    assert report["tool"] == "macos_carbon_hotkey_spike"
    assert report["disclaimer"] == spike.EVIDENCE_DISCLAIMER
    assert json.loads(json.dumps(report)) == report
    assert set(report["evidence"]) >= {
        "rule",
        "tcc_source",
        "preflights",
        "preflights_all_false",
        "carbon_event_delivered",
        "delivery_conclusion",
        "variant_ok",
        "usable_as_evidence",
        "notes",
    }
    assert report["not_covered"] == list(spike.NOT_COVERED)


def test_the_report_says_what_it_does_not_cover() -> None:
    covered = " ".join(spike.NOT_COVERED)
    assert "Variant G" in covered and "flip criterion 1" in covered
    assert "off-main REGISTER" in covered
    markdown = spike.render_summary_markdown(_report())
    text = spike.render_summary_text(_report())
    for item in spike.NOT_COVERED:
        assert item in markdown and item in text


def test_the_disclaimer_is_the_required_sentence() -> None:
    assert spike.EVIDENCE_DISCLAIMER.startswith(
        "Runner evidence only: runners pre-grant TCC to bash/osascript/Terminal; "
        "no dialog is shown, no physical keyboard exists, "
        "no key-hold semantics were exercised"
    )


def test_markdown_summary_has_the_tables_the_sentence_and_clean_cells() -> None:
    markdown = spike.render_summary_markdown(_report())
    assert spike.EVIDENCE_DISCLAIMER in markdown
    assert "| Experiment | Outcome | Exit | Signal | Secs | Observations |" in markdown
    assert "| TCC context (recorded first) | Value |" in markdown
    assert (
        "| control_signal_capture | crash (control as expected) | -6 | SIGABRT | 0.2 |" in markdown
    )
    assert "requires macOS (running on linux)" in markdown
    assert "iohid_check_access_listen | 1 (denied)" in markdown
    # A pipe or newline in an observation must not break the table.
    row = next(line for line in markdown.splitlines() if line.startswith("| duplicate_register"))
    assert row.count("|") == 7 and "\n" not in row
    assert "Evidence gate:" in markdown and markdown.endswith("\n")


def test_markdown_carries_the_whole_evidence_rule_not_a_truncated_one() -> None:
    # The rule defines what counts as evidence; md_cell's 160-character cap used to cut it off
    # in 'AXIsPro...' so a reader of the step summary never saw the IOHIDCheckAccess condition.
    assert len(spike.EVIDENCE_RULE) > 160
    markdown = spike.render_summary_markdown(_report())
    assert spike.EVIDENCE_RULE in markdown
    assert (
        "IOHIDCheckAccess(listen)" in markdown and "..." not in markdown.split("Evidence gate")[1]
    )


def test_a_partial_report_is_marked_in_both_summaries() -> None:
    partial = spike.build_report(
        runner={},
        tcc={"supported": False, "reason": "x", "probes": {}},
        experiments=[],
        started_at="a",
        finished_at="b",
        options={},
        complete=False,
    )
    assert partial["complete"] is False
    assert "Partial report" in spike.render_summary_markdown(partial)
    assert "PARTIAL REPORT" in spike.render_summary_text(partial)
    assert "Partial" not in spike.render_summary_markdown(_report())


def test_text_summary_lists_every_experiment_and_the_sentence() -> None:
    text = spike.render_summary_text(_report())
    for name in ("control_signal_capture", "duplicate_register", "no_nsapp_register"):
        assert name in text
    assert "evidence: preflights_all_false=" in text
    assert text.rstrip().endswith(spike.EVIDENCE_DISCLAIMER)
    assert "macOS 15.5 (24F74) arm64" in text


def test_summaries_render_an_empty_experiment_list() -> None:
    report = _report([])
    assert "evidence:" in spike.render_summary_text(report)
    assert "Evidence gate" in spike.render_summary_markdown(report)


def test_md_cell() -> None:
    assert spike.md_cell("a|b\nc") == "a/b c"
    assert len(spike.md_cell("x" * 500)) == 160
    assert len(spike.md_cell("x" * 500, limit=300)) == 300
    assert spike.md_cell("x|y\n" * 500, limit=None) == ("x/y " * 500).strip()  # never cut


# --- the ctypes surface (shape only; no framework is loaded) -----------------------


def test_four_character_codes_and_struct_sizes() -> None:
    assert spike.fourcc("keyb") == 0x6B657962 == spike.K_EVENT_CLASS_KEYBOARD
    assert spike.fourcc("----") == 0x2D2D2D2D == spike.K_EVENT_PARAM_DIRECT_OBJECT
    assert spike.fourcc("hkid") == 0x686B6964 == spike.TYPE_EVENT_HOT_KEY_ID
    assert spike.fourcc("JRVS") == 0x4A525653 == spike.HOT_KEY_SIGNATURE
    with pytest.raises(ValueError, match="exactly 4"):
        spike.fourcc("abc")
    assert ctypes.sizeof(spike.EventHotKeyID) == 8
    assert ctypes.sizeof(spike.EventTypeSpec) == 8
    assert spike.TEST_MODIFIERS == 0x1800 and spike.TEST_KEY_CODE == 0x26
    assert (spike.VK_OPTION, spike.VK_RIGHT_OPTION) == (58, 61)


def test_signature_table_pins_the_red_team_crash_surface() -> None:
    table = spike.CARBON_SIGNATURES
    assert set(table) == {
        "GetApplicationEventTarget",
        "GetEventDispatcherTarget",
        "InstallEventHandler",
        "RemoveEventHandler",
        "RegisterEventHotKey",
        "UnregisterEventHotKey",
        "GetEventClass",
        "GetEventKind",
        "GetEventParameter",
        "IsSecureEventInputEnabled",
        "EnableSecureEventInput",
        "DisableSecureEventInput",
    }
    # A default c_int restype would truncate a 64-bit pointer.
    assert table["GetApplicationEventTarget"][0] is ctypes.c_void_p
    assert table["GetEventDispatcherTarget"][0] is ctypes.c_void_p
    # EventHotKeyID is passed BY VALUE (argument 3), never as a pointer.
    assert table["RegisterEventHotKey"][1][2] is spike.EventHotKeyID
    assert len(table["RegisterEventHotKey"][1]) == 6
    assert table["InstallEventHandler"][1][1] is spike.HANDLER_PROTO
    assert len(table["InstallEventHandler"][1]) == 6
    for restype, argtypes in table.values():
        assert restype is not None and isinstance(argtypes, tuple)


class _FakeFunction:
    def __init__(self, implementation: Any) -> None:
        self.implementation = implementation
        self.restype: Any = None
        self.argtypes: Any = None
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.implementation(*args)


class FakeCarbon:
    """An in-memory Carbon. It models only what the spike reads back; nothing more."""

    #: Needs more than 32 bits: a c_int restype or argument would truncate it.
    APP_TARGET = 0x1000_0000_0000_1234

    def __init__(
        self,
        *,
        missing: tuple[str, ...] = (),
        leak_off_main: bool = False,
        alias_install: bool = False,
        leak_always: bool = False,
    ) -> None:
        self.missing = set(missing)
        self.leak_off_main = leak_off_main
        #: Unregister claims success on EVERY thread but keeps the registration.
        self.leak_always = leak_always
        #: Export InstallEventHandler only as ``_InstallEventHandler`` (the doubt in the SDK).
        self.alias_install = alias_install
        self.chords: dict[int, tuple[int, int, int]] = {}
        self.handlers: dict[int, Any] = {}
        self.secure = 0
        self.fail_install = False
        self.events: dict[int, tuple[int, int]] = {}
        self._next = 0xA000
        self._functions: dict[str, _FakeFunction] = {}

    def _ref(self) -> int:
        self._next += 1
        return self._next

    def _function(self, name: str) -> _FakeFunction:
        if name not in self._functions:
            self._functions[name] = _FakeFunction(getattr(self, f"_impl_{name}"))
        return self._functions[name]

    def __getattr__(self, name: str) -> _FakeFunction:
        if name == "_InstallEventHandler" and self.alias_install:
            return self._function("InstallEventHandler")
        if name.startswith("_") or name in self.missing or name not in spike.CARBON_SIGNATURES:
            raise AttributeError(name)
        return self._function(name)

    def _impl_GetApplicationEventTarget(self) -> int:
        return self.APP_TARGET

    def _impl_GetEventDispatcherTarget(self) -> int:
        return 0x2000

    def _impl_InstallEventHandler(self, target, handler, count, specs, user_data, out_ref) -> int:
        if self.fail_install:
            raise RuntimeError("install exploded")
        assert target == self.APP_TARGET and count == 2 and user_data is None
        assert [(s.eventClass, s.eventKind) for s in specs] == [
            (spike.K_EVENT_CLASS_KEYBOARD, 5),
            (spike.K_EVENT_CLASS_KEYBOARD, 6),
        ]
        ref = self._ref()
        self.handlers[ref] = handler
        out_ref._obj.value = ref
        return 0

    def _impl_RemoveEventHandler(self, ref) -> int:
        return 0 if self.handlers.pop(getattr(ref, "value", ref), None) else -50

    def _impl_RegisterEventHotKey(self, key, modifiers, identity, target, options, out_ref) -> int:
        assert isinstance(identity, spike.EventHotKeyID)
        assert target == self.APP_TARGET and options == 0
        if any((k, m) == (key, modifiers) for k, m, _ in self.chords.values()):
            return spike.EVENT_HOT_KEY_EXISTS_ERR
        ref = self._ref()
        self.chords[ref] = (key, modifiers, identity.id)
        out_ref._obj.value = ref
        return 0

    def _impl_UnregisterEventHotKey(self, ref) -> int:
        value = getattr(ref, "value", ref)
        assert value is not None, "a NULL reference must never reach Carbon"
        off_main = threading.current_thread() is not threading.main_thread()
        if self.leak_always or (self.leak_off_main and off_main):
            return 0  # reports success but keeps the registration (a silent leak)
        return 0 if self.chords.pop(value, None) else -9879

    def _impl_GetEventClass(self, event) -> int:
        return spike.K_EVENT_CLASS_KEYBOARD

    def _impl_GetEventKind(self, event) -> int:
        return self.events[event][0]

    def _impl_GetEventParameter(self, event, name, desired, actual_type, size, actual, data) -> int:
        assert name == spike.K_EVENT_PARAM_DIRECT_OBJECT and desired == spike.TYPE_EVENT_HOT_KEY_ID
        assert size == 8
        data._obj.signature = spike.HOT_KEY_SIGNATURE
        data._obj.id = self.events[event][1]
        return 0

    def _impl_IsSecureEventInputEnabled(self) -> int:
        return self.secure

    def _impl_EnableSecureEventInput(self) -> int:
        self.secure = 1
        return 0

    def _impl_DisableSecureEventInput(self) -> int:
        self.secure = 0
        return 0

    def press(self, key: int = spike.TEST_KEY_CODE, modifiers: int = spike.TEST_MODIFIERS) -> None:
        """Deliver Pressed then Released through the REAL ctypes callback."""
        for _ref, (k, m, ident) in list(self.chords.items()):
            if (k, m) != (key, modifiers):
                continue
            for handler in list(self.handlers.values()):
                for kind in (spike.K_EVENT_HOT_KEY_PRESSED, spike.K_EVENT_HOT_KEY_RELEASED):
                    event = self._ref()
                    self.events[event] = (kind, ident)
                    assert handler(0, event, 0) == spike.NO_ERR


def test_bind_carbon_sets_restype_and_argtypes_on_every_function() -> None:
    fake = FakeCarbon()
    bound, missing, aliases = spike.bind_carbon(fake)
    assert missing == [] and bound == list(spike.CARBON_SIGNATURES) and aliases == {}
    for name, (restype, argtypes) in spike.CARBON_SIGNATURES.items():
        function = getattr(fake, name)
        assert function.restype is restype and function.argtypes == list(argtypes)


def test_bind_carbon_reports_a_missing_symbol_instead_of_raising() -> None:
    bound, missing, aliases = spike.bind_carbon(FakeCarbon(missing=("EnableSecureEventInput",)))
    assert missing == ["EnableSecureEventInput"] and aliases == {}
    assert "EnableSecureEventInput" not in bound and "RegisterEventHotKey" in bound


def test_bind_carbon_falls_back_to_the_underscore_install_symbol_and_records_it() -> None:
    # The doubt: the SDK might export InstallEventHandler only as _InstallEventHandler. One
    # missing symbol must not turn every Carbon scenario into a harness error.
    fake = FakeCarbon(missing=("InstallEventHandler",), alias_install=True)
    bound, missing, aliases = spike.bind_carbon(fake)
    assert missing == [] and "InstallEventHandler" in bound
    assert aliases == {"InstallEventHandler": "_InstallEventHandler"}
    shim = spike.CarbonShim(lib=FakeCarbon(missing=("InstallEventHandler",), alias_install=True))
    assert shim.aliases == {"InstallEventHandler": "_InstallEventHandler"}
    status, handler = shim.install_handler()  # callers keep the one canonical spelling
    assert status == 0 and handler is not None
    # The plain name wins when both exist, and no alias is recorded.
    assert spike.CarbonShim(lib=FakeCarbon(alias_install=True)).aliases == {}
    # Neither spelling: reported as missing, never raised.
    _bound, gone, none = spike.bind_carbon(FakeCarbon(missing=("InstallEventHandler",)))
    assert gone == ["InstallEventHandler"] and none == {}


def test_shim_passes_arguments_in_the_order_carbon_expects() -> None:
    fake = FakeCarbon()
    shim = spike.CarbonShim(lib=fake)
    status, handler = shim.install_handler()
    assert status == 0 and handler is not None
    status, ref = shim.register(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS, 7)
    assert status == 0 and fake.chords[ref] == (0x26, 0x1800, 7)
    call = fake.RegisterEventHotKey.calls[0]
    assert call[:2] == (0x26, 0x1800) and call[2].signature == spike.HOT_KEY_SIGNATURE
    assert call[3] == FakeCarbon.APP_TARGET  # the full 64-bit target, not truncated
    assert shim.unregister(ref) == 0 and fake.chords == {}
    assert shim.remove_handler(handler) == 0


def test_a_duplicate_registration_returns_the_status_and_a_null_reference() -> None:
    shim = spike.CarbonShim(lib=FakeCarbon())
    shim.install_handler()
    assert shim.register(0x26, 0x1800, 1)[0] == 0
    status, ref = shim.register(0x26, 0x1800, 2)
    assert status == spike.EVENT_HOT_KEY_EXISTS_ERR and ref is None


def test_the_ctypes_callback_records_events_and_never_raises_into_carbon() -> None:
    fake = FakeCarbon()
    shim = spike.CarbonShim(lib=fake)
    shim.install_handler()
    shim.register(0x26, 0x1800, 5)
    fake.press()
    assert [e["kind"] for e in spike._STATE.events] == ["pressed", "released"]
    assert {e["hot_key_id"] for e in spike._STATE.events} == {5}
    assert all(e["on_main_thread"] for e in spike._STATE.events)
    # The same module-level singleton is handed to every install.
    assert spike.callback_singleton() is spike.callback_singleton()
    assert spike.spec_array_singleton() is spike.spec_array_singleton()
    # An internal error returns eventNotHandledErr and is recorded, not swallowed.
    handler = spike.callback_singleton()
    assert handler(0, 987654, 0) == spike.EVENT_NOT_HANDLED_ERR
    assert spike._STATE.errors and "carbon handler" in spike._STATE.errors[0]


def test_the_callback_notices_an_off_main_thread_delivery() -> None:
    fake = FakeCarbon()
    shim = spike.CarbonShim(lib=fake)
    shim.install_handler()
    shim.register(0x26, 0x1800, 5)
    worker = threading.Thread(target=fake.press)
    worker.start()
    worker.join()
    assert spike.count_events(spike._STATE.events)["off_main_thread"] == 2


# --- scenarios against the fake (sequencing only) ----------------------------------


def _drive(scenario: Any, *, settle: bool = True) -> dict[str, Any] | None:
    """Run a scenario coroutine to its end, waiting for each ``Wait`` like the loop does."""
    try:
        while True:
            wait = next(scenario)
            assert isinstance(wait, spike.Wait) and wait.timeout_s > 0
            deadline = threading.Event()
            for _ in range(400):
                if wait.predicate():
                    break
                deadline.wait(0.01)
            assert wait.predicate(), "the injector never finished"
    except StopIteration as stop:
        return stop.value


def _steps() -> dict[str, Any]:
    return {row["step"]: row["value"] for row in spike._STATE.steps}


class _PressingInjector(spike.Injector):
    """Stands in for osascript: presses the fake chord from the injector thread."""

    lib: FakeCarbon

    def __init__(self, repeat: int = 1) -> None:
        super().__init__(delay_s=0.0, repeat=repeat, run=lambda *a, **k: None)

    def _work(self) -> None:
        try:
            for _ in range(self.repeat):
                type(self).lib.press()
            self.result = {"returncode": 0}
        finally:
            self.done.set()


def test_scenario_register_unregister_without_a_synthetic_key() -> None:
    fake = FakeCarbon()
    shim = spike.CarbonShim(lib=fake)
    result = _drive(spike._co_nsapp_register_unregister(shim))
    steps = _steps()
    assert steps["install_handler"] == 0 and steps["register"] == 0 and steps["unregister"] == 0
    assert steps["inject_while_registered"] == "not_requested"
    assert steps["reregister_after_unregister"] == 0 and steps["unregister_second"] == 0
    assert fake.chords == {} and result == {"carbon_route": "injected"}
    assert spike.child_ok(spike._STATE.steps, spike._STATE.errors)


def test_scenario_register_unregister_with_a_delivered_synthetic_key(monkeypatch) -> None:
    fake = FakeCarbon()
    shim = spike.CarbonShim(lib=fake)
    _PressingInjector.lib = fake
    monkeypatch.setattr(spike, "Injector", _PressingInjector)
    spike._STATE.inject = True
    _drive(spike._co_nsapp_register_unregister(shim))
    steps = _steps()
    # Delivered while registered; after the unregister the second press goes nowhere. Each
    # count is a WINDOW (events since the previous count), so 'after' is 0, not a running total.
    assert steps["pressed_while_registered"] == 1 and steps["released_while_registered"] == 1
    assert steps["pressed_after_unregister"] == 0 and steps["released_after_unregister"] == 0
    assert steps["inject_while_registered_osascript_exit"] == 0
    assert spike.count_events(spike._STATE.events)["pressed"] == 1  # the total is still 1


def _counters(monkeypatch, before: Any, after: Any) -> None:
    """Make the key-down control read ``before`` then ``after`` (None = unreadable)."""
    values = iter([before, after] * 4)
    monkeypatch.setattr(spike, "read_keydown_counters", lambda *a, **k: next(values))


def test_scenario_records_whether_the_injection_was_seen_independently_of_carbon(
    monkeypatch,
) -> None:
    fake = FakeCarbon()
    _PressingInjector.lib = fake
    monkeypatch.setattr(spike, "Injector", _PressingInjector)
    spike._STATE.inject = True
    _counters(
        monkeypatch,
        {"combined_session": 5, "hid_system": 5},
        {"combined_session": 6, "hid_system": 5},
    )
    _drive(spike._co_nsapp_register_unregister(spike.CarbonShim(lib=fake)))
    step = next(r for r in spike._STATE.steps if r["step"] == "injection_seen_while_registered")
    assert step["value"] is True
    assert (
        step["counters_before"]["combined_session"] == 5
        and step["counters_after"]["combined_session"] == 6
    )

    spike.reset_child_state()
    spike._STATE.inject = True
    fake = FakeCarbon()
    _PressingInjector.lib = fake
    _counters(monkeypatch, {"combined_session": 5}, None)  # the counter became unreadable
    _drive(spike._co_nsapp_register_unregister(spike.CarbonShim(lib=fake)))
    assert _steps()["injection_seen_while_registered"] is None


def test_read_keydown_counters_reads_both_source_states_or_reports_unreadable() -> None:
    seen: list[tuple[int, int]] = []

    def bind(_frameworks: Any, symbol: str, _restype: Any, _argtypes: Any) -> Any:
        assert symbol == "CGEventSourceCounterForEventType"

        def counter(state: int, event_type: int) -> int:
            seen.append((state, event_type))
            return 7 + state

        return counter

    assert spike.read_keydown_counters(bind) == {"combined_session": 7, "hid_system": 8}
    assert seen == [(0, spike.K_CG_EVENT_KEY_DOWN), (1, spike.K_CG_EVENT_KEY_DOWN)]
    assert spike.K_CG_EVENT_KEY_DOWN == 10

    def missing(*_args: Any) -> Any:
        raise OSError("CGEventSourceCounterForEventType not found")

    assert spike.read_keydown_counters(missing) is None
    assert "keydown_counter_unreadable" in _steps()


def test_a_leaking_unregister_is_not_clean_evidence_even_though_a_press_arrived(
    monkeypatch,
) -> None:
    # The whole chain of the finding: a Pressed arrived while registered, but the unregister did
    # not take effect (the second press still arrives, the re-register fails with -9878).
    fake = FakeCarbon(leak_always=True)
    _PressingInjector.lib = fake
    monkeypatch.setattr(spike, "Injector", _PressingInjector)
    spike._STATE.inject = True
    _counters(monkeypatch, {"a": 1}, {"a": 2})
    _drive(spike._co_nsapp_register_unregister(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert steps["pressed_while_registered"] == 1 and steps["pressed_after_unregister"] == 1
    assert steps["reregister_after_unregister"] == spike.EVENT_HOT_KEY_EXISTS_ERR
    result = spike.assemble_child_result(
        "nsapp_register_unregister",
        {},
        steps=spike._STATE.steps,
        events=spike._STATE.events,
        errors=spike._STATE.errors,
        tcc=_tcc(),
        inject=True,
    )
    assert result["ok"] is False
    row = {
        "name": "nsapp_register_unregister",
        "outcome": spike.classify_outcome(returncode=0, timed_out=False, child_ok=result["ok"]),
        "observations": result,
    }
    verdict = spike.evaluate_evidence(_tcc(), [row])
    assert verdict["carbon_event_delivered"] is True and verdict["variant_ok"] is False
    assert verdict["usable_as_evidence"] is False


def test_scenario_cycles_register_and_unregister_without_a_synthetic_key() -> None:
    fake = FakeCarbon()
    result = _drive(spike._co_register_unregister_cycles(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert steps["register_unregister_cycles"] == spike.REGISTER_CYCLES == 200
    assert steps["cycle_failures"] == 0 and steps["reregister_after_cycles"] == 0
    assert steps["press_cycles_requested"] == 0 and steps["every_press_arrived"] is None
    assert fake.chords == {} and result == {"carbon_route": "injected"}
    assert len(fake.RegisterEventHotKey.calls) == 200 + 2
    assert spike.child_ok(spike._STATE.steps, spike._STATE.errors)


def test_scenario_cycles_count_every_press_of_one_repeated_injection(monkeypatch) -> None:
    fake = FakeCarbon()
    _PressingInjector.lib = fake
    monkeypatch.setattr(spike, "Injector", _PressingInjector)
    spike._STATE.inject = True
    _drive(spike._co_register_unregister_cycles(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert spike.PRESS_CYCLES == 50 and steps["press_cycles_requested"] == 50
    assert steps["pressed_press_cycles"] == 50 and steps["released_press_cycles"] == 50
    assert steps["every_press_arrived"] is True
    assert steps["inject_press_cycles_osascript_exit"] == 0
    assert spike.child_ok(spike._STATE.steps, spike._STATE.errors)


def test_scenario_cycles_detect_a_leaked_slot_and_cap_the_failure_details() -> None:
    fake = FakeCarbon(leak_always=True)  # the unregister never frees the slot
    _drive(spike._co_register_unregister_cycles(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert steps["cycle_failures"] == 199  # cycle 0 registers; every later one hits -9878
    details = next(r for r in spike._STATE.steps if r["step"] == "cycle_failures")["details"]
    assert len(details) == spike.CYCLE_FAILURE_DETAILS == 5
    assert details[0] == {"cycle": 1, "step": "register", "status": spike.EVENT_HOT_KEY_EXISTS_ERR}
    assert spike.child_ok(spike._STATE.steps, spike._STATE.errors) is False


def test_scenario_duplicate_register_records_the_status_and_skips_the_null_ref() -> None:
    fake = FakeCarbon()
    _drive(spike._co_duplicate_register(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert steps["register_first"] == 0
    assert steps["register_second"] == spike.EVENT_HOT_KEY_EXISTS_ERR
    assert steps["second_returned_hotkey_exists_err"] is True
    assert steps["unregister_first"] == 0 and steps["unregister_second_skipped_null_ref"] is True
    assert spike.child_ok(spike._STATE.steps, spike._STATE.errors)
    assert fake.chords == {}


def test_scenario_off_main_unregister_detects_a_silent_leak() -> None:
    clean = FakeCarbon()
    _drive(spike._co_unregister_off_main(spike.CarbonShim(lib=clean)))
    steps = _steps()
    assert steps["unregister_off_main"] == 0 and steps["reregister_after_off_main_unregister"] == 0

    spike.reset_child_state()
    leaky = FakeCarbon(leak_off_main=True)
    _drive(spike._co_unregister_off_main(spike.CarbonShim(lib=leaky)))
    steps = _steps()
    # The off-main call claims success, yet the slot is still taken.
    assert steps["unregister_off_main"] == 0
    assert steps["reregister_after_off_main_unregister"] == spike.EVENT_HOT_KEY_EXISTS_ERR
    returned = next(r for r in spike._STATE.steps if r["step"] == "unregister_off_main")
    assert returned["returned"] is True and returned["error"] is None


def test_scenario_secure_input_is_always_disabled_again() -> None:
    fake = FakeCarbon()
    _drive(spike._co_secure_input(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert steps["secure_input_before"] == 0 and steps["secure_input_after_enable"] == 1
    assert steps["register_under_secure_input"] == 0 and steps["secure_input_after_disable"] == 0
    assert fake.secure == 0

    spike.reset_child_state()
    broken = FakeCarbon()
    broken.fail_install = True
    with pytest.raises(RuntimeError, match="install exploded"):
        _drive(spike._co_secure_input(spike.CarbonShim(lib=broken)))
    assert broken.secure == 0  # the finally block ran


def test_scenario_install_handler_twice_counts_both_deliveries(monkeypatch) -> None:
    fake = FakeCarbon()
    _PressingInjector.lib = fake
    monkeypatch.setattr(spike, "Injector", _PressingInjector)
    spike._STATE.inject = True
    _drive(spike._co_install_handler_twice(spike.CarbonShim(lib=fake)))
    steps = _steps()
    assert steps["install_handler_first"] == 0 and steps["install_handler_second"] == 0
    assert steps["handler_refs_distinct"] is True
    # Two handlers on one target: every press arrives twice.
    assert steps["pressed_two_handlers"] == 2 and steps["released_two_handlers"] == 2
    assert steps["remove_handler_first"] == 0 and steps["remove_handler_second"] == 0
    assert fake.handlers == {} and fake.chords == {}


def test_a_repeated_injection_is_one_osascript_run_with_a_longer_timeout() -> None:
    many = spike.Injector(delay_s=0.0, repeat=50, run=lambda *a, **k: None)
    script = many.script()
    assert script.startswith('tell application "System Events"\n')
    assert "repeat 50 times" in script and "key code 38 using {control down, option down}" in script
    assert f"delay {spike.Injector.REPEAT_GAP_S}" in script and script.endswith("end tell")
    assert many.timeout_s > spike.Injector(run=lambda *a, **k: None).timeout_s
    assert spike.Injector(repeat=0, run=lambda *a, **k: None).repeat == 1


def test_injector_records_an_osascript_failure_and_always_finishes() -> None:
    def failing(*_a: Any, **_k: Any) -> Any:
        raise FileNotFoundError("osascript")

    injector = spike.Injector(delay_s=0.0, run=failing)
    injector.start()
    assert injector.done.wait(5)
    assert injector.result["returncode"] is None and "FileNotFoundError" in injector.result["error"]
    assert "key code 38" in injector.script() and "control down, option down" in injector.script()
    assert "repeat" not in injector.script()  # one press stays a one-liner
    assert injector.argv() == ["osascript", "-e", injector.script()]

    done = spike.Injector(delay_s=0.0, run=lambda *a, **k: _Done(0, b"out", b"err"))
    done.start()
    assert done.done.wait(5) and done.result["returncode"] == 0
    assert done.result["stderr"] == "err"


# --- the REAL ctypes marshalling, against a C library with the Carbon prototypes ---------
#
# FakeCarbon above is a Python object: it accepts any argument, so it can never notice a wrong
# ``argtypes`` or ``restype``. This library is compiled C with the real prototypes, so it only
# answers noErr when the arguments really arrive as Carbon would see them: the event target is a
# full 64-bit pointer (a default ``c_int`` restype truncates it) and ``EventHotKeyID`` arrives
# BY VALUE (an instance passed by pointer makes the signature field garbage).

_C_CARBON = r"""
#include <stdint.h>
#include <string.h>

typedef int32_t OSStatus;
typedef struct { uint32_t signature; uint32_t id; } EventHotKeyID;
typedef struct { uint32_t eventClass; uint32_t eventKind; } EventTypeSpec;
typedef OSStatus (*Handler)(void *call_ref, void *event, void *user_data);

#define APP_TARGET ((void *)0x1000000000001234ULL)
#define DISPATCHER_TARGET ((void *)0x2000ULL)
#define HANDLER_REF ((void *)0x7000000000000001ULL)
#define KEY_REF_BASE 0x7000000000000100ULL

static Handler g_handler;
static void *g_handler_user;
static int g_handler_installed;
static struct { int used; uint32_t key; uint32_t mods; EventHotKeyID id; } g_keys[16];
static struct { uint32_t kind; EventHotKeyID id; } g_events[256];
static int g_event_count;
static unsigned char g_secure;

void *GetApplicationEventTarget(void) { return APP_TARGET; }
void *GetEventDispatcherTarget(void) { return DISPATCHER_TARGET; }

OSStatus InstallEventHandler(void *target, Handler handler, uint32_t count,
                             const EventTypeSpec *specs, void *user_data, void **out_ref) {
    if (target != APP_TARGET) return -50;          /* paramErr: a truncated pointer lands here */
    if (count != 2 || specs == 0 || out_ref == 0) return -50;
    if (specs[0].eventClass != 0x6B657962u || specs[0].eventKind != 5u) return -50;
    if (specs[1].eventClass != 0x6B657962u || specs[1].eventKind != 6u) return -50;
    g_handler = handler; g_handler_user = user_data; g_handler_installed = 1;
    *out_ref = HANDLER_REF;
    return 0;
}

OSStatus RemoveEventHandler(void *ref) {
    if (ref != HANDLER_REF || !g_handler_installed) return -50;
    g_handler_installed = 0;
    return 0;
}

OSStatus RegisterEventHotKey(uint32_t key, uint32_t mods, EventHotKeyID id, void *target,
                             uint32_t options, void **out_ref) {
    int i;
    if (target != APP_TARGET || options != 0 || out_ref == 0) return -50;
    if (id.signature != 0x4A525653u) return -50;   /* 'JRVS': the struct arrived BY VALUE */
    for (i = 0; i < 16; i++)
        if (g_keys[i].used && g_keys[i].key == key && g_keys[i].mods == mods) return -9878;
    for (i = 0; i < 16; i++) {
        if (g_keys[i].used) continue;
        g_keys[i].used = 1; g_keys[i].key = key; g_keys[i].mods = mods; g_keys[i].id = id;
        *out_ref = (void *)(KEY_REF_BASE + (uint64_t)i);
        return 0;
    }
    return -50;
}

OSStatus UnregisterEventHotKey(void *ref) {
    uint64_t index = (uint64_t)ref - KEY_REF_BASE;
    if ((uint64_t)ref < KEY_REF_BASE || index >= 16 || !g_keys[index].used) return -9879;
    g_keys[index].used = 0;
    return 0;
}

uint32_t GetEventClass(void *event) { (void)event; return 0x6B657962u; }

uint32_t GetEventKind(void *event) {
    uint64_t index = (uint64_t)event - 1;
    return index < 256 ? g_events[index].kind : 0;
}

OSStatus GetEventParameter(void *event, uint32_t name, uint32_t type, uint32_t *actual_type,
                           uint32_t size, uint32_t *actual_size, void *data) {
    uint64_t index = (uint64_t)event - 1;
    (void)actual_type; (void)actual_size;
    if (name != 0x2D2D2D2Du || type != 0x686B6964u || size != 8u || data == 0 || index >= 256)
        return -9870;
    memcpy(data, &g_events[index].id, 8);
    return 0;
}

unsigned char IsSecureEventInputEnabled(void) { return g_secure; }
OSStatus EnableSecureEventInput(void) { g_secure = 1; return 0; }
OSStatus DisableSecureEventInput(void) { g_secure = 0; return 0; }

/* Test helper, NOT a Carbon symbol: deliver Pressed then Released for a registered chord. */
int fake_press(uint32_t key, uint32_t mods) {
    int i, k, delivered = 0;
    uint32_t kinds[2] = {5u, 6u};
    if (!g_handler_installed) return -1;
    for (i = 0; i < 16; i++) {
        if (!g_keys[i].used || g_keys[i].key != key || g_keys[i].mods != mods) continue;
        for (k = 0; k < 2; k++) {
            if (g_event_count >= 256) return -2;
            g_events[g_event_count].kind = kinds[k];
            g_events[g_event_count].id = g_keys[i].id;
            g_event_count++;
            if (g_handler((void *)0, (void *)(uintptr_t)g_event_count, g_handler_user) != 0)
                return -3;
            delivered++;
        }
    }
    return delivered;
}
"""

C_APP_TARGET = 0x1000_0000_0000_1234
C_HANDLER_REF = 0x7000_0000_0000_0001
C_KEY_REF_BASE = 0x7000_0000_0000_0100
C_PARAM_ERR = -50


@pytest.fixture(scope="module")
def c_carbon_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    compiler = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if compiler is None or os.name == "nt":
        pytest.skip("no C compiler: the real-marshalling tests need one")
    directory = tmp_path_factory.mktemp("fake_carbon")
    source = directory / "fake_carbon.c"
    source.write_text(_C_CARBON, encoding="utf-8")
    library = directory / (
        "libfake_carbon.dylib" if sys.platform == "darwin" else "libfake_carbon.so"
    )
    flags = ["-dynamiclib"] if sys.platform == "darwin" else ["-shared", "-fPIC"]
    done = subprocess.run(
        [compiler, *flags, "-o", str(library), str(source)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    # A compile error is a bug in THIS test source, so it fails instead of skipping.
    assert done.returncode == 0, done.stderr
    return library


@pytest.fixture
def c_carbon(c_carbon_path: Path, tmp_path: Path) -> Any:
    """A FRESH instance of the library per test (a copy at a new path has its own statics)."""
    private = tmp_path / c_carbon_path.name
    shutil.copy(c_carbon_path, private)
    lib = ctypes.CDLL(str(private))
    lib.fake_press.restype = ctypes.c_int
    lib.fake_press.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    return lib


def test_real_ctypes_calls_marshal_pointers_and_the_by_value_struct_correctly(c_carbon) -> None:
    shim = spike.CarbonShim(lib=c_carbon, route="c-library")
    assert shim.bound == list(spike.CARBON_SIGNATURES) and shim.missing == [] and shim.aliases == {}
    assert shim.target() == C_APP_TARGET  # all 64 bits: the c_void_p restype works
    status, handler = shim.install_handler()
    assert status == 0 and handler == C_HANDLER_REF
    status, ref = shim.register(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS, 7)
    assert status == 0 and ref == C_KEY_REF_BASE  # EventHotKeyID arrived by value (signature ok)
    duplicate_status, duplicate = shim.register(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS, 8)
    assert duplicate_status == spike.EVENT_HOT_KEY_EXISTS_ERR and duplicate is None

    # The callback is invoked FROM C through the module-level ctypes trampoline and reads the id
    # back through GetEventParameter's by-reference out parameter.
    assert c_carbon.fake_press(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS) == 2
    events = spike._STATE.events
    assert [event["kind"] for event in events] == ["pressed", "released"]
    assert {event["hot_key_id"] for event in events} == {7}
    assert {event["parameter_status"] for event in events} == {0}
    assert all(event["on_main_thread"] for event in events) and spike._STATE.errors == []

    assert shim.unregister(ref) == 0
    assert c_carbon.fake_press(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS) == 0  # slot is free
    assert shim.remove_handler(handler) == 0
    assert shim.remove_handler(handler) == C_PARAM_ERR  # the C side really tracks the handler


def test_real_ctypes_secure_input_functions_round_trip(c_carbon) -> None:
    shim = spike.CarbonShim(lib=c_carbon)
    assert shim.lib.IsSecureEventInputEnabled() == 0
    assert shim.lib.EnableSecureEventInput() == 0 and shim.lib.IsSecureEventInputEnabled() == 1
    assert shim.lib.DisableSecureEventInput() == 0 and shim.lib.IsSecureEventInputEnabled() == 0


def test_the_c_library_catches_a_truncated_event_target(c_carbon, monkeypatch) -> None:
    # The classic crash: no restype on a pointer getter, so ctypes reads a 32-bit int.
    table = dict(spike.CARBON_SIGNATURES)
    table["GetApplicationEventTarget"] = (ctypes.c_int, ())
    monkeypatch.setattr(spike, "CARBON_SIGNATURES", table)
    shim = spike.CarbonShim(lib=c_carbon)
    assert shim.target() != C_APP_TARGET
    status, handler = shim.install_handler()
    assert status == C_PARAM_ERR and handler is None


def test_the_c_library_catches_a_hot_key_id_passed_by_pointer(c_carbon, monkeypatch) -> None:
    table = dict(spike.CARBON_SIGNATURES)
    restype, argtypes = table["RegisterEventHotKey"]
    by_pointer = (*argtypes[:2], ctypes.POINTER(spike.EventHotKeyID), *argtypes[3:])
    table["RegisterEventHotKey"] = (restype, by_pointer)
    monkeypatch.setattr(spike, "CARBON_SIGNATURES", table)
    shim = spike.CarbonShim(lib=c_carbon)
    assert shim.install_handler()[0] == 0
    status, ref = shim.register(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS, 1)
    assert status == C_PARAM_ERR and ref is None


class _CPressingInjector(spike.Injector):
    """osascript stand-in that delivers through the compiled library, ``repeat`` times."""

    lib: Any

    def __init__(self, repeat: int = 1) -> None:
        super().__init__(delay_s=0.0, repeat=repeat, run=lambda *a, **k: None)

    def _work(self) -> None:
        try:
            for _ in range(self.repeat):
                type(self).lib.fake_press(spike.TEST_KEY_CODE, spike.TEST_MODIFIERS)
            self.result = {"returncode": 0}
        finally:
            self.done.set()


def test_the_scenarios_run_through_real_ctypes_against_the_c_library(c_carbon, monkeypatch) -> None:
    _CPressingInjector.lib = c_carbon
    monkeypatch.setattr(spike, "Injector", _CPressingInjector)
    monkeypatch.setattr(spike, "read_keydown_counters", lambda *a, **k: None)
    spike._STATE.inject = True
    shim = spike.CarbonShim(lib=c_carbon)
    _drive(spike._co_nsapp_register_unregister(shim))
    steps = _steps()
    assert steps["register"] == 0 and steps["pressed_while_registered"] == 1
    assert steps["pressed_after_unregister"] == 0 and steps["reregister_after_unregister"] == 0
    assert spike.child_ok(spike._STATE.steps, spike._STATE.errors)

    spike.reset_child_state()
    spike._STATE.inject = True
    _drive(spike._co_register_unregister_cycles(spike.CarbonShim(lib=c_carbon)))
    steps = _steps()
    assert steps["cycle_failures"] == 0 and steps["pressed_press_cycles"] == spike.PRESS_CYCLES
    assert steps["every_press_arrived"] is True and spike._STATE.errors == []


def test_the_scenario_reports_a_leak_through_real_ctypes_as_a_duplicate_registration(
    c_carbon,
) -> None:
    shim = spike.CarbonShim(lib=c_carbon)
    _drive(spike._co_duplicate_register(shim))
    steps = _steps()
    assert steps["register_first"] == 0 and steps["second_returned_hotkey_exists_err"] is True
    assert steps["unregister_first"] == 0 and steps["unregister_second_skipped_null_ref"] is True


# --- the version guard --------------------------------------------------------------


def _module_source() -> str:
    return Path(spike.__file__).read_text(encoding="utf-8")


def test_the_version_guard_runs_before_the_first_3_11_only_import() -> None:
    tree = ast.parse(_module_source())
    guard_index = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.If) and "version_info" in ast.unparse(node.test)
    )
    utc_index = next(
        index
        for index, node in enumerate(tree.body)
        if isinstance(node, ast.ImportFrom)
        and node.module == "datetime"
        and any(alias.name == "UTC" for alias in node.names)
    )
    assert guard_index < utc_index
    # The guard can only run on an old interpreter if the file still PARSES there.
    ast.parse(_module_source(), feature_version=(3, 9))


def test_the_version_guard_exits_with_a_clear_message_on_an_old_python() -> None:
    tree = ast.parse(_module_source())
    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.If) and "version_info" in ast.unparse(node.test)
    )

    class OldSys:
        version_info = (3, 9, 18, "final", 0)
        version = "3.9.18 (default, Aug 2024)"

        @staticmethod
        def exit(message: str) -> None:
            raise SystemExit(message)

    with pytest.raises(SystemExit) as raised:
        exec(compile(ast.unparse(guard), "<guard>", "exec"), {"sys": OldSys})  # noqa: S102
    message = str(raised.value)
    assert "needs Python 3.11 or newer" in message and "3.9.18" in message

    class NewSys(OldSys):
        version_info = (3, 12, 1, "final", 0)

    exec(compile(ast.unparse(guard), "<guard>", "exec"), {"sys": NewSys})  # noqa: S102 - no exit


@pytest.mark.skipif(shutil.which("python3.10") is None, reason="no python3.10 on this host")
def test_a_real_python_3_10_gets_the_message_not_an_import_error(tmp_path: Path) -> None:
    done = subprocess.run(
        [str(shutil.which("python3.10")), spike.__file__, "--out", str(tmp_path / "x.json")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode != 0
    assert "needs Python 3.11 or newer" in done.stderr and "ImportError" not in done.stderr
    assert not (tmp_path / "x.json").exists()


# --- the main-thread driver ---------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_main_loop_drives_a_scenario_through_waits_without_blocking() -> None:
    clock = _Clock()
    finished: list[tuple[dict[str, Any], int]] = []
    loop = spike.MainLoop(lambda extra, code: finished.append((extra, code)), clock)
    flag = {"go": False}
    order: list[str] = []

    def scenario() -> Any:
        order.append("start")
        yield spike.Wait(lambda: flag["go"], timeout_s=5.0, settle_s=0.5)
        order.append("resumed")
        return {"done": True}

    loop.drive(scenario())
    loop.tick()  # advance -> first yield
    loop.tick()  # poll: predicate false
    assert order == ["start"] and finished == []
    clock.now = 0.2
    flag["go"] = True
    loop.tick()  # poll sees it, schedules the settle
    loop.tick()
    assert order == ["start"], "the settle delay has not passed"
    clock.now = 0.8
    loop.tick()  # then -> advance (adds a job for the next tick)
    loop.tick()
    assert order == ["start", "resumed"] and finished == [({"done": True}, 0)]


def test_main_loop_gives_up_waiting_at_the_deadline() -> None:
    clock = _Clock()
    finished: list[tuple[dict[str, Any], int]] = []
    loop = spike.MainLoop(lambda extra, code: finished.append((extra, code)), clock)

    def scenario() -> Any:
        yield spike.Wait(lambda: False, timeout_s=2.0)
        return {"waited": True}

    loop.drive(scenario())
    for _ in range(60):
        loop.tick()
        clock.now += 0.1
        if finished:
            break
    assert finished == [({"waited": True}, 0)] and clock.now >= 2.0


def test_main_loop_turns_a_scenario_exception_into_a_recorded_error_and_exit_1() -> None:
    clock = _Clock()
    finished: list[tuple[dict[str, Any], int]] = []
    loop = spike.MainLoop(lambda extra, code: finished.append((extra, code)), clock)

    def scenario() -> Any:
        raise ValueError("harness bug")
        yield  # pragma: no cover - makes this a generator

    loop.drive(scenario())
    loop.tick()
    assert finished == [({"ok": False}, 1)]
    assert any("ValueError: harness bug" in error for error in spike._STATE.errors)


# --- child entry point and the real harness on a non-macOS host --------------------


def test_run_child_reports_an_unknown_experiment(capsys: pytest.CaptureFixture[str]) -> None:
    assert spike.run_child("nope", inject=False) == 2
    result = spike.parse_child_result(capsys.readouterr().out)
    assert result is not None and result["ok"] is False and "unknown experiment" in result["error"]


@pytest.mark.skipif(sys.platform == "darwin", reason="would load Carbon for real")
def test_run_child_refuses_macos_experiments_elsewhere(capsys: pytest.CaptureFixture[str]) -> None:
    assert spike.run_child("carbon_symbols", inject=True) == 1
    result = spike.parse_child_result(capsys.readouterr().out)
    assert result is not None and result["ok"] is False
    assert "requires macOS" in result["error"] and result["synthetic_key_requested"] is True


@pytest.mark.skipif(sys.platform == "darwin", reason="would probe the real frameworks")
def test_tcc_context_is_unsupported_off_macos_and_never_raises() -> None:
    tcc = spike.collect_tcc_context()
    assert tcc["supported"] is False and tcc["probes"] == {} and "requires macOS" in tcc["reason"]
    facts = spike.collect_runner_facts({"RUNNER_OS": "macOS", "AWS_SECRET_ACCESS_KEY": "x"})
    assert facts["ci_env"] == {"RUNNER_OS": "macOS"}  # an allowlist, nothing else
    assert facts["platform"] == sys.platform and facts["python_version"]


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="real POSIX child; no Carbon")
def test_the_real_harness_end_to_end_records_a_real_crash_and_exits_zero(tmp_path: Path) -> None:
    out = tmp_path / "nested" / "spike.json"
    md = tmp_path / "summary.md"
    done = subprocess.run(
        [sys.executable, str(spike.__file__), "--out", str(out), "--summary-md", str(md)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert [e["name"] for e in report["experiments"]] == list(EXPECTED_NAMES)
    control = report["experiments"][0]
    assert control["outcome"] == "crash" and control["signal"] == "SIGABRT"
    assert control["returncode"] == -int(signal.SIGABRT) and control["as_expected"] is True
    assert control["last_step"] == "about_to_abort"
    # The TCC context is collected by a CHILD (the parent loads no framework): on a non-macOS
    # host that child runs, reports "unsupported" and ends ok; every Carbon experiment is skipped.
    by_name = {e["name"]: e for e in report["experiments"]}
    assert by_name["tcc_context"]["outcome"] == "ok" and by_name["tcc_context"]["returncode"] == 0
    assert all(
        e["outcome"] == "skipped"
        for e in report["experiments"]
        if e["name"] not in ("control_signal_capture", "tcc_context")
    )
    assert report["tcc"]["supported"] is False and "requires macOS" in report["tcc"]["reason"]
    assert report["evidence"]["usable_as_evidence"] is False
    assert report["evidence"]["tcc_source"] == "tcc_context"
    assert report["complete"] is True and report["not_covered"] == list(spike.NOT_COVERED)
    assert not any(row["group_killed"] for row in report["experiments"])
    markdown = md.read_text(encoding="utf-8")
    assert spike.EVIDENCE_DISCLAIMER in markdown and spike.EVIDENCE_RULE in markdown
    assert spike.EVIDENCE_DISCLAIMER in done.stdout
    assert sorted(path.name for path in tmp_path.rglob("*.tmp")) == []  # atomic writes tidy up


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="real POSIX child; no Carbon")
def test_only_still_runs_the_tcc_context_child(tmp_path: Path) -> None:
    out = tmp_path / "spike.json"
    assert spike.main(["--out", str(out), "--only", "duplicate_register"]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert [e["name"] for e in report["experiments"]] == ["tcc_context", "duplicate_register"]
    assert report["options"]["only"] == "duplicate_register"


def test_a_killed_parent_still_leaves_every_finished_experiment_on_disk(
    tmp_path: Path, monkeypatch
) -> None:
    finished = {
        "name": "tcc_context",
        "outcome": "ok",
        "returncode": 0,
        "signal": None,
        "duration_s": 0.1,
        "observations": {"tcc": {"supported": False, "reason": "x", "probes": {}}},
    }

    def interrupted_after_one(planned, *, on_result, **_kwargs):
        on_result([finished])  # one experiment finished and was persisted ...
        raise KeyboardInterrupt  # ... then the job is cancelled

    out, md = tmp_path / "r.json", tmp_path / "r.md"
    monkeypatch.setattr(spike, "run_experiments", interrupted_after_one)
    with pytest.raises(KeyboardInterrupt):
        spike.main(["--out", str(out), "--summary-md", str(md)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["complete"] is False
    assert [e["name"] for e in report["experiments"]] == ["tcc_context"]
    assert report["tcc"]["reason"] == "x"
    assert "Partial report" in md.read_text(encoding="utf-8")

    # Even a parent killed before the first experiment ends leaves a (empty, partial) file.
    out2 = tmp_path / "r2.json"
    monkeypatch.setattr(
        spike, "run_experiments", lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        spike.main(["--out", str(out2)])
    first = json.loads(out2.read_text(encoding="utf-8"))
    assert first["complete"] is False and first["experiments"] == []
    assert "did not run" in first["tcc"]["reason"]


def test_run_experiments_hands_the_rows_so_far_to_the_callback_after_each_one() -> None:
    ok = spike.RESULT_PREFIX + json.dumps({"ok": True})
    runner = _Runner(_Done(0, ok.encode()))
    seen: list[list[str]] = []
    rows = spike.run_experiments(
        [
            spike.experiment_by_name("tcc_context"),
            spike.experiment_by_name("control_signal_capture"),
        ],
        script_path=Path("/x/spike.py"),
        inject=False,
        on_result=lambda rows: seen.append([row["name"] for row in rows]),
        run=runner,
    )
    assert seen == [["tcc_context"], ["tcc_context", "control_signal_capture"]]
    assert [row["name"] for row in rows] == seen[-1] and len(runner.calls) == 2


def test_tcc_comes_from_the_tcc_context_child_or_says_why_it_is_missing() -> None:
    good = {"supported": True, "probes": {}, "window_server": None}
    rows = [{"name": "tcc_context", "outcome": "ok", "observations": {"tcc": good}}]
    assert spike.tcc_from_experiments(rows) == good
    crashed = [
        {"name": "tcc_context", "outcome": "crash", "signal": "SIGSEGV", "last_step": "probe=1"}
    ]
    tcc = spike.tcc_from_experiments(crashed)
    assert tcc["supported"] is False and tcc["probes"] == {}
    assert "outcome=crash" in tcc["reason"] and "SIGSEGV" in tcc["reason"]
    assert "did not run" in spike.tcc_from_experiments([])["reason"]


def test_write_text_is_atomic_and_creates_parents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "report.json"
    spike._write_text(target, "one")
    spike._write_text(target, "two")
    assert target.read_text(encoding="utf-8") == "two"
    assert [path.name for path in target.parent.iterdir()] == ["report.json"]


def test_main_validates_its_arguments(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as no_out:
        spike.main([])
    assert no_out.value.code == 2
    with pytest.raises(SystemExit) as bad_only:
        spike.main(["--out", str(tmp_path / "x.json"), "--only", "bogus"])
    assert bad_only.value.code == 2


# --- the script's own promises -----------------------------------------------------


def test_the_script_never_imports_a_network_module() -> None:
    tree = ast.parse(Path(spike.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    network = {"socket", "ssl", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib"}
    assert not imported & network
    assert "jarvis" not in imported  # stdlib + ctypes only


# --- the workflow ------------------------------------------------------------------


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for job in doc["jobs"].values() for step in job["steps"]]


def test_workflow_is_dispatch_only_with_a_read_only_token() -> None:
    doc = _workflow()
    triggers = doc.get("on", doc.get(True))  # PyYAML reads the bare key `on` as True
    assert set(triggers) == {"workflow_dispatch"}
    assert doc["permissions"] == {"contents": "read"}
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")


def test_workflow_pins_every_action_to_a_commit() -> None:
    uses = [step["uses"] for step in _steps_of(_workflow()) if "uses" in step]
    assert uses, "the workflow uses no action at all"
    for reference in uses:
        assert re.search(r"@[0-9a-f]{40}$", reference), reference


def test_workflow_matrix_has_one_intel_and_one_arm64_runner_by_default() -> None:
    doc = _workflow()
    plan = doc["jobs"]["plan"]["steps"][0]["run"]
    assert '"runner":"macos-15-intel","arch":"x86_64"' in plan
    assert '"runner":"macos-15","arch":"arm64"' in plan
    assert doc["jobs"]["spike"]["runs-on"] == "${{ matrix.runner }}"
    assert doc["jobs"]["spike"]["strategy"]["fail-fast"] is False
    # The optional macOS 26 row is opt-in: a missing label would queue forever.
    inputs = (doc.get("on", doc.get(True)))["workflow_dispatch"]["inputs"]
    assert inputs["include_macos_26"]["default"] is False
    assert "macos-26" in plan and 'INCLUDE_MACOS_26" = "true"' in plan


def test_workflow_runs_the_script_uploads_the_json_and_appends_the_summary() -> None:
    steps = _steps_of(_workflow())
    run = next(s for s in steps if s.get("name") == "Run the Carbon hotkey spike")["run"]
    assert "scripts/ci/macos_carbon_hotkey_spike.py" in run
    assert "--out" in run and "--summary-md" in run and "--inject-synthetic-key" in run
    summary = next(s for s in steps if s.get("name") == "Publish the step summary")
    assert summary["if"] == "always()"
    assert "GITHUB_STEP_SUMMARY" in summary["run"]
    # The fallback text must not drift from the script's sentence.
    assert spike.EVIDENCE_DISCLAIMER in summary["run"]
    upload = next(s for s in steps if s.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always()" and upload["with"]["path"] == "spike-out/"
    assert "carbon-hotkey-spike-" in upload["with"]["name"]
