"""WebView2 runtime check for Windows 11.

**Context:** On Windows 11 the evergreen WebView2 runtime is preinstalled by
default ([Microsoft Learn: Evergreen vs. Fixed][ms]). LTSC, Validation,
and old Enterprise images can be missing it, though, and so can custom
deployments. This check prevents pywebview from crashing at startup with a
cryptic `InitializationError`.

[ms]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version
"""
from __future__ import annotations

from dataclasses import dataclass

WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# 64-bit installations live under the WOW6432Node path (Edge is registered as a
# 32-bit client, but the binaries themselves are 64-bit).
_CANDIDATE_KEYS: tuple[tuple[int, str], ...] = (
    (0x80000002, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
    (0x80000002, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
    (0x80000001, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"),
)
# Constants: HKEY_LOCAL_MACHINE=0x80000002, HKEY_CURRENT_USER=0x80000001.


@dataclass(slots=True)
class WebView2CheckResult:
    installed: bool
    version: str | None
    scope: str                      # "machine" | "user" | "none"
    bootstrapper_url: str = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"


def check_webview2() -> WebView2CheckResult:
    """Checks whether the WebView2 runtime is installed.

    Returns version + scope; `installed=False` if it's missing.
    No raise — the caller decides whether to abort or redirect the user to
    the bootstrapper download.
    """
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        # Non-Windows — WebView2 is irrelevant, pywebview uses other engines.
        return WebView2CheckResult(installed=True, version=None, scope="none")

    for hive_key, subkey in _CANDIDATE_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEYType(hive_key), subkey) as key:  # type: ignore[attr-defined]
                version, _ = winreg.QueryValueEx(key, "pv")
                if version:
                    scope = "machine" if hive_key == 0x80000002 else "user"
                    return WebView2CheckResult(
                        installed=True, version=str(version), scope=scope
                    )
        except OSError:
            continue

    # Fallback: the HKEYType derivation above fails on some systems.
    # Direct access via the OpenKey constants.
    try:
        import winreg  # type: ignore[import-not-found]

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (
                rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}",
                rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}",
            ):
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        version, _ = winreg.QueryValueEx(key, "pv")
                        if version:
                            scope = (
                                "machine"
                                if root == winreg.HKEY_LOCAL_MACHINE
                                else "user"
                            )
                            return WebView2CheckResult(
                                installed=True,
                                version=str(version),
                                scope=scope,
                            )
                except OSError:
                    continue
    except ImportError:
        pass

    return WebView2CheckResult(installed=False, version=None, scope="none")
