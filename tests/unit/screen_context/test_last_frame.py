"""The deck's mirror of the last capture: one frame, a TTL, and an off switch.

The mirror is a second, user-facing copy of a picture the service already
produced. What must hold: it never keeps more than one frame, it forgets on
its own after the budget, and a budget of zero means it keeps nothing at all —
the capture path calls ``set`` regardless and must not have to care.
"""

from __future__ import annotations

from jarvis.screen_context.last_frame import LastFrameMirror


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000_000

    def __call__(self) -> int:
        return self.now

    def advance_s(self, seconds: float) -> None:
        self.now += int(seconds * 1_000_000_000)


def _mirror(ttl_s: float = 120.0) -> tuple[LastFrameMirror, _Clock]:
    clock = _Clock()
    return LastFrameMirror(ttl_s=ttl_s, clock=clock), clock


def test_empty_mirror_holds_nothing():
    mirror, _ = _mirror()
    assert mirror.get() is None


def test_set_then_get_returns_the_frame_with_a_moving_seq():
    mirror, _ = _mirror()
    seq1 = mirror.set(b"one", mime="image/jpeg", width=10, height=5, source="screen_context")
    seq2 = mirror.set(b"two", mime="image/jpeg", width=10, height=5, source="screen_context")

    assert (seq1, seq2) == (1, 2)
    held = mirror.get()
    assert held is not None
    # Only the newest frame survives — there is no history by construction.
    assert held.image == b"two"
    assert held.seq == 2
    assert (held.width, held.height, held.mime) == (10, 5, "image/jpeg")


def test_frame_expires_after_the_budget():
    mirror, clock = _mirror(ttl_s=60)
    mirror.set(b"x", mime="image/png", width=1, height=1, source="screen_context")

    clock.advance_s(59)
    assert mirror.get() is not None
    clock.advance_s(2)
    # Expiry is enforced on read: no timer, no background task to leak.
    assert mirror.get() is None
    assert mirror.get() is None


def test_zero_budget_switches_the_mirror_off():
    mirror, _ = _mirror(ttl_s=0)
    assert mirror.enabled is False
    # The capture path calls set() unconditionally; off must be a no-op, not
    # an exception, and it must report "nothing stored".
    assert mirror.set(b"x", mime="image/png", width=1, height=1, source="screen_context") == 0
    assert mirror.get() is None


def test_lowering_the_budget_to_zero_drops_a_held_frame():
    mirror, _ = _mirror(ttl_s=120)
    mirror.set(b"x", mime="image/png", width=1, height=1, source="screen_context")
    mirror.set_ttl(0)
    assert mirror.get() is None


def test_clear_forgets_immediately():
    mirror, _ = _mirror()
    mirror.set(b"x", mime="image/png", width=1, height=1, source="screen_context")
    mirror.clear()
    assert mirror.get() is None
