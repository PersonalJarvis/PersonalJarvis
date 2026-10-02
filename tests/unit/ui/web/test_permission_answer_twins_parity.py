"""AP-4 parity for the two permission answers that are not typed dicts.

Two shapes cross Python, JSON and TypeScript without a TypedDict of their own:

* the "Mute music while dictating" switch-on answer: ``DuckPermissionReport.as_dict()`` and
  its per-player ``PlayerPermission.as_dict()`` (``jarvis/audio/ducking/protocol.py``),
  twinned by ``MuteMusicPermission`` / ``MuteMusicPlayerPermission`` in
  ``hooks/useMuteMusic.ts``;
* the wake switch's answer ``{outcome, reason, can_open_settings}`` of
  ``POST /api/settings/wake-word/activation`` (``settings_routes``), twinned by
  ``WakeActivationPermission`` in ``components/permissions/wakeActivation.ts``.

A key renamed on one side renders as a missing per-player line or a note that falls through
to "unavailable" with no error anywhere, so the TS interfaces are regex-read here (the
same way ``test_shortcuts_status_parity.py`` reads its twin) and compared key for key with
what Python really sends. ``outcome`` and ``reason`` must be declared with the shared
unions, not widened to ``string``, so a new value is a compile error in the UI.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.audio.ducking.protocol import DuckPermissionReport, PlayerPermission
from jarvis.core.events import PERMISSION_NEEDED_REASONS
from jarvis.platform.permission_service import PermissionOutcome
from jarvis.ui.web import settings_routes

_SRC = Path(__file__).resolve().parents[4] / "jarvis" / "ui" / "web" / "frontend" / "src"
_MUTE_TS = _SRC / "hooks" / "useMuteMusic.ts"
_WAKE_TS = _SRC / "components" / "permissions" / "wakeActivation.ts"
_SNAPSHOT_TS = _SRC / "lib" / "permissionSnapshot.ts"
_EVENTS_TS = _SRC / "lib" / "permissionEvents.ts"


def _strip_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", source)


def _ts_interface(path: Path, name: str) -> dict[str, str]:
    """Field name -> declared type of ``export interface <name>`` (no nested objects)."""
    assert path.exists(), f"frontend twin missing: {path}"
    source = _strip_comments(path.read_text(encoding="utf-8"))
    match = re.search(rf"export interface {name}\s*\{{(?P<body>[^{{}}]*)\}}", source)
    assert match is not None, f"interface {name} not found in {path.name}"
    fields = dict(re.findall(r"^\s*([a-z_]+)\??:\s*([^;]+);", match.group("body"), re.MULTILINE))
    assert fields, f"parsed no fields from {name}"
    return fields


def _ts_union(path: Path, name: str) -> set[str]:
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"export type {name} =((?:\s*\|?\s*\"[a-z_]+\")+);", source)
    assert match, f"union {name} missing from {path.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _ts_const_tuple(path: Path, name: str) -> set[str]:
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, f"tuple {name} missing from {path.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def _a_player() -> PlayerPermission:
    return PlayerPermission(
        player="Spotify",
        target="com.spotify.client",
        outcome="needs_settings",
        reason="needs_settings",
        can_open_settings=True,
        asked=True,
        outside_installed_app=False,
        detail="x",
    )


def test_player_permission_twin_carries_exactly_the_python_keys() -> None:
    ts = _ts_interface(_MUTE_TS, "MuteMusicPlayerPermission")
    assert set(ts) == set(_a_player().as_dict())


def test_mute_music_permission_twin_carries_exactly_the_python_keys() -> None:
    ts = _ts_interface(_MUTE_TS, "MuteMusicPermission")
    report = DuckPermissionReport(players=(_a_player(),), not_running=("Music",), note="")
    assert set(ts) == set(report.as_dict())
    # The per-player list is twinned by the interface above, by name.
    assert ts["players"] == "MuteMusicPlayerPermission[]"
    assert ts["not_running"] == "string[]"


def test_wake_activation_twin_carries_exactly_the_keys_the_route_sends() -> None:
    ts = _ts_interface(_WAKE_TS, "WakeActivationPermission")
    # Switch off / not macOS: the early answer. The route's other answers use the same keys.
    answer = settings_routes._ask_microphone_for_wake_switch(SimpleNamespace(), enabled=False)
    assert set(ts) == set(answer)


@pytest.mark.parametrize("outcome", list(PermissionOutcome))
def test_wake_route_answers_with_the_shared_vocabulary(
    outcome: PermissionOutcome, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every outcome the service can produce is one the TS union declares, with a known reason."""
    result = SimpleNamespace(
        outcome=outcome,
        reason=""
        if outcome in (PermissionOutcome.GRANTED, PermissionOutcome.NOT_REQUIRED)
        else "denied",
        can_open_settings=True,
    )
    service = SimpleNamespace(ensure=lambda *args, **kwargs: result)
    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)
    monkeypatch.setattr(settings_routes, "_microphone_service", lambda request: service)

    answer = settings_routes._ask_microphone_for_wake_switch(SimpleNamespace(), enabled=True)

    assert set(answer) == set(_ts_interface(_WAKE_TS, "WakeActivationPermission"))
    assert answer["outcome"] in _ts_union(_SNAPSHOT_TS, "PermissionOutcome")
    assert answer["reason"] in {*PERMISSION_NEEDED_REASONS, ""}


def test_the_failure_answer_of_the_wake_route_stays_inside_the_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no service")

    monkeypatch.setattr(settings_routes, "_is_macos", lambda: True)
    monkeypatch.setattr(
        settings_routes, "_microphone_service", lambda request: SimpleNamespace(ensure=boom)
    )

    answer = settings_routes._ask_microphone_for_wake_switch(SimpleNamespace(), enabled=True)

    assert set(answer) == set(_ts_interface(_WAKE_TS, "WakeActivationPermission"))
    assert answer["outcome"] in _ts_union(_SNAPSHOT_TS, "PermissionOutcome")
    assert answer["reason"] in PERMISSION_NEEDED_REASONS


@pytest.mark.parametrize(
    ("path", "interface"),
    [
        (_MUTE_TS, "MuteMusicPlayerPermission"),
        (_WAKE_TS, "WakeActivationPermission"),
    ],
)
def test_outcome_and_reason_are_declared_with_the_shared_unions_not_string(
    path: Path, interface: str
) -> None:
    """A twin widened to ``string`` would let a renamed value through the compiler."""
    fields = _ts_interface(path, interface)
    assert fields["outcome"] == "PermissionOutcome"
    assert fields["reason"].replace(" ", "") == 'PermissionNeededReason|""'


def test_the_unions_those_twins_use_are_the_python_vocabularies() -> None:
    assert _ts_union(_SNAPSHOT_TS, "PermissionOutcome") == {o.value for o in PermissionOutcome}
    assert _ts_const_tuple(_EVENTS_TS, "PERMISSION_NEEDED_REASONS") == set(
        PERMISSION_NEEDED_REASONS
    )


def test_the_dictation_start_failures_are_refusal_reasons_python_can_send() -> None:
    """The WS hook resets the recording pill only for these tokens: they must exist (AP-4)."""
    from jarvis.core.events import DICTATION_REFUSAL_REASONS

    source = (_SRC / "store" / "permissions.ts").read_text(encoding="utf-8")
    match = re.search(
        r"export const DICTATION_START_FAILURES: ReadonlySet<string> = new Set\(\[(.*?)\]\);",
        source,
        re.DOTALL,
    )
    assert match, "DICTATION_START_FAILURES missing from store/permissions.ts"
    tokens = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert tokens, "parsed no tokens"
    assert tokens <= set(DICTATION_REFUSAL_REASONS)
