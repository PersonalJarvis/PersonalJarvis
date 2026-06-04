"""Probe protocol for the awareness deep-probes layer (Phase A5).

Every probe implements ``async def probe(*, cwd, process_name) -> dict``.
Return values are probe-specific (e.g. ``{"git_branch": str | None}``,
``{"open_file_hint": str | None}``). ``Manager.probe_all`` merges the
dicts from all probes.

Hard Negative §9: probe errors MUST NOT propagate — every probe MUST
catch exceptions internally and return a dict (with None fields).
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Probe(Protocol):
    """Probe for the FrameSnapshot build path.

    name: stable identifier for logging/telemetry.
    probe(): called by AwarenessManager.probe_all() in parallel with all
    other probes via asyncio.gather. 200 ms total budget.
    """

    name: str

    async def probe(
        self, *, cwd: str | None, process_name: str = "",
    ) -> dict[str, Any]:
        """Returns probe-specific metadata as a dict.

        ``cwd`` may be None (process has none or permission was denied).
        ``process_name`` is informational (e.g. for probes that are only
        relevant for certain apps).

        MUST return a dict (with None fields) on every failure;
        never raise an exception — otherwise the watcher drain loop crashes.
        """
        ...
