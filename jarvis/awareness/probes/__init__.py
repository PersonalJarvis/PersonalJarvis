"""jarvis.awareness.probes — Deep-probes layer (Phase A5).

Probes run before PrivacyFilter and enrich the FrameSnapshot with
additional metadata (git_branch, open_file_hint). Every probe is
defensive — errors NEVER propagate into the FrameUpdate path (Hard
Negative §9). Manager.probe_all calls all probes in parallel with a
200 ms total budget.

A5-Lite implements only the two simple probes:
- ``GitProbe`` — branch via .git/HEAD or asyncio git rev-parse
- ``FileSystemProbe`` — watchdog FS watcher emitting FileSaved bus events

MCP/LSP are deferred to Phase 6 (user default + Plan §9 sanctions
"Optional, can be deferred").
"""
from __future__ import annotations

from jarvis.awareness.probes.base import Probe
from jarvis.awareness.probes.filesystem import FileSystemProbe
from jarvis.awareness.probes.git import GitProbe

__all__ = ["FileSystemProbe", "GitProbe", "Probe"]
