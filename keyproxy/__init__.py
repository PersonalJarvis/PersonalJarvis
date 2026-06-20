"""keyproxy — a vendor-aware streaming reverse proxy for LLM API keys.

A small, self-contained FastAPI + httpx + SQLite service that holds real LLM
vendor API keys server-side and lets team clients reach the vendors with a
per-user token instead of the real key. Zero dependency on the ``jarvis``
package; boots on a fresh ``python:3.11-slim`` with only ``fastapi``,
``httpx``, and ``uvicorn``.

Public surface:
    - :func:`keyproxy.app.create_app` — the FastAPI application factory.
    - ``/p/{provider_id}/{path:path}`` — the generic streaming passthrough.
    - ``/admin/tokens`` / ``/admin/usage`` — admin endpoints (bearer-guarded).
    - :mod:`keyproxy.cli` — admin CLI (issue-token, list-tokens, revoke, usage).
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
