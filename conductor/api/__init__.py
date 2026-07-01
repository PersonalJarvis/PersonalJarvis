"""Conductor API — FastAPI router.

Two deployment modes:

1. **Standalone** — ``conductor.api.app.create_app()`` returns a fully
   configured ``FastAPI`` app. The CLI ``python -m conductor serve`` uses
   this.

2. **Embedded** — only the ``router`` is imported by Jarvis (or another
   host) and mounted via ``app.include_router(router)``. Jarvis's
   web server does exactly that.
"""
from __future__ import annotations

from .routes import router

__all__ = ["router"]
