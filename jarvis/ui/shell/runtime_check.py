"""WebView2-Runtime-Check für Windows 11.

**Kontext:** Auf Windows 11 ist die Evergreen-WebView2-Runtime standardmässig
vorinstalliert ([Microsoft Learn: Evergreen vs. Fixed][ms]). LTSC-, Validation-
und alte Enterprise-Images können sie jedoch missen, und Custom-Deployments
ebenfalls. Dieser Check verhindert, dass pywebview beim Start mit einer
kryptischen `InitializationError` bricht.

[ms]: https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/evergreen-vs-fixed-version
"""
from __future__ import annotations

from dataclasses import dataclass

WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

# 64-Bit-Installationen liegen unter dem WOW6432Node-Pfad (Edge ist als 32-Bit
# Client registriert, die Binaries selbst sind aber 64-Bit).
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
    """Prüft ob WebView2-Runtime installiert ist.

    Gibt Version + Scope zurück; bei fehlender Installation `installed=False`.
    Kein Raise — der Aufrufer entscheidet ob er abbricht oder den User zum
    Bootstrapper-Download weiterleitet.
    """
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        # Nicht-Windows — WebView2 ist irrelevant, pywebview nutzt andere Engines.
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

    # Fallback: die HKEYType-Ableitung oben schlägt auf manchen Systemen fehl.
    # Direkter Zugriff über die OpenKey-Constants.
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
