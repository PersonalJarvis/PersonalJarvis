"""Agentic IDE — a voice-aware coding workspace inside the desktop app.

The user picks a folder, chooses how many terminals to open and which coding
agent (Claude Code / Codex) runs in each, and gets a grid of real terminals in
the app. Each terminal carries a spoken call-sign — Mika, Nova, Aria — so the
whole workspace is addressable by voice: *"what is Mika doing?"*, *"tell Nova to
run the tests"*. A focus mode narrows Jarvis to that workspace for as long as
the user wants, and switches back cleanly.

Module map:

* ``names`` — the call-sign pool and fuzzy spoken-name resolution,
* ``transcript`` — a sanitized, bounded view of what a terminal printed,
* ``folders`` — folder picking plus a cheap profile of the chosen codebase,
* ``session`` — the live registry: terminals, PTYs, prompt injection,
* ``context`` — the workspace-awareness block used while focus mode is on.

Nothing here is imported at boot; the REST layer
(``jarvis.ui.web.agentic_ide_routes``) pulls it in on first use, and the PTY
stack itself is imported lazily inside the registry (AP-26).
"""
from __future__ import annotations

__all__ = ["context", "folders", "names", "session", "transcript"]
