"""The brand palette shared by every server-rendered page.

Once the home of the on-demand visualisation renderer (spec → HTML, five fixed
shapes). That was retired on 2026-08-23 in favour of artifacts — whole pages a
mission worker writes on the strongest model (:mod:`jarvis.artifacts`). What
stays is the one thing both the mission map and the artifact brief still need:
the palette.
"""

from __future__ import annotations

from jarvis.visuals.brand import BRAND

__all__ = ["BRAND"]
