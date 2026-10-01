#!/usr/bin/env python3
"""Runner evidence for a FUTURE Carbon ``RegisterEventHotKey`` hotkey backend.

No Carbon backend ships in this repository. macOS global shortcuts still use the
listen-only CGEventTap (``QuartzHotkeyBackend``). This script exists so that the
follow-up that would add a Carbon backend starts from measured facts instead of
third-party claims: it records what the macOS runner reports, then runs every
risky ctypes variant in a CHILD process so that a native crash (SIGSEGV, SIGILL,
SIGTRAP, SIGABRT) becomes a row in a table instead of killing the job.

What it does, in this order:

1. Records the TCC context FIRST of everything that loads native code (only the
   signal control, which loads nothing, runs earlier), in a CHILD process like all
   native code here (the ``tcc_context`` experiment): ``CGPreflightListenEventAccess``,
   ``CGPreflightPostEventAccess``, ``AXIsProcessTrusted``, ``IOHIDCheckAccess``
   (listen and post), the macOS version/build, the architecture, the Python
   build and whether a window-server session exists. Every probe is wrapped; a
   failure is recorded, never raised. It only ever calls non-prompting reads: no
   ``Request`` API, no event tap. The parent process loads no framework at all.
2. Runs each experiment of :data:`EXPERIMENTS` in its own child process
   (``python <this file> --child NAME``) in its own process group under a hard
   timeout and records the return code, the signal name (a negative return
   code), the outcome and the stdout/stderr tails. A timeout, an interrupt and a
   normal exit all end with a ``SIGKILL`` of the whole group, so a hung
   ``osascript`` cannot outlive its experiment and press keys into the next one.
   The experiments are: register/unregister under a real NSApplication loop
   (plus 200 register/unregister cycles and 50 synthetic presses), register with
   no NSApplication, unregister from a non-main thread, duplicate registration,
   registration under secure event input, installing the handler twice, the
   Appshot both-Option read (with the key actually held, through three
   injection routes), and two controls that tell a harness bug from a Carbon
   crash.
3. Writes ONE JSON document (``--out``) after EVERY experiment (atomically, with
   ``"complete": false`` until the last one), and prints a short summary. With
   ``--summary-md`` it also writes the same table as Markdown for
   ``$GITHUB_STEP_SUMMARY``. A killed parent therefore still leaves a report.

What the ctypes code deliberately mirrors from the red-team crash-surface list,
so that the evidence is about the shape a real shim would have: ``restype`` is
``c_void_p`` on the event-target getters, ``EventHotKeyID`` is passed by value,
the callback and the ``EventTypeSpec`` array are module-level singletons, the
handler is installed on the APPLICATION event target, the callback returns
``eventNotHandledErr`` on an internal error, and every native call runs on the
main thread inside a running ``NSApplication`` loop (except where the variant
exists to test the opposite).

What this can NOT show, and the report says so: runners pre-grant TCC to
bash/osascript/Terminal; no dialog is shown; no physical keyboard exists; and no
key-hold semantics (modifier lifted first, auto-repeat) are exercised. A run
counts as evidence for "Carbon needs no TCC grant" only when the preflights are
false in the SAME process that received a hot key event
(:func:`evaluate_evidence`), the registration returned noErr, a Pressed arrived
while the chord was registered, and the variant ended ``ok``. The synthetic key
comes from ``osascript``; it is sent only with ``--inject-synthetic-key`` (the
workflow passes it) because ``osascript`` driving System Events can raise an
Automation prompt on a developer's own Mac. "No event arrived" is only
evidence that Carbon did not deliver when an independent control saw the key
(the system-wide key-down counter moved); without it the verdict is
``inconclusive`` (a missing Automation/Accessibility grant for ``osascript``
looks exactly like a silent Carbon). Nothing here is verified on a Mac by its
author.

NOT covered, and the report says so (:data:`NOT_COVERED`): variant G of the
red-team list (the hop test, ``AppHelper.callAfter`` versus
``performSelectorOnMainThread:withObject:waitUntilDone:modes:`` under an
event-tracking run-loop mode; it needs pyobjc and an Objective-C target object,
which this standard-library script does not build) and the off-main REGISTER
half of variant C (only the off-main UNREGISTER is measured). Flip criterion 1
("spike A, D, E, G green") therefore stays OPEN until G exists.

The script never touches the network, needs only the standard library plus
ctypes, and is import-safe on every OS (no framework is loaded at import).
It exits 0 even when a variant crashes (a crash is data). It exits non-zero only
when the harness itself breaks (unwritable output, bad arguments). It needs
Python 3.11 or newer and says so instead of raising an ImportError.

Usage (macOS, Python 3.11+):
    python3.11 scripts/ci/macos_carbon_hotkey_spike.py --out spike.json \\
        --summary-md summary.md --inject-synthetic-key
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):  # noqa: UP036 - the repo targets 3.11, an old Mac python does not
    sys.exit(
        "macos_carbon_hotkey_spike.py needs Python 3.11 or newer "
        f"(running {sys.version.split()[0]}): use python3.11 or the CI runner's Python."
    )

import argparse
import ctypes
import ctypes.util
import faulthandler
import importlib
import json
import os
import platform
import signal
import subprocess
import sysconfig
import threading
import time
import traceback
from collections.abc import Callable, Generator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

SCHEMA_VERSION = 1
TOOL_NAME = "macos_carbon_hotkey_spike"

#: The sentence the step summary and the docs must carry next to any number.
EVIDENCE_DISCLAIMER = (
    "Runner evidence only: runners pre-grant TCC to bash/osascript/Terminal; no dialog is "
    "shown, no physical keyboard exists, no key-hold semantics were exercised."
)
EVIDENCE_RULE = (
    "Counts as evidence for 'Carbon needs no TCC grant' only if, in the SAME process that "
    "received a hot key event, CGPreflightListenEventAccess is false, AXIsProcessTrusted is "
    "false and IOHIDCheckAccess(listen) is not 'granted', the registration returned noErr, a "
    "Pressed arrived while the chord was registered and the variant ended ok."
)

#: What this tool does not measure; copied into the report and the step summary so a
#: reader never mistakes a green table for the whole flip criterion.
NOT_COVERED = (
    "Variant G (hop test: AppHelper.callAfter vs performSelectorOnMainThread:modes: under an "
    "event-tracking run-loop mode): flip criterion 1 'spike A, D, E, G green' stays open.",
    "Variant C is only half covered: the off-main UNREGISTER is measured, the off-main "
    "REGISTER is not.",
    "Hold semantics (modifier lifted first, auto-repeat), a physical keyboard, a first press "
    "with clean TCC and the frozen app are not exercised (physical-Mac / built-app items).",
)

OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_CRASH = "crash"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_SKIPPED = "skipped"
OUTCOMES = (OUTCOME_OK, OUTCOME_ERROR, OUTCOME_CRASH, OUTCOME_TIMEOUT, OUTCOME_SKIPPED)

RESULT_PREFIX = "SPIKE_RESULT "
STEP_PREFIX = "STEP "
TAIL_CHARS = 2000

#: Environment variables copied into the report. A fixed allowlist: nothing else
#: from the environment is ever recorded.
CI_ENV_ALLOWLIST = (
    "GITHUB_ACTIONS",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_SHA",
    "GITHUB_WORKFLOW",
    "ImageOS",
    "ImageVersion",
    "RUNNER_ARCH",
    "RUNNER_OS",
)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# ---------------------------------------------------------------------------
# Carbon constants and signatures. Values are from the public headers as the
# author remembers them (Events.h, CarbonEvents.h, MacTypes.h); the child's own
# results are the only verification. Nothing here loads a framework.
# ---------------------------------------------------------------------------


def fourcc(text: str) -> int:
    """A four-character code ('keyb') as the big-endian UInt32 Carbon uses."""
    raw = text.encode("ascii")
    if len(raw) != 4:
        raise ValueError(f"a four-character code needs exactly 4 characters: {text!r}")
    return int.from_bytes(raw, "big")


CARBON_FRAMEWORK = "Carbon"
NO_ERR = 0
EVENT_NOT_HANDLED_ERR = -9874
EVENT_HOT_KEY_EXISTS_ERR = -9878
K_EVENT_CLASS_KEYBOARD = fourcc("keyb")
K_EVENT_HOT_KEY_PRESSED = 5
K_EVENT_HOT_KEY_RELEASED = 6
K_EVENT_PARAM_DIRECT_OBJECT = fourcc("----")
TYPE_EVENT_HOT_KEY_ID = fourcc("hkid")
HOT_KEY_SIGNATURE = fourcc("JRVS")

CMD_KEY = 0x100
SHIFT_KEY = 0x200
OPTION_KEY = 0x800
CONTROL_KEY = 0x1000

VK_ANSI_J = 0x26
VK_OPTION = 0x3A  # kVK_Option (58)
VK_RIGHT_OPTION = 0x3D  # kVK_RightOption (61)

#: The chord every variant registers: Control+Option+J. Every shipped Jarvis
#: default carries Ctrl or Cmd, so this is a representative "modifiers + one key".
TEST_KEY_CODE = VK_ANSI_J
TEST_MODIFIERS = CONTROL_KEY | OPTION_KEY

NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY = 1

K_CG_EVENT_SOURCE_STATE_COMBINED_SESSION = 0
K_CG_EVENT_SOURCE_STATE_HID_SYSTEM = 1
#: The two source states every Appshot read and the key-down control sample.
OPTION_SOURCE_STATES = (
    ("combined_session", K_CG_EVENT_SOURCE_STATE_COMBINED_SESSION),
    ("hid_system", K_CG_EVENT_SOURCE_STATE_HID_SYSTEM),
)
K_CG_EVENT_KEY_DOWN = 10
K_CG_EVENT_FLAG_MASK_ALTERNATE = 0x00080000
NX_DEVICE_LEFT_ALT_KEY_MASK = 0x20
NX_DEVICE_RIGHT_ALT_KEY_MASK = 0x40

IOHID_REQUEST_POST_EVENT = 0
IOHID_REQUEST_LISTEN_EVENT = 1
IOHID_ACCESS_GRANTED = 0
IOHID_ACCESS_LABELS = {0: "granted", 1: "denied", 2: "unknown"}


class EventHotKeyID(ctypes.Structure):
    """``EventHotKeyID`` (8 bytes). Carbon takes it BY VALUE."""

    _fields_ = (("signature", ctypes.c_uint32), ("id", ctypes.c_uint32))


class EventTypeSpec(ctypes.Structure):
    """``EventTypeSpec`` (8 bytes)."""

    _fields_ = (("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32))


#: ``OSStatus (*)(EventHandlerCallRef, EventRef, void *userData)``.
HANDLER_PROTO = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
#: ``void (*CFRunLoopTimerCallBack)(CFRunLoopTimerRef, void *info)``.
TIMER_PROTO = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)

#: name -> (restype, argtypes). The pinned crash-surface items live here:
#: ``c_void_p`` restype on the target getters (a default ``c_int`` would truncate
#: a 64-bit pointer), ``EventHotKeyID`` by value, strict argtypes everywhere.
CARBON_SIGNATURES: dict[str, tuple[Any, tuple[Any, ...]]] = {
    "GetApplicationEventTarget": (ctypes.c_void_p, ()),
    "GetEventDispatcherTarget": (ctypes.c_void_p, ()),
    "InstallEventHandler": (
        ctypes.c_int32,
        (
            ctypes.c_void_p,
            HANDLER_PROTO,
            ctypes.c_uint32,
            ctypes.POINTER(EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ),
    ),
    "RemoveEventHandler": (ctypes.c_int32, (ctypes.c_void_p,)),
    "RegisterEventHotKey": (
        ctypes.c_int32,
        (
            ctypes.c_uint32,
            ctypes.c_uint32,
            EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ),
    ),
    "UnregisterEventHotKey": (ctypes.c_int32, (ctypes.c_void_p,)),
    "GetEventClass": (ctypes.c_uint32, (ctypes.c_void_p,)),
    "GetEventKind": (ctypes.c_uint32, (ctypes.c_void_p,)),
    "GetEventParameter": (
        ctypes.c_int32,
        (
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ),
    ),
    "IsSecureEventInputEnabled": (ctypes.c_ubyte, ()),
    # Believed to return OSStatus; the raw value is recorded, the read-back via
    # IsSecureEventInputEnabled is the actual evidence.
    "EnableSecureEventInput": (ctypes.c_int32, ()),
    "DisableSecureEventInput": (ctypes.c_int32, ()),
}


#: Symbols whose exported name might differ from the header name. The author believes
#: ``InstallEventHandler`` is a plain exported function but could not rule out that the
#: SDK reaches it through an underscore-prefixed variant. The alias is tried ONLY when the
#: plain name is missing, and the report records which name bound.
SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {"InstallEventHandler": ("_InstallEventHandler",)}


def bind_carbon(lib: Any) -> tuple[list[str], list[str], dict[str, str]]:
    """Set ``restype``/``argtypes`` on every function of :data:`CARBON_SIGNATURES`.

    Returns ``(bound, missing, aliases)``. A symbol the library does not export is
    reported, never raised, so one missing function is a row in the report. When a
    symbol only exists under an alias of :data:`SYMBOL_ALIASES`, the alias function is
    also set on ``lib`` under the canonical name (so callers keep one spelling) and
    ``aliases`` maps canonical name to the name that bound.
    """
    bound: list[str] = []
    missing: list[str] = []
    aliases: dict[str, str] = {}
    for name, (restype, argtypes) in CARBON_SIGNATURES.items():
        function = None
        for candidate in (name, *SYMBOL_ALIASES.get(name, ())):
            try:
                function = getattr(lib, candidate)
            except AttributeError:
                continue  # not exported under this spelling: the next candidate is tried
            if candidate != name:
                aliases[name] = candidate
                setattr(lib, name, function)
            break
        if function is None:
            missing.append(name)
            continue
        function.restype = restype
        function.argtypes = list(argtypes)
        bound.append(name)
    return bound, missing, aliases


# ---------------------------------------------------------------------------
# The experiment catalogue: names and ORDER are part of the report contract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Experiment:
    name: str
    title: str
    #: "control" tells a harness bug from a Carbon crash; "variant" is a risky
    #: Carbon call pattern; "probe" is a plain API read.
    kind: str
    question: str
    timeout_s: float
    requires_macos: bool = True
    #: Only controls have an expectation; variants are measurements.
    expected_outcome: str | None = None


EXPERIMENTS: tuple[Experiment, ...] = (
    Experiment(
        name="control_signal_capture",
        title="Harness control: the child kills itself with SIGABRT",
        kind="control",
        question="Does the harness record a signal exit as outcome=crash with the signal name?",
        timeout_s=30.0,
        requires_macos=False,
        expected_outcome=OUTCOME_CRASH,
    ),
    Experiment(
        name="tcc_context",
        title="Record the TCC context (non-prompting preflight reads) in a child process",
        kind="probe",
        question=(
            "What do CGPreflightListenEventAccess, AXIsProcessTrusted and IOHIDCheckAccess "
            "report, and is there a window-server session? (Runs before every Carbon variant.)"
        ),
        timeout_s=60.0,
        requires_macos=False,
    ),
    Experiment(
        name="carbon_symbols",
        title="Harness control: load Carbon by explicit path and bind its functions",
        kind="control",
        question="Does the explicit-path load resolve every symbol the shim needs?",
        timeout_s=30.0,
        expected_outcome=OUTCOME_OK,
    ),
    Experiment(
        name="nsapp_register_unregister",
        title="Register + unregister on the main thread inside a real NSApplication loop",
        kind="variant",
        question=(
            "Does the registration return noErr and are Pressed/Released delivered "
            "(with the preflights false), and does unregister free the slot?"
        ),
        timeout_s=120.0,
    ),
    Experiment(
        name="register_unregister_cycles",
        title="200 register/unregister cycles and 50 synthetic presses in a real NSApplication",
        kind="variant",
        question=(
            "Does a re-registration stay noErr across 200 cycles (no leaked slot), and does "
            "each of 50 presses arrive?"
        ),
        timeout_s=300.0,
    ),
    Experiment(
        name="no_nsapp_register",
        title="Register with NO NSApplication running",
        kind="variant",
        question="Does it fail cleanly with an OSStatus or crash (browser-degrade, --headless)?",
        timeout_s=60.0,
    ),
    Experiment(
        name="unregister_off_main_thread",
        title="UnregisterEventHotKey from a non-main thread",
        kind="variant",
        question="Silent leak (-9878 on re-register), an error, a hang or a crash?",
        timeout_s=120.0,
    ),
    Experiment(
        name="duplicate_register",
        title="Register the same key and modifiers twice",
        kind="variant",
        question="Which OSStatus does the second registration return; does a press fire twice?",
        timeout_s=120.0,
    ),
    Experiment(
        name="register_under_secure_input",
        title="Register while secure event input is enabled in the process",
        kind="variant",
        question="Does registration work, and is the chord still delivered under secure input?",
        timeout_s=120.0,
    ),
    Experiment(
        name="install_handler_twice",
        title="Install the application event handler twice",
        kind="variant",
        question="Do both installs succeed, and is each press delivered once or twice?",
        timeout_s=120.0,
    ),
    Experiment(
        name="appshot_option_state_read",
        title="Appshot both-Option read: CGEventSourceKeyState vs CGEventSourceFlagsState",
        kind="probe",
        question=(
            "With an Option key actually held (three injection routes, left and right), what do "
            "KeyState (kVK_Option, kVK_RightOption) and the alternate-flag and device bits of "
            "FlagsState return, per source state, with the preflights recorded in this process?"
        ),
        timeout_s=150.0,
    ),
)

#: Always run, and first among the Carbon-adjacent experiments: every report needs its TCC
#: context, and ``--only`` must not be able to drop it.
ALWAYS_RUN = ("tcc_context",)


def experiment_names() -> tuple[str, ...]:
    return tuple(experiment.name for experiment in EXPERIMENTS)


def experiment_by_name(name: str) -> Experiment:
    for experiment in EXPERIMENTS:
        if experiment.name == name:
            return experiment
    raise KeyError(name)


def select_experiments(only: str) -> tuple[Experiment, ...]:
    """The catalogue, or the comma-separated subset ``only`` (catalogue order)."""
    wanted = [part.strip() for part in only.split(",") if part.strip()]
    if not wanted:
        return EXPERIMENTS
    unknown = [name for name in wanted if name not in experiment_names()]
    if unknown:
        raise ValueError(f"unknown experiment(s): {', '.join(unknown)}")
    return tuple(experiment for experiment in EXPERIMENTS if experiment.name in wanted)


def plan_experiments(only: str) -> tuple[Experiment, ...]:
    """What a run executes: the ``--only`` selection plus :data:`ALWAYS_RUN`, catalogue order."""
    selected = {experiment.name for experiment in select_experiments(only)}
    selected.update(ALWAYS_RUN)
    return tuple(experiment for experiment in EXPERIMENTS if experiment.name in selected)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested on Linux).
# ---------------------------------------------------------------------------


def signal_name(returncode: int | None) -> str | None:
    """The signal that ended a child, from a NEGATIVE return code; else ``None``."""
    if returncode is None or returncode >= 0:
        return None
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"SIG{-returncode}"


def classify_outcome(*, returncode: int | None, timed_out: bool, child_ok: bool | None) -> str:
    """Map a finished child to ``ok|error|crash|timeout``.

    * ``timeout``: the hard timeout fired (the child was killed).
    * ``crash``: the child died from a signal (negative return code).
    * ``ok``: it exited 0 AND reported ``ok`` (every step it expected to succeed did).
    * ``error``: anything else (could not start, non-zero exit, exit 0 with a
      failed expectation or without a result line).
    """
    if timed_out:
        return OUTCOME_TIMEOUT
    if returncode is None:
        return OUTCOME_ERROR
    if returncode < 0:
        return OUTCOME_CRASH
    if returncode != 0:
        return OUTCOME_ERROR
    return OUTCOME_OK if child_ok is True else OUTCOME_ERROR


def tail_text(data: bytes | str | None, limit: int = TAIL_CHARS) -> str:
    """The last ``limit`` characters of captured output, decoded and NUL-free."""
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = text.replace("\x00", "")
    return text[-limit:]


def summarize_argv(argv: Sequence[str]) -> list[str]:
    """``argv`` without machine-specific directories (interpreter and script basenames)."""
    if not argv:
        return []
    summary = [Path(argv[0]).name]
    for index, part in enumerate(argv[1:], start=1):
        summary.append(Path(part).name if index == 1 and part.endswith(".py") else part)
    return summary


def parse_child_result(stdout: str) -> dict[str, Any] | None:
    """The child's final ``SPIKE_RESULT {json}`` line, or ``None`` when there is none.

    An unparseable line yields ``{"_parse_error": ...}`` (no ``ok`` key), so the
    outcome rule reports an error and the report shows why.
    """
    for line in reversed(stdout.splitlines()):
        if not line.startswith(RESULT_PREFIX):
            continue
        try:
            parsed = json.loads(line[len(RESULT_PREFIX) :])
        except ValueError as exc:
            return {"_parse_error": f"{type(exc).__name__}: {exc}"}
        return parsed if isinstance(parsed, dict) else {"_parse_error": "result is not an object"}
    return None


def last_breadcrumb(stdout: str) -> str | None:
    """The last ``STEP ...`` line the child printed: where a crash happened."""
    for line in reversed(stdout.splitlines()):
        if line.startswith(STEP_PREFIX):
            return line[len(STEP_PREFIX) :].strip()
    return None


def child_environment(base: Mapping[str, str]) -> dict[str, str]:
    """The environment of a child: unbuffered, UTF-8, faulthandler on."""
    env = dict(base)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PYTHONFAULTHANDLER": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def md_cell(value: object, limit: int | None = 160) -> str:
    """One Markdown cell: no pipes, no newlines; bounded length unless ``limit`` is ``None``.

    Table cells are bounded. Sentences that DEFINE the evidence (the rule, the notes) are
    rendered with ``limit=None``: a truncated rule is worse than a long line.
    """
    text = str(value).replace("|", "/").replace("\r", " ").replace("\n", " ").strip()
    if limit is None or len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def probe(name: str, sources: Sequence[tuple[str, Callable[[], Any]]]) -> dict[str, Any]:
    """Run the first source that works; record every failure, never raise.

    ``sources`` is ordered by preference (pyobjc if importable, then ctypes).
    """
    attempts: list[dict[str, str]] = []
    for label, function in sources:
        try:
            value = function()
        except Exception as exc:  # recorded in `attempts`; the next source is tried
            attempts.append({"source": label, "error": f"{type(exc).__name__}: {exc}"})
            continue
        return {"name": name, "value": value, "source": label, "error": None, "attempts": attempts}
    error = "; ".join(f"{a['source']}: {a['error']}" for a in attempts) or "no source available"
    return {"name": name, "value": None, "source": None, "error": error, "attempts": attempts}


def _probe_value(tcc: Mapping[str, Any], key: str) -> Any:
    probes = tcc.get("probes")
    entry = probes.get(key) if isinstance(probes, Mapping) else None
    return entry.get("value") if isinstance(entry, Mapping) else None


def preflight_flags(tcc: Mapping[str, Any]) -> dict[str, bool | None]:
    """Whether each of the three gates reports 'granted' (``None`` = unreadable)."""
    listen = _probe_value(tcc, "cg_preflight_listen_event")
    trusted = _probe_value(tcc, "ax_is_process_trusted")
    iohid = _probe_value(tcc, "iohid_check_access_listen")
    return {
        "listen_event_granted": listen if isinstance(listen, bool) else None,
        "accessibility_trusted": trusted if isinstance(trusted, bool) else None,
        "iohid_listen_granted": (iohid == IOHID_ACCESS_GRANTED) if isinstance(iohid, int) else None,
    }


def step_value(observations: Mapping[str, Any], name: str) -> Any:
    """The value of the child's recorded step ``name``; ``None`` when it never ran."""
    steps = observations.get("steps")
    if not isinstance(steps, Sequence) or isinstance(steps, str | bytes):
        return None
    for step in steps:
        if isinstance(step, Mapping) and step.get("step") == name:
            return step.get("value")
    return None


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def evaluate_evidence(
    tcc: Mapping[str, Any], experiments: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Decide whether this run can count as evidence (see :data:`EVIDENCE_RULE`).

    The TCC values of the SAME child process that received the event are
    preferred over the ``tcc_context`` process's; a granted gate anywhere voids the
    run. Delivery is judged on the ``pressed_while_registered`` step only (a Pressed
    that arrives after the unregister, or without a successful registration, proves
    nothing about a registered chord), and the variant must have ended ``ok``.
    """
    nsapp = next((e for e in experiments if e.get("name") == "nsapp_register_unregister"), None)
    observations = (nsapp or {}).get("observations")
    observations = observations if isinstance(observations, Mapping) else {}
    child_tcc = observations.get("tcc")
    use_child = isinstance(child_tcc, Mapping) and bool(child_tcc.get("probes"))
    flags = preflight_flags(child_tcc if use_child else tcc)
    values = list(flags.values())
    if any(value is True for value in values):
        all_false: bool | None = False
    elif all(value is False for value in values):
        all_false = True
    else:
        all_false = None
    register_status = step_value(observations, "register")
    pressed_registered = step_value(observations, "pressed_while_registered")
    injection_seen = step_value(observations, "injection_seen_while_registered")
    variant_ok = (nsapp or {}).get("outcome") == OUTCOME_OK
    delivered: bool | None = None
    if (
        nsapp is not None
        and observations.get("synthetic_key_requested") is True
        and _is_count(register_status)
        and _is_count(pressed_registered)
    ):
        delivered = register_status == NO_ERR and pressed_registered >= 1
    if delivered is True:
        conclusion = "delivered"
    elif delivered is False and register_status != NO_ERR:
        conclusion = "registration_failed"
    elif delivered is False and injection_seen is True:
        conclusion = "not_delivered"
    elif delivered is False:
        conclusion = "inconclusive"
    else:
        conclusion = "unknown"
    notes: list[str] = []
    if all_false is False:
        notes.append("a preflight reports granted in this process: discard the run as evidence")
    elif all_false is None:
        notes.append("a preflight could not be read: the run cannot be claimed as evidence")
    if delivered is None:
        notes.append("no synthetic key was sent (or the variant did not run): delivery unknown")
    elif delivered is False and _is_count(register_status) and register_status != NO_ERR:
        notes.append(
            f"the registration returned OSStatus {register_status}, not noErr: "
            "no Pressed can count as delivered by a registered chord"
        )
    elif delivered is False and conclusion == "not_delivered":
        notes.append(
            "no Pressed arrived while registered although the system-wide key-down counter "
            "moved: the key reached the session but Carbon did not deliver it"
        )
    elif delivered is False:
        notes.append(
            "no Pressed arrived while registered and the injection was NOT confirmed "
            "independently of Carbon (key-down counter / osascript exit status): "
            "inconclusive, not evidence that Carbon failed; see the osascript step"
        )
    if delivered is True and not variant_ok:
        notes.append(
            "a Pressed arrived but the variant did not end ok (an unexpected status, e.g. the "
            "unregister did not take effect): not clean evidence"
        )
    return {
        "rule": EVIDENCE_RULE,
        "tcc_source": "child" if use_child else "tcc_context",
        "preflights": flags,
        "preflights_all_false": all_false,
        "carbon_event_delivered": delivered,
        "injection_seen_independently": injection_seen
        if isinstance(injection_seen, bool)
        else None,
        "delivery_conclusion": conclusion,
        "variant_ok": variant_ok,
        "usable_as_evidence": all_false is True and delivered is True and variant_ok,
        "notes": notes,
    }


def injection_seen(
    before: Mapping[str, int] | None, after: Mapping[str, int] | None
) -> bool | None:
    """Did the system-wide key-down counter move? ``None`` when it could not be read.

    The control for 'no Pressed arrived': it is independent of Carbon, so a counter that
    moved while no Pressed arrived points at Carbon, and a counter that did not move
    points at the injection (a missing Automation or Accessibility grant for ``osascript``).
    """
    if before is None or after is None:
        return None
    return any(after.get(label, 0) > before.get(label, 0) for label in after)


def compare_option_reads(reads: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Compare ``CGEventSourceKeyState`` with the alternate bits of ``FlagsState``.

    ``reads`` maps a source-state label to ``{"key_left", "key_right", "flags_raw"}``.
    For the BASELINE read no key is held, so every value is expected to be false and
    ``as_expected_no_key_held`` is the check; for a HELD phase the same fields say what
    each API reported (see :func:`option_hold_verdict`). A baseline read alone cannot
    show whether KeyState needs Input Monitoring.
    """
    per_state: dict[str, dict[str, Any]] = {}
    any_pressed = False
    for label, read in reads.items():
        flags = int(read.get("flags_raw", 0))
        key_left = bool(read.get("key_left"))
        key_right = bool(read.get("key_right"))
        alternate = bool(flags & K_CG_EVENT_FLAG_MASK_ALTERNATE)
        left_bit = bool(flags & NX_DEVICE_LEFT_ALT_KEY_MASK)
        right_bit = bool(flags & NX_DEVICE_RIGHT_ALT_KEY_MASK)
        per_state[label] = {
            "keystate_left": key_left,
            "keystate_right": key_right,
            "flags_raw_hex": hex(flags),
            "flags_alternate": alternate,
            "flags_left_device_bit": left_bit,
            "flags_right_device_bit": right_bit,
            "agree": (key_left or key_right) == alternate,
        }
        any_pressed = any_pressed or key_left or key_right or alternate or left_bit or right_bit
    return {
        "states": per_state,
        "any_pressed_reported": any_pressed,
        "as_expected_no_key_held": not any_pressed,
        "limitation": (
            "a read with no key held cannot show whether KeyState needs Input "
            "Monitoring; it shows only that the calls return and what they return"
        ),
    }


def merge_option_samples(
    samples: Sequence[Mapping[str, Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    """The PEAK of a sampling window, per source state: any key read true, flag words OR-ed.

    The held key is down for only part of the window (``osascript`` needs time to start), so
    a single read would depend on timing; the peak does not.
    """
    merged: dict[str, dict[str, Any]] = {}
    for sample in samples:
        for label, read in sample.items():
            peak = merged.setdefault(label, {"key_left": False, "key_right": False, "flags_raw": 0})
            peak["key_left"] = peak["key_left"] or bool(read.get("key_left"))
            peak["key_right"] = peak["key_right"] or bool(read.get("key_right"))
            peak["flags_raw"] |= int(read.get("flags_raw", 0))
    return merged


def option_hold_verdict(
    baseline: Mapping[str, Mapping[str, Any]],
    phases: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """What each API reported while an Option key was held, and what that does NOT show.

    ``phases`` maps a phase name to ``{"reads": <peak per source state>, "injection_exit":
    <osascript exit status or None>}``. The verdict states observations ("KeyState saw the key
    where FlagsState did not"), never a policy: whether either read needs Input Monitoring is
    decided from these values together with the preflights recorded in the same process, by a
    maintainer, and a phase where NO read saw the key is ``inconclusive`` (the injection may
    simply not have set modifier state).
    """
    per_phase: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    injected = 0
    any_seen = False
    for name, phase in phases.items():
        compared = compare_option_reads(phase.get("reads") or {})
        exit_status = phase.get("injection_exit")
        states: dict[str, dict[str, Any]] = {}
        phase_seen = False
        for label, state in compared["states"].items():
            keystate = bool(state["keystate_left"] or state["keystate_right"])
            flags = bool(
                state["flags_alternate"]
                or state["flags_left_device_bit"]
                or state["flags_right_device_bit"]
            )
            sides = [
                side
                for side, seen in (
                    ("left", state["keystate_left"] or state["flags_left_device_bit"]),
                    ("right", state["keystate_right"] or state["flags_right_device_bit"]),
                )
                if seen
            ]
            states[label] = {
                "keystate_saw_option": keystate,
                "flagsstate_saw_option": flags,
                "sides_seen": sides,
                "flags_raw_hex": state["flags_raw_hex"],
            }
            phase_seen = phase_seen or keystate or flags
            if keystate and not flags:
                notes.append(f"{name}/{label}: KeyState saw the key, FlagsState did not")
            elif flags and not keystate:
                notes.append(f"{name}/{label}: FlagsState saw the key, KeyState did not")
        if exit_status == 0:
            injected += 1
        any_seen = any_seen or phase_seen
        per_phase[name] = {
            "injection_exit": exit_status,
            "samples": phase.get("samples"),
            "any_api_saw_option": phase_seen,
            "states": states,
        }
    if not phases:
        notes.append("no injection was requested: only the no-key baseline was read")
    elif injected == 0:
        notes.append("no injection route exited 0: nothing can be concluded about the held key")
    elif not any_seen:
        notes.append(
            "inconclusive: an injection exited 0 but no read saw an Option key (the injected "
            "key may not set modifier state, or every read needs a grant)"
        )
    return {
        "baseline": compare_option_reads(baseline),
        "phases": per_phase,
        "injected_routes_exit_0": injected,
        "any_api_saw_option": any_seen,
        "notes": notes,
        "limitation": (
            "observations only: whether KeyState or FlagsState needs Input Monitoring is not "
            "decided here; the preflights of this same process are in the child's tcc block"
        ),
    }


def count_events(events: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Count delivered hot key events by kind, and how many came off the main thread."""
    counts = {"pressed": 0, "released": 0, "other": 0, "off_main_thread": 0}
    for event in events:
        kind = event.get("kind")
        counts[kind if kind in ("pressed", "released") else "other"] += 1
        if event.get("on_main_thread") is False:
            counts["off_main_thread"] += 1
    return counts


def child_ok(steps: Sequence[Mapping[str, Any]], errors: Sequence[str]) -> bool:
    """A child is ok when no recorded error exists and every step with an expectation met it."""
    return not errors and all(step.get("ok", True) is not False for step in steps)


def build_headline(steps: Sequence[Mapping[str, Any]], counts: Mapping[str, int]) -> str:
    """One line of ``step=value`` pairs plus the event counts, for the summary table."""
    parts = []
    for step in steps:
        value = step.get("value")
        if isinstance(value, bool | int) or (isinstance(value, str) and len(value) <= 24):
            parts.append(f"{step.get('step')}={value}")
    if counts.get("pressed") or counts.get("released"):
        parts.append(f"pressed={counts.get('pressed', 0)} released={counts.get('released', 0)}")
    return " ".join(parts)


def assemble_child_result(
    name: str,
    extra: Mapping[str, Any],
    *,
    steps: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    errors: Sequence[str],
    tcc: Mapping[str, Any] | None,
    inject: bool,
) -> dict[str, Any]:
    """The JSON object a child prints on its last line."""
    counts = count_events(events)
    result: dict[str, Any] = dict(extra)
    forced = extra.get("ok")
    result.update(
        {
            "name": name,
            "ok": child_ok(steps, errors) and forced is not False,
            "synthetic_key_requested": inject,
            "tcc": dict(tcc) if tcc is not None else None,
            "steps": [dict(step) for step in steps],
            "events": [dict(event) for event in events],
            "event_counts": counts,
            "errors": list(errors),
        }
    )
    result["headline"] = build_headline(steps, counts)
    return result


def tcc_from_experiments(experiments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The TCC context the ``tcc_context`` CHILD reported (the parent loads no framework).

    A child that crashed, hung or never ran yields an unsupported block that says why, so a
    probe that kills its process is a row in the report and not a lost job.
    """
    row = next((e for e in experiments if e.get("name") == "tcc_context"), None)
    observations = (row or {}).get("observations")
    tcc = observations.get("tcc") if isinstance(observations, Mapping) else None
    if isinstance(tcc, Mapping) and "supported" in tcc:
        return dict(tcc)
    if row is None:
        reason = "the tcc_context experiment did not run"
    else:
        reason = (
            f"the tcc_context child ended with outcome={row.get('outcome')} "
            f"(signal {row.get('signal')}, last step {row.get('last_step')})"
        )
    return {"supported": False, "reason": reason, "probes": {}, "window_server": None}


def build_report(
    *,
    runner: Mapping[str, Any],
    tcc: Mapping[str, Any],
    experiments: Sequence[Mapping[str, Any]],
    started_at: str,
    finished_at: str,
    options: Mapping[str, Any],
    complete: bool = True,
) -> dict[str, Any]:
    """The one JSON document this tool emits (``complete`` is false for a partial write)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "disclaimer": EVIDENCE_DISCLAIMER,
        "complete": complete,
        "started_at": started_at,
        "finished_at": finished_at,
        "options": dict(options),
        "runner": dict(runner),
        "tcc": dict(tcc),
        "experiments": [dict(experiment) for experiment in experiments],
        "evidence": evaluate_evidence(tcc, experiments),
        "not_covered": list(NOT_COVERED),
    }


def _runner_line(report: Mapping[str, Any]) -> str:
    runner = report.get("runner", {})
    if runner.get("mac_ver"):
        build = f" ({runner['macos_build']})" if runner.get("macos_build") else ""
        system = f"macOS {runner['mac_ver']}{build}"
    else:
        system = f"{runner.get('system', '?')} {runner.get('kernel_release', '')}".strip()
    return f"{system} {runner.get('machine', '?')}, Python {runner.get('python_version', '?')}"


def _tcc_pairs(report: Mapping[str, Any]) -> list[tuple[str, str]]:
    tcc = report.get("tcc", {})
    if not tcc.get("supported", False):
        return [("tcc", str(tcc.get("reason", "not collected")))]
    pairs: list[tuple[str, str]] = []
    for key, entry in tcc.get("probes", {}).items():
        value = entry.get("value")
        shown = "unreadable" if value is None else str(value)
        if entry.get("label"):
            shown = f"{shown} ({entry['label']})"
        pairs.append((key, shown))
    server = tcc.get("window_server") or {}
    pairs.append(("window_server_session", str(server.get("cg_session_dictionary_present"))))
    pairs.append(("launchctl_managername", str(server.get("launchctl_managername"))))
    return pairs


def _experiment_row(experiment: Mapping[str, Any]) -> list[str]:
    observations = experiment.get("observations")
    headline = observations.get("headline", "") if isinstance(observations, Mapping) else ""
    if experiment.get("skip_reason"):
        headline = str(experiment["skip_reason"])
    elif not headline and experiment.get("last_step"):
        headline = f"last step: {experiment['last_step']}"
    marker = ""
    if experiment.get("as_expected") is True:
        marker = " (control as expected)"
    elif experiment.get("as_expected") is False:
        marker = " (CONTROL NOT AS EXPECTED)"
    if experiment.get("group_killed") and experiment.get("outcome") != OUTCOME_TIMEOUT:
        marker += " (leftover child processes killed)"
    returncode = experiment.get("returncode")
    return [
        str(experiment.get("name")),
        f"{experiment.get('outcome')}{marker}",
        "-" if returncode is None else str(returncode),
        str(experiment.get("signal") or "-"),
        f"{experiment.get('duration_s', 0):.1f}",
        str(headline),
    ]


def render_summary_text(report: Mapping[str, Any]) -> str:
    """The short human summary printed to the log."""
    rows = [_experiment_row(e) for e in report.get("experiments", [])]
    header = ["experiment", "outcome", "exit", "signal", "secs", "observations"]
    widths = [max([len(header[i]), *(len(row[i]) for row in rows)]) for i in range(5)]
    lines = [
        f"macOS Carbon hotkey spike (schema {report.get('schema_version')})",
        f"runner: {_runner_line(report)}",
        "tcc: " + " ".join(f"{key}={value}" for key, value in _tcc_pairs(report)),
        "",
        "  ".join(header[i].ljust(widths[i]) for i in range(5)) + "  " + header[5],
    ]
    for row in rows:
        lines.append("  ".join(row[i].ljust(widths[i]) for i in range(5)) + "  " + row[5])
    evidence = report.get("evidence", {})
    lines += [
        "",
        f"evidence: preflights_all_false={evidence.get('preflights_all_false')} "
        f"event_delivered={evidence.get('carbon_event_delivered')} "
        f"conclusion={evidence.get('delivery_conclusion')} "
        f"usable={evidence.get('usable_as_evidence')}",
    ]
    lines += [f"  note: {note}" for note in evidence.get("notes", [])]
    if report.get("complete") is False:
        lines += ["", "PARTIAL REPORT: the run did not finish; later experiments are missing."]
    lines += ["", "not covered:"]
    lines += [f"  - {item}" for item in report.get("not_covered", [])]
    lines += ["", EVIDENCE_DISCLAIMER]
    return "\n".join(lines)


def render_summary_markdown(report: Mapping[str, Any]) -> str:
    """The Markdown for ``$GITHUB_STEP_SUMMARY``: TCC context, experiment table, verdict."""
    evidence = report.get("evidence", {})
    lines = [
        "### macOS Carbon hotkey spike",
        "",
        EVIDENCE_DISCLAIMER,
        "",
        f"Runner: {md_cell(_runner_line(report))}",
        "",
        "| TCC context (recorded first) | Value |",
        "| --- | --- |",
    ]
    lines += [f"| {md_cell(key)} | {md_cell(value)} |" for key, value in _tcc_pairs(report)]
    lines += [
        "",
        "| Experiment | Outcome | Exit | Signal | Secs | Observations |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for experiment in report.get("experiments", []):
        *cells, observations = _experiment_row(experiment)
        shown = [md_cell(cell) for cell in cells] + [md_cell(observations, limit=300)]
        lines.append("| " + " | ".join(shown) + " |")
    lines += [
        "",
        f"Evidence gate: preflights all false = `{evidence.get('preflights_all_false')}`, "
        f"Carbon event delivered = `{evidence.get('carbon_event_delivered')}` "
        f"(`{evidence.get('delivery_conclusion')}`), "
        f"usable as evidence = `{evidence.get('usable_as_evidence')}`.",
        "",
        # The rule DEFINES what counts as evidence: never truncated.
        md_cell(evidence.get("rule", EVIDENCE_RULE), limit=None),
    ]
    lines += [f"- {md_cell(note, limit=None)}" for note in evidence.get("notes", [])]
    if report.get("complete") is False:
        lines += ["", "**Partial report: the run did not finish; later experiments are missing.**"]
    lines += ["", "Not covered by this run:"]
    lines += [f"- {md_cell(item, limit=None)}" for item in report.get("not_covered", [])]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Framework loading and the TCC context (every call is wrapped and recorded).
# ---------------------------------------------------------------------------


def framework_path(name: str) -> str:
    return f"/System/Library/Frameworks/{name}.framework/{name}"


def load_framework(name: str) -> tuple[ctypes.CDLL, str]:
    """Load a system framework by explicit path, falling back to ``find_library``.

    Returns the library and which route worked (recorded in the report: the
    frozen app must load Carbon by explicit path).
    """
    explicit = framework_path(name)
    try:
        return ctypes.CDLL(explicit), f"explicit:{explicit}"
    except OSError as explicit_error:
        found = ctypes.util.find_library(name)
        if not found:
            raise OSError(
                f"{name}: explicit path failed ({explicit_error}); no find_library hit"
            ) from explicit_error
        return ctypes.CDLL(found), f"find_library:{found}"


def _bind_symbol(frameworks: Sequence[str], symbol: str, restype: Any, argtypes: Sequence[Any]):
    errors: list[str] = []
    for name in frameworks:
        try:
            library, _route = load_framework(name)
            function = getattr(library, symbol)
        except (OSError, AttributeError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        function.restype = restype
        function.argtypes = list(argtypes)
        return function
    raise OSError(f"{symbol} not found ({'; '.join(errors)})")


def _ctypes_bool(frameworks: Sequence[str], symbol: str) -> Callable[[], bool]:
    def call() -> bool:
        return bool(_bind_symbol(frameworks, symbol, ctypes.c_ubyte, ())())

    return call


def _pyobjc_bool(module: str, symbol: str) -> Callable[[], bool]:
    def call() -> bool:
        return bool(getattr(importlib.import_module(module), symbol)())

    return call


def _iohid_check(request_type: int) -> Callable[[], int]:
    def call() -> int:
        function = _bind_symbol(("IOKit",), "IOHIDCheckAccess", ctypes.c_uint32, (ctypes.c_uint32,))
        return int(function(request_type))

    return call


def probe_window_server() -> dict[str, Any]:
    """Whether a window-server (GUI) session exists, by two independent reads."""
    result: dict[str, Any] = {
        "cg_session_dictionary_present": None,
        "launchctl_managername": None,
        "errors": [],
    }
    try:
        function = _bind_symbol(
            ("CoreGraphics", "ApplicationServices"),
            "CGSessionCopyCurrentDictionary",
            ctypes.c_void_p,
            (),
        )
        result["cg_session_dictionary_present"] = bool(function())
    except Exception as exc:  # recorded under "errors"
        result["errors"].append(f"CGSessionCopyCurrentDictionary: {type(exc).__name__}: {exc}")
    try:
        done = subprocess.run(
            ["launchctl", "managername"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=_NO_WINDOW,
        )
        result["launchctl_managername"] = done.stdout.strip() or None
    except Exception as exc:  # recorded under "errors"
        result["errors"].append(f"launchctl managername: {type(exc).__name__}: {exc}")
    return result


def collect_tcc_context() -> dict[str, Any]:
    """The TCC state the process sees, read WITHOUT prompting (preflight reads only)."""
    if sys.platform != "darwin":
        return {
            "supported": False,
            "reason": f"requires macOS (running on {sys.platform})",
            "probes": {},
            "window_server": None,
        }
    quartz = ("CoreGraphics", "ApplicationServices")
    probes = {
        "cg_preflight_listen_event": probe(
            "CGPreflightListenEventAccess",
            [
                ("pyobjc", _pyobjc_bool("Quartz", "CGPreflightListenEventAccess")),
                ("ctypes", _ctypes_bool(quartz, "CGPreflightListenEventAccess")),
            ],
        ),
        "cg_preflight_post_event": probe(
            "CGPreflightPostEventAccess",
            [
                ("pyobjc", _pyobjc_bool("Quartz", "CGPreflightPostEventAccess")),
                ("ctypes", _ctypes_bool(quartz, "CGPreflightPostEventAccess")),
            ],
        ),
        "ax_is_process_trusted": probe(
            "AXIsProcessTrusted",
            [
                ("pyobjc", _pyobjc_bool("ApplicationServices", "AXIsProcessTrusted")),
                (
                    "ctypes",
                    _ctypes_bool(("ApplicationServices", "HIServices"), "AXIsProcessTrusted"),
                ),
            ],
        ),
        "iohid_check_access_listen": probe(
            "IOHIDCheckAccess(listen)", [("ctypes", _iohid_check(IOHID_REQUEST_LISTEN_EVENT))]
        ),
        "iohid_check_access_post": probe(
            "IOHIDCheckAccess(post)", [("ctypes", _iohid_check(IOHID_REQUEST_POST_EVENT))]
        ),
    }
    for key in ("iohid_check_access_listen", "iohid_check_access_post"):
        probes[key]["label"] = IOHID_ACCESS_LABELS.get(probes[key]["value"], "unreadable")
    return {
        "supported": True,
        "reason": None,
        "probes": probes,
        "window_server": probe_window_server(),
    }


def _macos_build() -> tuple[str | None, str | None]:
    if sys.platform != "darwin":
        return None, None
    try:
        done = subprocess.run(
            ["sw_vers", "-buildVersion"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except Exception as exc:  # returned as the error string
        return None, f"{type(exc).__name__}: {exc}"
    return done.stdout.strip() or None, None


def collect_runner_facts(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Facts about the machine and interpreter; only allowlisted environment variables."""
    env = os.environ if environ is None else environ
    build, build_error = _macos_build()
    return {
        "platform": sys.platform,
        "system": platform.system(),
        "kernel_release": platform.release(),
        "machine": platform.machine(),
        "mac_ver": platform.mac_ver()[0] or None,
        "macos_build": build,
        "macos_build_error": build_error,
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_framework_build": sysconfig.get_config_var("PYTHONFRAMEWORK") or "",
        "ci_env": {key: env[key] for key in CI_ENV_ALLOWLIST if key in env},
    }


# ---------------------------------------------------------------------------
# Parent side: run every experiment in a child process.
# ---------------------------------------------------------------------------

#: How long to wait for the pipes to close after the group was killed.
DRAIN_TIMEOUT_S = 5.0
#: How often a running child is checked for having exited while a descendant holds the pipes.
EXIT_POLL_S = 0.5


@dataclass(frozen=True)
class ChildRun:
    """What :func:`run_in_process_group` returns for a child that was not timed out.

    ``group_killed`` is true when the group still had members after the child's own exit and
    they were killed (leftover ``osascript``, a descendant that held the stdout pipe).
    """

    returncode: int | None
    stdout: bytes
    stderr: bytes
    group_killed: bool = False
    cleanup_error: str | None = None


class ChildTimeout(subprocess.TimeoutExpired):
    """``TimeoutExpired`` that also says whether the group kill reached anything."""

    def __init__(
        self,
        cmd: Sequence[str],
        timeout: float,
        *,
        output: bytes | None,
        stderr: bytes | None,
        group_killed: bool,
        cleanup_error: str | None,
    ) -> None:
        super().__init__(list(cmd), timeout, output=output, stderr=stderr)
        self.group_killed = group_killed
        self.cleanup_error = cleanup_error


def kill_process_group(
    group: int | None, *, leader: subprocess.Popen[bytes]
) -> tuple[bool, str | None]:
    """SIGKILL every member of ``group``; returns ``(something_was_killed, error)``.

    POSIX only (elsewhere only the leader is killed: this tool needs macOS anyway). An empty
    group is the normal case after a clean exit, so ``ProcessLookupError`` is not an error;
    anything else is returned as text and ends up in the report, never swallowed.
    """
    if group is None or os.name != "posix":
        try:
            leader.kill()
        except OSError as exc:
            return False, f"{type(exc).__name__}: {exc}"
        return True, None
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        return False, None
    except OSError as exc:  # EPERM (e.g. a zombie-only group on Darwin) is reported, not hidden
        return False, f"killpg: {type(exc).__name__}: {exc}"
    return True, None


def _drain(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes, str | None]:
    """Collect what is left on the pipes after a group kill; never blocks for long."""
    try:
        stdout, stderr = proc.communicate(timeout=DRAIN_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        # A descendant that left the group (its own setsid) still holds the pipes: take what
        # arrived and close our ends so the harness cannot hang on it.
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
        return (
            exc.stdout or b"",
            exc.stderr or b"",
            "pipes still held after the group kill (a descendant left the process group)",
        )
    return stdout or b"", stderr or b"", None


def run_in_process_group(
    argv: Sequence[str],
    *,
    timeout: float,
    stdin: Any = None,
    env: Mapping[str, str] | None = None,
    creationflags: int = 0,
) -> ChildRun:
    """``subprocess.run`` with capture, plus a SIGKILL of the child's whole process group.

    The child starts in its OWN process group (``process_group=0``: no ``setsid``, so the
    session and controlling terminal stay as they were), so ``killpg`` reaches every
    descendant. Three exits end in a group kill:

    * timeout: raises :class:`ChildTimeout` (output so far attached) after the kill, when the
      child itself outlived the timeout;
    * normal exit (or a crash): the group is killed afterwards to reap strays
      (``group_killed`` says so). A descendant that keeps the pipes open after the child has
      exited is noticed within :data:`EXIT_POLL_S` and killed (not after the whole timeout), and
      the child's REAL exit status is returned, not a timeout;
    * any exception (a KeyboardInterrupt of the parent): the group is killed, then it is re-raised.
    """
    extra: dict[str, Any] = {"process_group": 0} if os.name == "posix" else {}
    proc = subprocess.Popen(
        list(argv),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        creationflags=creationflags,
        **extra,
    )
    group = proc.pid if os.name == "posix" else None
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(
                    timeout=max(0.01, min(EXIT_POLL_S, deadline - time.monotonic()))
                )
            except subprocess.TimeoutExpired:
                leader_status = proc.poll()  # None: the child itself is still running
                if leader_status is None and time.monotonic() < deadline:
                    continue
                # Either the child outlived its timeout, or it exited and only a descendant
                # still holds the pipes: kill the group and take what was written.
                killed, error = kill_process_group(group, leader=proc)
                stdout, stderr, drain_error = _drain(proc)
                error = error or drain_error
                if leader_status is None:
                    raise ChildTimeout(
                        argv,
                        timeout,
                        output=stdout,
                        stderr=stderr,
                        group_killed=killed,
                        cleanup_error=error,
                    ) from None
                return ChildRun(
                    leader_status, stdout, stderr, group_killed=killed, cleanup_error=error
                )
            break
    except ChildTimeout:
        raise
    except BaseException:
        kill_process_group(group, leader=proc)
        proc.wait()
        raise
    killed, error = kill_process_group(group, leader=proc)
    return ChildRun(proc.returncode, stdout or b"", stderr or b"", killed, error)


def run_experiment(
    experiment: Experiment,
    *,
    script_path: Path,
    python: str | None = None,
    inject: bool = False,
    run: Callable[..., Any] = run_in_process_group,
    clock: Callable[[], float] = time.monotonic,
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run one experiment in a child process and describe what happened."""
    host = sys.platform if platform_name is None else platform_name
    base: dict[str, Any] = {
        "name": experiment.name,
        "title": experiment.title,
        "kind": experiment.kind,
        "question": experiment.question,
        "expected_outcome": experiment.expected_outcome,
        "timeout_s": experiment.timeout_s,
    }
    if experiment.requires_macos and host != "darwin":
        return {
            **base,
            "argv": [],
            "returncode": None,
            "signal": None,
            "outcome": OUTCOME_SKIPPED,
            "skip_reason": f"requires macOS (running on {host})",
            "duration_s": 0.0,
            "stdout_tail": "",
            "stderr_tail": "",
            "last_step": None,
            "observations": None,
            "as_expected": None,
            "group_killed": False,
        }
    argv = [python or sys.executable, str(script_path), "--child", experiment.name]
    if inject:
        argv.append("--inject-synthetic-key")
    env = child_environment(os.environ if environ is None else environ)
    started = clock()
    returncode: int | None = None
    stdout: bytes | str | None = None
    stderr: bytes | str | None = None
    timed_out = False
    spawn_error: str | None = None
    group_killed = False
    cleanup_error: str | None = None
    try:
        done = run(
            argv,
            timeout=experiment.timeout_s,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=_NO_WINDOW,
        )
        returncode, stdout, stderr = done.returncode, done.stdout, done.stderr
        group_killed = bool(getattr(done, "group_killed", False))
        cleanup_error = getattr(done, "cleanup_error", None)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout, stderr = exc.stdout, exc.stderr
        group_killed = bool(getattr(exc, "group_killed", False))
        cleanup_error = getattr(exc, "cleanup_error", None)
    except OSError as exc:
        spawn_error = f"{type(exc).__name__}: {exc}"
    duration = clock() - started
    stdout_text = tail_text(stdout, limit=10_000_000)
    observations = parse_child_result(stdout_text)
    child_flag = observations.get("ok") if isinstance(observations, dict) else None
    outcome = classify_outcome(
        returncode=returncode,
        timed_out=timed_out,
        child_ok=child_flag if isinstance(child_flag, bool) else None,
    )
    row: dict[str, Any] = {
        **base,
        "argv": summarize_argv(argv),
        "returncode": returncode,
        "signal": signal_name(returncode),
        "outcome": outcome,
        "duration_s": round(duration, 3),
        "stdout_tail": tail_text(stdout),
        "stderr_tail": tail_text(stderr),
        "last_step": last_breadcrumb(stdout_text),
        "observations": observations,
        "as_expected": None
        if experiment.expected_outcome is None
        else (outcome == experiment.expected_outcome),
        "group_killed": group_killed,
    }
    if cleanup_error is not None:
        row["cleanup_error"] = cleanup_error
    if spawn_error is not None:
        row["spawn_error"] = spawn_error
    return row


def run_experiments(
    experiments: Sequence[Experiment],
    *,
    script_path: Path,
    inject: bool,
    progress: Callable[[str], None] | None = None,
    on_result: Callable[[list[dict[str, Any]]], None] | None = None,
    run: Callable[..., Any] = run_in_process_group,
) -> list[dict[str, Any]]:
    """Run each experiment in turn; ``on_result`` gets the rows so far after EACH one.

    The caller persists the partial report there, so a parent that is killed (a job timeout,
    a runner hiccup) still leaves every finished experiment on disk.
    """
    results: list[dict[str, Any]] = []
    for index, experiment in enumerate(experiments, start=1):
        row = run_experiment(experiment, script_path=script_path, inject=inject, run=run)
        results.append(row)
        if progress is not None:
            progress(
                f"[{index}/{len(experiments)}] {experiment.name}: {row['outcome']}"
                f" (exit {row['returncode']}, signal {row['signal']}, {row['duration_s']}s)"
            )
        if on_result is not None:
            on_result(results)
    return results


# ---------------------------------------------------------------------------
# Child side (macOS only). Everything below runs inside a child process.
# ---------------------------------------------------------------------------


class _ChildState:
    """The one place the child collects what it observed."""

    def __init__(self) -> None:
        self.name = ""
        self.inject = False
        self.tcc: dict[str, Any] | None = None
        self.steps: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.main_thread_ident: int | None = threading.main_thread().ident
        self.started = time.monotonic()
        #: How many events earlier ``_record_counts`` calls already accounted for.
        self.counted_upto = 0


_STATE = _ChildState()
#: Module-level singletons. The Carbon callback, the EventTypeSpec array and the
#: timer callback must outlive every registration: a per-object attribute dies with
#: its owner while Carbon still holds the pointer (a jump into freed memory).
_SINGLETONS: dict[str, Any] = {}


def reset_child_state() -> None:
    """Forget everything observed (the unit tests call this between cases)."""
    global _STATE
    _STATE = _ChildState()
    _SINGLETONS.clear()


def breadcrumb(message: str) -> None:
    """Print where the child is, flushed at once, so a crash leaves its location."""
    print(f"{STEP_PREFIX}{message}", flush=True)


def record_step(name: str, value: Any = None, *, expect: Any = None, **extra: Any) -> None:
    """Record one observation; with ``expect`` it also records whether it was met."""
    row: dict[str, Any] = {"step": name, "value": value}
    if expect is not None:
        row["expected"] = expect
        row["ok"] = value == expect
    row.update(extra)
    _STATE.steps.append(row)
    breadcrumb(f"{name}={value}")


def _carbon_handler(call_ref: Any, event: Any, user_data: Any) -> int:
    """Record the event. Does nothing else, and never raises into Carbon."""
    try:
        carbon = _SINGLETONS["carbon_lib"]
        kind = carbon.GetEventKind(event)
        hot_key = EventHotKeyID()
        status = carbon.GetEventParameter(
            event,
            K_EVENT_PARAM_DIRECT_OBJECT,
            TYPE_EVENT_HOT_KEY_ID,
            None,
            ctypes.sizeof(hot_key),
            None,
            ctypes.byref(hot_key),
        )
        _STATE.events.append(
            {
                "kind": {
                    K_EVENT_HOT_KEY_PRESSED: "pressed",
                    K_EVENT_HOT_KEY_RELEASED: "released",
                }.get(kind, f"kind_{kind}"),
                "hot_key_id": hot_key.id if status == NO_ERR else None,
                "parameter_status": status,
                "on_main_thread": threading.get_ident() == _STATE.main_thread_ident,
                "t": round(time.monotonic() - _STATE.started, 3),
            }
        )
        return NO_ERR
    except Exception as exc:  # the callback must not raise; the error is recorded
        _STATE.errors.append(f"carbon handler: {type(exc).__name__}: {exc}")
        return EVENT_NOT_HANDLED_ERR


def callback_singleton() -> Any:
    if "callback" not in _SINGLETONS:
        _SINGLETONS["callback"] = HANDLER_PROTO(_carbon_handler)
    return _SINGLETONS["callback"]


def spec_array_singleton() -> Any:
    if "specs" not in _SINGLETONS:
        _SINGLETONS["specs"] = (EventTypeSpec * 2)(
            (K_EVENT_CLASS_KEYBOARD, K_EVENT_HOT_KEY_PRESSED),
            (K_EVENT_CLASS_KEYBOARD, K_EVENT_HOT_KEY_RELEASED),
        )
    return _SINGLETONS["specs"]


def timer_callback_singleton() -> Any:
    if "timer_callback" not in _SINGLETONS:

        def _tick(_timer: Any, _info: Any) -> None:
            loop = _SINGLETONS.get("loop")
            if loop is not None:
                loop.tick()

        _SINGLETONS["timer_callback"] = TIMER_PROTO(_tick)
    return _SINGLETONS["timer_callback"]


class CarbonShim:
    """The thin spike-only wrapper: records OSStatus values, never raises on a status."""

    def __init__(self, lib: Any = None, route: str = "injected") -> None:
        if lib is None:
            lib, route = load_framework(CARBON_FRAMEWORK)
        self.lib = lib
        self.route = route
        self.bound, self.missing, self.aliases = bind_carbon(lib)
        _SINGLETONS["carbon_lib"] = lib

    def target(self) -> int | None:
        """The APPLICATION event target (``c_void_p`` restype: an int or ``None``)."""
        return self.lib.GetApplicationEventTarget()

    def install_handler(self) -> tuple[int, int | None]:
        ref = ctypes.c_void_p()
        specs = spec_array_singleton()
        status = self.lib.InstallEventHandler(
            self.target(), callback_singleton(), len(specs), specs, None, ctypes.byref(ref)
        )
        return status, ref.value

    def remove_handler(self, ref: int) -> int:
        return self.lib.RemoveEventHandler(ctypes.c_void_p(ref))

    def register(self, key_code: int, modifiers: int, hot_key_id: int) -> tuple[int, int | None]:
        ref = ctypes.c_void_p()
        identity = EventHotKeyID(HOT_KEY_SIGNATURE, hot_key_id)
        status = self.lib.RegisterEventHotKey(
            key_code, modifiers, identity, self.target(), 0, ctypes.byref(ref)
        )
        return status, ref.value

    def unregister(self, ref: int) -> int:
        return self.lib.UnregisterEventHotKey(ctypes.c_void_p(ref))


def unregister_if(shim: CarbonShim, ref: int | None, label: str, *, expect: int | None) -> None:
    """Unregister a non-null reference; a NULL reference must never reach Carbon."""
    if ref is None:
        record_step(f"{label}_skipped_null_ref", True)
        return
    record_step(label, shim.unregister(ref), expect=expect)


class Injector:
    """Send synthetic Control+Option+J through ``osascript`` from a helper thread.

    ``repeat`` is the number of presses ONE ``osascript`` run sends (a single process, so the
    cost of starting it is paid once).
    """

    TIMEOUT_S = 30.0
    #: Seconds between two presses of a repeated injection.
    REPEAT_GAP_S = 0.1

    def __init__(
        self,
        *,
        delay_s: float = 0.5,
        repeat: int = 1,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        self.delay_s = delay_s
        self.repeat = max(1, repeat)
        #: A repeated injection needs proportionally longer than one press.
        self.timeout_s = self.TIMEOUT_S + self.repeat * (self.REPEAT_GAP_S + 0.5)
        self._run = run
        self.done = threading.Event()
        self.result: dict[str, Any] = {}
        self._thread = threading.Thread(target=self._work, name="spike-injector", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def script(self) -> str:
        press = f"key code {TEST_KEY_CODE} using {{control down, option down}}"
        if self.repeat == 1:
            return f'tell application "System Events" to {press}'
        return (
            'tell application "System Events"\n'
            f"    repeat {self.repeat} times\n"
            f"        {press}\n"
            f"        delay {self.REPEAT_GAP_S}\n"
            "    end repeat\n"
            "end tell"
        )

    def argv(self) -> list[str]:
        return ["osascript", "-e", self.script()]

    def _work(self) -> None:
        try:
            time.sleep(self.delay_s)
            started = time.monotonic()
            done = self._run(
                self.argv(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
                creationflags=_NO_WINDOW,
            )
            self.result = {
                "returncode": done.returncode,
                "stdout": tail_text(done.stdout, 300),
                "stderr": tail_text(done.stderr, 600),
                "duration_s": round(time.monotonic() - started, 3),
            }
        except Exception as exc:  # recorded as the injection result
            self.result = {"returncode": None, "error": f"{type(exc).__name__}: {exc}"}
        finally:
            self.done.set()


@dataclass(frozen=True)
class OptionHoldPhase:
    """One way of holding an Option key down while both Appshot APIs are sampled."""

    name: str
    #: "applescript" (System Events) or "javascript" (JXA, posts a CGEvent for ``key_code``).
    language: str
    key_code: int | None
    description: str


#: Three routes, because none of them is known to work: System Events ``key down option`` (the
#: red-team's literal form; it cannot choose a side, the side is READ from the bits that light
#: up), and a CGEvent key-down for kVK_Option (58) and kVK_RightOption (61) posted from inside
#: ``osascript`` (JXA), which can choose. A route that fails is a recorded exit status, not an
#: error of the experiment.
OPTION_HOLD_PHASES = (
    OptionHoldPhase("system_events_option", "applescript", None, "System Events `key down option`"),
    OptionHoldPhase(
        "cgevent_left_option", "javascript", VK_OPTION, "CGEventPost key down, key code 58"
    ),
    OptionHoldPhase(
        "cgevent_right_option", "javascript", VK_RIGHT_OPTION, "CGEventPost key down, key code 61"
    ),
)
#: How long the key stays down, and how often both APIs are sampled meanwhile.
OPTION_HOLD_S = 2.5
OPTION_SAMPLE_INTERVAL_S = 0.05


def option_hold_script(phase: OptionHoldPhase, hold_s: float = OPTION_HOLD_S) -> str:
    """The ``osascript`` source that holds the key for ``hold_s`` seconds and always releases it."""
    if phase.language == "applescript":
        return (
            'tell application "System Events"\n'
            "    try\n"
            "        key down option\n"
            f"        delay {hold_s}\n"
            "        key up option\n"
            "    on error errorMessage number errorNumber\n"
            "        try\n"
            "            key up option\n"
            "        end try\n"
            "        error errorMessage number errorNumber\n"
            "    end try\n"
            "end tell"
        )
    if phase.language == "javascript" and phase.key_code is not None:
        return (
            "ObjC.import('ApplicationServices');\n"
            "ObjC.import('Foundation');\n"
            "function post(down) {\n"
            f"  var event = $.CGEventCreateKeyboardEvent(null, {phase.key_code}, down);\n"
            "  $.CGEventPost($.kCGHIDEventTap, event);\n"
            "}\n"
            "post(true);\n"
            "try {\n"
            f"  $.NSThread.sleepForTimeInterval({hold_s});\n"
            "} finally {\n"
            "  post(false);\n"
            "}\n"
        )
    raise ValueError(f"unsupported hold phase: {phase!r}")


class OptionHolder(Injector):
    """Hold an Option key down through ``osascript`` for :data:`OPTION_HOLD_S` seconds."""

    def __init__(
        self,
        phase: OptionHoldPhase,
        *,
        hold_s: float = OPTION_HOLD_S,
        delay_s: float = 0.0,
        run: Callable[..., Any] = subprocess.run,
    ) -> None:
        super().__init__(delay_s=delay_s, run=run)
        self.phase = phase
        self.hold_s = hold_s

    def script(self) -> str:
        return option_hold_script(self.phase, self.hold_s)

    def argv(self) -> list[str]:
        language = ["-l", "JavaScript"] if self.phase.language == "javascript" else []
        return ["osascript", *language, "-e", self.script()]


def hold_and_sample(
    phase: OptionHoldPhase,
    read: Callable[[], Mapping[str, Mapping[str, Any]]],
    *,
    holder_factory: Callable[[OptionHoldPhase], Injector] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Hold the key and sample ``read`` until the holder finishes; return the peak per state.

    One more read happens after the holder is done, so a key still reported down AFTER the
    release shows up in ``reads_after_release`` (a stuck key would void later experiments).
    """
    holder = (holder_factory or OptionHolder)(phase)
    holder.start()
    samples: list[Mapping[str, Mapping[str, Any]]] = []
    deadline = clock() + holder.timeout_s + 5.0
    while not holder.done.is_set() and clock() < deadline:
        samples.append(read())
        sleep(OPTION_SAMPLE_INTERVAL_S)
    after_release = read()
    return {
        "reads": merge_option_samples(samples or [after_release]),
        "reads_after_release": merge_option_samples([after_release]),
        "samples": len(samples),
        "injection_exit": holder.result.get("returncode"),
        "injection": dict(holder.result),
        "finished": holder.done.is_set(),
    }


@dataclass(frozen=True)
class Wait:
    """What a scenario coroutine yields: block (loop keeps running) until ``predicate``."""

    predicate: Callable[[], bool]
    timeout_s: float
    settle_s: float = 0.0


Scenario = Generator[Wait, None, dict[str, Any] | None]


class MainLoop:
    """Runs a scenario coroutine from timer ticks on the main thread, inside the loop.

    The scenario must run INSIDE ``[NSApp run]`` (so hot key events can be
    delivered while it waits) and ON the main thread (Carbon's rule), which is
    why it is a coroutine driven by a repeating run-loop timer instead of a thread.
    """

    def __init__(
        self,
        finish: Callable[[dict[str, Any], int], None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._finish = finish
        self._clock = clock
        self._jobs: list[tuple[float, Callable[[], None]]] = []

    def call_later(self, delay_s: float, function: Callable[[], None]) -> None:
        self._jobs.append((self._clock() + delay_s, function))

    def tick(self) -> None:
        now = self._clock()
        due = sorted((job for job in self._jobs if job[0] <= now), key=lambda job: job[0])
        self._jobs = [job for job in self._jobs if job[0] > now]
        for _when, function in due:
            try:
                function()
            except Exception as exc:  # a harness bug is the child's result, then it ends
                _STATE.errors.append(f"{type(exc).__name__}: {exc}")
                _STATE.errors.append(traceback.format_exc()[-1200:])
                self._finish({"ok": False}, 1)
                return

    def wait_for(
        self,
        predicate: Callable[[], bool],
        then: Callable[[], None],
        *,
        timeout_s: float,
        settle_s: float,
    ) -> None:
        deadline = self._clock() + timeout_s

        def poll() -> None:
            if predicate() or self._clock() >= deadline:
                self.call_later(settle_s, then)
            else:
                self.call_later(0.1, poll)

        self.call_later(0.0, poll)

    def drive(self, scenario: Scenario) -> None:
        def advance() -> None:
            try:
                request = next(scenario)
            except StopIteration as stop:
                self._finish(stop.value or {}, 0)
                return
            self.wait_for(
                request.predicate, advance, timeout_s=request.timeout_s, settle_s=request.settle_s
            )

        self.call_later(0.0, advance)


def emit_result(extra: Mapping[str, Any], exit_code: int) -> int:
    """Print the ``SPIKE_RESULT`` line; return the exit code for the caller."""
    result = assemble_child_result(
        _STATE.name,
        extra,
        steps=_STATE.steps,
        events=_STATE.events,
        errors=_STATE.errors,
        tcc=_STATE.tcc,
        inject=_STATE.inject,
    )
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True), flush=True)
    return exit_code


def finish_child(extra: dict[str, Any], exit_code: int) -> NoReturn:
    """End the child from inside the run loop (it never returns to Python's main)."""
    code = emit_result(extra, exit_code)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


class ObjC:
    """Just enough of the Objective-C runtime to start and run ``NSApplication``."""

    def __init__(self) -> None:
        self.lib = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self.lib.objc_getClass.restype = ctypes.c_void_p
        self.lib.objc_getClass.argtypes = [ctypes.c_char_p]
        self.lib.sel_registerName.restype = ctypes.c_void_p
        self.lib.sel_registerName.argtypes = [ctypes.c_char_p]
        self.appkit, _route = load_framework("AppKit")

    def cls(self, name: bytes) -> int:
        return self.lib.objc_getClass(name)

    def send(
        self,
        receiver: int,
        selector: bytes,
        *,
        restype: Any = ctypes.c_void_p,
        argtypes: Sequence[Any] = (),
        args: Sequence[Any] = (),
    ) -> Any:
        prototype = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)
        function = prototype(("objc_msgSend", self.lib))
        return function(receiver, self.lib.sel_registerName(selector), *args)


class CoreFoundation:
    """The run-loop functions the spike needs."""

    def __init__(self) -> None:
        self.lib, _route = load_framework("CoreFoundation")
        lib = self.lib
        lib.CFRunLoopGetMain.restype = ctypes.c_void_p
        lib.CFRunLoopGetMain.argtypes = []
        lib.CFAbsoluteTimeGetCurrent.restype = ctypes.c_double
        lib.CFAbsoluteTimeGetCurrent.argtypes = []
        lib.CFRunLoopTimerCreate.restype = ctypes.c_void_p
        lib.CFRunLoopTimerCreate.argtypes = [
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_double,
            ctypes.c_ulong,
            ctypes.c_long,
            TIMER_PROTO,
            ctypes.c_void_p,
        ]
        lib.CFRunLoopAddTimer.restype = None
        lib.CFRunLoopAddTimer.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        lib.CFRunLoopRunInMode.restype = ctypes.c_int32
        lib.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_ubyte]
        self.common_modes = ctypes.c_void_p.in_dll(lib, "kCFRunLoopCommonModes").value
        self.default_mode = ctypes.c_void_p.in_dll(lib, "kCFRunLoopDefaultMode").value

    def add_main_timer(self, callback: Any, interval_s: float) -> None:
        timer = self.lib.CFRunLoopTimerCreate(
            None,
            self.lib.CFAbsoluteTimeGetCurrent() + interval_s,
            interval_s,
            0,
            0,
            callback,
            None,
        )
        self.lib.CFRunLoopAddTimer(self.lib.CFRunLoopGetMain(), timer, self.common_modes)

    def pump(self, seconds: float) -> int:
        return self.lib.CFRunLoopRunInMode(self.default_mode, seconds, 0)


def run_in_nsapp_loop(make_scenario: Callable[[], Scenario]) -> NoReturn:
    """Create ``NSApplication`` (accessory policy), run it on the main thread, and
    drive the scenario from inside it. The process ends through :func:`finish_child`."""
    objc = ObjC()
    core_foundation = CoreFoundation()
    application = objc.send(objc.cls(b"NSApplication"), b"sharedApplication")
    record_step("nsapp_shared_application_created", bool(application))
    objc.send(
        application,
        b"setActivationPolicy:",
        restype=ctypes.c_byte,
        argtypes=(ctypes.c_long,),
        args=(NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY,),
    )
    loop = MainLoop(finish_child)
    _SINGLETONS["loop"] = loop
    core_foundation.add_main_timer(timer_callback_singleton(), 0.05)
    loop.call_later(0.3, lambda: loop.drive(make_scenario()))
    breadcrumb("nsapp_run:begin")
    objc.send(application, b"run", restype=None)
    _STATE.errors.append("NSApplication run returned before the scenario finished")
    finish_child({"ok": False}, 1)


def read_keydown_counters(
    bind: Callable[..., Any] = _bind_symbol,
) -> dict[str, int] | None:
    """The system-wide key-down counters (``CGEventSourceCounterForEventType``), or ``None``.

    This is the positive control for a synthetic key: it does not depend on Carbon, creates no
    event tap and asks for no permission, so a counter that moved shows the key reached the
    event system whether or not Carbon delivered it. That the call is allowed without a grant
    is itself an assumption (unverified); an unreadable counter makes the control
    ``inconclusive``, never ``seen``.
    """
    try:
        counter = bind(
            ("CoreGraphics", "ApplicationServices"),
            "CGEventSourceCounterForEventType",
            ctypes.c_uint32,
            (ctypes.c_int32, ctypes.c_uint32),
        )
        return {
            label: int(counter(state, K_CG_EVENT_KEY_DOWN)) for label, state in OPTION_SOURCE_STATES
        }
    except Exception as exc:  # the control reads 'unreadable' and the verdict says inconclusive
        record_step("keydown_counter_unreadable", f"{type(exc).__name__}: {exc}"[:200])
        return None


def _inject_and_settle(
    label: str, injector_factory: Callable[[], Injector] | None = None
) -> Generator[Wait, None, None]:
    """Send synthetic chord(s) (only with ``--inject-synthetic-key``) and let them arrive.

    Records ``injection_seen_<label>``: whether the key-down counter moved while the chord was
    sent (the control that separates 'Carbon did not deliver' from 'nothing was injected').
    """
    if not _STATE.inject:
        record_step(f"inject_{label}", "not_requested")
        return
    before = read_keydown_counters()
    injector = (injector_factory or Injector)()
    injector.start()
    yield Wait(injector.done.is_set, timeout_s=injector.timeout_s + 10.0, settle_s=1.0)
    record_step(
        f"inject_{label}_osascript_exit",
        injector.result.get("returncode"),
        detail=injector.result,
        finished=injector.done.is_set(),
    )
    after = read_keydown_counters()
    record_step(
        f"injection_seen_{label}",
        injection_seen(before, after),
        counters_before=before,
        counters_after=after,
    )


def _record_counts(label: str) -> dict[str, int]:
    """Record the Pressed/Released events that arrived SINCE the previous call.

    A window, not a running total: ``pressed_after_unregister`` must mean 'arrived after the
    unregister', otherwise a press delivered while registered would be counted again there.
    """
    window = _STATE.events[_STATE.counted_upto :]
    _STATE.counted_upto = len(_STATE.events)
    counts = count_events(window)
    record_step(f"pressed_{label}", counts["pressed"])
    record_step(f"released_{label}", counts["released"])
    return counts


# --- scenarios -----------------------------------------------------------------


def _scenario_control_signal_capture() -> dict[str, Any] | None:
    try:
        import resource  # POSIX only; a core file would be noise

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError) as exc:
        record_step("core_limit_not_lowered", f"{type(exc).__name__}: {exc}")
    breadcrumb("about_to_abort")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGABRT)
    time.sleep(5)
    return {"ok": False, "error": "the process survived its own SIGABRT"}


def _scenario_carbon_symbols() -> dict[str, Any]:
    shim = CarbonShim()
    record_step("missing_symbols", len(shim.missing), expect=0)
    return {
        "carbon_route": shim.route,
        "bound": shim.bound,
        "missing": shim.missing,
        # Canonical name -> the alias that bound, e.g. InstallEventHandler -> _InstallEventHandler.
        "aliases": shim.aliases,
        "struct_sizes": {
            "EventHotKeyID": ctypes.sizeof(EventHotKeyID),
            "EventTypeSpec": ctypes.sizeof(EventTypeSpec),
        },
    }


def _co_nsapp_register_unregister(shim: CarbonShim) -> Scenario:
    status, _handler = shim.install_handler()
    record_step("install_handler", status, expect=NO_ERR)
    status, hot_key = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1)
    record_step("register", status, expect=NO_ERR)
    yield from _inject_and_settle("while_registered")
    _record_counts("while_registered")
    unregister_if(shim, hot_key, "unregister", expect=NO_ERR)
    yield from _inject_and_settle("after_unregister")
    _record_counts("after_unregister")
    status, again = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 2)
    record_step("reregister_after_unregister", status, expect=NO_ERR)
    unregister_if(shim, again, "unregister_second", expect=NO_ERR)
    return {"carbon_route": shim.route}


def _scenario_nsapp_register_unregister() -> NoReturn:
    shim = CarbonShim()
    run_in_nsapp_loop(lambda: _co_nsapp_register_unregister(shim))


#: Variant A's stress numbers from the red-team list.
REGISTER_CYCLES = 200
PRESS_CYCLES = 50
#: At most this many failing cycles are described in the report (all are counted).
CYCLE_FAILURE_DETAILS = 5


def _co_register_unregister_cycles(shim: CarbonShim) -> Scenario:
    status, _handler = shim.install_handler()
    record_step("install_handler", status, expect=NO_ERR)
    failures: list[dict[str, Any]] = []
    for cycle in range(REGISTER_CYCLES):
        status, ref = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1000 + cycle)
        if status != NO_ERR or ref is None:
            failures.append({"cycle": cycle, "step": "register", "status": status})
            continue
        status = shim.unregister(ref)
        if status != NO_ERR:
            failures.append({"cycle": cycle, "step": "unregister", "status": status})
    record_step("register_unregister_cycles", REGISTER_CYCLES)
    record_step("cycle_failures", len(failures), expect=0, details=failures[:CYCLE_FAILURE_DETAILS])
    status, hot_key = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 2000)
    record_step("register_for_presses", status, expect=NO_ERR)
    record_step("press_cycles_requested", PRESS_CYCLES if _STATE.inject else 0)
    yield from _inject_and_settle("press_cycles", lambda: Injector(repeat=PRESS_CYCLES))
    counts = _record_counts("press_cycles")
    record_step(
        "every_press_arrived",
        counts["pressed"] == PRESS_CYCLES if _STATE.inject else None,
    )
    unregister_if(shim, hot_key, "unregister_after_presses", expect=NO_ERR)
    status, again = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 2001)
    record_step("reregister_after_cycles", status, expect=NO_ERR)
    unregister_if(shim, again, "unregister_last", expect=NO_ERR)
    return {"carbon_route": shim.route}


def _scenario_register_unregister_cycles() -> NoReturn:
    shim = CarbonShim()
    run_in_nsapp_loop(lambda: _co_register_unregister_cycles(shim))


def _scenario_no_nsapp_register() -> dict[str, Any]:
    shim = CarbonShim()
    core_foundation = CoreFoundation()
    record_step("nsapp_created", False)
    status, handler = shim.install_handler()
    record_step("install_handler", status)
    status, hot_key = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1)
    record_step("register", status)
    record_step("run_loop_pump_result", core_foundation.pump(0.5))
    unregister_if(shim, hot_key, "unregister", expect=None)
    if handler is not None:
        record_step("remove_handler", shim.remove_handler(handler))
    return {"carbon_route": shim.route}


def _co_unregister_off_main(shim: CarbonShim) -> Scenario:
    status, _handler = shim.install_handler()
    record_step("install_handler", status, expect=NO_ERR)
    status, hot_key = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1)
    record_step("register", status, expect=NO_ERR)
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["status"] = shim.unregister(hot_key) if hot_key is not None else None
        except Exception as exc:  # recorded as the worker's result
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=worker, name="spike-offmain-unregister")
    thread.start()
    thread.join(10.0)
    record_step(
        "unregister_off_main",
        outcome.get("status"),
        returned=not thread.is_alive(),
        error=outcome.get("error"),
    )
    status, again = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 2)
    record_step("reregister_after_off_main_unregister", status)
    yield from _inject_and_settle("after_off_main_unregister")
    _record_counts("after_off_main_unregister")
    unregister_if(shim, again, "unregister_second", expect=None)
    return {"carbon_route": shim.route}


def _scenario_unregister_off_main_thread() -> NoReturn:
    shim = CarbonShim()
    run_in_nsapp_loop(lambda: _co_unregister_off_main(shim))


def _co_duplicate_register(shim: CarbonShim) -> Scenario:
    status, _handler = shim.install_handler()
    record_step("install_handler", status, expect=NO_ERR)
    status, first = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1)
    record_step("register_first", status, expect=NO_ERR)
    status, second = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 2)
    record_step("register_second", status)
    record_step("second_returned_hotkey_exists_err", status == EVENT_HOT_KEY_EXISTS_ERR)
    yield from _inject_and_settle("with_duplicate")
    _record_counts("with_duplicate")
    unregister_if(shim, first, "unregister_first", expect=NO_ERR)
    unregister_if(shim, second, "unregister_second", expect=None)
    return {"carbon_route": shim.route}


def _scenario_duplicate_register() -> NoReturn:
    shim = CarbonShim()
    run_in_nsapp_loop(lambda: _co_duplicate_register(shim))


def _co_secure_input(shim: CarbonShim) -> Scenario:
    lib = shim.lib
    record_step("secure_input_before", lib.IsSecureEventInputEnabled(), expect=0)
    record_step("enable_secure_input_raw_status", lib.EnableSecureEventInput())
    try:
        record_step("secure_input_after_enable", lib.IsSecureEventInputEnabled(), expect=1)
        status, _handler = shim.install_handler()
        record_step("install_handler", status, expect=NO_ERR)
        status, hot_key = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1)
        record_step("register_under_secure_input", status, expect=NO_ERR)
        yield from _inject_and_settle("under_secure_input")
        _record_counts("under_secure_input")
        unregister_if(shim, hot_key, "unregister", expect=NO_ERR)
    finally:
        record_step("disable_secure_input_raw_status", lib.DisableSecureEventInput())
        record_step("secure_input_after_disable", lib.IsSecureEventInputEnabled(), expect=0)
    return {"carbon_route": shim.route}


def _scenario_register_under_secure_input() -> NoReturn:
    shim = CarbonShim()
    run_in_nsapp_loop(lambda: _co_secure_input(shim))


def _co_install_handler_twice(shim: CarbonShim) -> Scenario:
    first_status, first_ref = shim.install_handler()
    record_step("install_handler_first", first_status, expect=NO_ERR)
    second_status, second_ref = shim.install_handler()
    record_step("install_handler_second", second_status)
    record_step(
        "handler_refs_distinct",
        first_ref is not None and second_ref is not None and first_ref != second_ref,
    )
    status, hot_key = shim.register(TEST_KEY_CODE, TEST_MODIFIERS, 1)
    record_step("register", status, expect=NO_ERR)
    yield from _inject_and_settle("two_handlers")
    _record_counts("two_handlers")
    unregister_if(shim, hot_key, "unregister", expect=NO_ERR)
    for label, ref in (("remove_handler_first", first_ref), ("remove_handler_second", second_ref)):
        if ref is None:
            record_step(f"{label}_skipped_null_ref", True)
        else:
            record_step(label, shim.remove_handler(ref))
    return {"carbon_route": shim.route}


def _scenario_install_handler_twice() -> NoReturn:
    shim = CarbonShim()
    run_in_nsapp_loop(lambda: _co_install_handler_twice(shim))


def _scenario_appshot_option_state_read() -> dict[str, Any]:
    key_state = _bind_symbol(
        ("CoreGraphics", "ApplicationServices"),
        "CGEventSourceKeyState",
        ctypes.c_ubyte,
        (ctypes.c_int32, ctypes.c_uint16),
    )
    flags_state = _bind_symbol(
        ("CoreGraphics", "ApplicationServices"),
        "CGEventSourceFlagsState",
        ctypes.c_uint64,
        (ctypes.c_int32,),
    )

    def read() -> dict[str, dict[str, Any]]:
        return {
            label: {
                "key_left": bool(key_state(state, VK_OPTION)),
                "key_right": bool(key_state(state, VK_RIGHT_OPTION)),
                "flags_raw": int(flags_state(state)),
            }
            for label, state in OPTION_SOURCE_STATES
        }

    baseline = read()
    for label, state_read in baseline.items():
        record_step(f"{label}_keystate_left", state_read["key_left"])
        record_step(f"{label}_keystate_right", state_read["key_right"])
        record_step(
            f"{label}_flags_alternate",
            bool(state_read["flags_raw"] & K_CG_EVENT_FLAG_MASK_ALTERNATE),
        )
    phases: dict[str, dict[str, Any]] = {}
    if _STATE.inject:
        for phase in OPTION_HOLD_PHASES:
            phases[phase.name] = hold_and_sample(phase, read)
            record_step(
                f"{phase.name}_osascript_exit",
                phases[phase.name]["injection_exit"],
                detail=phases[phase.name]["injection"],
            )
            record_step(
                f"{phase.name}_stuck_after_release",
                any(
                    state_read["key_left"] or state_read["key_right"]
                    for state_read in phases[phase.name]["reads_after_release"].values()
                ),
            )
    else:
        record_step("hold_phases", "not_requested")
    verdict = option_hold_verdict(baseline, phases)
    for name, phase_verdict in verdict["phases"].items():
        record_step(f"{name}_any_api_saw_option", phase_verdict["any_api_saw_option"])
    return {
        "reads": baseline,
        "comparison": compare_option_reads(baseline),
        "phases": phases,
        "verdict": verdict,
    }


def _scenario_tcc_context() -> dict[str, Any]:
    """Nothing to do: ``run_child`` records the TCC context for this scenario, and only that."""
    return {}


#: name -> (function, collect the TCC context BEFORE the scenario). The no-NSApplication
#: variant reads TCC afterwards so the probes cannot stand in for an application.
_SCENARIOS: dict[str, tuple[Callable[[], dict[str, Any] | None], bool]] = {
    "control_signal_capture": (_scenario_control_signal_capture, False),
    "tcc_context": (_scenario_tcc_context, True),
    "carbon_symbols": (_scenario_carbon_symbols, True),
    "nsapp_register_unregister": (_scenario_nsapp_register_unregister, True),
    "register_unregister_cycles": (_scenario_register_unregister_cycles, True),
    "no_nsapp_register": (_scenario_no_nsapp_register, False),
    "unregister_off_main_thread": (_scenario_unregister_off_main_thread, True),
    "duplicate_register": (_scenario_duplicate_register, True),
    "register_under_secure_input": (_scenario_register_under_secure_input, True),
    "install_handler_twice": (_scenario_install_handler_twice, True),
    "appshot_option_state_read": (_scenario_appshot_option_state_read, True),
}


def run_child(name: str, *, inject: bool) -> int:
    """Entry point of ``--child NAME``: run one scenario, print its result line."""
    _STATE.name = name
    _STATE.inject = inject
    entry = _SCENARIOS.get(name)
    if entry is None:
        return emit_result({"ok": False, "error": f"unknown experiment {name!r}"}, 2)
    if experiment_by_name(name).requires_macos and sys.platform != "darwin":
        return emit_result({"ok": False, "error": f"requires macOS (running on {sys.platform})"}, 1)
    faulthandler.enable()
    function, tcc_first = entry
    try:
        if tcc_first:
            _STATE.tcc = collect_tcc_context()
        extra = function()
    except Exception as exc:  # the failure is the child's result, with a traceback
        _STATE.errors.append(f"{type(exc).__name__}: {exc}")
        _STATE.errors.append(traceback.format_exc()[-1500:])
        return emit_result({"ok": False}, 1)
    if not tcc_first:
        _STATE.tcc = collect_tcc_context()
    return emit_result(extra or {}, 0)


# ---------------------------------------------------------------------------
# Command line.
# ---------------------------------------------------------------------------


def _write_text(path: Path, text: str) -> None:
    """Write atomically (temp file + replace): a parent killed mid-write leaves the old report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="Write the JSON report here.")
    parser.add_argument("--summary-md", type=Path, help="Also write the summary as Markdown here.")
    parser.add_argument("--only", default="", help="Comma-separated experiment names to run.")
    parser.add_argument(
        "--inject-synthetic-key",
        action="store_true",
        help="Send Control+Option+J through osascript (needs System Events automation).",
    )
    parser.add_argument("--child", metavar="NAME", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.child:
        return run_child(args.child, inject=args.inject_synthetic_key)
    if args.out is None:
        parser.error("--out is required")
    try:
        planned = plan_experiments(args.only)
    except ValueError as exc:
        parser.error(str(exc))

    started_at = _utc_now()
    runner = collect_runner_facts()  # pure Python + sw_vers: the parent loads no framework

    def persist(rows: list[dict[str, Any]], *, complete: bool) -> dict[str, Any]:
        report = build_report(
            runner=runner,
            tcc=tcc_from_experiments(rows),
            experiments=rows,
            started_at=started_at,
            finished_at=_utc_now(),
            options={"inject_synthetic_key": args.inject_synthetic_key, "only": args.only or None},
            complete=complete,
        )
        _write_text(args.out, json.dumps(report, indent=2) + "\n")
        if args.summary_md is not None:
            _write_text(args.summary_md, render_summary_markdown(report))
        return report

    # An empty first write: even a parent killed before the first experiment ends leaves a file.
    persist([], complete=False)
    experiments = run_experiments(
        planned,
        script_path=Path(__file__).resolve(),
        inject=args.inject_synthetic_key,
        progress=lambda line: print(line, flush=True),
        on_result=lambda rows: persist(rows, complete=False),
    )
    report = persist(experiments, complete=True)
    print()
    print(render_summary_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
