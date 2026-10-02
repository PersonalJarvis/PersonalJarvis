"""Screen Recording behaviour of Computer-Use capture (just-in-time permissions).

Every test runs the REAL permission service on a REAL ``SystemPermissionPort`` that
sits on ``FakeTCC`` (a stateful model of macOS privacy), with ``FakeScreenGrab``
producing the pixels. Nothing here ran on a real Mac: the call log of the fake is
the proof of what was (not) asked, and each simulated macOS behaviour is labelled
documented or unverified in ``tests/fakes/fake_tcc.py``.

The rules under test: the perception entry (``capture_stable_frame``) may ASK, once;
a helper (``grab_region``, ``grab_visual_probe``) never asks and degrades; a denial
is a stable state (no second request); a frame that is wallpaper-only while the state
claims GRANTED is never a success; off macOS nothing is read or requested.
"""

from __future__ import annotations

import contextlib

import pytest

pytest.importorskip("PIL", reason="pillow required for capture tests")

import jarvis.cu.capture as capture_mod
from jarvis.cu.capture import (
    capture_stable_frame,
    grab_region,
    grab_visual_probe,
    grabber_for,
)
from jarvis.cu.geometry import MonitorInfo
from jarvis.platform import screen_access, window_capture
from jarvis.platform.permission_service import PermissionService, get_permission_service
from jarvis.platform.permissions import PermissionId, PermissionState
from jarvis.platform.screen_access import ScreenCaptureRefused
from tests.fakes.fake_permission_service import FakePermissionService
from tests.fakes.fake_screen_pixels import (
    pixels_grabber,
    textured_pixels,
    wallpaper_pixels,
)
from tests.fakes.fake_tcc import (
    DialogPolicy,
    FakeScreenGrab,
    FakeTCC,
    TccService,
    install_port,
    make_non_darwin_port,
)

pytestmark = pytest.mark.real_tcc_gate

_SR = PermissionId.SCREEN_RECORDING
_SIZE = (192, 108)


def _monitor(**kw) -> MonitorInfo:
    defaults = dict(left=0, top=0, width=_SIZE[0], height=_SIZE[1])
    defaults.update(kw)
    return MonitorInfo(**defaults)


def _darwin(monkeypatch, **tcc_kwargs) -> FakeTCC:
    tcc = FakeTCC(**tcc_kwargs)
    install_port(monkeypatch, tcc.port("darwin"))
    return tcc


def _capture(grab, **kw):
    return capture_stable_frame(_monitor(), grab=grab, sleep=lambda _s: None, **kw)


@pytest.fixture(autouse=True)
def _fresh_evidence_cache():
    screen_access.reset_window_evidence_cache()
    yield
    screen_access.reset_window_evidence_cache()


# ---------------------------------------------------------------------------
# The perception entry asks (once); a decision is a stable state
# ---------------------------------------------------------------------------


def test_first_frame_asks_once_and_the_answer_is_not_a_grant(monkeypatch):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    screen = FakeScreenGrab(tcc)

    with pytest.raises(ScreenCaptureRefused) as first:
        _capture(pixels_grabber(screen))
    with pytest.raises(ScreenCaptureRefused):
        _capture(pixels_grabber(screen))

    # Exactly one native request across two attempts, and the capture itself never
    # touched the screen (no implicit prompt, no frame): the dialog is still open.
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.implicit_prompts() == []
    assert screen.frames == []
    assert str(first.value).startswith("[permission_needed:screen_recording] ")
    assert first.value.agent_detail == str(first.value)
    assert first.value.user_detail
    assert first.value.permission == "screen_recording"


def test_the_frame_after_the_ask_proceeds_once_the_user_allowed(monkeypatch):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.ALLOW)
    screen = FakeScreenGrab(tcc)

    # The preflight is frozen per process (BUG-161), so the very first frame may still
    # be refused while the grant is only visible to the window-title oracle; the next
    # one must go through, with no second request.
    with contextlib.suppress(ScreenCaptureRefused):
        _capture(pixels_grabber(screen))
    frame = _capture(pixels_grabber(screen))

    assert frame.stable is True
    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.implicit_prompts() == []
    assert screen.frames and not any(item.wallpaper_only for item in screen.frames)
    # The request's return value is never the evidence: the grant was READ back.
    assert get_permission_service().check_deep(_SR) is PermissionState.GRANTED


def test_denied_is_a_stable_state_with_no_second_request(monkeypatch):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.DENY)
    screen = FakeScreenGrab(tcc)

    with pytest.raises(ScreenCaptureRefused):
        _capture(pixels_grabber(screen))
    with pytest.raises(ScreenCaptureRefused) as second:
        _capture(pixels_grabber(screen))

    assert len(tcc.requests(TccService.SCREEN_RECORDING)) == 1
    assert tcc.ignored_requests() == []
    assert screen.frames == []
    # Screen Recording has no tri-state: a decline reads "off", and the refusal
    # says where to turn it on.
    assert second.value.reason == "needs_settings"
    assert "turn Personal Jarvis on" in second.value.user_detail


def test_a_grant_given_mid_session_is_seen_without_a_restart(monkeypatch):
    # The preflight is frozen per process (BUG-161): the window-title oracle is what
    # sees the grant the user just gave in System Settings.
    tcc = _darwin(monkeypatch)
    screen = FakeScreenGrab(tcc)
    tcc.deny(TccService.SCREEN_RECORDING)
    tcc.grant(TccService.SCREEN_RECORDING)

    frame = _capture(pixels_grabber(screen))

    assert frame.stable is True
    assert tcc.requests() == []


def test_a_granted_frame_never_asks_and_reads_the_state_once_per_frame(monkeypatch):
    gate = FakePermissionService()
    monkeypatch.setattr(screen_access, "permission_gate", lambda: gate)
    grabs = {"n": 0}

    def grab(_bbox):
        grabs["n"] += 1
        return textured_pixels(_SIZE)

    frame = _capture(grab)

    assert frame.stable is True
    assert grabs["n"] == 2  # the stability loop re-grabbed
    assert gate.ensure_calls() == []
    # One read for the entry and one after the final grab (the revoke check of the
    # pixel sanity check). Not one per stability re-grab.
    assert len(gate.check_calls(_SR)) == 2


# ---------------------------------------------------------------------------
# Helpers never ask
# ---------------------------------------------------------------------------


def test_region_helper_degrades_silently_when_not_determined(monkeypatch):
    tcc = _darwin(monkeypatch)
    calls = {"grab": 0}

    def grab(_bbox):
        calls["grab"] += 1
        return textured_pixels((4, 4))

    got = grab_region({"left": 0, "top": 0, "width": 4, "height": 4}, grab=grab)

    assert got is None
    assert calls["grab"] == 0
    tcc.assert_no_prompts()


def test_visual_probe_helper_degrades_silently_when_not_determined(monkeypatch):
    tcc = _darwin(monkeypatch)

    got = grab_visual_probe({"left": 0, "top": 0, "width": 8, "height": 8}, point=(2, 2))

    assert got is None
    tcc.assert_no_prompts()


def test_helpers_do_not_ask_after_a_denial_either(monkeypatch):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.DENY)
    with pytest.raises(ScreenCaptureRefused):
        _capture(pixels_grabber(FakeScreenGrab(tcc)))
    before = len(tcc.requests())

    assert grab_region({"left": 0, "top": 0, "width": 4, "height": 4}) is None
    assert grab_visual_probe({"left": 0, "top": 0, "width": 8, "height": 8}) is None

    assert len(tcc.requests()) == before


def test_region_helper_grabs_when_the_grant_is_live(monkeypatch):
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])
    region = textured_pixels((4, 4))

    got = grab_region({"left": 0, "top": 0, "width": 4, "height": 4}, grab=lambda _b: region)

    assert got == region


def test_the_engine_gate_is_silent_and_sees_a_mid_session_grant(monkeypatch):
    tcc = _darwin(monkeypatch)

    with pytest.raises(ScreenCaptureRefused) as refused:
        capture_mod._require_macos_screen_recording_permission()
    assert str(refused.value).startswith("[permission_needed:screen_recording] ")
    tcc.assert_no_prompts()

    tcc.grant(TccService.SCREEN_RECORDING)
    capture_mod._require_macos_screen_recording_permission()  # no raise
    tcc.assert_no_prompts()


# ---------------------------------------------------------------------------
# Wallpaper-only frames are never a success
# ---------------------------------------------------------------------------


def test_wallpaper_frame_after_a_revoke_opens_an_episode_and_is_no_success(monkeypatch):
    # The state read says GRANTED (cached), then the user revokes in System Settings
    # while the grab runs: macOS hands back the wallpaper and no error.
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    screen = FakeScreenGrab(tcc)
    assert get_permission_service().check(_SR) is PermissionState.GRANTED
    grabs = {"n": 0}

    def grab(bbox):
        grabs["n"] += 1
        if grabs["n"] == 1:
            tcc.deny(TccService.SCREEN_RECORDING)
        return pixels_grabber(screen)(bbox)

    with pytest.raises(ScreenCaptureRefused) as refused:
        _capture(grab)

    assert refused.value.reason == "needs_settings"
    assert screen.frames and all(item.wallpaper_only for item in screen.frames)
    episodes = get_permission_service().outstanding()
    assert [(e.permissions, e.feature, e.origin) for e in episodes] == [
        (("screen_recording",), "computer_use", "user")
    ]
    # macOS does not re-ask after a decision: no dialog was shown for the revoked grant.
    assert tcc.dialogs_shown() == []


def test_a_revoke_that_lands_after_the_cached_grant_expired_is_no_success(monkeypatch):
    # The grab itself takes longer than the 1 s granted cache (the stability loop
    # runs up to 1.2 s, a native capture timeout 3 s): the user revokes meanwhile.
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    clock = {"now": 0.0}
    service = PermissionService(clock=lambda: clock["now"])
    monkeypatch.setattr(screen_access, "permission_gate", lambda: service)
    screen = FakeScreenGrab(tcc)
    grabs = {"n": 0}

    def grab(bbox):
        grabs["n"] += 1
        if grabs["n"] == 1:
            tcc.deny(TccService.SCREEN_RECORDING)
            clock["now"] += 1.5  # the cached GRANTED is long expired
        return pixels_grabber(screen)(bbox)

    try:
        with pytest.raises(ScreenCaptureRefused) as refused:
            _capture(grab)
        assert refused.value.agent_detail.startswith("[permission_needed:screen_recording] ")
        assert screen.frames and all(item.wallpaper_only for item in screen.frames)
        assert [e.feature for e in service.outstanding()] == ["computer_use"]
    finally:
        service._shutdown()


@pytest.mark.parametrize("photo", [False, True], ids=["flat", "photographic"])
def test_wallpaper_with_an_unusable_grant_refuses_and_names_the_permission(monkeypatch, photo):
    # State GRANTED everywhere, yet the capture is the wallpaper and other apps'
    # windows are on screen without a readable title: the grant is not usable. The
    # macOS default wallpaper is a photograph, so a flat-colour check alone misses it.
    tcc = _darwin(monkeypatch, preflight_frozen=(), screen_grant_needs_relaunch=True)
    tcc.grant(TccService.SCREEN_RECORDING)
    screen = FakeScreenGrab(tcc)
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (2, 0))

    with pytest.raises(ScreenCaptureRefused) as refused:
        _capture(pixels_grabber(screen, photo_wallpaper=photo))

    assert refused.value.agent_detail.startswith("[permission_needed:screen_recording] ")
    assert all(item.wallpaper_only for item in screen.frames)


def test_an_unusable_grant_opens_one_restart_hint_episode_and_a_real_frame_ends_it(monkeypatch):
    # A REAL failed attempt while the state reads granted: the honest advice is "quit
    # and reopen", offered through a user-origin episode. Nothing restarts, nothing is
    # asked, and the capture itself is still refused.
    tcc = _darwin(monkeypatch, preflight_frozen=(), screen_grant_needs_relaunch=True)
    tcc.grant(TccService.SCREEN_RECORDING)
    screen = FakeScreenGrab(tcc)
    evidence = {"value": (2, 0)}
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: evidence["value"])

    with pytest.raises(ScreenCaptureRefused) as refused:
        _capture(pixels_grabber(screen))
    with pytest.raises(ScreenCaptureRefused):
        _capture(pixels_grabber(screen))  # the same failure again: still one episode

    assert refused.value.reason == "restart_hint"
    assert "quit and reopened" in refused.value.user_detail
    assert refused.value.agent_detail.startswith("[permission_needed:screen_recording] ")
    (episode,) = get_permission_service().outstanding()
    assert (episode.permissions, episode.feature, episode.reason, episode.origin) == (
        ("screen_recording",),
        "computer_use",
        "restart_hint",
        "user",
    )
    assert tcc.requests() == [] and tcc.dialogs_shown() == []  # a hint never asks

    # The grant works now (the user restarted, or the entry caught up): a frame that
    # shows readable titles of other apps' windows ends the episode, no more nagging.
    evidence["value"] = (3, 2)
    _capture(lambda _b: textured_pixels(_SIZE))
    get_permission_service().refresh_episodes()
    assert get_permission_service().outstanding() == []


def test_a_background_capture_with_an_unusable_grant_opens_no_user_card(monkeypatch):
    tcc = _darwin(monkeypatch, preflight_frozen=(), screen_grant_needs_relaunch=True)
    tcc.grant(TccService.SCREEN_RECORDING)
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: (2, 0))
    size, pixels = wallpaper_pixels(_SIZE)

    with pytest.raises(ScreenCaptureRefused) as refused:
        screen_access.verify_frame_is_real(
            size, pixels, feature="screen_context", interactive=False
        )

    assert refused.value.reason == "needs_settings"
    assert get_permission_service().outstanding() == []
    assert tcc.requests() == []


def test_the_window_list_is_not_read_again_for_every_frame(monkeypatch):
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    reads: list[int] = []

    def evidence():
        reads.append(1)
        return (3, 2)

    monkeypatch.setattr(screen_access, "_window_evidence", evidence)

    for _ in range(3):
        _capture(lambda _b: textured_pixels(_SIZE))

    assert len(reads) == 1


@pytest.mark.parametrize(
    "evidence", [(3, 1), (0, 0), None], ids=["titles", "no_windows", "unknown"]
)
def test_a_genuinely_blank_screen_is_accepted_while_the_grant_works(monkeypatch, evidence):
    tcc = _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING], preflight_frozen=())
    monkeypatch.setattr(screen_access, "_window_evidence", lambda: evidence)

    frame = _capture(lambda _b: wallpaper_pixels(_SIZE))

    assert frame.stable is True
    tcc.assert_no_prompts()


def test_pixel_check_samples_bgrx_frames_like_rgb(monkeypatch):
    # mss hands out BGRX (4 bytes per pixel); a textured frame must not read as blank.
    assert screen_access.frame_is_blank(_SIZE, b"\x10\x20\x30\x00" * (192 * 108), bytes_per_pixel=4)
    rgb = textured_pixels(_SIZE)[1]
    bgrx = bytearray()
    for i in range(0, len(rgb), 3):
        bgrx += bytes((rgb[i + 2], rgb[i + 1], rgb[i], 0))
    assert not screen_access.frame_is_blank(_SIZE, bgrx, bytes_per_pixel=4)


# ---------------------------------------------------------------------------
# A native window capture that times out while macOS may be asking
# ---------------------------------------------------------------------------


def _window_target() -> MonitorInfo:
    return _monitor(name="window:x", window_handle=9)


def _pending_native(monkeypatch):
    def native(_handle, _bbox):
        window_capture._failure.reason = "pending_confirmation"
        return None

    monkeypatch.setattr(window_capture, "grab_window", native)


def test_pending_native_capture_is_not_a_denial_while_the_grant_is_live(monkeypatch):
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])
    _pending_native(monkeypatch)
    fallback = textured_pixels((80, 60))
    monkeypatch.setattr(capture_mod, "mss_grab", lambda _bbox: fallback)
    bbox = {"left": 0, "top": 0, "width": 80, "height": 60}

    assert grabber_for(_window_target())(bbox) == fallback


def test_pending_native_capture_while_a_request_is_open_says_macos_may_be_asking(monkeypatch):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.NEVER_ANSWERED)
    get_permission_service().ensure(_SR, feature="computer_use", wait_s=0)
    assert tcc.dialog_open(TccService.SCREEN_RECORDING)
    _pending_native(monkeypatch)
    monkeypatch.setattr(
        capture_mod,
        "mss_grab",
        lambda _bbox: (_ for _ in ()).throw(AssertionError("no wallpaper grab while pending")),
    )
    bbox = {"left": 0, "top": 0, "width": 80, "height": 60}

    with pytest.raises(ScreenCaptureRefused) as refused:
        grabber_for(_window_target())(bbox)

    assert refused.value.reason == "pending"
    # The Screen Recording dialog has no Allow button: the text sends the user to
    # System Settings and never tells them to "choose Allow".
    assert "macOS may be showing a dialog" in refused.value.user_detail
    assert "turn Personal Jarvis on" in refused.value.user_detail
    assert "Allow" not in refused.value.user_detail
    assert len(tcc.requests()) == 1  # nothing new was asked


def _native_fails_with(monkeypatch, reason):
    def native(_handle, _bbox):
        window_capture._failure.reason = reason
        return None

    monkeypatch.setattr(window_capture, "grab_window", native)


def test_a_plain_timeout_the_state_does_not_explain_refuses_instead_of_grabbing_wallpaper(
    monkeypatch,
):
    tcc = _darwin(monkeypatch, default_policy=DialogPolicy.DENY)
    get_permission_service().ensure(_SR, feature="computer_use", wait_s=0)
    _native_fails_with(monkeypatch, "timeout")
    monkeypatch.setattr(
        capture_mod,
        "mss_grab",
        lambda _bbox: (_ for _ in ()).throw(AssertionError("no wallpaper grab after a denial")),
    )
    before = len(tcc.requests())
    bbox = {"left": 0, "top": 0, "width": 80, "height": 60}

    with pytest.raises(ScreenCaptureRefused) as refused:
        grabber_for(_window_target())(bbox)

    assert refused.value.reason == "needs_settings"
    assert len(tcc.requests()) == before


def test_a_timeout_with_a_live_grant_is_logged_and_carries_on(monkeypatch, caplog):
    _darwin(monkeypatch, granted=[TccService.SCREEN_RECORDING])
    _native_fails_with(monkeypatch, "pending_confirmation")
    fallback = textured_pixels((80, 60))
    monkeypatch.setattr(capture_mod, "mss_grab", lambda _bbox: fallback)
    bbox = {"left": 0, "top": 0, "width": 80, "height": 60}

    with caplog.at_level("WARNING", logger=capture_mod.logger.name):
        assert grabber_for(_window_target())(bbox) == fallback

    assert any("macOS may be waiting for a confirmation" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Off macOS nothing changes
# ---------------------------------------------------------------------------


def test_non_darwin_capture_reads_and_requests_nothing(monkeypatch):
    port, tcc = make_non_darwin_port("linux")
    install_port(monkeypatch, port)
    # A flat frame is fine here: the wallpaper check is a macOS-only trap.
    frame = _capture(lambda _b: wallpaper_pixels(_SIZE))

    assert frame.stable is True
    region = wallpaper_pixels((4, 4))
    assert grab_region({"left": 0, "top": 0, "width": 4, "height": 4}, grab=lambda _b: region)
    capture_mod._require_macos_screen_recording_permission()
    tcc.assert_silent()
    assert get_permission_service().outstanding() == []
