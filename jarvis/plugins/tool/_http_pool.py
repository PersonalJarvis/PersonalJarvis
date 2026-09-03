"""Shared connection-pool helper for the REST-backed router tools.

The implementation moved to :mod:`jarvis.core.http_pool` when BUG-215 turned
"a fresh client per request is slow" into "a fresh connection per request can
empty the machine's socket pool" — at which point it stopped being a plugin
convenience and became a core rule. This module stays as the import the REST
plugins (gmail, vercel) already use; there is only one implementation.
"""

from __future__ import annotations

from jarvis.core.http_pool import HttpClientPool

__all__ = ["HttpClientPool"]
