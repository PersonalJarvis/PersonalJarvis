"""Client-safe error text for web routes.

A caught exception's ``str()`` can carry internal paths, library reprs or a
provider's raw response body. Routes that answer a local UI with an error
string use :func:`internal_error` instead: the full exception (with its
traceback) goes to the server log, and the client gets a short, stable
sentence it can show next to the button that failed.

Messages the UI must act on (a validation error we raise ourselves, a clear
"package not installed" hint) keep their own text; this helper is for the
unexpected ``except Exception`` branches.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)

LOG_HINT = "Details are in the Jarvis log."


def internal_error(
    action: str,
    exc: BaseException,
    *,
    logger: logging.Logger | None = None,
) -> str:
    """Log *exc* with its traceback and return a generic client message.

    ``action`` is a short lowercase phrase ("config write", "memory read") that
    names what failed; it must not contain exception data.
    """
    (logger or _log).warning("%s failed", action, exc_info=(type(exc), exc, exc.__traceback__))
    return f"{action} failed. {LOG_HINT}"


__all__ = ["LOG_HINT", "internal_error"]
