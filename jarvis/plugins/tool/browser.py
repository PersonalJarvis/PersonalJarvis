"""browser tool: drive a real Chrome over CDP through browser-harness.

Why this exists (maintainer mandate 2026-08-13, docs/plans/autonomous-missions.md
C6/C12): until now the ONLY route into a web page was Computer-Use — screenshot
in, mouse out. That route has three defects for autonomous work, and all three
are structural rather than fixable:

1. It guesses click points from an image instead of addressing elements, so
   browser tasks were the most frequent failure.
2. It IS the physical pointer and the foreground window, so the user cannot
   keep working while it runs.
3. For exactly that reason mission workers are forbidden to touch it
   (``_FORBIDDEN_EXACT`` in jarvis/missions/workers/worker_tool_broker.py,
   AP-5/AP-14) — which left a sub-agent with no way into a web page at all.

CDP has none of them: elements are addressed by the accessibility tree, the
window needs no focus and may be minimised, and nothing about it is a shared
physical resource. So this tool is sub-agent-safe by construction and is
deliberately NOT added to the worker denylist.

Computer-Use is not deprecated by this. It remains the route for native desktop
applications and for the case where the user explicitly asks for screen
control; the router prompt states the ladder (plugin/MCP -> browser -> screen).

## Why a script, not a click/type/read action set

An action-per-call tool would spend one brain round per click. A single booking
flow is easily a hundred interactions, which is precisely the iteration budget
this plan exists to escape. browser-harness accepts Python with its helpers
pre-imported, so one call can navigate, read the accessibility tree, click,
verify and return a distilled result. The executed code runs inside the
browser-harness process, never in ours.

Risk tier is "monitor" (logged, no confirmation) because reading and navigating
the web is not a destructive act. Purchase-shaped work escalates to "ask" via
``risk_tier_for_args`` — see the honest limits documented there.

## Logging in

A script may reference the user's stored logins as ``SECRET("service", "field")``
and NEVER as a literal value. ``jarvis.logins.injection`` swaps the
reference for the real value on the way to this subprocess's stdin, and redacts
it back out of stdout — so the password exists in the piped bytes and nowhere
else. The model writes the reference, the logged tool call keeps the reference,
and the value is never in either. Read that module's docstring before touching
the two call sites below; the whole guarantee lives in those few lines.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from contextlib import suppress
from typing import Any, Final

from jarvis.core.protocols import ExecutionContext, ToolResult

log = logging.getLogger(__name__)

#: Executable names, in resolution order. The installer ships both a short
#: alias and the full name; on Windows the ``.cmd`` shim is what lands on PATH,
#: and ``shutil.which`` resolves it from the bare name via PATHEXT.
_BINARY_CANDIDATES: Final[tuple[str, ...]] = ("browser-harness", "bh")

#: Hard ceiling for one browser script. Deliberately generous — a real page
#: flow includes network waits and load settling — but bounded, because a
#: wedged CDP call must surface as a diagnosable failure rather than hold a
#: mission forever. A mission that needs more does several calls; C1 ("done
#: means done") lives at the mission level, never inside one tool call.
_TIMEOUT_S: Final[float] = 180.0

#: Result payload ceiling. The accessibility tree is thousands of nodes and a
#: naive dump would blow the loop's payload budget; the skill instructs
#: filtering in Python before printing, and this is the backstop for when the
#: model forgets.
_MAX_OUTPUT_CHARS: Final[int] = 12_000

#: Purchase / payment vocabulary (de/en/es). A match escalates the call to the
#: "ask" tier so the user confirms before money can move.
#:
#: HONEST LIMIT — read before trusting this: the brake reads the *stated goal*
#: and the *literal text* of the script. A checkout button clicked blindly via
#: coordinates carries none of these words, so this cannot be a guarantee. It
#: is the first stage of contract rule C11, and the real brake belongs at the
#: mission layer where the intent is known before the clicking starts. Do not
#: remove this one when that lands — defence in depth is the point.
_PURCHASE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    r"\bcheckout\b|\bcheck\s?out\b|\bpayment\b|\bpay\s?now\b|\bplace\s+order\b"
    r"|\bbuy\b|\bpurchase\b|\bsubscribe\b|\bcredit\s?card\b|\bpaypal\b"
    r"|\bkauf\w*|\bbestell\w*|\bzahlungspflichtig\b|\bbezahl\w*|\bkasse\b"
    r"|\bkreditkarte\b|\bwarenkorb\b"
    r"|\bcomprar\b|\bpagar\b|\bpago\b|\btarjeta\b"
    r")",
    re.IGNORECASE,
)


def _resolve_binary() -> str | None:
    """Absolute path to the browser-harness executable, or None if absent.

    Resolution is PATH-based on every OS — no hardcoded install location, so a
    Linux server that installed it elsewhere works unchanged (§3).
    """
    for candidate in _BINARY_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text
    return text[:_MAX_OUTPUT_CHARS] + "\n… [output truncated]"


class BrowserTool:
    """Run one browser-harness script against a real Chrome and return its output."""

    name: str = "browser"
    risk_tier: str = "monitor"
    description: str = (
        "Control a real web browser to read pages and interact with websites: "
        "navigate, click, type, fill forms, read results, work inside a "
        "logged-in session. This is the RIGHT tool for anything that happens on "
        "a web page and is not covered by a dedicated plugin — booking sites, "
        "shops, webmail without an integration (e.g. GMX), airline and hotel "
        "sites, any portal. Prefer a dedicated plugin or MCP tool when one "
        "covers the service (Gmail, Calendar, Drive); use this when none does. "
        "Do NOT use screen control (computer-use) for web pages — this tool is "
        "faster, more reliable, needs no foreground window, and leaves the "
        "user's mouse and keyboard free.\n\n"
        "To log in somewhere, never type a password you were told — call "
        "credentials(action='find', url=...) first, and if an entry exists put "
        "SECRET(\"<service_id>\", \"password\") and SECRET(\"<service_id>\", "
        "\"username\") straight into the script. Those placeholders are filled "
        "in on the way to the browser; you never see the values, and you must "
        "never print a credential field.\n\n"
        "You write a short Python script. These helpers are pre-imported: "
        "new_tab(url) for the first navigation, goto_url(url) afterwards, "
        "wait_for_load(), ensure_real_tab(), page_info(), js(code) to inspect "
        "or extract from the DOM, click_at_xy(x, y), and cdp(method, **params) "
        "for raw CDP. Find elements through the accessibility tree "
        "(cdp('Accessibility.getFullAXTree')['nodes']) and FILTER IN PYTHON "
        "before printing — it is thousands of nodes. Print only the distilled "
        "result you actually need; whatever the script prints is what you get "
        "back. Verify an action took effect before reporting it as done."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": (
                    "What this script is meant to achieve, in one short plain "
                    "sentence, e.g. 'read the flight results for Nice on 12 "
                    "September'. Shown to the user as the live activity label "
                    "and used by the purchase brake — always state it honestly."
                ),
            },
            "script": {
                "type": "string",
                "description": (
                    "Python for browser-harness. Helpers are pre-imported; no "
                    "imports or boilerplate needed. Print the distilled result."
                ),
            },
        },
        "required": ["goal", "script"],
    }

    def __init__(self, *, timeout_s: float = _TIMEOUT_S) -> None:
        self._timeout_s = timeout_s

    def risk_tier_for_args(self, args: dict[str, Any]) -> str | None:
        """Escalate purchase-shaped work to the confirmation tier (C11).

        Returns None for the ordinary case, which leaves the declared
        "monitor" tier in place. See ``_PURCHASE_RE`` for what this can and
        cannot catch.
        """
        haystack = f"{args.get('goal', '')}\n{args.get('script', '')}"
        if _PURCHASE_RE.search(haystack):
            log.info("browser tool: purchase vocabulary matched — escalating to ask tier")
            return "ask"
        return self._credential_tier(args.get("script") or "")

    @staticmethod
    def _credential_tier(script: str) -> str | None:
        """Ask before the FIRST login with a given stored credential.

        The agreed rule: confirm once per service, then stay quiet. "Once" is
        the record's own status — an entry that has never produced a successful
        login is unproven, and the first use is exactly where the user wants to
        see that the right site was picked. A rejected login flips it back to
        unproven by itself, so a changed password surfaces as a question rather
        than as a silent failure loop.

        Fail-closed: if the vault cannot be read, ask. A confirmation prompt is
        a small cost; a silent unattended login attempt on an unverified record
        is not.
        """
        from jarvis.logins.injection import (  # noqa: PLC0415
            has_secret_placeholder,
            needs_confirmation,
            placeholder_services,
        )

        if not has_secret_placeholder(script):
            return None
        try:
            from jarvis.logins import store as store_module  # noqa: PLC0415

            if needs_confirmation(
                placeholder_services(script), store_module.default_store()
            ):
                log.info("browser tool: unproven stored login — escalating to ask tier")
                return "ask"
        except Exception:  # noqa: BLE001 -- an unreadable vault must not silently proceed
            log.warning("browser tool: could not read the login vault — asking first")
            return "ask"
        return None

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        script = (args.get("script") or "").strip()
        goal = (args.get("goal") or "").strip()
        if not script:
            return ToolResult(
                success=False,
                output=None,
                error="No script given — pass the Python to run in the browser.",
            )

        binary = _resolve_binary()
        if binary is None:
            # Honest degradation (§3): name the one thing that fixes it and
            # tell the model what to do meanwhile, rather than failing blankly.
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "browser-harness is not installed on this machine, so there "
                    "is no browser to drive. Install it "
                    "(https://github.com/browser-use/browser-harness), or use a "
                    "dedicated plugin for this service if one is connected."
                ),
            )

        log.info("browser tool: %s", goal or "(no goal stated)")

        # Fill in any SECRET("service", "field") reference the model wrote. This
        # is the ONLY place a stored password enters the picture, and it never
        # goes further than this subprocess's stdin — see the module docstring.
        from jarvis.logins.injection import (  # noqa: PLC0415
            SecretResolutionError,
            resolve_secrets,
            scrub_secrets,
        )

        try:
            resolution = resolve_secrets(script)
        except SecretResolutionError as exc:
            # Honest, actionable failure: the model cannot create a login, so
            # say what is missing instead of letting an undefined name blow up
            # inside the harness with a message that explains nothing.
            return ToolResult(success=False, output=None, error=str(exc))
        if resolution.touched_vault:
            log.info(
                "browser tool: using stored login(s) for %s",
                ", ".join(resolution.services),
            )

        # UTF-8 on every platform (§5): the harness prints page text, and the
        # Windows OEM codepage would mangle any non-ASCII content on the way
        # back — the bug class recorded in the Windows deep-dive.
        env = dict(os.environ)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")

        creationflags = 0
        if sys.platform == "win32":
            from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS  # noqa: PLC0415

            creationflags = NO_WINDOW_CREATIONFLAGS

        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
                creationflags=creationflags,
            )
        except OSError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"Could not start browser-harness: {exc}",
            )

        try:
            async with asyncio.timeout(self._timeout_s):
                stdout, _ = await proc.communicate(resolution.script.encode("utf-8"))
        except TimeoutError:
            proc.kill()
            # Reap the process so no zombie is left behind; a kill that races
            # with normal exit must not turn into a second, misleading error.
            with suppress(Exception):
                await proc.wait()
            return ToolResult(
                success=False,
                output=None,
                error=(
                    f"The browser script did not finish within {self._timeout_s:.0f} s "
                    "and was stopped. Split the work into smaller steps, or wait "
                    "for the page explicitly before reading it."
                ),
            )

        # Redact before truncating, and before ANY return path can see it: a
        # script that printed a credential field would otherwise hand the value
        # straight back to the model through the tool result. The skill tells
        # scripts never to print one; this is the backstop for when it happens
        # anyway, including on the failure path below.
        output = _truncate(
            scrub_secrets(
                stdout.decode("utf-8", errors="replace").strip(),
                resolution.secrets,
            )
        )

        if proc.returncode != 0:
            return ToolResult(
                success=False,
                output=output or None,
                error=(
                    f"The browser script failed (exit {proc.returncode}). The output "
                    "above is what the harness reported — read it and try a different "
                    "route before concluding this is impossible."
                ),
            )

        return ToolResult(success=True, output=output or "(script produced no output)")


__all__ = ["BrowserTool"]
