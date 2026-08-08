"""Human-presence boundary for desktop restart and quit actions."""

from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Request

LifecycleAction = Literal["restart", "quit"]


def require_interactive_desktop_action(
    request: Request,
    *,
    action: LifecycleAction,
    require_origin: bool = False,
) -> None:
    """Reject lifecycle actions issued by Control-API or non-browser clients.

    The desktop UI uses an HttpOnly session cookie (or trusted loopback browser
    access) and never sends ``Authorization``. Coding agents and ``jarvis`` CLI
    clients use the persistent Control-API Bearer, so a Bearer is authoritative
    evidence that no desktop-UI click authorized this action.

    ``require_origin`` closes the same gap on auth-exempt first-boot onboarding
    routes: browsers attach Origin to unsafe same-origin requests, while a local
    shell client does not. SurfaceSecurity validates the value itself before a
    production request reaches this helper.
    """
    headers = getattr(request, "headers", None)
    authorization = headers.get("authorization", "") if headers is not None else ""
    origin = headers.get("origin", "") if headers is not None else ""
    if authorization.strip() or (require_origin and not origin.strip()):
        raise HTTPException(
            status_code=403,
            detail={
                "error": f"interactive_{action}_required",
                "message": (
                    f"Desktop {action} actions require an explicit click in the "
                    "desktop UI; Control API and coding-agent clients cannot "
                    f"{action} the app."
                ),
            },
        )


__all__ = ["require_interactive_desktop_action"]
