"""Harness plugins — the Phase-4 in-process harness system.

Registered entry points (``jarvis.harness`` group): ``python-script`` and
``screenshot`` (ADR-0008 Computer-Use loop). ``open_interpreter.py`` and
``mcp_remote.py`` exist on disk but are NOT registered. There is no
``jarvis-agent`` or ``codex`` harness plugin — heavy coding-agent work runs
through the Phase-6 mission workers (``jarvis/missions/workers/``), spawned
via the ``spawn_worker`` tool, never through this plugin group.
"""
