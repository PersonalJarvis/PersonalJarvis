"""The fast-boot desktop bind must survive the self-restart port-release race.

Live forensic (2026-06-25): after an in-app restart the app "shut down but
never came back" — the relauncher spawned a fresh instance, but the fast-boot
desktop backend (``_desktop_backend_main``) treated *any* port-bind failure as
"another instance is already running", focused a (now dead) window and quit.
The real cause is timing: the just-exited old process is still releasing the
admin port for a few hundred ms, so the first bind fails *transiently* — NOT
because a second live instance holds it.

``_serve_bootstrap_with_retry`` fixes that: it retries the bind a few times
before concluding "already running". The single-instance lock (acquired later)
stays the authoritative "is another instance live?" check — this only stops a
transient post-restart bind race from being misread.
"""
from __future__ import annotations

import asyncio

from jarvis.ui.web.launcher import _serve_bootstrap_with_retry


class _FakeBootstrap:
    """Stand-in for FastBootstrap whose ``serve`` either binds or raises once."""

    def __init__(self, *, fail: bool) -> None:
        self._fail = fail
        self.served = False

    async def serve(self, host: str, port: int) -> None:
        if self._fail:
            raise OSError("address already in use")
        self.served = True


def _factory_sequence(fail_count: int):
    """A FastBootstrap factory whose first ``fail_count`` instances fail to bind
    and every later one binds cleanly — one fresh object per attempt, mirroring
    the production retry (a failed bind is never reused)."""
    made: list[_FakeBootstrap] = []

    def factory() -> _FakeBootstrap:
        fb = _FakeBootstrap(fail=len(made) < fail_count)
        made.append(fb)
        return fb

    return factory, made


def test_binds_on_first_attempt_with_no_backoff() -> None:
    """Port is free (the normal start): the first bind wins, zero delay."""
    loop = asyncio.new_event_loop()
    try:
        factory, made = _factory_sequence(fail_count=0)
        slept: list[float] = []
        bootstrap = _serve_bootstrap_with_retry(
            loop, "127.0.0.1", 47821, _factory=factory, _sleep=slept.append
        )
        assert bootstrap is not None
        assert bootstrap.served is True
        assert len(made) == 1
        assert slept == []
    finally:
        loop.close()


def test_recovers_from_transient_post_restart_bind_failure() -> None:
    """The restart race: the old process is still releasing the port, so the
    first two binds fail transiently — a retry must bind and return the live
    bootstrap, NOT conclude 'already running'."""
    loop = asyncio.new_event_loop()
    try:
        factory, made = _factory_sequence(fail_count=2)
        slept: list[float] = []
        bootstrap = _serve_bootstrap_with_retry(
            loop,
            "127.0.0.1",
            47821,
            attempts=5,
            delay=0.4,
            _factory=factory,
            _sleep=slept.append,
        )
        assert bootstrap is not None, "must come up, not bounce as 'already running'"
        assert bootstrap.served is True
        assert len(made) == 3, "bound on the third attempt"
        assert slept == [0.4, 0.4], "backed off before each retry, not after success"
    finally:
        loop.close()


def test_gives_up_when_port_stays_busy() -> None:
    """A genuinely live second instance never frees the port: after every
    attempt the retry returns None so the caller maps it to 'already running'
    (the single-instance lock then confirms)."""
    loop = asyncio.new_event_loop()
    try:
        factory, made = _factory_sequence(fail_count=99)
        slept: list[float] = []
        bootstrap = _serve_bootstrap_with_retry(
            loop,
            "127.0.0.1",
            47821,
            attempts=4,
            delay=0.2,
            _factory=factory,
            _sleep=slept.append,
        )
        assert bootstrap is None
        assert len(made) == 4, "tried exactly `attempts` times"
        assert slept == [0.2, 0.2, 0.2], "slept between attempts, not after the last"
    finally:
        loop.close()
