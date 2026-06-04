"""CLI integration for Personal Jarvis (Phase: CLI-Integration).

Users can install, connect, and manage curated and self-registered CLI tools
(gcloud, gh, supabase, stripe, docker, ...) from the desktop app.
The brain sees one dedicated tool per connected CLI, with risk-tier integration.

Entry points:
- ``catalog.CliCatalog``     — Load seed + custom specs.
- ``prober.CliStatusProber`` — Binary/auth health check.
- ``usage_log.UsageLog``     — SQLite-backed invocation history.
- ``tool.CliTool``           — Tool-protocol implementation per connected CLI.
- ``loader.CliToolLoader``   — The single static entry_point.
- ``registry.CliToolRegistry`` — Orchestrates catalog + prober + tools + auth.
"""
from __future__ import annotations

__all__: tuple[str, ...] = ()
