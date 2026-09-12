"""Read-only visualization of mouse commands successfully sent to Chrome."""

from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger(__name__)


class PointerTracker:
    def __init__(self, generation: str, emit: Any, enabled: Any, project: Any) -> None:
        self.generation = generation
        self.emit = emit
        self.enabled = enabled
        self.project = project
        self.click_id = 0
        self.click = (0.0, 0.0)

    def wrap(self, send: Any) -> Any:
        async def observed(method: str, params: Any = None, session_id: str | None = None) -> Any:
            result = await send(method=method, params=params, session_id=session_id)
            if self.enabled() and method == "Input.dispatchMouseEvent" and isinstance(params, dict):
                try:
                    kind = params.get("type")
                    if kind not in {"mouseMoved", "mousePressed", "mouseWheel"}:
                        return result
                    x, y = float(params["x"]), float(params["y"])
                    if not math.isfinite(x) or not math.isfinite(y):
                        return result
                    x, y, width, height = self.project(x, y)
                    if kind == "mousePressed":
                        self.click_id += 1
                        self.click = (x, y)
                    self.emit(
                        "pointer",
                        visible=True,
                        generation=self.generation,
                        x=x,
                        y=y,
                        width=width,
                        height=height,
                        click_id=self.click_id,
                        click_x=self.click[0],
                        click_y=self.click[1],
                    )
                except Exception:
                    # Visualization must never turn a successful browser action into failure.
                    log.debug("Browser pointer visualization unavailable", exc_info=True)
            return result

        return observed

    def clear(self) -> None:
        self.click_id = 0
        self.click = (0.0, 0.0)
        self.emit("pointer", visible=False, generation=self.generation)
