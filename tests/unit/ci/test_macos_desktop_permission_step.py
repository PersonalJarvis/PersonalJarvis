"""The macOS lane's permission steps, run against FakeTCC.

``.github/workflows/macos-desktop.yml`` carries small scripts that assert a bare
Python process (which is not the installed app) is never allowed to start a native
permission request of its own, that the native symbols the port binds resolve, and
that the service never asks without a gesture. The scripts only really run on a
macOS runner, so this test extracts them from the workflow and runs the very same
text against the stateful FakeTCC: a step cannot rot into something that always
passes, every state a runner can report is proven to pass, and each assertion is
proven to bite before a runner ever sees it.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest
import yaml

from jarvis.platform.permission_service import PermissionService
from tests.fakes.fake_tcc import TccService, install_port, make_darwin_port

_WORKFLOW = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "macos-desktop.yml"
_STEP_NAME = "Verify headless permission asks fail closed"
_SYMBOL_STEP = "Probe the native permission symbols"
_SMOKE_STEP = "Smoke the permission service without asking"


def _step_script(name: str = _STEP_NAME) -> str:
    document = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = document["jobs"]["desktop"]["steps"]
    run = next(step["run"] for step in steps if step.get("name") == name)
    match = re.search(r"python - <<'PY'\n(?P<code>.*?)\nPY\s*$", run, flags=re.DOTALL)
    assert match is not None, "the step must be a quoted-heredoc python script"
    return match.group("code")


def _run_step(name: str = _STEP_NAME) -> None:
    exec(compile(_step_script(name), str(_WORKFLOW), "exec"), {"__name__": "__ci_step__"})  # noqa: S102


def test_the_step_replaces_the_deleted_port_request_and_snapshot_calls() -> None:
    text = _step_script() + _WORKFLOW.read_text(encoding="utf-8")

    assert "ensure(PermissionId.MICROPHONE, feature=" in text
    assert "SystemPermissionPort()" not in text
    assert ".snapshot(" not in text
    assert "p.request(" not in text


def test_a_bare_python_is_refused_and_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    port, tcc = make_darwin_port(bundle_id="org.python.python")
    install_port(monkeypatch, port)

    _run_step()

    assert tcc.requests() == []
    tcc.assert_no_prompts()


def test_a_runner_that_already_holds_the_grant_is_read_as_it_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_darwin_port(bundle_id="org.python.python", granted=[TccService.MICROPHONE])
    install_port(monkeypatch, port)

    _run_step()

    assert tcc.requests() == []


@pytest.mark.parametrize(
    "state",
    ["not_determined", "granted", "denied", "restricted"],
)
@pytest.mark.parametrize("headless", [False, True])
def test_every_state_a_runner_can_report_passes_the_step(
    monkeypatch: pytest.MonkeyPatch, state: str, headless: bool
) -> None:
    """A denied or restricted microphone, or no desktop session, is not a failure."""
    port, tcc = make_darwin_port(bundle_id="org.python.python", headless=headless)
    if state == "granted":
        tcc.grant(TccService.MICROPHONE)
    elif state == "denied":
        tcc.deny(TccService.MICROPHONE)
    elif state == "restricted":
        tcc.restrict(TccService.MICROPHONE)
    install_port(monkeypatch, port)

    _run_step()

    assert tcc.requests() == []
    tcc.assert_no_prompts()


def test_the_step_replaces_the_native_request_seams_with_tripwires() -> None:
    script = _step_script()

    for seam in ("port.request_native", "port._iohid_request", "port._automation_consent_runner"):
        assert f"{seam} = forbidden" in script


def test_the_step_bites_when_the_installed_app_could_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run as the installed app the step's own precondition fails: it is not a no-op."""
    port, _tcc = make_darwin_port()
    install_port(monkeypatch, port)

    with pytest.raises(AssertionError):
        _run_step()


def test_the_step_bites_when_the_service_asks_outside_the_installed_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The precondition holds (a bare Python), yet the service asks anyway: the step must fail.

    The ask is forced through ``allow_outside_app``, the one switch that lifts the
    refusal. The failure must come from the tripwire or the ``asked`` assertion, not
    from the precondition.
    """
    port, tcc = make_darwin_port(bundle_id="org.python.python")
    install_port(monkeypatch, port)
    assert port.outside_installed_app is True
    real_ensure = PermissionService.ensure

    def asking_ensure(self, permission, **kwargs):
        return real_ensure(self, permission, **{**kwargs, "allow_outside_app": True})

    monkeypatch.setattr(PermissionService, "ensure", asking_ensure)

    with pytest.raises(AssertionError, match="native"):
        _run_step()

    # The tripwire sat in front of the fake: nothing reached it.
    assert tcc.requests() == []


# --- Native symbol binding


class _Framework:
    """A framework stand-in that has every symbol except the ones listed."""

    def __init__(self, missing: frozenset[str] = frozenset()) -> None:
        self._missing = missing

    def __getattr__(self, name: str) -> object:
        if name in self._missing:
            raise AttributeError(name)
        return object()


def test_the_symbol_probe_passes_when_every_required_symbol_binds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda _name: _Framework())

    _run_step(_SYMBOL_STEP)


def test_the_symbol_probe_names_a_missing_required_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: _Framework(frozenset({"CGPreflightListenEventAccess"})),
    )

    with pytest.raises(AssertionError, match="CGPreflightListenEventAccess"):
        _run_step(_SYMBOL_STEP)


def test_the_symbol_probe_only_reports_a_missing_optional_symbol(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: _Framework(frozenset({"CGPreflightPostEventAccess"})),
    )

    _run_step(_SYMBOL_STEP)

    assert "optional symbol absent: Quartz.CGPreflightPostEventAccess" in capsys.readouterr().out


# --- Service smoke without a gesture


@pytest.mark.parametrize("bundle_id", ["org.python.python", None])
def test_the_service_smoke_reads_every_permission_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch, bundle_id: str | None
) -> None:
    kwargs = {"bundle_id": bundle_id} if bundle_id else {}
    port, tcc = make_darwin_port(**kwargs)
    install_port(monkeypatch, port)

    _run_step(_SMOKE_STEP)

    assert tcc.requests() == []
    tcc.assert_no_prompts()


def test_the_service_smoke_bites_when_a_background_ensure_asks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, _tcc = make_darwin_port()
    install_port(monkeypatch, port)
    real_ensure = PermissionService.ensure

    def asking_ensure(self, permission, **kwargs):
        return real_ensure(self, permission, **{**kwargs, "interactive": True})

    monkeypatch.setattr(PermissionService, "ensure", asking_ensure)

    with pytest.raises(AssertionError):
        _run_step(_SMOKE_STEP)


def test_the_service_smoke_bites_on_a_state_that_is_not_a_permission_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, _tcc = make_darwin_port()
    install_port(monkeypatch, port)
    monkeypatch.setattr(PermissionService, "check", lambda self, permission, **_kw: "granted")

    with pytest.raises(AssertionError):
        _run_step(_SMOKE_STEP)


def test_the_service_smoke_bites_on_a_hung_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    port, _tcc = make_darwin_port()
    install_port(monkeypatch, port)
    release = threading.Event()
    port._automation_probe = lambda _bundle, _ask: release.wait(60)
    # A short deadline so the failing case does not wait the real 30 seconds.
    real_join = threading.Thread.join
    monkeypatch.setattr(threading.Thread, "join", lambda self, timeout=None: real_join(self, 0.2))
    try:
        with pytest.raises(AssertionError, match="did not return"):
            _run_step(_SMOKE_STEP)
    finally:
        release.set()
