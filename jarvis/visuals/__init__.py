"""On-demand visualisations — the pictures a user explicitly asked for.

Three small pieces, deliberately separable:

* :mod:`~jarvis.visuals.spec` — the shape a caller has to supply, and the
  validation that turns loose model-authored JSON into it.
* :mod:`~jarvis.visuals.render` — spec in, one self-contained HTML page out.
  Pure function, no filesystem, no network: trivially testable.
* :mod:`~jarvis.visuals.store` — where that page is written so the existing
  Outputs/Visualization surfaces find it without a new REST route.

The gate that decides whether any of this runs at all lives elsewhere, in
:mod:`jarvis.brain.visualize_gate` — rendering is only ever reached on a turn
that asked for a picture in so many words.
"""

from __future__ import annotations

from jarvis.visuals.brand import BRAND
from jarvis.visuals.render import render_visual_html
from jarvis.visuals.spec import VisualItem, VisualSpec, VisualSpecError, parse_spec
from jarvis.visuals.store import StoredVisual, store_visual

__all__ = [
    "BRAND",
    "StoredVisual",
    "VisualItem",
    "VisualSpec",
    "VisualSpecError",
    "parse_spec",
    "render_visual_html",
    "store_visual",
]
