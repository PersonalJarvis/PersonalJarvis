# Managed agent browser

Jarvis provisions an isolated Browser-Use Python environment and a managed
browser automatically during installation and after the server becomes ready.
Opening an agent subscribes to that agent's actual rendered tab in the right
Options rail. The browser remains open between tasks. Expand the view and take
control to navigate or sign in, then return control to the agent.

The Jarvis root chat uses the lead agent's same browser profile and its selected
chat model. Its Add picker includes the browser tool. Stopping the browser task
also stops the owning chat, whether it is the root chat or a specialist session.

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

The managed installation and real-browser contracts passed on Linux x64/ARM64,
Windows x64/ARM64, and macOS Intel/Apple Silicon in
[the six-platform run](https://github.com/PersonalJarvis/PersonalJarvis/actions/runs/34451340158).
Subsequent changes must pass the same matrix before integration.

A full ordinary agent-chat turn selected the browser capability, submitted the
disposable form and verified its success heading using one existing OpenRouter
key. The browser job completed in three steps; the chat finished in 23.7 seconds.
The root Jarvis chat separately completed the same real form workflow through
its selected OpenRouter model in 31.8 seconds, without changing the lead's saved
provider/model settings. Real file tests also cover upload, completed downloads,
workspace containment and omission of old downloads from a later task's result.
Real-browser tests cover pause/resume of the same task, cancelled takeover,
exclusive ownership and idle animation. A 60-second local Windows animation
soak delivered 13.61 fps with capture-to-backend p95 age of 74.56 ms. This is a
transport measurement, not a measurement of the final frontend paint latency.

The corresponding Chrome canvas measurement ran for 188 seconds in the actual
Options rail: 2,536 rendered frames, 13.48 fps, and 133.45 ms p95 capture-to-paint
age. The agent was idle throughout. These are local Windows measurements,
not a performance guarantee for every host or network.

Chrome checks verified the actual right-rail pixels, expanded view, manual text
entry and form submission, tab creation/switching, reconnection and light/dark
appearance. These UI checks were on Windows; the native CI matrix exercises
the shared headless browser contract, not a desktop UI on every host.

Run the deterministic suite with:
```
python -m pytest tests/contract/test_browser_contract.py tests/unit/society/test_browser.py
```
Real-browser tests use JARVIS_BROWSER_TEST_PYTHON and
JARVIS_BROWSER_TEST_EXECUTABLE to select an isolated verified environment.
Set JARVIS_BROWSER_SOAK_SECONDS=60 for the longer animation measurement.
Live model tests additionally require JARVIS_BROWSER_MODEL_TEST=1 and an
explicit test data root; they never use real third-party forms.
