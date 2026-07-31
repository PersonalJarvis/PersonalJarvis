"""Guards for the third-party-input reachability probe.

The failure this pins down (live forensic 2026-07-25): the desktop app was
running elevated, so Windows UIPI silently discarded every synthetic keystroke
and automation query coming from ordinary user software. Dictation and
voice-typing apps, text expanders, and password-manager auto-type all went dead
INSIDE the desktop window while working everywhere else — with no error
anywhere, because Windows does not report a UIPI drop to the sender.

These tests never read the host's real privilege state: they inject it, so the
same assertions hold on a maintainer's elevated Windows box, a CI container, and
a Mac.
"""

from __future__ import annotations

import os

import pytest

from jarvis.platform.input_isolation import (
    InputIsolationReason,
    describe_input_isolation,
    windows_process_is_elevated,
)


def _report(platform: str, elevated: bool | None):
    return describe_input_isolation(
        _platform=lambda: platform,
        _elevated=lambda: elevated,
        _euid=lambda: 1000,
    )


class TestWindows:
    def test_elevated_window_is_reported_as_blocked(self):
        report = _report("win32", True)
        assert report.blocked is True
        assert report.reason is InputIsolationReason.ELEVATED
        assert report.can_restart_unelevated is True

    def test_normal_window_is_reachable(self):
        report = _report("win32", False)
        assert report.blocked is False
        assert report.reason is InputIsolationReason.NONE
        assert report.can_restart_unelevated is False

    def test_undecidable_privilege_state_is_reported_honestly(self):
        """An unreadable token must never masquerade as 'all good'."""
        report = _report("win32", None)
        assert report.reason is InputIsolationReason.UNKNOWN
        # Unknown is not a defect claim: we do not nag the user on a guess.
        assert report.blocked is False
        assert report.can_restart_unelevated is False


class TestPosix:
    @pytest.mark.parametrize("platform", ["darwin", "linux"])
    def test_ordinary_user_session_is_reachable(self, platform):
        report = describe_input_isolation(
            _platform=lambda: platform,
            _elevated=lambda: None,
            _euid=lambda: 1000,
        )
        assert report.blocked is False
        assert report.reason is InputIsolationReason.NONE

    @pytest.mark.parametrize("platform", ["darwin", "linux"])
    def test_root_session_is_flagged_without_promising_a_self_repair(self, platform):
        """Running as root isolates us the same way, but dropping back to the
        original user is not something we can do safely — say so instead of
        offering a button that cannot work."""
        report = describe_input_isolation(
            _platform=lambda: platform,
            _elevated=lambda: None,
            _euid=lambda: 0,
        )
        assert report.blocked is True
        assert report.reason is InputIsolationReason.ROOT
        assert report.can_restart_unelevated is False

    def test_windows_probe_is_not_consulted_on_posix(self):
        """The ctypes token probe must never run off-Windows (it would raise)."""

        def _explode() -> bool:
            raise AssertionError("the Windows token probe ran on a POSIX host")

        report = describe_input_isolation(
            _platform=lambda: "linux", _elevated=_explode, _euid=lambda: 1000
        )
        assert report.reason is InputIsolationReason.NONE


class TestMessaging:
    def test_blocked_report_names_the_affected_software_categories(self):
        """The user-facing text must be recognizable to someone whose dictation
        app just went quiet — not a lecture about integrity levels."""
        summary = _report("win32", True).summary.lower()
        assert "dictation" in summary
        assert "administrator" in summary or "admin" in summary

    def test_healthy_report_carries_no_remedy_noise(self):
        assert _report("win32", False).remedy == ""

    def test_report_is_json_serializable_for_the_api(self):
        payload = _report("win32", True).to_dict()
        assert payload["blocked"] is True
        assert payload["reason"] == "elevated"
        assert isinstance(payload["summary"], str)
        assert isinstance(payload["can_restart_unelevated"], bool)


class TestRealHostProbe:
    def test_probe_never_raises_on_any_host(self):
        """Import-clean + crash-free everywhere: a diagnostic must not be the
        thing that takes the app down."""
        assert windows_process_is_elevated() in (True, False, None)
        report = describe_input_isolation()
        assert isinstance(report.blocked, bool)

    @pytest.mark.skipif(os.name != "nt", reason="Windows token probe")
    def test_probe_agrees_with_an_independent_windows_reference(self):
        """Cross-check the token read against shell32's own answer.

        This exists because of a real 2026-07-25 miss: undeclared ctypes
        prototypes truncated the 64-bit process handle, `OpenProcessToken`
        failed, and the probe returned `None` on EVERY Windows host. That is
        indistinguishable from our deliberate "privilege state unreadable"
        fail-open, so the banner would simply never appear and the whole fix
        would be silently inert. Only a live check caught it.

        `IsUserAnAdmin` answers the same question through an unrelated code
        path, so a disagreement — including `None` where it says False — means
        our probe is broken, not merely unsure.
        """
        import ctypes  # noqa: PLC0415

        reference = bool(ctypes.windll.shell32.IsUserAnAdmin())
        assert windows_process_is_elevated() == reference
