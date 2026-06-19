"""Conductor-API — FastAPI-Router.

Zwei Deployment-Modi:

1. **Standalone** — ``conductor.api.app.create_app()`` gibt eine fertig
   konfigurierte ``FastAPI``-App. CLI ``python -m conductor serve`` nutzt
   das.

2. **Embedded** — nur der ``router`` wird von Jarvis (oder einem anderen
   Host) importiert und via ``app.include_router(router)`` eingehaengt.
   Jarvis' WebServer macht genau das.
"""
from __future__ import annotations

from .routes import router

__all__ = ["router"]
