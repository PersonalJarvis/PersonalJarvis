"""Fake-VisionSource: scripted Observations ohne echten Screenshot/UIA-Call.

Nutzung in Tests:

    fake = FakeVisionSource(scripted=[observation1, observation2])
    obs = await fake.observe()
    assert obs == observation1
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable
from uuid import uuid4

from jarvis.core.protocols import CancelToken, Observation, UIANode


class FakeVisionSource:
    """Liefert vorkonfigurierte Observations; cyclet am Ende der Liste."""

    name: str = "fake-vision"
    kind: str = "composite"

    def __init__(
        self,
        *,
        scripted: Iterable[Observation] | None = None,
        default_nodes: tuple[UIANode, ...] = (),
        default_window_title: str = "Fake Window",
    ) -> None:
        self._scripted: list[Observation] = list(scripted or [])
        self._default_nodes = default_nodes
        self._default_window_title = default_window_title
        self._index = 0
        self.observe_calls: list[dict[str, object]] = []
        self.closed = False

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        self.observe_calls.append({
            "cancel_token": cancel_token,
            "window_title_filter": window_title_filter,
        })
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")

        if self._scripted:
            obs = self._scripted[self._index % len(self._scripted)]
            self._index += 1
            return obs

        now_ns = time.time_ns()
        placeholder_png = f"fake-screenshot-{now_ns}".encode()
        return Observation(
            trace_id=uuid4(),
            timestamp_ns=now_ns,
            screenshot_path=None,
            screenshot_hash=hashlib.sha256(placeholder_png).hexdigest(),
            nodes=self._default_nodes,
            window_title=window_title_filter or self._default_window_title,
            active_pid=0,
            source="full",
            pruning_stats={"nodes_before": len(self._default_nodes),
                            "nodes_after": len(self._default_nodes),
                            "depth_used": 6},
        )

    async def close(self) -> None:
        self.closed = True
