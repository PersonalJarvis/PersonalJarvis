"""Guards for the measured-pace model (:mod:`jarvis.ultrawiki.throughput`).

The defect this module exists to prevent is a screen that states a backlog
without stating how long it takes, so the tests are mostly about the three
ways the answer is allowed to be "I do not know" — too early, standing still,
and deliberately parked. Every one of those used to render as either silence
or a confident number, and both were wrong in the same direction.
"""

from __future__ import annotations

from jarvis.ultrawiki.throughput import (
    MIN_SAMPLE_SPAN_S,
    ThroughputTracker,
    build_throughput,
    format_duration,
)


class TestThroughputTracker:
    def test_no_rate_before_the_minimum_observation_span(self) -> None:
        """A first-minute extrapolation is the defect, not the feature."""
        tracker = ThroughputTracker()
        tracker.sample(0.0, 0)
        tracker.sample(MIN_SAMPLE_SPAN_S - 1.0, 500)
        assert tracker.rate_per_second() is None

    def test_measures_a_steady_rate(self) -> None:
        tracker = ThroughputTracker()
        tracker.sample(0.0, 0)
        tracker.sample(200.0, 100)
        assert tracker.rate_per_second() == 0.5

    def test_a_lane_that_moved_nothing_reports_zero_not_none(self) -> None:
        """Observed long enough and nothing happened is a real answer.

        ``None`` would make a standstill indistinguishable from a pipeline that
        started ten seconds ago, and the UI has to tell those apart.
        """
        tracker = ThroughputTracker()
        tracker.sample(0.0, 7)
        tracker.sample(600.0, 7)
        assert tracker.rate_per_second() == 0.0

    def test_a_restarted_counter_resets_instead_of_going_negative(self) -> None:
        """The worker's counters begin again at zero after a restart."""
        tracker = ThroughputTracker()
        tracker.sample(0.0, 900)
        tracker.sample(100.0, 0)  # restart
        tracker.sample(300.0, 100)
        rate = tracker.rate_per_second()
        assert rate is not None and rate > 0

    def test_the_window_forgets_old_samples(self) -> None:
        """A fast first hour must not hide a collapse happening now."""
        tracker = ThroughputTracker(window_s=300.0)
        tracker.sample(0.0, 0)
        tracker.sample(100.0, 1000)  # fast
        tracker.sample(1000.0, 1010)  # much later, barely moved
        tracker.sample(1300.0, 1012)
        rate = tracker.rate_per_second()
        # The fast opening is outside the window; what is left is a crawl.
        assert rate is not None and rate < 1.0

    def test_a_crawling_lane_is_reportable_once_the_window_is_full(self) -> None:
        """The slower a lane runs, the MORE it needs an estimate.

        Requiring a fixed number of completed items before answering made the
        floor scale with the slowness: a lane doing two items a quarter-hour
        would have stayed "still measuring" forever, which is the one case a
        backlog duration is genuinely wanted for.
        """
        tracker = ThroughputTracker(window_s=300.0)
        tracker.sample(0.0, 0)
        tracker.sample(300.0, 2)
        rate = tracker.rate_per_second()
        assert rate is not None and 0 < rate < 0.01

    def test_a_thin_sample_stays_silent_while_the_window_fills(self) -> None:
        """Before a full window, a couple of items is still not a rate."""
        tracker = ThroughputTracker(window_s=900.0)
        tracker.sample(0.0, 0)
        tracker.sample(120.0, 2)
        assert tracker.rate_per_second() is None


class TestBuildThroughput:
    @staticmethod
    def _snapshot(rate: float | None, items: int = 500) -> dict[str, object]:
        return {"rate_per_second": rate, "measured_s": 600.0, "measured_items": items}

    def test_eta_is_backlog_over_measured_rate(self) -> None:
        payload = build_throughput(
            embed=self._snapshot(0.5),
            distill=self._snapshot(0.1),
            embed_backlog=1800,
            distill_backlog=360,
        )
        assert payload["embed"]["eta_seconds"] == 3600
        assert payload["distill"]["eta_seconds"] == 3600
        # An item is not finished until it is summarised, and the stages run
        # one after the other rather than side by side.
        assert payload["eta_seconds"] == 7200

    def test_a_stalled_lane_gets_no_completion_time(self) -> None:
        """"Never at this rate" is not a duration and must not render as one."""
        payload = build_throughput(
            embed=self._snapshot(0.0, items=0),
            distill=self._snapshot(0.0, items=0),
            embed_backlog=200_000,
            distill_backlog=200_000,
        )
        assert payload["embed"]["eta_seconds"] is None
        assert payload["embed"]["stalled"] is True
        assert payload["eta_seconds"] is None

    def test_an_unmeasured_lane_leaves_the_total_open(self) -> None:
        """Half a measurement must not shorten the estimate to half the job.

        This is the live 2026-07-27 shape: embedding measurable, summarising
        parked at zero for the whole rebuild. Reporting the embedding half as
        the total is the same optimism the module exists to remove.
        """
        payload = build_throughput(
            embed=self._snapshot(0.65),
            distill=self._snapshot(None),
            embed_backlog=232_163,
            distill_backlog=235_915,
            distill_paused_reason="summaries are paused while the index rebuilds",
        )
        assert payload["embed"]["eta_seconds"] is not None
        assert payload["distill"]["eta_seconds"] is None
        assert payload["eta_seconds"] is None
        assert "rebuild" in payload["distill"]["paused_reason"]

    def test_an_empty_backlog_is_done_not_unknown(self) -> None:
        payload = build_throughput(
            embed=self._snapshot(0.5),
            distill=self._snapshot(0.5),
            embed_backlog=0,
            distill_backlog=0,
        )
        assert payload["eta_seconds"] == 0.0
        assert payload["embed"]["stalled"] is False


class TestFormatDuration:
    def test_none_formats_to_nothing(self) -> None:
        assert format_duration(None) == ""

    def test_the_live_backlog_reads_in_days(self) -> None:
        """232 163 items at the measured 0.65/s — the number that started this."""
        assert "days" in format_duration(232_163 / 0.65)

    def test_buckets_climb_with_the_duration(self) -> None:
        assert format_duration(30) == "under a minute"
        assert "minutes" in format_duration(600)
        assert "hours" in format_duration(6 * 3600)
        assert "days" in format_duration(4 * 86_400)
        assert "weeks" in format_duration(30 * 86_400)
        assert "months" in format_duration(200 * 86_400)
