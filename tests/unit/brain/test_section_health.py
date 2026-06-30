"""Unit tests for the pure section-health rollup core.

Guards the two pure functions the ``/api/providers/section-health`` endpoint
composes, plus the status vocabulary itself. The I/O orchestration (resolving the
active provider, running the real connectivity test) is exercised separately; here
we only pin the rules so the tab indicator can never silently change meaning.
"""
from __future__ import annotations

import pytest

from jarvis.brain import provider_test
from jarvis.brain import section_health as sh


class TestSectionStatusForTest:
    def test_missing_credential_is_needs_setup_regardless_of_test(self) -> None:
        # No key stored → the tab is "not set up", even if a stale test string is
        # passed in. Missing always wins over any test outcome.
        assert sh.section_status_for_test(None, configured=False) == sh.NEEDS_SETUP
        assert sh.section_status_for_test("ok", configured=False) == sh.NEEDS_SETUP
        assert sh.section_status_for_test("bad_key", configured=False) == sh.NEEDS_SETUP

    def test_configured_and_ok_is_ok(self) -> None:
        assert sh.section_status_for_test("ok", configured=True) == sh.OK

    def test_configured_but_not_tested_is_unknown(self) -> None:
        # Honesty: a stored key we haven't called yet must not claim "ok".
        assert sh.section_status_for_test(None, configured=True) == sh.UNKNOWN

    def test_test_not_configured_is_needs_setup(self) -> None:
        # The live call itself found no key — treat as not set up, never a red error.
        assert sh.section_status_for_test("not_configured", configured=True) == sh.NEEDS_SETUP

    @pytest.mark.parametrize(
        "bad",
        ["bad_key", "no_credits", "rate_limited", "model_unavailable", "unreachable", "error"],
    )
    def test_every_failing_test_status_is_error(self, bad: str) -> None:
        assert sh.section_status_for_test(bad, configured=True) == sh.ERROR

    def test_covers_every_provider_test_status(self) -> None:
        # Anti-drift: every status the provider test can emit must map to a
        # defined section bucket (no unmapped/silently-dropped outcome).
        for status in provider_test.PROVIDER_TEST_STATUSES:
            mapped = sh.section_status_for_test(status, configured=True)
            assert mapped in sh.SECTION_HEALTH_STATUSES


class TestAggregate:
    def test_empty_is_unknown(self) -> None:
        assert sh.aggregate([]) == sh.UNKNOWN

    def test_single_passthrough(self) -> None:
        assert sh.aggregate([sh.OK]) == sh.OK
        assert sh.aggregate([sh.NEEDS_SETUP]) == sh.NEEDS_SETUP

    def test_error_beats_everything(self) -> None:
        assert sh.aggregate([sh.OK, sh.NEEDS_SETUP, sh.ERROR]) == sh.ERROR
        assert sh.aggregate([sh.ERROR, sh.UNKNOWN]) == sh.ERROR

    def test_needs_setup_beats_ok_and_unknown(self) -> None:
        assert sh.aggregate([sh.OK, sh.NEEDS_SETUP]) == sh.NEEDS_SETUP
        assert sh.aggregate([sh.UNKNOWN, sh.NEEDS_SETUP]) == sh.NEEDS_SETUP

    def test_ok_beats_unknown(self) -> None:
        assert sh.aggregate([sh.UNKNOWN, sh.OK]) == sh.OK


def test_vocabulary_is_exactly_four() -> None:
    assert set(sh.SECTION_HEALTH_STATUSES) == {sh.OK, sh.NEEDS_SETUP, sh.ERROR, sh.UNKNOWN}
    assert len(sh.SECTION_HEALTH_STATUSES) == 4
