"""The update progress tracker: the percentage behind "Updating 70%".

What matters here is not that a number exists but that it is *trustworthy*. The
bar is what a user decides "is this stuck, should I kill it?" from, so these
tests pin the three properties that make it worth looking at:

* it moves on real evidence (download bytes, git's own object counters),
* it never rewinds, even though git reports two separate 0→100 % counters,
* and it always reaches a terminal state — a run that dies without a verdict
  would leave the button reading "Updating 42%" forever.
"""

from __future__ import annotations

import pytest

from jarvis.ui.web.update_routes import (
    INSTALL_KIND_FROZEN,
    INSTALL_KIND_MANAGED,
    PHASE_DOWNLOADING,
    PHASE_FAILED,
    PHASE_IDLE,
    PHASE_INSTALLING,
    PHASE_READY,
    PHASE_RESOLVING,
    PHASE_VERIFYING,
    _human_bytes,
    _on_git_progress,
    _progress,
    _UpdateProgress,
)


@pytest.fixture
def tracker() -> _UpdateProgress:
    return _UpdateProgress()


# --------------------------------------------------------------------------- #
# The phase model
# --------------------------------------------------------------------------- #
def test_idle_before_anything_runs(tracker: _UpdateProgress) -> None:
    snapshot = tracker.snapshot()
    assert snapshot["phase"] == PHASE_IDLE
    assert snapshot["percent"] == 0
    assert snapshot["active"] is False


def test_download_percentage_follows_the_bytes(tracker: _UpdateProgress) -> None:
    """Half the bytes must land halfway through the download's window."""
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.enter(PHASE_DOWNLOADING)
    start = tracker.percent
    tracker.advance(PHASE_DOWNLOADING, 1.0)
    end = tracker.percent

    tracker2 = _UpdateProgress()
    tracker2.begin(INSTALL_KIND_FROZEN)
    tracker2.advance(PHASE_DOWNLOADING, 0.5)
    assert tracker2.percent == pytest.approx((start + end) / 2, abs=1)


def test_phases_run_forward_to_a_hundred(tracker: _UpdateProgress) -> None:
    tracker.begin(INSTALL_KIND_FROZEN)
    seen = [tracker.percent]
    for phase in (PHASE_RESOLVING, PHASE_DOWNLOADING, PHASE_VERIFYING, PHASE_INSTALLING):
        tracker.advance(phase, 1.0)
        seen.append(tracker.percent)
    tracker.finish(version="1.3.0", restart_required=False)
    seen.append(tracker.percent)

    assert seen == sorted(seen), f"the bar went backwards: {seen}"
    assert seen[-1] == 100


def test_percentage_never_rewinds(tracker: _UpdateProgress) -> None:
    """git restarts its counter at 0 % for delta resolution; the bar must not."""
    tracker.begin(INSTALL_KIND_MANAGED)
    tracker.advance(PHASE_DOWNLOADING, 0.9)
    high = tracker.percent
    tracker.advance(PHASE_DOWNLOADING, 0.1)
    assert tracker.percent == high


def test_begin_is_the_only_reset(tracker: _UpdateProgress) -> None:
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.advance(PHASE_INSTALLING, 1.0)
    assert tracker.percent == 100
    tracker.begin(INSTALL_KIND_FROZEN)
    assert tracker.percent == 0
    assert tracker.phase == PHASE_RESOLVING


def test_out_of_range_fractions_are_clamped(tracker: _UpdateProgress) -> None:
    """A bogus Content-Length must not produce a 400 %-wide bar."""
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.advance(PHASE_DOWNLOADING, 4.0)
    assert tracker.percent <= 100
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.advance(PHASE_DOWNLOADING, -1.0)
    assert tracker.percent >= 0


def test_the_two_install_kinds_weight_the_phases_differently(
    tracker: _UpdateProgress,
) -> None:
    """A frozen install is dominated by its download; a managed one is not."""
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.advance(PHASE_DOWNLOADING, 1.0)
    frozen_after_download = tracker.percent

    managed = _UpdateProgress()
    managed.begin(INSTALL_KIND_MANAGED)
    managed.advance(PHASE_DOWNLOADING, 1.0)

    assert frozen_after_download > managed.percent


def test_an_unknown_phase_leaves_the_bar_alone(tracker: _UpdateProgress) -> None:
    """A future phase id must never zero the bar an install is being judged by."""
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.advance(PHASE_DOWNLOADING, 1.0)
    before = tracker.percent
    tracker.advance("something-new", 0.5)
    assert tracker.percent == before


# --------------------------------------------------------------------------- #
# Terminal states
# --------------------------------------------------------------------------- #
def test_finish_reports_ready_and_stops_being_active(tracker: _UpdateProgress) -> None:
    tracker.begin(INSTALL_KIND_MANAGED)
    tracker.finish(version="1.3.0", restart_required=True)
    snapshot = tracker.snapshot()
    assert snapshot["phase"] == PHASE_READY
    assert snapshot["percent"] == 100
    assert snapshot["active"] is False
    assert snapshot["restart_required"] is True
    assert snapshot["version"] == "1.3.0"


def test_failure_keeps_the_percentage_and_carries_the_reason(
    tracker: _UpdateProgress,
) -> None:
    """Where it died is diagnostic; zeroing it would throw that away."""
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.advance(PHASE_DOWNLOADING, 0.5)
    midway = tracker.percent
    tracker.fail("git fetch failed: could not resolve host")

    snapshot = tracker.snapshot()
    assert snapshot["phase"] == PHASE_FAILED
    assert snapshot["percent"] == midway
    assert snapshot["active"] is False
    assert "could not resolve host" in str(snapshot["error"])


def test_a_failure_message_cannot_flood_the_ui(tracker: _UpdateProgress) -> None:
    tracker.begin(INSTALL_KIND_FROZEN)
    tracker.fail("x" * 5000)
    assert len(str(tracker.snapshot()["error"])) <= 300


def test_finish_clears_an_earlier_error(tracker: _UpdateProgress) -> None:
    """A retry that works must not keep showing the previous run's failure."""
    tracker.begin(INSTALL_KIND_MANAGED)
    tracker.fail("network unreachable")
    tracker.begin(INSTALL_KIND_MANAGED)
    tracker.finish(version="1.3.0", restart_required=True)
    assert tracker.snapshot()["error"] is None


# --------------------------------------------------------------------------- #
# git's progress lines
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "line",
    [
        "Receiving objects:  43% (430/1000), 1.2 MiB | 500 KiB/s",
        "Receiving objects: 100% (1000/1000), 12.00 MiB | 3.00 MiB/s, done.",
        "Resolving deltas:  50% (6/12)",
    ],
)
def test_real_git_lines_move_the_bar(line: str) -> None:
    _progress.begin(INSTALL_KIND_MANAGED)
    _progress.enter(PHASE_DOWNLOADING)
    before = _progress.percent
    _on_git_progress(line)
    assert _progress.percent > before


def test_git_noise_is_ignored() -> None:
    _progress.begin(INSTALL_KIND_MANAGED)
    _progress.enter(PHASE_DOWNLOADING)
    before = _progress.percent
    for line in (
        "remote: Enumerating objects: 1000, done.",
        "From https://github.com/example/repo",
        " * [new tag]  v1.3.0 -> v1.3.0",
        "",
    ):
        _on_git_progress(line)
    assert _progress.percent == before


def test_delta_resolution_ranks_above_receiving() -> None:
    """The two counters are sequential stages, not competing measurements."""
    _progress.begin(INSTALL_KIND_MANAGED)
    _progress.enter(PHASE_DOWNLOADING)
    _on_git_progress("Receiving objects: 100% (1000/1000), done.")
    received = _progress.percent
    _on_git_progress("Resolving deltas: 100% (12/12), done.")
    assert _progress.percent > received


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 B"), (512, "512 B"), (128_000, "125 KB"), (134_217_728, "128.0 MB")],
)
def test_human_bytes(count: int, expected: str) -> None:
    assert _human_bytes(count) == expected


def test_human_bytes_stays_short() -> None:
    """It renders inside a top-bar pill, so it must never grow unbounded."""
    assert len(_human_bytes(1_500_000_000)) <= 10
