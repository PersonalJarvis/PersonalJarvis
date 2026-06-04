"""Hand-built ``Elevator`` fake (Wave 3, sub-task 3.4; EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. A real
elevator triggers an interactive OS prompt (UAC / polkit / Touch-ID) that cannot
run in CI, so :class:`FakeElevator` stands in for any
:class:`jarvis.admin.elevator.Elevator`. It records every
``ensure_elevated_helper`` call and returns a configurable
:class:`~jarvis.admin.elevator.ElevationResult`, so a test can drive the
``AdminClient`` elevation gate (available -> proceeds; unavailable -> typed
refusal) without spawning anything.

Structurally compatible with :class:`jarvis.admin.elevator.Elevator`:
``ensure_elevated_helper`` / ``is_available``.
"""

from __future__ import annotations

from jarvis.admin.elevator import ElevationResult


class FakeElevator:
    """A configurable ``Elevator`` that never spawns a process.

    :param available: what :meth:`is_available` returns.
    :param result: the :class:`ElevationResult` to return; defaults to an
        ``ok=available`` result so the common cases need no extra wiring.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        result: ElevationResult | None = None,
    ) -> None:
        self._available = available
        self._result = result
        self.calls: list[str] = []        # transport_addr values, in order.

    def is_available(self) -> bool:
        return self._available

    async def ensure_elevated_helper(self, transport_addr: str) -> ElevationResult:
        self.calls.append(transport_addr)
        if self._result is not None:
            return self._result
        if self._available:
            return ElevationResult(
                ok=True, transport_addr=transport_addr, pid=4242,
            )
        return ElevationResult(
            ok=False, transport_addr=transport_addr,
            error_code="no_elevation",
            message="fake elevator: no elevation mechanism available.",
        )


__all__ = ["FakeElevator"]
