"""Snapshot v2 and the single-row read of the permission routes.

``GET /api/permissions/status`` is passive: it never prompts and never blocks. Every
test runs the REAL ``PermissionService`` on a REAL ``SystemPermissionPort`` that sits
on ``FakeTCC`` (``tests/fakes/fake_tcc.py``), behind a real FastAPI ``TestClient``.
The FakeTCC call log is the proof that nothing was asked. Nothing here ran on a real
Mac: every macOS behaviour is a MODEL (see the fidelity ledger in ``fake_tcc.py``).

The key sets of the snapshot cross Python, JSON and the TypeScript twin the frontend
reads (AP-4): the TypedDicts of ``permissions_routes`` are the Python side, read back
here against the real answer and against the TypeScript interfaces.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.core.events import PERMISSION_FEATURES, PermissionResolved
from jarvis.platform import permission_service as service_module
from jarvis.platform.permission_service import Episode, PermissionService, get_permission_service
from jarvis.platform.permissions import PermissionId, SystemPermissionPort
from jarvis.ui.web import permissions_routes as routes
from jarvis.ui.web.control_auth import require_control_key_or_session
from jarvis.ui.web.permissions_routes import (
    AppIdentityBlock,
    NeededEpisode,
    PermissionRow,
    PermissionsSnapshot,
    router,
)
from tests.fakes.fake_tcc import DMG_BUNDLE_ID, DialogPolicy, FakeTCC, TccService, install_port

_MUSIC = "com.apple.Music"
_SPOTIFY = "com.spotify.client"
_REPO_ROOT = Path(__file__).resolve().parents[4]
# The TypeScript twin of the snapshot types (interfaces PermissionSnapshot,
# PermissionAppIdentity, PermissionRow, PermissionNeededEpisode). It must exist: a
# missing file is a failure, never a skip.
_TS_TWIN = _REPO_ROOT / "jarvis/ui/web/frontend/src/lib/permissionSnapshot.ts"

_REMOVED_KEYS = {"features", "wanted", "active", "identity_reset", "restart_required", "foreground"}


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------


class _RecordingBus:
    """A bus stand-in: ``publish`` is a coroutine, like ``EventBus.publish``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        with self._lock:
            self.events.append(event)


class _LoopThread:
    """An asyncio loop on its own thread, like the server's loop next to a worker thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="test-loop", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def flush(self) -> None:
        for _ in range(3):
            asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.loop).result(timeout=5)

    def stop(self) -> None:
        self.flush()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()


@dataclass
class Env:
    client: TestClient
    tcc: FakeTCC
    app: FastAPI

    def status(self, query: str = "") -> dict:
        response = self.client.get(f"/api/permissions/status{query}")
        assert response.status_code == 200, response.text
        return response.json()

    def row(self, permission: str) -> dict:
        response = self.client.get(f"/api/permissions/{permission}")
        assert response.status_code == 200, response.text
        return response.json()

    def rows(self, query: str = "") -> dict[str, dict]:
        return {row["id"]: row for row in self.status(query)["permissions"]}

    def post(self, path: str, **kwargs: Any):
        return self.client.post(f"/api/permissions/{path}", **kwargs)


@pytest.fixture
def make_env(monkeypatch: pytest.MonkeyPatch):
    def build(*, platform: str = "darwin", config: object | None = None, **tcc_kwargs: Any) -> Env:
        tcc = FakeTCC(**tcc_kwargs)
        install_port(monkeypatch, tcc.port(platform))  # type: ignore[arg-type]
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_control_key_or_session] = lambda: None
        runtime = routes._Runtime()
        # No cooldown: these tests ask more than once for the same permission.
        runtime.limiter = routes._RateLimiter(cooldown_s=0, global_max=10_000)
        app.state.permissions_runtime = runtime
        if config is not None:
            app.state.config = config
        return Env(TestClient(app), tcc, app)

    return build


@pytest.fixture
def loop_thread() -> Iterator[_LoopThread]:
    thread = _LoopThread()
    yield thread
    # The watcher task lives on this loop: cancel it before the loop stops.
    service_module._reset_for_tests()
    thread.stop()


def _ducking(enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(ducking=SimpleNamespace(enabled=enabled))


def _typed_keys(typed_dict: type) -> set[str]:
    return set(get_type_hints(typed_dict))


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _walk_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _walk_keys(inner)


def _restart_hint_episode(permission: str) -> Episode:
    """What the service will publish once a consumer reports a restart hint."""
    return Episode(
        permissions=(permission,),
        feature="computer_use",
        reason="restart_hint",
        phase="blocked",
        origin="user",
        target="",
        can_prompt=False,
        can_open_settings=True,
        outside_app=False,
        detail="",
        trace_id="00000000-0000-0000-0000-000000000000",
        opened_at_ns=1,
    )


# ----------------------------------------------------------------------
# The key set (AP-4)
# ----------------------------------------------------------------------


def test_the_snapshot_has_exactly_the_keys_of_its_typed_dicts(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.post("microphone/request")  # one open episode, so `needed` has an entry to check

    snapshot = env.status("?include=automation")

    assert set(snapshot) == _typed_keys(PermissionsSnapshot)
    assert set(snapshot["app_identity"]) == _typed_keys(AppIdentityBlock)
    assert snapshot["permissions"], "no rows"
    for row in snapshot["permissions"]:
        assert set(row) == _typed_keys(PermissionRow), row["id"]
    assert snapshot["needed"], "no open episode"
    for episode in snapshot["needed"]:
        assert set(episode) == _typed_keys(NeededEpisode)


def test_the_row_read_has_the_keys_of_a_snapshot_row(make_env) -> None:
    env = make_env()

    for permission in ("microphone", "screen_recording", "accessibility", "input_monitoring"):
        assert set(env.row(permission)) == _typed_keys(PermissionRow), permission


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_the_non_macos_snapshot_has_the_same_key_set(make_env, platform: str) -> None:
    env = make_env(platform=platform)

    snapshot = env.status("?include=automation")

    assert set(snapshot) == _typed_keys(PermissionsSnapshot)
    assert set(snapshot["app_identity"]) == _typed_keys(AppIdentityBlock)
    assert all(set(row) == _typed_keys(PermissionRow) for row in snapshot["permissions"])


def test_the_retired_keys_are_gone_everywhere(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.post("microphone/request")

    keys = set(_walk_keys(env.status("?include=automation")))

    assert not keys & _REMOVED_KEYS, keys & _REMOVED_KEYS
    assert not set(_walk_keys(env.row("microphone"))) & _REMOVED_KEYS


def test_the_keys_other_consumers_read_are_unchanged(make_env) -> None:
    """``check_frozen_browser.py`` and the CLI relay read these four."""
    env = make_env(granted=[TccService.MICROPHONE])

    snapshot = env.status()

    assert snapshot["platform"] == "darwin"
    assert snapshot["app_identity"]["bundle_id"] == "com.personal-jarvis.desktop"
    microphone = next(row for row in snapshot["permissions"] if row["id"] == "microphone")
    assert microphone["status"] == "granted"


def test_the_snapshot_survives_a_json_round_trip(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.post("microphone/request")

    snapshot = env.status("?include=automation")

    assert json.loads(json.dumps(snapshot)) == snapshot


def test_used_for_speaks_the_event_feature_vocabulary(make_env) -> None:
    rows = make_env().rows("?include=automation")

    for row in rows.values():
        assert set(row["used_for"]) <= set(PERMISSION_FEATURES), row["id"]
    assert rows["credential_store"]["used_for"] == []  # the Keychain has no event feature
    assert rows["microphone"]["used_for"][0] == "voice"
    assert rows["automation"]["used_for"] == ["audio_ducking"]


def test_every_used_for_entry_is_a_known_feature() -> None:
    for permission, features in routes._USED_FOR.items():
        assert set(features) <= set(PERMISSION_FEATURES), permission
    assert set(routes._USED_FOR) == set(routes._ROW_ORDER)
    assert set(routes._ROW_LABELS) == set(routes._ROW_ORDER)


def _ts_interface(name: str) -> dict[str, str]:
    source = _TS_TWIN.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert match, f"interface {name} missing from {_TS_TWIN.name}"
    fields = dict(re.findall(r"^\s{2}([a-z_]+)\??: ([^;\n]+);", match.group(1), re.M))
    assert fields, f"no fields parsed from {name}"
    return fields


@pytest.mark.parametrize(
    ("typed_dict", "interface"),
    [
        (PermissionsSnapshot, "PermissionSnapshot"),
        (AppIdentityBlock, "PermissionAppIdentity"),
        (PermissionRow, "PermissionRow"),
        (NeededEpisode, "PermissionNeededEpisode"),
    ],
)
def test_typescript_twin_matches_the_typed_dict(typed_dict: type, interface: str) -> None:
    py_fields = _typed_keys(typed_dict)
    ts_fields = _ts_interface(interface)
    extra, missing = set(ts_fields) - py_fields, py_fields - set(ts_fields)
    assert not extra and not missing, f"{interface} drift: extra={extra}, missing={missing}"


def _ts_union(name: str) -> set[str]:
    source = _TS_TWIN.read_text(encoding="utf-8")
    match = re.search(rf"export type {name} =((?:\s*\|?\s*\"[a-z_]+\")+);", source)
    assert match, f"union {name} missing from {_TS_TWIN.name}"
    return set(re.findall(r'"([a-z_]+)"', match.group(1)))


def test_typescript_twin_speaks_the_same_state_outcome_and_row_vocabulary() -> None:
    """The values behind the interfaces cross the same five layers (AP-4)."""
    from jarvis.platform.permission_service import PermissionOutcome
    from jarvis.platform.permissions import PermissionId, PermissionState

    assert _ts_union("PermissionState") == {state.value for state in PermissionState}
    assert _ts_union("PermissionOutcome") == {outcome.value for outcome in PermissionOutcome}

    source = _TS_TWIN.read_text(encoding="utf-8")
    ids = re.search(r"export const PERMISSION_ROW_IDS = \[(.*?)\] as const;", source, re.S)
    assert ids, "PERMISSION_ROW_IDS missing"
    row_ids = re.findall(r'"([a-z_]+)"', ids.group(1))
    assert row_ids == [permission.value for permission in routes._ROW_ORDER]
    # PermissionId = the rows plus the one alias the routes still accept.
    assert set(row_ids) | {"event_posting"} == {permission.value for permission in PermissionId}
    assert "event_posting" in source


@pytest.mark.parametrize(
    ("typed_dict", "interface"),
    [
        (routes.EnsurePayload, "PermissionEnsurePayload"),
        (routes.OperationPayload, "PermissionOperationPayload"),
        (routes.RateLimitedPayload, "PermissionRateLimitedPayload"),
    ],
)
def test_typescript_twin_matches_the_answer_typed_dicts(typed_dict: type, interface: str) -> None:
    """The three answer shapes of the ask routes are twinned key for key as well (AP-4)."""
    py_fields = _typed_keys(typed_dict)
    ts_fields = _ts_interface(interface)
    extra, missing = set(ts_fields) - py_fields, py_fields - set(ts_fields)
    assert not extra and not missing, f"{interface} drift: extra={extra}, missing={missing}"


@pytest.mark.parametrize(
    ("interface", "field", "declared"),
    [
        ("PermissionRow", "id", "PermissionRowId"),
        ("PermissionRow", "status", "PermissionState"),
        ("PermissionNeededEpisode", "feature", "PermissionFeature"),
        ("PermissionNeededEpisode", "reason", "PermissionNeededReason"),
        ("PermissionNeededEpisode", "phase", "PermissionNeededPhase"),
        ("PermissionNeededEpisode", "origin", "PermissionNeededOrigin"),
        ("PermissionEnsurePayload", "outcome", "PermissionOutcome"),
        ("PermissionEnsurePayload", "state", "PermissionState"),
    ],
)
def test_typescript_twin_declares_the_enumerated_fields_with_their_union(
    interface: str, field: str, declared: str
) -> None:
    """A twin that widened an enumerated field to ``string`` would let a new value through."""
    assert _ts_interface(interface)[field] == declared


def test_the_real_answers_carry_exactly_the_keys_of_their_typed_dicts(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    monkeypatch.setattr(subprocess, "run", env.tcc.run_tccutil)

    ensured = env.post("microphone/request").json()
    refused = env.post("microphone/request")  # inside the route's own cooldown
    opened = env.post("microphone/open-settings").json()
    env.tcc.deny("microphone")
    reset = env.post("microphone/reset").json()

    assert set(ensured) == _typed_keys(routes.EnsurePayload)
    assert set(opened) == _typed_keys(routes.OperationPayload)
    assert set(reset) == _typed_keys(routes.OperationPayload)
    assert set(opened["permission"]) == _typed_keys(PermissionRow)
    if refused.status_code == 429:  # the harness limiter has no cooldown: cover the 429 shape
        assert set(refused.json()) == _typed_keys(routes.RateLimitedPayload)
    limited = routes._rate_limited(
        routes._Refusal("permission", 4.2), "request", PermissionId.MICROPHONE
    )
    assert set(json.loads(limited.body)) == _typed_keys(routes.RateLimitedPayload)


# ----------------------------------------------------------------------
# Passive: never prompts, never blocks
# ----------------------------------------------------------------------


def test_status_never_prompts_on_a_fresh_mac(make_env) -> None:
    env = make_env()  # every permission not_determined

    for _ in range(3):
        env.status("?include=automation")
        env.row("microphone")
        env.row("screen_recording")

    env.tcc.assert_no_prompts()
    assert env.tcc.requests() == [] and env.tcc.implicit_prompts() == []
    assert env.tcc.launches == []
    assert env.status()["needed"] == []  # nothing asked, nothing is waiting


def test_status_never_blocks_on_a_dialog_nobody_answered(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.post("microphone/request")

    started = time.monotonic()
    snapshot = env.status()
    elapsed = time.monotonic() - started

    assert elapsed < 2.0
    assert [episode["permissions"] for episode in snapshot["needed"]] == [["microphone"]]


def test_status_reads_screen_recording_with_the_shallow_preflight_only(make_env) -> None:
    env = make_env()
    env.tcc.grant("screen_recording")  # mid-process: the preflight stays frozen (BUG-161)

    row = env.rows()["screen_recording"]

    # Documented limitation of the snapshot: the window-title oracle is a gesture
    # and watcher tool (it enumerates windows), never a status read.
    assert row["status"] == "not_granted"


def test_status_never_uses_the_old_readiness_aggregation(make_env, monkeypatch) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])

    def forbidden(self, *args, **kwargs):  # noqa: ANN001, ARG001
        raise AssertionError("the status routes must not aggregate")

    monkeypatch.setattr(SystemPermissionPort, "snapshot", forbidden)
    # The aggregate Automation read rewrites the consent record: never from here.
    monkeypatch.setattr(SystemPermissionPort, "_automation_state", forbidden)

    snapshot = env.status("?include=automation")

    assert [row["id"] for row in snapshot["permissions"]] == [
        "microphone",
        "screen_recording",
        "accessibility",
        "input_monitoring",
        "automation",
        "credential_store",
    ]


def test_event_posting_has_no_row_of_its_own(make_env) -> None:
    env = make_env()

    assert "event_posting" not in env.rows("?include=automation")
    folded = env.row("event_posting")  # the alias answers with its pane family
    assert folded["id"] == "accessibility"


def test_unknown_permission_is_a_422(make_env) -> None:
    assert make_env().client.get("/api/permissions/camera").status_code == 422


# ----------------------------------------------------------------------
# The rows on a Mac
# ----------------------------------------------------------------------


def test_a_fresh_mac_offers_every_row_a_way_forward(make_env) -> None:
    rows = make_env().rows()

    assert list(rows) == [
        "microphone",
        "screen_recording",
        "accessibility",
        "input_monitoring",
        "credential_store",
    ]
    assert rows["microphone"]["status"] == "not_determined"
    for permission in ("microphone", "screen_recording", "accessibility", "input_monitoring"):
        row = rows[permission]
        assert row["can_request"] is True, permission
        assert row["can_open_settings"] is True, permission
        assert row["can_reset"] is True, permission
        assert row["restart_hint"] is False
        assert row["settings_path"].startswith("System Settings > Privacy & Security > ")
    assert rows["microphone"]["detail"].startswith("Personal Jarvis needs Microphone access")


def test_granted_rows_offer_nothing_to_fix(make_env) -> None:
    env = make_env(granted=[TccService.MICROPHONE, TccService.ACCESSIBILITY])

    rows = env.rows()

    for permission in ("microphone", "accessibility"):
        row = rows[permission]
        assert row["status"] == "granted" and row["detail"] == ""
        assert row["can_request"] is False and row["can_reset"] is False
        assert row["can_open_settings"] is True  # a person may still want to look at the pane


def test_the_keychain_row_offers_nothing_while_the_vault_works(make_env) -> None:
    row = make_env(credential_backend="platform").rows()["credential_store"]

    assert row["status"] == "granted" and row["can_request"] is False and row["detail"] == ""


def test_the_keychain_row_offers_try_again_after_a_declined_prompt(make_env) -> None:
    declined = make_env(credential_backend="file").rows()["credential_store"]

    assert declined["status"] == "not_granted" and declined["can_request"] is True
    assert "local file" in declined["detail"]
    assert declined["can_open_settings"] is False and declined["settings_path"] is None
    assert declined["can_reset"] is False  # not a TCC row


def test_a_denied_row_points_at_settings_and_offers_ask_again_but_no_prompt(make_env) -> None:
    env = make_env()
    env.tcc.deny("microphone")

    row = env.rows()["microphone"]

    assert row["status"] == "denied"
    assert row["can_request"] is False  # macOS will not ask a second time
    assert row["can_reset"] is True and row["can_open_settings"] is True
    assert "turned off" in row["detail"] and "Microphone" in row["detail"]


def test_a_restricted_row_is_explained_not_actionable(make_env) -> None:
    env = make_env()
    env.tcc.restrict("microphone")

    row = env.rows()["microphone"]

    assert row["status"] == "restricted"
    assert row["can_request"] is False and row["can_reset"] is False
    assert "restricted" in row["detail"]


def test_a_missing_framework_reads_unavailable_with_a_fixed_sentence(make_env) -> None:
    env = make_env()
    env.tcc.drop_framework("AVFoundation")

    row = env.rows()["microphone"]

    assert row["status"] == "unavailable" and row["can_request"] is False
    assert row["detail"] == "Personal Jarvis cannot ask for Microphone access in this session."


def test_a_missing_usage_string_means_the_row_cannot_be_asked(make_env) -> None:
    env = make_env(usage_strings=[TccService.AUTOMATION])  # no NSMicrophoneUsageDescription

    row = env.rows()["microphone"]

    assert row["status"] == "not_determined"
    assert row["can_request"] is False  # asking would abort the process (BUG-058 class)
    assert env.tcc.requests() == []


def test_a_headless_session_offers_no_dialog_and_no_window(make_env) -> None:
    env = make_env(headless=True)

    snapshot = env.status()

    assert snapshot["headless"] is True
    for row in snapshot["permissions"]:
        assert not (row["can_request"] or row["can_open_settings"] or row["can_reset"]), row["id"]


def test_outside_the_installed_app_asking_and_opening_a_pane_stay_possible_but_resetting_does_not(
    make_env,
) -> None:
    env = make_env(bundle_id=None, bundle_path=None)

    snapshot = env.status()

    assert snapshot["outside_installed_app"] is True
    assert snapshot["app_identity"]["stable"] is False
    assert snapshot["app_identity"]["launched_as_bundle"] is False
    row = next(row for row in snapshot["permissions"] if row["id"] == "microphone")
    assert row["can_request"] is True  # after an explicit confirmation naming the grantee
    assert row["can_reset"] is False  # tccutil is scoped to the installed app's own bundle id
    # Opening a pane is not an action on the permission (P6): it needs a desktop
    # session, not the installed-app identity.
    assert row["can_open_settings"] is True


def test_the_installed_app_reports_a_stable_identity(make_env) -> None:
    snapshot = make_env().status()

    assert snapshot["outside_installed_app"] is False
    assert snapshot["app_identity"] == {
        "app_name": "Personal Jarvis",
        "bundle_id": "com.personal-jarvis.desktop",
        "bundle_path": "/Applications/Personal Jarvis.app",
        "launched_as_bundle": True,
        "stable": True,
    }


def test_the_dmg_app_is_an_installed_app_too(make_env) -> None:
    snapshot = make_env(bundle_id=DMG_BUNDLE_ID).status()

    assert snapshot["app_identity"]["bundle_id"] == DMG_BUNDLE_ID
    assert snapshot["outside_installed_app"] is False


# ----------------------------------------------------------------------
# Off macOS
# ----------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_off_macos_every_row_is_not_required_and_nothing_is_open(make_env, platform: str) -> None:
    env = make_env(platform=platform)

    snapshot = env.status("?include=automation")

    assert snapshot["platform"] == platform and snapshot["supported"] is False
    assert snapshot["outside_installed_app"] is False  # there is no app bundle to be outside of
    assert snapshot["needed"] == []
    assert len(snapshot["permissions"]) == 6
    for row in snapshot["permissions"]:
        assert row["status"] == "not_required", row["id"]
        assert not (row["can_request"] or row["can_open_settings"] or row["can_reset"])
        assert row["restart_hint"] is False and row["settings_path"] is None
        assert row["detail"] == "This operating system does not require a macOS privacy permission."
    env.tcc.assert_silent()  # the FakeTCC call log stays empty


# ----------------------------------------------------------------------
# needed[]: the open episodes
# ----------------------------------------------------------------------


def test_needed_reflects_the_open_episodes_and_empties_when_they_resolve(make_env) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.post("microphone/request", json={"feature": "dictation"})

    [episode] = env.status()["needed"]

    assert episode["permissions"] == ["microphone"] and episode["feature"] == "dictation"
    assert episode["origin"] == "user" and episode["phase"] == "os_dialog"
    assert episode["reason"] == "not_determined"
    assert episode["trace_id"] and episode["opened_at_ns"] > 0

    env.tcc.answer("microphone", DialogPolicy.ALLOW)
    get_permission_service().invalidate()  # a negative answer is cached for ~250 ms
    snapshot = env.status()

    assert snapshot["needed"] == []
    assert (
        next(r for r in snapshot["permissions"] if r["id"] == "microphone")["status"] == "granted"
    )


def test_needed_lists_a_background_consumer_without_asking(make_env) -> None:
    env = make_env()
    result = get_permission_service().ensure("microphone", feature="wake_word", interactive=False)

    [episode] = env.status()["needed"]

    assert not result.asked and env.tcc.requests() == []
    assert (episode["origin"], episode["feature"]) == ("background", "wake_word")
    assert episode["can_prompt"] is True


def test_needed_keeps_denied_episodes_and_folds_event_posting(make_env) -> None:
    env = make_env()
    env.tcc.deny("microphone")
    get_permission_service().ensure("microphone", feature="voice")
    get_permission_service().ensure("event_posting", feature="computer_use", interactive=False)

    needed = {tuple(e["permissions"]): e for e in env.status()["needed"]}

    assert set(needed) == {("microphone",), ("accessibility",)}
    assert needed[("microphone",)]["reason"] == "denied"


def test_a_status_read_publishes_the_resolve_edge(make_env, loop_thread: _LoopThread) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    bus = _RecordingBus()
    get_permission_service().attach_bus(bus, loop_thread.loop)
    env.post("microphone/request")
    env.tcc.answer("microphone", DialogPolicy.ALLOW)
    get_permission_service().invalidate()  # a negative answer is cached for ~250 ms
    loop_thread.flush()
    assert not [e for e in bus.events if isinstance(e, PermissionResolved)]

    env.status()  # the watcher has not ticked: this path sees the edge first
    loop_thread.flush()

    resolved = [e for e in bus.events if isinstance(e, PermissionResolved)]
    assert [(e.permissions, e.granted) for e in resolved] == [(("microphone",), True)]


def test_the_single_row_read_publishes_the_resolve_edge_too(
    make_env, loop_thread: _LoopThread
) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    bus = _RecordingBus()
    get_permission_service().attach_bus(bus, loop_thread.loop)
    env.post("microphone/request")
    env.tcc.answer("microphone", DialogPolicy.ALLOW)
    get_permission_service().invalidate()  # a negative answer is cached for ~250 ms

    row = env.row("microphone")
    loop_thread.flush()

    assert row["status"] == "granted"
    assert [e.granted for e in bus.events if isinstance(e, PermissionResolved)] == [True]


def test_a_restart_hint_episode_marks_its_row(make_env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env()
    hint = _restart_hint_episode("screen_recording")
    monkeypatch.setattr(PermissionService, "outstanding", lambda self: [hint])

    rows = env.rows()

    assert rows["screen_recording"]["restart_hint"] is True
    assert "quit and reopened" in rows["screen_recording"]["detail"]
    assert rows["microphone"]["restart_hint"] is False


def test_a_restart_hint_never_marks_a_granted_row(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(granted=[TccService.SCREEN_RECORDING])
    hint = _restart_hint_episode("screen_recording")
    monkeypatch.setattr(PermissionService, "outstanding", lambda self: [hint])

    row = env.rows()["screen_recording"]

    assert row["status"] == "granted" and row["restart_hint"] is False and row["detail"] == ""


# ----------------------------------------------------------------------
# The Automation row
# ----------------------------------------------------------------------


def test_the_automation_row_is_not_computed_while_ducking_is_off(make_env) -> None:
    env = make_env(config=_ducking(False), installed_players=[_MUSIC], running_players=[_MUSIC])

    assert "automation" not in env.rows()
    assert env.tcc.probes("automation") == [] and env.tcc.requests("automation") == []


def test_the_automation_row_is_absent_without_a_configuration_too(make_env) -> None:
    assert "automation" not in make_env().rows()


def test_the_automation_row_appears_when_ducking_is_on(make_env) -> None:
    env = make_env(config=_ducking(True), installed_players=[_MUSIC], running_players=[_MUSIC])

    rows = env.rows()

    assert rows["automation"]["status"] == "not_determined"
    assert rows["automation"]["can_request"] is True
    assert "Checked only while Music or Spotify is running." in rows["automation"]["detail"]
    assert env.tcc.requests("automation") == []  # reading asks nothing


def test_the_automation_row_appears_on_request(make_env) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])

    assert "automation" in env.rows("?include=automation")
    assert "automation" in env.rows("?include=foo, automation")
    assert "automation" not in env.rows("?include=foo")


@pytest.mark.parametrize(
    ("setup", "status", "can_request"),
    [
        ({"installed_players": [_MUSIC], "running_players": [_MUSIC]}, "not_determined", True),
        ({"installed_players": [_MUSIC]}, "not_determined", True),  # closed: unknown, not "no"
        ({}, "not_required", False),  # no scriptable player installed
    ],
    ids=["running", "closed", "no-players"],
)
def test_the_automation_row_reflects_the_players(
    make_env, setup: dict, status: str, can_request: bool
) -> None:
    row = make_env(**setup).rows("?include=automation")["automation"]

    assert row["status"] == status and row["can_request"] is can_request


def test_the_automation_row_reads_granted_and_denied_per_player(make_env) -> None:
    env = make_env(installed_players=[_MUSIC, _SPOTIFY], running_players=[_MUSIC, _SPOTIFY])
    env.tcc.grant("automation", _MUSIC)
    env.tcc.grant("automation", _SPOTIFY)
    assert env.rows("?include=automation")["automation"]["status"] == "granted"

    env.tcc.deny("automation", _SPOTIFY)  # the strictest player wins
    get_permission_service().invalidate()  # a grant is cached for a second at most
    row = env.rows("?include=automation")["automation"]

    assert row["status"] == "denied" and row["can_request"] is False and row["can_reset"] is True


def test_the_automation_row_never_launches_a_player_or_writes_a_consent_file(
    make_env, tmp_path: Path
) -> None:
    env = make_env(installed_players=[_MUSIC, _SPOTIFY])

    env.rows("?include=automation")

    assert env.tcc.launches == []
    assert not list(tmp_path.rglob("macos-automation-consent.json"))


def test_an_automation_probe_that_hangs_is_bounded_and_not_piled_up(
    make_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(installed_players=[_MUSIC], running_players=[_MUSIC])
    release = threading.Event()
    reached = threading.Event()  # set by the stub itself: no assertion races a slow thread start
    entered: list[int] = []

    def hanging(service: PermissionService):
        entered.append(1)
        reached.set()
        release.wait(10)
        return routes._worst_automation([])

    monkeypatch.setattr(routes, "_read_automation", hanging)
    monkeypatch.setattr(routes, "_AUTOMATION_PROBE_TIMEOUT_S", 0.2)
    try:
        started = time.monotonic()
        first = env.rows("?include=automation")["automation"]
        second = env.rows("?include=automation")["automation"]
        elapsed = time.monotonic() - started

        assert elapsed < 5.0
        for row in (first, second):
            assert row["status"] == "unavailable" and row["can_request"] is False
            assert row["detail"].startswith("The Automation check did not answer in time.")
        assert reached.wait(5)
        assert entered == [1]  # one hung thread, never a second
    finally:
        release.set()
        thread = env.app.state.permissions_runtime.automation_probe._thread
        if thread is not None:
            thread.join(5)


def test_a_hanging_episode_refresh_is_bounded(make_env, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(default_policy=DialogPolicy.NEVER_ANSWERED)
    env.post("microphone/request")  # an open episode, so the refresh has work to do
    release = threading.Event()
    monkeypatch.setattr(PermissionService, "refresh_episodes", lambda self: release.wait(10))
    monkeypatch.setattr(routes, "_REFRESH_TIMEOUT_S", 0.2)
    try:
        started = time.monotonic()
        snapshot = env.status()
        elapsed = time.monotonic() - started

        assert elapsed < 5.0
        assert len(snapshot["needed"]) == 1  # still answered, from the registry
    finally:
        release.set()
        thread = env.app.state.permissions_runtime.refresh._thread
        if thread is not None:
            thread.join(5)


def test_a_quiet_status_read_does_not_start_a_refresh_thread(make_env) -> None:
    env = make_env()

    env.status()

    assert env.app.state.permissions_runtime.refresh._thread is None  # nothing was open


# ----------------------------------------------------------------------
# The runtime
# ----------------------------------------------------------------------


def test_the_runtime_is_created_once_per_app(make_env) -> None:
    env = make_env()
    del env.app.state.permissions_runtime  # a production app starts without one

    env.status()
    first = env.app.state.permissions_runtime
    env.status()

    assert env.app.state.permissions_runtime is first
