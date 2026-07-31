"""Make the desktop window answer the FIRST accessibility question truthfully.

Dictation apps, text expanders, clipboard managers, password-manager auto-type
and grammar/translation overlays all work the same way: at the moment they are
about to act they ask the operating system's accessibility layer "what field
has focus in the window in front?", and they act on that one answer.

**Chromium builds that tree lazily, and the query that triggers the build is
answered before it finishes.** So the first client to ask a cold window gets a
near-empty stub, and only a later question sees the real thing. Measured on
this window with an ordinary automation client (2026-07-26):

    without the flag   first query:  23 elements   second query: 547
    with the flag      first query: 547 elements   second query: 547

Reproduced twice on a purpose-built throwaway window, and the same 23 came back
from the live app. A tool that asks once — which is all of them — therefore
sees a window containing no text fields at all, and either pastes blindly or
refuses to do anything. Whether it works depends on whether something else
happened to warm the tree first, which is exactly why this reads to a user as
"some of my tools randomly don't work in this app".

``--force-renderer-accessibility`` tells Chromium to build the tree up front
instead. The cost is the memory of holding it; the benefit is that assistive
software — including real screen readers, not only the convenience tools that
prompted this — sees the window the way it sees every other application.

Windows only, because the problem is: WKWebView on macOS and WebKitGTK/AT-SPI
on Linux do not answer a first query from an unbuilt tree. There the function
reports an honest no-op rather than pretending to have done something.
"""

from __future__ import annotations

import logging
import os
from collections.abc import MutableMapping

from . import detect_platform

log = logging.getLogger(__name__)

#: Read by the WebView2 loader when it creates the browser process. Going
#: through the environment rather than the host's own creation properties is
#: deliberate: pywebview hardcodes ``AdditionalBrowserArguments`` and offers no
#: hook, and this variable was measured to take effect anyway.
WEBVIEW2_ARGS_ENV = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"

#: Build the accessibility tree up front instead of on first request.
FORCE_ACCESSIBILITY_FLAG = "--force-renderer-accessibility"


def enable_webview_accessibility_tree(
    *,
    env: MutableMapping[str, str] | None = None,
    platform=detect_platform,
) -> bool:
    """Ask the embedded browser to keep a built accessibility tree.

    Must run **before** the WebView is created — the flag is read once, when
    the browser process starts.

    Returns ``True`` when the flag is in place for the next WebView, ``False``
    on a platform that does not need it. Never raises: an app that cannot start
    because a convenience flag could not be set would be a far worse bug than
    the one this fixes.
    """
    target = os.environ if env is None else env

    if platform() != "win32":
        log.debug(
            "webview accessibility: no flag needed on %s — its web view does "
            "not defer building the tree",
            platform(),
        )
        return False

    existing = target.get(WEBVIEW2_ARGS_ENV, "")
    if FORCE_ACCESSIBILITY_FLAG in existing:
        return True

    # Appended, never assigned: the variable is a shared Chromium channel and
    # something else — the user, a launcher, a debugging session — may already
    # be steering the browser through it.
    target[WEBVIEW2_ARGS_ENV] = (
        f"{existing} {FORCE_ACCESSIBILITY_FLAG}".strip()
        if existing
        else FORCE_ACCESSIBILITY_FLAG
    )
    log.info(
        "webview accessibility: assistive software will see this window's "
        "fields on its first query"
    )
    return True


__all__ = [
    "FORCE_ACCESSIBILITY_FLAG",
    "WEBVIEW2_ARGS_ENV",
    "enable_webview_accessibility_tree",
]
