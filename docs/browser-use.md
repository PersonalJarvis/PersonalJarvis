# Managed agent browser

Jarvis provisions an isolated Browser-Use Python environment and a managed
browser automatically during installation and after the server becomes ready.
Opening an agent subscribes to that agent's actual rendered tab in the right
Options rail. The browser remains open between tasks. Expand the view and take
control to navigate or sign in, then return control to the agent.

## Runtime and data

Browser-Use 0.13.10 and Playwright 1.62.0 are pinned independently of the app's
Python dependencies. The complete dependency graph is locked with hashes for
all platforms. The installer downloads the browser revision selected by that
Playwright version, checks a real browser launch, text input and image
decode, and atomically records readiness. Failed upgrades do not replace the
previous runtime. The manifest under the configured data directory records
versions and the installed Python package/license inventory.

The pip bootstrap is pinned separately. Windows ARM uses a managed x64 Python
helper through Windows emulation because the crypto stack lacks ARM wheels.

This Playwright version delivers **Chrome for Testing 151.0.7922.34**, based on
Chromium. It does not overwrite the user's installed Chrome. Each agent owns
a separate persistent profile and workspace. Raw CDP ports are local and are
never provided to the frontend. The frontend uses the app's authenticated
WebSocket boundary and short-lived handshake tickets.

Browser-Use cloud sessions, telemetry and automatic browser extensions are
disabled. Model requests use the existing Jarvis provider interface and
credentials. A supported text-only subscription or local model can operate
from DOM observations; a model rejected for image input retries without images.
Website login, MFA and site-imposed restrictions still require user interaction.

## Licenses

Browser-Use is MIT-licensed. Its license is shipped in
[the browser asset notices](../jarvis/assets/browser/LICENSE.browser-use).
Installed wheels retain their distribution license files. Playwright uses
Apache-2.0; its downloaded browser preserves the vendor's ABOUT and built-in
credits/terms. Chrome for Testing is a Google Chrome distribution, so the
Chromium source license must not be mistaken for the only applicable notice.

Primary references:
- [Browser-Use license](https://github.com/browser-use/browser-use/blob/0.13.10/LICENSE)
- [Playwright license](https://github.com/microsoft/playwright/blob/main/LICENSE)
- [Chromium license](https://chromium.googlesource.com/chromium/src/+/main/LICENSE)
- [Chrome terms](https://www.google.com/chrome/terms/)

Do not remove package notices or browser credits when packaging or mirroring
runtime artifacts. The isolated environment's manifest inventories the actual
versions, including optional platform dependencies.

## Verification

Windows: real managed install and rendered frames verified; a real Codex
subscription task filled and submitted an isolated test form, and a real
OpenRouter key completed the same task after DOM fallback. A live Chrome UI
check verified the right-rail browser and the @browser entry.

Linux: Browser-Use/Playwright installed in python:3.11-slim with browser system
libraries; actual headless launch, text input and screenshot decoding passed.
The application container includes the required native libraries.

The browser-runtime workflow exercises the managed install and real browser
contract on Windows, macOS and Linux. Native macOS results and complete
cross-platform UI acceptance must be read from actual CI/host runs; the existence
of the workflow is not evidence of a pass.

Run the deterministic suite with:
```
python -m pytest tests/contract/test_browser_contract.py tests/unit/society/test_browser.py
```
Real-browser tests use JARVIS_BROWSER_TEST_PYTHON and
JARVIS_BROWSER_TEST_EXECUTABLE to select an isolated verified environment.
Live model tests additionally require JARVIS_BROWSER_MODEL_TEST=1 and an
explicit test data root; they never use real third-party forms.
