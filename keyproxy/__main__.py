"""``python -m keyproxy`` -> the admin CLI.

The HTTP service is started with ``uvicorn keyproxy.app:app`` (see the
Dockerfile / README); ``python -m keyproxy`` is the admin entry point.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
