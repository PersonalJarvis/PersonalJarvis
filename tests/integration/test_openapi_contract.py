"""Contract checks on the OpenAPI document the whole app publishes.

``/api/openapi.json`` is what the ``jarvis`` CLI builds its ``api`` commands
from and what any external client generator reads. A route whose methods
share one operationId (``api_route(methods=["GET", "HEAD"])``) made the
document invalid for generators; this keeps it from coming back.
"""

from __future__ import annotations

import collections

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


def _spec() -> dict:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    return WebServer(cfg, bus=EventBus()).app.openapi()


def test_operation_ids_are_unique() -> None:
    counts = collections.Counter(
        op.get("operationId")
        for item in _spec()["paths"].values()
        for op in item.values()
        if isinstance(op, dict)
    )
    duplicates = sorted(op_id for op_id, n in counts.items() if n > 1)
    assert duplicates == []
