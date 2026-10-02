"""Unit coverage for ``jarvis.platform.screen_access``: the Screen Recording seam of the
capture consumers. The gate is the scripted ``FakePermissionService`` (or a tiny
hand-written stub where a method the Protocol lacks matters); the macOS behaviour
itself is covered with ``FakeTCC`` in ``tests/unit/cu/test_capture_permissions.py``.
"""

from __future__ import annotations

import pytest

from jarvis.platform import screen_access
from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.platform.screen_access import (
    ScreenCaptureRefused,
    frame_is_blank,
    refusal_for_state,
    require_screen_recording,
    require_screen_recording_async,
    screen_recording_blocked,
    screen_recording_state,
    verify_frame_is_real,
)
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_screen_pixels import textured_pixels, wallpaper_pixels

_SR = PermissionId.SCREEN_RECORDING
_SIZE = (64, 36)


class _StubGate:
    """A gate with the optional ``check_deep`` / ``invalidate`` / ``ensure`` of the service."""

    def __init__(self, states, deep=None, ensure=None):
        self._states = list(states)
        self._deep = list(deep or [])
        self._ensure = ensure
        self.log: list[str] = []

    def check(self, permission, *, target=None):
        self.log.append("check")
        return self._states.pop(0) if len(self._states) > 1 else self._states[0]

    def check_deep(self, permission, *, target=None):
        self.log.append("check_deep")
        return self._deep.pop(0) if len(self._deep) > 1 else self._deep[0]

    def invalidate(self, permission=None):
        self.log.append("invalidate")

    def ensure(self, permission, **kwargs):
        self.log.append("ensure")
        return self._ensure


# ---------------------------------------------------------------------------
# Silent reads
# ---------------------------------------------------------------------------


def test_a_not_granted_preflight_is_re_read_deeply_before_it_is_believed():
    gate = _StubGate([PermissionState.NOT_GRANTED], deep=[PermissionState.GRANTED])

    assert screen_recording_state(gate) is PermissionState.GRANTED
    assert gate.log == ["check", "check_deep"]


def test_a_granted_preflight_costs_one_cached_read_and_no_window_enumeration():
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.NOT_GRANTED])

    assert screen_recording_state(gate) is PermissionState.GRANTED
    assert gate.log == ["check"]


@pytest.mark.parametrize("state", [PermissionState.RESTRICTED, PermissionState.UNAVAILABLE])
def test_restricted_and_unavailable_are_final_and_skip_the_deep_read(state):
    gate = _StubGate([state], deep=[PermissionState.GRANTED])

    assert screen_recording_state(gate) is state
    assert gate.log == ["check"]


def test_a_gate_without_a_deep_read_answers_with_check_alone():
    gate = FakePermissionService({_SR: "needs_settings"})

    assert screen_recording_blocked(gate) is True
    assert gate.native_free()
    assert gate.ensure_calls() == []


def test_the_helper_read_never_asks():
    gate = FakePermissionService({_SR: "pending"})

    assert screen_recording_blocked(gate) is True

    assert gate.ensure_calls() == []


def test_off_macos_the_read_is_not_blocked():
    assert screen_recording_blocked(FakePermissionService({_SR: "not_required"})) is False


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (PermissionState.DENIED, "denied"),
        (PermissionState.NOT_GRANTED, "needs_settings"),
        (PermissionState.NOT_DETERMINED, "not_determined"),
        (PermissionState.RESTRICTED, "restricted"),
        (PermissionState.UNAVAILABLE, "unavailable"),
    ],
)
def test_refusal_text_is_prohibitive_for_the_agent_and_plain_for_people(state, reason):
    refused = refusal_for_state(state)

    assert refused.reason == reason
    assert str(refused).startswith("[permission_needed:screen_recording] ")
    assert "must not" in refused.agent_detail
    assert refused.user_detail and not refused.user_detail.startswith("[")
    # Fixed templates only: no exception text, file path or window title.
    for text in (refused.agent_detail, refused.user_detail):
        assert "Traceback" not in text and ".py" not in text


# ---------------------------------------------------------------------------
# The gesture entry asks
# ---------------------------------------------------------------------------


def test_the_gesture_entry_asks_once_interactively_without_waiting():
    gate = FakePermissionService({_SR: "pending"})

    with pytest.raises(ScreenCaptureRefused) as refused:
        require_screen_recording("screen_context", gate=gate)

    (call,) = gate.ensure_calls(_SR)
    assert (call.feature, call.interactive, call.wait_s) == ("screen_context", True, 0.0)
    assert refused.value.reason == "not_determined"


def test_the_gesture_entry_does_not_ask_when_the_grant_is_live():
    gate = FakePermissionService({_SR: "granted"})

    require_screen_recording("computer_use", gate=gate)

    assert gate.ensure_calls() == []


def test_a_grant_the_oracle_sees_does_not_ask_either():
    gate = _StubGate([PermissionState.NOT_GRANTED], deep=[PermissionState.GRANTED])

    require_screen_recording("computer_use", gate=gate)

    assert "ensure" not in gate.log


@pytest.mark.parametrize("outcome", ["pending", "needs_settings", "denied", "unavailable"])
def test_only_a_live_grant_proceeds(outcome):
    gate = FakePermissionService({_SR: outcome})

    with pytest.raises(ScreenCaptureRefused):
        require_screen_recording("computer_use", gate=gate)


@pytest.mark.asyncio
async def test_the_async_gesture_entry_uses_ensure_async():
    gate = FakePermissionService({_SR: "needs_settings"})

    with pytest.raises(ScreenCaptureRefused):
        await require_screen_recording_async("appshot", gate=gate)

    (call,) = gate.ensure_calls(_SR)
    assert call.method == "ensure_async"
    assert call.feature == "appshot"
    assert call.wait_s == 0.0


@pytest.mark.asyncio
async def test_the_async_gesture_entry_passes_when_granted():
    gate = FakePermissionService({_SR: "granted"})

    await require_screen_recording_async("screen_context", gate=gate)

    assert gate.ensure_calls() == []


# ---------------------------------------------------------------------------
# The pixel sanity check
# ---------------------------------------------------------------------------


def test_blank_detection_on_flat_textured_short_and_empty_frames():
    assert frame_is_blank(_SIZE, wallpaper_pixels(_SIZE)[1])
    assert not frame_is_blank(_SIZE, textured_pixels(_SIZE)[1])
    assert frame_is_blank(_SIZE, b"\x00" * 10)  # shorter than the size claims
    assert frame_is_blank((0, 0), b"")
    # A one-pixel edge on an otherwise flat frame is still content when sampled.
    sparse = bytearray(wallpaper_pixels(_SIZE)[1])
    for i in range(0, len(sparse), 3):
        sparse[i] = (i // 3) % 200  # a ramp in one channel
    assert not frame_is_blank(_SIZE, bytes(sparse))


@pytest.fixture(autouse=True)
def _fresh_evidence_cache():
    screen_access.reset_window_evidence_cache()
    yield
    screen_access.reset_window_evidence_cache()


def test_a_real_frame_costs_one_fresh_read_and_never_asks(monkeypatch):
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.NOT_GRANTED])
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (3, 1))

    verify_frame_is_real(_SIZE, textured_pixels(_SIZE)[1], feature="computer_use", gate=gate)

    # One preflight after the grab (the cache is dropped first): no deep read, no ask.
    assert gate.log == ["invalidate", "check"]


@pytest.mark.parametrize("pixels", [wallpaper_pixels, textured_pixels], ids=["flat", "textured"])
def test_a_frame_with_a_state_that_is_not_granted_is_refused_after_the_grab(pixels):
    # The entry gate ran before the grab; the revoke landed during it. Blank or not,
    # the frame is not a success and the episode is opened.
    from tests.fakes.fake_permission_service import make_result

    gate = _StubGate(
        [PermissionState.NOT_GRANTED],
        deep=[PermissionState.NOT_GRANTED],
        ensure=make_result(_SR, "needs_settings"),
    )

    with pytest.raises(ScreenCaptureRefused) as refused:
        verify_frame_is_real(_SIZE, pixels(_SIZE)[1], feature="computer_use", gate=gate)

    assert gate.log == ["invalidate", "check", "check_deep", "ensure"]
    assert refused.value.reason == "needs_settings"


def test_a_not_granted_frame_that_the_service_now_grants_is_still_not_trusted():
    from tests.fakes.fake_permission_service import make_result

    gate = _StubGate(
        [PermissionState.NOT_GRANTED],
        deep=[PermissionState.NOT_GRANTED],
        ensure=make_result(_SR, "granted"),
    )

    with pytest.raises(ScreenCaptureRefused):
        verify_frame_is_real(_SIZE, textured_pixels(_SIZE)[1], feature="computer_use", gate=gate)


def test_the_background_origin_is_passed_on_when_a_frame_is_refused():
    gate = FakePermissionService({_SR: "needs_settings"})

    with pytest.raises(ScreenCaptureRefused):
        verify_frame_is_real(
            _SIZE,
            textured_pixels(_SIZE)[1],
            feature="computer_use",
            interactive=False,
            gate=gate,
        )

    (call,) = gate.ensure_calls(_SR)
    assert (call.feature, call.interactive, call.wait_s) == ("computer_use", False, 0.0)


def test_off_macos_a_frame_reads_no_window_list_and_asks_nothing(monkeypatch):
    gate = FakePermissionService({_SR: "not_required"})
    monkeypatch.setattr(
        screen_access,
        "_window_evidence",
        lambda: (_ for _ in ()).throw(AssertionError("no window list off macOS")),
    )

    verify_frame_is_real(_SIZE, wallpaper_pixels(_SIZE)[1], feature="computer_use", gate=gate)

    assert gate.ensure_calls() == []


def test_a_blank_frame_under_a_grant_only_the_oracle_sees_is_still_checked(monkeypatch):
    # The frozen preflight says "not granted" (BUG-161) but the grant works: the
    # sanity check must not skip just because the cheap read is stale-negative.
    gate = _StubGate(
        [PermissionState.NOT_GRANTED], deep=[PermissionState.GRANTED, PermissionState.GRANTED]
    )
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (3, 0))

    with pytest.raises(ScreenCaptureRefused):
        verify_frame_is_real(_SIZE, wallpaper_pixels(_SIZE)[1], feature="computer_use", gate=gate)


@pytest.mark.parametrize("pixels", [wallpaper_pixels, textured_pixels], ids=["flat", "textured"])
def test_a_frame_with_windows_but_no_readable_title_is_refused(monkeypatch, pixels):
    # A photographic wallpaper is not a flat frame: the verdict must not wait for blankness.
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.GRANTED])
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (4, 0))

    with pytest.raises(ScreenCaptureRefused) as refused:
        verify_frame_is_real(_SIZE, pixels(_SIZE)[1], feature="computer_use", gate=gate)

    assert refused.value.reason == "needs_settings"


@pytest.mark.parametrize(
    "evidence", [(4, 2), (0, 0), None], ids=["titles", "no_windows", "unknown"]
)
@pytest.mark.parametrize("pixels", [wallpaper_pixels, textured_pixels], ids=["flat", "textured"])
def test_a_frame_is_accepted_when_the_window_list_does_not_contradict_the_grant(
    monkeypatch, evidence, pixels
):
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.GRANTED])
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: evidence)

    verify_frame_is_real(_SIZE, pixels(_SIZE)[1], feature="computer_use", gate=gate)

    assert gate.log == ["invalidate", "check"]


def test_the_window_list_is_read_once_per_few_seconds_not_once_per_frame(monkeypatch):
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.GRANTED])
    reads: list[int] = []
    clock = {"now": 100.0}

    def evidence():
        reads.append(1)
        return (3, 1)

    monkeypatch.setattr(screen_access, "_window_evidence", evidence)
    monkeypatch.setattr(screen_access, "_monotonic", lambda: clock["now"])
    frame = textured_pixels(_SIZE)[1]

    for _ in range(5):
        verify_frame_is_real(_SIZE, frame, feature="computer_use", gate=gate)
    assert len(reads) == 1

    clock["now"] += screen_access._EVIDENCE_TTL_S + 0.1
    verify_frame_is_real(_SIZE, frame, feature="computer_use", gate=gate)
    assert len(reads) == 2


def test_a_flat_frame_accepts_only_a_younger_verdict(monkeypatch):
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.GRANTED])
    reads: list[int] = []
    clock = {"now": 100.0}

    def evidence():
        reads.append(1)
        return (3, 1)

    monkeypatch.setattr(screen_access, "_window_evidence", evidence)
    monkeypatch.setattr(screen_access, "_monotonic", lambda: clock["now"])
    verify_frame_is_real(_SIZE, textured_pixels(_SIZE)[1], feature="computer_use", gate=gate)
    clock["now"] += screen_access._BLANK_EVIDENCE_TTL_S + 0.1

    verify_frame_is_real(_SIZE, wallpaper_pixels(_SIZE)[1], feature="computer_use", gate=gate)

    assert len(reads) == 2


def test_an_unusable_verdict_is_never_cached_so_a_fixed_grant_is_believed_at_once(monkeypatch):
    gate = _StubGate([PermissionState.GRANTED], deep=[PermissionState.GRANTED])
    answers = iter([(4, 0), (4, 2)])
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: next(answers))
    frame = textured_pixels(_SIZE)[1]

    with pytest.raises(ScreenCaptureRefused):
        verify_frame_is_real(_SIZE, frame, feature="computer_use", gate=gate)
    verify_frame_is_real(_SIZE, frame, feature="computer_use", gate=gate)  # no raise


def test_the_blank_sample_does_not_alias_on_power_of_two_native_sizes():
    # 2560x1600 sampled with a plain count // 2048 step only ever hit 32 columns: a
    # frame whose content sits in the other columns read as blank.
    width, height = 2560, 1600
    data = bytearray(width * height * 3)
    for y in range(0, height, 2):
        for x in range(1, width, 2):  # content in odd columns only
            data[(y * width + x) * 3] = 255
    assert not frame_is_blank((width, height), bytes(data))


def test_the_default_window_evidence_is_none_without_the_native_bridge():
    # No Quartz here: "no evidence", never an exception.
    assert screen_access._default_window_evidence() is None
