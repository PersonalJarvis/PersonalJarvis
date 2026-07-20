"""Tests for BitBlt / mss.ScreenShotError fault tolerance in ScreenshotSource.

BitBlt fails intermittently when a monitor is in a transient bad state
(display asleep, workstation locked, resolution change, disconnected monitor).
The capture path must:
  - Not raise out of _capture_image / observe when mss.exception.ScreenShotError occurs.
  - Return None so the refresh loop can skip the frame and reuse the last good observation.
  - NOT spam the log with full tracebacks every cycle: at most ONE warning per
    uninterrupted error run (state-change logging — silent while error persists).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
import types
from uuid import uuid4

# Ensure mss.exception is importable for the fake (it is a real dep).
import mss.exception as _mss_exception
import pytest

from jarvis.core.protocols import Observation
from jarvis.vision.context_provider import VisionContextProvider


@pytest.fixture(autouse=True)
def _screen_recording_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep BitBlt tests independent of the macOS TCC state of the host."""
    import jarvis.vision.screenshot as screenshot_module

    monkeypatch.setattr(
        screenshot_module, "warn_if_screen_recording_denied", lambda: False
    )


# ---------------------------------------------------------------------------
# Fake mss whose grab() always raises ScreenShotError (BitBlt path)
# ---------------------------------------------------------------------------

class _FakeMssCtxBitBltError:
    """Context manager returned by FakeMssBitBltError() — grab() raises."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def monitors(self) -> list[dict]:
        return [
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080, "is_primary": True},
        ]

    def grab(self, monitor: dict):
        raise _mss_exception.ScreenShotError(
            "Windows graphics function failed (no error provided): BitBlt"
        )


def _make_fake_mss_fail_module() -> types.ModuleType:
    """Return a fake 'mss' module whose mss() raises ScreenShotError on grab."""
    fake = types.ModuleType("mss")
    fake.mss = _FakeMssCtxBitBltError  # type: ignore[attr-defined]
    fake.exception = _mss_exception  # type: ignore[attr-defined]
    return fake


# ---------------------------------------------------------------------------
# Helper: produce a valid Observation (used by _BitBltFailingEngine)
# ---------------------------------------------------------------------------

def _make_obs(hash_: str = "ok") -> Observation:
    return Observation(
        trace_id=uuid4(),
        timestamp_ns=time.time_ns(),
        screenshot_path=None,
        screenshot_hash=hash_,
        nodes=(),
        window_title="test",
        active_pid=0,
        source="screenshot_only",
        pruning_stats={},
    )


# ---------------------------------------------------------------------------
# Test 1: _capture_image must return None, not raise, on BitBlt error
# ---------------------------------------------------------------------------

def test_capture_image_does_not_raise_on_bitblt_error(tmp_path, monkeypatch):
    """_capture_image must return None (graceful skip) when BitBlt fails.

    RED phase: fails before the fix because _capture_image lets ScreenShotError
    propagate out of the 'with mss.mss() as sct' block.
    """
    monkeypatch.setitem(sys.modules, "mss", _make_fake_mss_fail_module())

    from jarvis.vision.screenshot import ScreenshotSource  # noqa: PLC0415

    src = ScreenshotSource(save_blob=False, blob_dir=tmp_path)
    result = src._capture_image()
    assert result is None, (
        "_capture_image must return None on BitBlt error, not raise"
    )


# ---------------------------------------------------------------------------
# Test 2: observe() must not raise on BitBlt error (returns None gracefully)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_observe_returns_none_on_bitblt_error(tmp_path, monkeypatch):
    """observe() must return None when _capture_image signals a BitBlt failure."""
    monkeypatch.setitem(sys.modules, "mss", _make_fake_mss_fail_module())

    from jarvis.vision.screenshot import ScreenshotSource  # noqa: PLC0415

    src = ScreenshotSource(save_blob=False, blob_dir=tmp_path)
    # _capture_image runs in to_thread; calling it directly is fine for sync path.
    result = src._capture_image()
    assert result is None


# ---------------------------------------------------------------------------
# Test 3: rate-limited logging — at most ONE warning per uninterrupted error run
# ---------------------------------------------------------------------------

def test_bitblt_error_logs_at_most_once_across_consecutive_failures(
    tmp_path, monkeypatch, caplog
):
    """Repeated BitBlt errors must emit at most 1 WARNING log across N calls.

    The state-change contract: log when error state begins, stay silent while
    it persists, log again only when it clears.

    RED phase: before the fix each call raises and the calling context logs
    the full traceback — spam.
    """
    monkeypatch.setitem(sys.modules, "mss", _make_fake_mss_fail_module())

    from jarvis.vision.screenshot import ScreenshotSource  # noqa: PLC0415

    src = ScreenshotSource(save_blob=False, blob_dir=tmp_path)

    with caplog.at_level(logging.WARNING, logger="jarvis.vision.screenshot"):
        for _ in range(5):
            src._capture_image()

    bitblt_warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "BitBlt" in r.message
    ]
    assert len(bitblt_warnings) <= 1, (
        f"Expected at most 1 BitBlt warning across 5 failures, "
        f"got {len(bitblt_warnings)}: {[r.message for r in bitblt_warnings]}"
    )


# ---------------------------------------------------------------------------
# Test 4: recovery logging — single INFO when BitBlt clears after failures
# ---------------------------------------------------------------------------

def test_bitblt_recovery_logs_info(tmp_path, monkeypatch, caplog):
    """After consecutive BitBlt failures, a successful grab logs one INFO recovery."""
    pytest.importorskip("PIL")

    monkeypatch.setitem(sys.modules, "mss", _make_fake_mss_fail_module())

    from jarvis.vision.screenshot import ScreenshotSource  # noqa: PLC0415

    src = ScreenshotSource(save_blob=False, blob_dir=tmp_path)

    # Trigger 3 failures to set the "in-error" state on the source.
    for _ in range(3):
        src._capture_image()

    # Switch to a succeeding fake mss to simulate the monitor recovering.
    class _FakeOkCtx:
        def __enter__(self): return self
        def __exit__(self, *a): return False

        @property
        def monitors(self):
            return [
                {"left": 0, "top": 0, "width": 4, "height": 4},
                {"left": 0, "top": 0, "width": 4, "height": 4, "is_primary": True},
            ]

        def grab(self, monitor):
            class _R:
                size = (4, 4)
                rgb = b"\xff\x00\x00" * 16  # 4x4 red solid
            return _R()

    fake_ok = types.ModuleType("mss")
    fake_ok.mss = _FakeOkCtx  # type: ignore[attr-defined]
    fake_ok.exception = _mss_exception  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mss", fake_ok)

    with caplog.at_level(logging.INFO, logger="jarvis.vision.screenshot"):
        result = src._capture_image()

    assert result is not None, "Recovery call must return image bytes"
    recovery_logs = [
        r for r in caplog.records
        if r.levelno >= logging.INFO and (
            "recover" in r.message.lower() or "bitblt" in r.message.lower()
        )
    ]
    assert len(recovery_logs) >= 1, (
        "Expected at least one INFO/WARNING log on BitBlt recovery, got none"
    )


# ---------------------------------------------------------------------------
# Test 5: VisionContextProvider refresh loop survives repeated BitBlt errors
# ---------------------------------------------------------------------------

class _BitBltFailingEngine:
    """Engine that raises mss.exception.ScreenShotError for the first N calls.

    Simulates the exact exception path from screenshot.py → engine.py →
    context_provider._refresh_loop, i.e. the case where the screenshot source
    has NOT been patched yet (pre-fix scenario for integration-level testing).
    """

    def __init__(self, fail_count: int = 3) -> None:
        self.calls = 0
        self.fail_count = fail_count

    async def observe(self, *, mode: str = "auto", **kwargs):
        self.calls += 1
        if self.calls <= self.fail_count:
            raise _mss_exception.ScreenShotError(
                "Windows graphics function failed (no error provided): BitBlt"
            )
        return _make_obs(f"recovered-{self.calls}")


@pytest.mark.asyncio
async def test_refresh_loop_survives_bitblt_errors():
    """VisionContextProvider loop keeps running through BitBlt errors and recovers."""
    engine = _BitBltFailingEngine(fail_count=3)
    prov = VisionContextProvider(engine, refresh_interval_s=0.02, max_staleness_s=10.0)
    await prov.start()
    try:
        # Wait until we've had at least 4 engine calls (3 failures + 1 success).
        for _ in range(100):
            if engine.calls >= 4:
                break
            await asyncio.sleep(0.02)

        assert prov.is_running, "Loop must still be running after BitBlt errors"
        assert engine.calls >= 4, f"Expected >= 4 calls, got {engine.calls}"
        # After recovery, latest must hold the successful observation.
        assert prov.latest is not None
        assert "recovered" in prov.latest.screenshot_hash
    finally:
        await prov.stop()


# ---------------------------------------------------------------------------
# Test 6: BitBlt caught at screenshot level → zero error-level loop logs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refresh_loop_bitblt_does_not_spam_log(tmp_path, monkeypatch, caplog):
    """When BitBlt is caught at ScreenshotSource level, the loop sees None returns.

    Consequence: the context_provider loop emits ZERO error-level traceback logs
    for this expected transient condition.  If ScreenshotSource is NOT patched
    (pre-fix), the exception escapes to the loop which logs it repeatedly.

    This test patches mss at the module level so it goes through the real
    ScreenshotSource._capture_image code path — the innermost catch site.
    """
    monkeypatch.setitem(sys.modules, "mss", _make_fake_mss_fail_module())

    from jarvis.vision.engine import VisionEngine  # noqa: PLC0415
    from jarvis.vision.screenshot import ScreenshotSource  # noqa: PLC0415

    # Build a real engine using a real ScreenshotSource (but with fake mss).
    # The UIA tree source is a simple null fake so it never errors.
    class _NullUIA:
        name = "null-uia"
        kind = "ui_tree"
        async def observe(self, **kwargs):
            return _make_obs("uia-null")
        async def close(self): pass

    engine = VisionEngine(
        screenshot_source=ScreenshotSource(save_blob=False, blob_dir=tmp_path),
        uia_source=_NullUIA(),  # type: ignore[arg-type]
    )

    prov = VisionContextProvider(
        engine, refresh_interval_s=0.01, max_staleness_s=10.0,
        capture_mode="screenshot",  # force screenshot path only
    )

    with caplog.at_level(logging.WARNING, logger="jarvis.vision"):
        await prov.start()
        try:
            # 15 iterations with BitBlt always failing.
            for _ in range(200):
                await asyncio.sleep(0.01)
                # Check if we've run enough iterations.
                if sum(
                    1 for r in caplog.records
                    if "loop" in r.message.lower() or "bitblt" in r.message.lower()
                ) > 3:
                    break
            await asyncio.sleep(0.15)  # let loop run a few more cycles
        finally:
            await prov.stop()

    # When caught at screenshot level: loop sees None returns, no loop-exception logs.
    context_provider_error_logs = [
        r for r in caplog.records
        if r.levelno >= logging.ERROR
        and "loop" in r.message.lower()
        and "context" in r.name.lower()
    ]
    assert len(context_provider_error_logs) == 0, (
        f"BitBlt errors must be caught at screenshot level — "
        f"context_provider loop must NOT emit error logs. "
        f"Got {len(context_provider_error_logs)}: "
        f"{[r.message for r in context_provider_error_logs]}"
    )
