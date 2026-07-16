from __future__ import annotations

from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.ui.desktop_app import _local_voice_permission_granted


class _PermissionPort:
    def __init__(self, state: PermissionState, *, stable: bool = True) -> None:
        self.permission_state = state
        self.stable = stable
        self.calls: list[PermissionId] = []

    def runtime_access_granted(self, permission_id: PermissionId) -> bool:
        self.calls.append(permission_id)
        return self.stable and self.permission_state is PermissionState.GRANTED


def test_macos_voice_gate_tracks_fresh_microphone_state() -> None:
    port = _PermissionPort(PermissionState.NOT_DETERMINED)

    assert not _local_voice_permission_granted(
        platform_name="darwin", permission_port=port
    )
    port.permission_state = PermissionState.GRANTED
    assert _local_voice_permission_granted(
        platform_name="darwin", permission_port=port
    )
    port.permission_state = PermissionState.DENIED
    assert not _local_voice_permission_granted(
        platform_name="darwin", permission_port=port
    )
    assert port.calls == [PermissionId.MICROPHONE] * 3


def test_macos_voice_gate_rejects_grant_under_unstable_identity() -> None:
    port = _PermissionPort(PermissionState.GRANTED, stable=False)

    assert not _local_voice_permission_granted(
        platform_name="darwin", permission_port=port
    )


def test_non_macos_voice_gate_never_consults_tcc() -> None:
    port = _PermissionPort(PermissionState.DENIED)

    assert _local_voice_permission_granted(
        platform_name="linux", permission_port=port
    )
    assert port.calls == []
