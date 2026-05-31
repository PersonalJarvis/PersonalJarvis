"""FakeSymconActor: programmable IP-Symcon actor stand-in for tests.

Lives in tests/ — not production. Per ADR-0010 the previous
``src/skillbook/symcon_bridge/actor.py:MockSymconActor`` was moved here and
renamed; ``failures_until_ok`` counter semantics are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FakeSymconActor:
    """Programmable actor for unit/integration tests and the capstone scenario.

    Raises ``TimeoutError`` on the first ``failures_until_ok`` invocations and
    returns ``success_result`` after that. Counts every call for assertions.

    Renamed from the previous in-tree ``MockSymconActor`` per ADR-0010 — the
    Meszaros xUnit Patterns vocabulary reserves *Mock* for behavior-verifying
    frameworks (e.g. ``unittest.mock``) and uses *Fake* for hand-written
    deterministic replacements.
    """

    name: str
    failures_until_ok: int = 0
    success_result: dict = field(default_factory=lambda: {"ok": True})
    timeout_message: str = "fake symcon timeout"
    _calls: int = field(default=0, init=False)

    async def call(self, params: Any) -> dict:
        self._calls += 1
        if self._calls <= self.failures_until_ok:
            raise TimeoutError(f"{self.timeout_message} (call #{self._calls})")
        out = dict(self.success_result)
        out.setdefault("call", self._calls)
        if isinstance(params, dict):
            out.setdefault("params", dict(params))
        return out

    @property
    def call_count(self) -> int:
        return self._calls
