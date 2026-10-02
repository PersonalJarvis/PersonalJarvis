"""The desktop voice gates are SILENT reads of the permission service.

Two predicates reach the speech pipeline: ``_local_voice_permission_granted``
(background voice: the wake word needs a live GRANT) and
``_local_voice_permission_usable`` (a user gesture: push-to-talk, a voice session,
dictation only need the microphone not to be refused, because the gesture asks).
Neither ever asks the OS: with the real service over ``FakeTCC`` the call log
holds probes only.
"""

from __future__ import annotations

import pytest

from jarvis.platform.permission_service import PermissionOutcome, get_permission_service
from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.ui.desktop_app import (
    _local_voice_permission_granted,
    _local_voice_permission_usable,
)
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_tcc import FakeTCC, TccService, install_port, make_darwin_port

_STATES = {
    "granted": PermissionOutcome.GRANTED,
    "not_determined": PermissionOutcome.PENDING,
    "denied": PermissionOutcome.DENIED,
    "needs_settings": PermissionOutcome.NEEDS_SETTINGS,
    "unavailable": PermissionOutcome.UNAVAILABLE,
}


def _gate(state: str) -> FakePermissionService:
    gate = FakePermissionService()
    gate.script(PermissionId.MICROPHONE, _STATES[state])
    return gate


@pytest.mark.parametrize(
    ("state", "background", "user"),
    [
        ("granted", True, True),
        ("not_determined", False, True),
        ("needs_settings", False, True),
        ("denied", False, False),
        ("unavailable", False, False),
    ],
)
def test_macos_voice_gates_follow_the_silent_microphone_state(
    state: str, background: bool, user: bool
) -> None:
    gate = _gate(state)

    assert (
        _local_voice_permission_granted(platform_name="darwin", permission_gate=gate) is background
    )
    assert _local_voice_permission_usable(platform_name="darwin", permission_gate=gate) is user
    # Silent: only ``check`` was called, never an ``ensure`` (which could ask).
    assert gate.ensure_calls() == []
    assert [call.permission for call in gate.check_calls()] == [PermissionId.MICROPHONE] * 2


def test_restricted_microphone_is_not_usable_by_a_gesture() -> None:
    class _Restricted:
        def check(self, _permission):  # noqa: ANN001, ANN202
            return PermissionState.RESTRICTED

    assert not _local_voice_permission_granted(
        platform_name="darwin", permission_gate=_Restricted()
    )
    assert not _local_voice_permission_usable(platform_name="darwin", permission_gate=_Restricted())


def test_an_unreadable_state_closes_both_gates() -> None:
    class _Broken:
        def check(self, _permission):  # noqa: ANN001, ANN202
            raise RuntimeError("native read failed")

    assert not _local_voice_permission_granted(platform_name="darwin", permission_gate=_Broken())
    assert not _local_voice_permission_usable(platform_name="darwin", permission_gate=_Broken())


def test_non_macos_voice_gates_never_consult_the_service() -> None:
    gate = _gate("denied")

    assert _local_voice_permission_granted(platform_name="linux", permission_gate=gate)
    assert _local_voice_permission_usable(platform_name="win32", permission_gate=gate)
    assert gate.calls == []


def test_the_real_service_over_fake_tcc_is_only_ever_probed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)
    service = get_permission_service()

    # An undecided microphone: wake stays closed, a gesture may try.
    assert not _local_voice_permission_granted(platform_name="darwin", permission_gate=service)
    assert _local_voice_permission_usable(platform_name="darwin", permission_gate=service)
    tcc.deny(TccService.MICROPHONE)
    service.invalidate()
    assert not _local_voice_permission_usable(platform_name="darwin", permission_gate=service)
    tcc.grant(TccService.MICROPHONE)
    service.invalidate()
    assert _local_voice_permission_granted(platform_name="darwin", permission_gate=service)

    assert tcc.requests() == []
    assert tcc.implicit_prompts() == []


def test_a_fresh_mac_boots_both_gates_without_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    tcc = FakeTCC()
    install_port(monkeypatch, tcc.port("darwin"))

    # The default resolver (no gate injected) is the process service.
    assert not _local_voice_permission_granted(platform_name="darwin")
    assert _local_voice_permission_usable(platform_name="darwin")

    tcc.assert_no_prompts()
    assert tcc.requests() == []


def test_the_boot_path_hands_both_silent_gates_to_the_speech_pipeline() -> None:
    """Wiring guard: ``_start_speech_and_orb`` must pass BOTH predicates.

    The two gates are built in a closure of that method, so the harness-built gates
    of the voice tests cannot notice a dropped kwarg. If ``user_activation_gate``
    went missing the pipeline would default it to ``lambda: True`` and the two-
    predicate design (wake needs a grant, a gesture only needs "not refused")
    would regress with every other test green. A source-level check is the
    cheapest honest guard for a closure that cannot be built without a desktop.
    """
    import inspect
    import re

    from jarvis.speech.pipeline import SpeechPipeline
    from jarvis.ui.desktop_app import DesktopApp

    init_params = inspect.signature(SpeechPipeline.__init__).parameters
    assert "activation_gate" in init_params and "user_activation_gate" in init_params

    source = inspect.getsource(DesktopApp._start_speech_and_orb)
    assert re.search(r"^\s+activation_gate=voice_activation_gate,$", source, re.M)
    assert re.search(r"^\s+user_activation_gate=voice_user_gate,$", source, re.M)
    # Each closure reads the matching silent predicate, not the other one.
    assert re.search(
        r"def voice_activation_gate\(\) -> bool:\s+return "
        r"_local_voice_permission_granted\(platform_name=sys\.platform\)",
        source,
    )
    assert re.search(
        r"def voice_user_gate\(\) -> bool:\s+return "
        r"_local_voice_permission_usable\(platform_name=sys\.platform\)",
        source,
    )
