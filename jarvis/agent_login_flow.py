"""In-app guided sign-in for agent accounts — the CLI's login over a hidden PTY.

Why this exists: the external sign-in window (:func:`jarvis.agent_accounts.
start_login`) opens the platform's raw console, and on Windows that console is
where the flow kept dying — pasting the OAuth code renders late or not at all,
Enter submits a half-arrived paste, and the single-use code is burned
("Invalid code" on every retry). On top of that the CLI auto-opens the OAuth
URL in the DEFAULT browser, which for a second subscription is usually signed
in as the WRONG account — the user needs the URL itself, to open in a private
window or another profile, and a console is the worst place to copy a
400-character line from.

So the same login runs here instead, invisibly, over the existing AD-6 PTY
seam (:func:`jarvis.terminal.backend.make_pty_backend`), and the app shows a
proper surface: the URL as a copyable link, an input for the code, and honest
progress. Nothing about the flow itself is reimplemented — it is still each
CLI's own interactive login pointed at the account's directory, the promise
:mod:`jarvis.agent_accounts` is built on. This module never parses, stores or
forwards a credential; the pasted code goes straight into the CLI's stdin and
success is judged by what the CLI wrote into the account directory
(:func:`jarvis.agent_accounts.describe`), never by trusting our own reading of
its output.

The PTY is deliberately WIDE (:data:`_COLS`): a terminal hard-wraps at its
width, and a wrapped OAuth URL scanned line-wise yields a truncated link — a
copy button that copies a broken URL is worse than no button. At 1000 columns
no real OAuth URL wraps.

Cross-platform: the PTY seam covers Windows (ConPTY), macOS and Linux, and —
unlike the external window — a headless server can complete this flow too (the
URL is copied out of the web UI into any browser anywhere). A host with no PTY
capability gets :class:`GuidedLoginUnavailable` with the external window as
the stated fallback, not a silent hang.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from jarvis import agent_accounts
from jarvis.agent_accounts import AgentAccount

#: Wide enough that no OAuth URL is ever hard-wrapped by the terminal (module
#: docstring) — a wrapped URL is what breaks the copy button.
_COLS = 1000
_ROWS = 50
_READ_SIZE = 8192
#: Idle back-off between empty reads; also the cadence cancellation is noticed.
_POLL_S = 0.05
#: An OAuth code is only valid for minutes; a flow nobody finishes must not
#: keep a hidden CLI process alive forever.
_FLOW_TIMEOUT_S = 15 * 60
#: How often the account directory is re-checked for a completed login while
#: the child is still running (an old CLI's TUI keeps running after success).
_VERIFY_EVERY_S = 2.0
#: How much cleaned transcript the state carries for the UI ("what is the CLI
#: asking right now?") — display context, not a log.
_TAIL_CHARS = 1000
#: RAW transcript kept and re-stripped per read. Raw, because a PTY read can
#: end in the middle of an escape sequence — stripping per chunk would leak the
#: torn halves into the text, possibly inside the URL being scanned for.
_SCAN_CHARS = 32_000

_URL_RE = re.compile(r"https://[^\s\"'<>\\`]+")
#: Which of several printed URLs is THE sign-in link. Every supported CLI's
#: login URL carries one of these; docs or support links printed alongside
#: (e.g. an error's "see https://docs...") do not.
_AUTH_URL_HINT = re.compile(r"oauth|authorize|auth\.|login|device", re.IGNORECASE)
#: The CLI is asking for the code from the browser.
_CODE_PROMPT_RE = re.compile(
    r"paste\s+(?:the\s+)?code|code\s+here|authorization\s+code|enter\s+(?:the\s+)?code",
    re.IGNORECASE,
)

#: Terminal statuses — the flow is over and the child process is gone.
_FINISHED = frozenset({"success", "failed", "cancelled"})


class GuidedLoginUnavailable(RuntimeError):
    """This host cannot run a hidden PTY — offer the external window instead."""


@dataclass
class _Flow:
    """One guided sign-in in progress. All mutable state behind ``lock``."""

    flow_id: str
    account: AgentAccount
    lock: threading.Lock = field(default_factory=threading.Lock)
    #: starting → awaiting_input → verifying → success | failed | cancelled
    status: str = "starting"
    url: str | None = None
    #: True once the CLI asked for a code — the UI shows the input then.
    code_expected: bool = False
    message: str = ""
    tail: str = ""
    created_at: float = field(default_factory=time.monotonic)
    handle: Any = None
    tree: Any = None
    stop: threading.Event = field(default_factory=threading.Event)
    _raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        with self.lock:
            return {
                "flow_id": self.flow_id,
                "account_id": self.account.id,
                "platform": self.account.platform,
                "label": self.account.label,
                "status": self.status,
                "url": self.url,
                "code_expected": self.code_expected,
                "message": self.message,
                "tail": self.tail,
                "finished": self.status in _FINISHED,
            }


_REGISTRY: dict[str, _Flow] = {}
_REGISTRY_LOCK = threading.Lock()


def _strip(text: str) -> str:
    from jarvis.agentic_ide.transcript import strip_ansi

    return strip_ansi(text)


def _visible_tail(cleaned: str) -> str:
    """The last screenful of readable lines, CR-overwrites honoured.

    A TUI repaints by rewriting lines with a leading CR; only what survives the
    last CR on each line is what the user would see. Blank runs collapse so the
    tail is text, not layout.
    """
    lines: list[str] = []
    for physical in cleaned.replace("\r\n", "\n").split("\n"):
        visible = physical.rsplit("\r", 1)[-1].rstrip()
        if visible.strip():
            lines.append(visible)
    return "\n".join(lines)[-_TAIL_CHARS:]


def _scan_for_url(cleaned: str) -> str | None:
    candidates = _URL_RE.findall(cleaned)
    if not candidates:
        return None
    trimmed = [c.rstrip(".,;:)]}\"'") for c in candidates]
    for candidate in trimmed:
        if _AUTH_URL_HINT.search(candidate):
            return candidate
    return trimmed[0]


def _connected(account: AgentAccount) -> bool:
    """Has a login actually landed in the account directory? The honest check."""
    try:
        return bool(agent_accounts.describe(account).connected)
    except Exception as exc:  # noqa: BLE001 — a probe failure is "not yet"
        logger.debug("Guided login: describe() failed during verify: {}", exc)
        return False


def _mark_onboarded(account: AgentAccount) -> None:
    """Record in the account's own config that first-run setup is behind it.

    Claude Code's wizard is keyed on ``hasCompletedOnboarding``, not on the
    credentials — without this, a freshly signed-in account still boots every
    new pane into "Select login method", which reads exactly like the login
    having failed (2026-08-08 report). Config parity carries the user's own
    marker across too; this stamp is for the machine where no native setup
    exists to carry it FROM. Only ever ADDS the key — a value the CLI wrote
    itself is never touched — and never fails the flow: the login is real
    whether or not this cosmetic marker could be written.
    """
    if account.platform != "claude":
        return
    import json

    from jarvis.agent_config_parity import setup_lock

    path = account.config_dir / ".claude.json"
    try:
        with setup_lock(account.config_dir):
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
            except (OSError, ValueError):
                # Missing or corrupt config is fine — it is just recreated below.
                data = {}
            if not isinstance(data, dict) or data.get("hasCompletedOnboarding"):
                return
            data["hasCompletedOnboarding"] = True
            tmp = path.with_name(f"{path.name}.{uuid4().hex[:8]}.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            import os

            os.replace(tmp, path)
    except OSError as exc:
        logger.debug("Guided login: onboarding marker not written: {}", exc)


def _finish(flow: _Flow, status: str, message: str) -> None:
    # BEFORE the status flips: anything that reacts to "success" — the dialog's
    # next poll, a pane respawn — must already find the marker on disk.
    if status == "success":
        _mark_onboarded(flow.account)
    with flow.lock:
        if flow.status in _FINISHED:
            return
        flow.status = status
        flow.message = message
    handle = flow.handle
    if handle is not None:
        try:
            if handle.isalive():
                handle.terminate(force=True)
        except Exception:  # noqa: BLE001, S110 — teardown is best-effort
            pass
    tree = flow.tree
    if tree is not None:
        flow.tree = None
        try:
            tree.close()
        except Exception as exc:  # noqa: BLE001 — reaping is best-effort
            logger.debug("Guided login: process-tree teardown failed: {}", exc)
    logger.info("Guided login {} for {!r}: {}", status, flow.account.label, message or "—")


def _ingest(flow: _Flow, data: str) -> None:
    """Fold one PTY read into the flow's visible state.

    The raw buffer is re-stripped as a whole each time (module constant) so an
    escape sequence torn across two reads never leaks its halves into the text
    the URL is scanned out of. Truncating the buffer can cut an old escape at
    the HEAD, which only garbles long-scrolled-away text, never the live tail.
    """
    with flow.lock:
        flow._raw = (flow._raw + data)[-_SCAN_CHARS:]
        cleaned = _strip(flow._raw)
        # A URL can arrive torn across two reads: the first half is already a
        # well-formed https URL, so "first match wins" would freeze a truncated
        # link into the copy button. Adopt a candidate that EXTENDS the current
        # one; an unrelated later URL never replaces the sign-in link.
        candidate = _scan_for_url(cleaned)
        if candidate is not None and (
            flow.url is None or (candidate.startswith(flow.url) and len(candidate) > len(flow.url))
        ):
            flow.url = candidate
        if not flow.code_expected and _CODE_PROMPT_RE.search(cleaned):
            flow.code_expected = True
        if flow.status == "starting" and (flow.url or flow.code_expected):
            flow.status = "awaiting_input"
        flow.tail = _visible_tail(cleaned)


def _reader(flow: _Flow) -> None:
    """Drain the hidden PTY until the login concludes — its own daemon thread."""
    handle = flow.handle
    deadline = flow.created_at + _FLOW_TIMEOUT_S
    last_verify = 0.0
    while not flow.stop.is_set():
        now = time.monotonic()
        if now >= deadline:
            _finish(
                flow,
                "failed",
                "The sign-in timed out — start it again for a fresh code.",
            )
            return
        try:
            data = handle.read(_READ_SIZE)
        except EOFError:
            # The PTY closed normally — the loop below already reports the
            # abnormal case (process died mid-read), so this one is quiet.
            break
        except Exception as exc:  # noqa: BLE001 — process died mid-read
            logger.debug("Guided login: PTY read ended: {}", exc)
            break
        if data:
            _ingest(flow, data)
        elif not handle.isalive():
            break
        else:
            flow.stop.wait(_POLL_S)
        # An old CLI's first-run TUI never exits after a successful login, and
        # codex signs in via a browser redirect with no code at all — so the
        # directory, not the process, is what says "done".
        if now - last_verify >= _VERIFY_EVERY_S:
            last_verify = now
            if _connected(flow.account):
                _finish(flow, "success", "Signed in.")
                return
    if flow.stop.is_set():
        return
    # The child exited by itself — the directory has the last word.
    if _connected(flow.account):
        _finish(flow, "success", "Signed in.")
        return
    with flow.lock:
        tail = flow.tail
    last_line = tail.splitlines()[-1].strip() if tail.splitlines() else ""
    _finish(
        flow,
        "failed",
        last_line or "The sign-in ended before a login was stored — try again.",
    )


def _drop_finished(now: float) -> None:
    """Forget concluded flows so polling a stale id answers 404, not history."""
    with _REGISTRY_LOCK:
        stale = [
            fid
            for fid, flow in _REGISTRY.items()
            if flow.status in _FINISHED and now - flow.created_at > 30 * 60
        ]
        for fid in stale:
            _REGISTRY.pop(fid, None)


def start_flow(account: AgentAccount) -> dict[str, Any]:
    """Begin a guided sign-in for *account*; any previous one is cancelled.

    Raises ``FileNotFoundError`` (CLI missing), ``AccountError`` (no login
    command / unusable directory) and :class:`GuidedLoginUnavailable` (no PTY
    capability on this host) — each a distinct, actionable answer.
    """
    from jarvis.core.process_tree import make_process_tree
    from jarvis.terminal.backend import make_pty_backend

    argv, _title = agent_accounts.login_command(account)
    agent_accounts.ensure_config_dir(account)
    env = agent_accounts.spawn_env(account.platform, account.id)

    now = time.monotonic()
    _drop_finished(now)
    with _REGISTRY_LOCK:
        running = [
            flow
            for flow in _REGISTRY.values()
            if flow.account.id == account.id and flow.status not in _FINISHED
        ]
    for flow in running:
        cancel_flow(flow.flow_id)

    try:
        handle = make_pty_backend().spawn(
            argv=tuple(argv),
            cwd=str(Path.home()),
            cols=_COLS,
            rows=_ROWS,
            env=env,
        )
    except RuntimeError as exc:
        raise GuidedLoginUnavailable(
            f"This host cannot run the sign-in in-app ({exc}) — "
            "use the terminal-window sign-in instead."
        ) from exc

    flow = _Flow(flow_id=uuid4().hex, account=account)
    flow.handle = handle
    flow.message = "Starting the sign-in…"
    flow.tree = make_process_tree(f"login:{account.id}")
    try:
        flow.tree.assign(int(getattr(handle, "pid", 0) or 0))
    except Exception as exc:  # noqa: BLE001 — containment is a safeguard, not a gate
        logger.warning("Guided login could not be contained: {}", exc)
    with _REGISTRY_LOCK:
        _REGISTRY[flow.flow_id] = flow
    threading.Thread(
        target=_reader,
        name=f"login-flow-{flow.flow_id[:8]}",
        args=(flow,),
        daemon=True,
    ).start()
    logger.info("Guided login started for {!r} ({})", account.label, account.platform)
    return flow.to_dict()


def flow_state(flow_id: str) -> dict[str, Any] | None:
    with _REGISTRY_LOCK:
        flow = _REGISTRY.get(flow_id)
    return flow.to_dict() if flow is not None else None


def submit_code(flow_id: str, code: str) -> dict[str, Any] | None:
    """Hand the pasted code to the CLI, exactly as typing it would.

    The code is passed through untouched apart from surrounding whitespace —
    this module has no opinion about its shape, only the CLI does. Control
    characters are refused because no OAuth code contains one and a stray
    escape sequence pasted by accident would drive the TUI blind.
    """
    with _REGISTRY_LOCK:
        flow = _REGISTRY.get(flow_id)
    if flow is None:
        return None
    value = code.strip()
    if not value:
        raise ValueError("The code is empty — paste the code from the browser.")
    if any(ch < " " for ch in value):
        raise ValueError("The code contains control characters — copy it again.")
    with flow.lock:
        if flow.status in _FINISHED:
            return flow.to_dict()
        handle = flow.handle
    try:
        handle.write(value + "\r")
    except Exception as exc:  # noqa: BLE001 — the child died under the write
        _finish(flow, "failed", f"The sign-in process is gone ({exc}) — try again.")
        return flow.to_dict()
    with flow.lock:
        if flow.status not in _FINISHED:
            flow.status = "verifying"
            flow.message = "Code sent — waiting for the CLI to confirm…"
    return flow.to_dict()


def cancel_flow(flow_id: str) -> dict[str, Any] | None:
    with _REGISTRY_LOCK:
        flow = _REGISTRY.get(flow_id)
    if flow is None:
        return None
    flow.stop.set()
    _finish(flow, "cancelled", "Sign-in cancelled.")
    return flow.to_dict()


__all__ = [
    "GuidedLoginUnavailable",
    "cancel_flow",
    "flow_state",
    "start_flow",
    "submit_code",
]
