"""The desktop window's last line of defence: never stay blank in silence.

``frontend/index.html`` already carries a watchdog, and it handles the case it
was written for — the page loaded, then the app above it died. It cannot handle
the case below it: a window in which **no page ever arrived**. Then there is no
script to run, no ``#root`` to write into, and no splash. What the user gets is
the native frame filled with the theme's ground colour, a title bar, and no way
back except killing the app (maintainer screenshots 2026-08-18 → 2026-08-20).

That state is reachable without anything being permanently broken, which is why
it kept coming back:

* **The server accepted but did not answer.** The whole backend — HTTP included
  — runs on one asyncio loop. Any synchronous call on that loop freezes the
  server without closing the socket, so the navigation gets no bytes and no
  error either; WebView2 keeps waiting and paints nothing. Measured on the
  maintainer's box the same day: 15 s, 20 s, 42 s, 61 s and 188 s of loop
  blockage, the long ones inside PortAudio's ``Pa_CloseStream`` during a
  microphone restart. ``_wait_for_backend`` had seen a healthy ``/api/health``
  moments earlier, so the window was already open when the freeze began.
* **The backend thread died after the port was up.** On 2026-08-20 13:45 an
  ``import uvicorn`` lost its ``click`` dependency mid-boot; the thread died and
  the window kept its ground colour for the rest of the process.
* **The web view itself never navigated** — a broken profile, a backend that
  could not start.

So the watchdog goes where it still runs in all three: the window process,
outside the loop it is watching. It asks the window what it is showing, waits
out a slow boot, reloads when the server comes back, and — when it cannot fix
it — puts the reason ON the window with a button, in the user's language. The
window heals itself first and explains itself second; a dark rectangle with no
words is the actual bug, whatever caused it.

The policy is a pure state machine (:class:`BlankWindowPolicy`) so every branch
is unit-testable without pywebview, a browser, or a clock.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

from loguru import logger

#: How often the window is asked what it is showing.
POLL_S = 1.0

#: How long a window may show nothing before that counts as a failure. A cold
#: WebView2 on a busy machine needs several seconds before it paints the boot
#: splash, and the splash itself is the proof that the page arrived — so this
#: only has to outlast browser start-up, not bundle loading (which the page's
#: own watchdog owns once it is running).
FIRST_PAINT_GRACE_S = 25.0

#: After a reload, the same grace applies again before judging the result.
RELOAD_SETTLE_S = 25.0

#: Automatic reloads per incident. Three is enough to ride out a long loop
#: freeze; beyond that a reload is not the answer and the window says so.
RELOAD_BUDGET = 3

#: How long a silent server is tolerated before the window explains itself. The
#: watchdog keeps watching afterwards and still reloads when the server returns,
#: so this bounds the silence the user sees, not the recovery.
SILENT_PATIENCE_S = 45.0

#: What the window is showing.
#:
#: ``up``     — our document is loaded (``#root`` exists). From here the page's
#:              own watchdog is in charge; this one only guards the frame.
#: ``notice`` — the explanation page this watchdog wrote.
#: ``blank``  — no document of ours: nothing ever arrived, or the view is empty.
PageState = Literal["up", "notice", "blank"]

#: One expression, because it runs in a document that may be barely alive.
#: ``#root`` is in the static HTML, so its presence proves our page arrived —
#: whether React has mounted yet or not.
PAGE_STATE_JS = (
    "(function(){try{"
    "if(window.__jarvisWatchdogNotice)return 'notice';"
    "return document.getElementById('root')?'up':'blank';"
    "}catch(e){return 'blank';}})()"
)


class BlankReason(Enum):
    """Why a window is blank — each one is a different sentence to the user.

    The ``value`` doubles as the key into the page's text table, so a new
    reason cannot be added without a sentence to go with it.
    """

    #: The backend thread is gone; nothing will serve this window again.
    BACKEND_DEAD = "backend_dead"
    #: The thread lives but the server does not answer (blocked loop, or dead
    #: listener). Recoverable: it usually comes back.
    BACKEND_SILENT = "backend_silent"
    #: The server answers, yet the window has no page — the web view did not
    #: navigate, or lost what it had.
    VIEW_EMPTY = "view_empty"


class Action(Enum):
    """What the watchdog does with this observation."""

    WAIT = "wait"
    RELOAD = "reload"
    EXPLAIN = "explain"


@dataclass(frozen=True, slots=True)
class Observation:
    """Everything one decision is made from."""

    page: PageState
    backend_alive: bool
    server_healthy: bool
    now: float


@dataclass(frozen=True, slots=True)
class Verdict:
    """A decision plus the reason to show, when there is one."""

    action: Action
    reason: BlankReason | None = None


class BlankWindowPolicy:
    """Decides — from observations alone — when to wait, reload, or explain.

    Kept free of threads, pywebview and wall-clock time so the awkward paths
    (a freeze that outlasts the reload budget, a server that returns after the
    explanation was shown) are ordinary unit tests.
    """

    def __init__(
        self,
        *,
        grace_s: float = FIRST_PAINT_GRACE_S,
        settle_s: float = RELOAD_SETTLE_S,
        reload_budget: int = RELOAD_BUDGET,
        silent_patience_s: float = SILENT_PATIENCE_S,
    ) -> None:
        self._grace_s = grace_s
        self._settle_s = settle_s
        self._budget = reload_budget
        self._silent_patience_s = silent_patience_s
        self._reloads_left = reload_budget
        self._deadline: float | None = None
        self._blank_since: float | None = None
        self._explained: BlankReason | None = None
        self._was_healthy = True

    def decide(self, obs: Observation) -> Verdict:
        if self._deadline is None:
            self._deadline = obs.now + self._grace_s

        if obs.page == "up":
            # A live page: hand over to the page's own watchdog and re-arm, so a
            # window that goes blank LATER in the same session is caught again
            # with a full budget.
            self._reloads_left = self._budget
            self._deadline = obs.now + self._grace_s
            self._blank_since = None
            self._explained = None
            self._was_healthy = obs.server_healthy
            return Verdict(Action.WAIT)

        if self._blank_since is None:
            # The grace measures how long the window has been EMPTY, not how
            # long ago it last held a page. A window that blanks after an hour
            # of use gets the same patience as one at boot — and an in-flight
            # navigation (which is briefly document-less) is not cut short.
            self._blank_since = obs.now
            self._deadline = obs.now + self._grace_s

        # A backend thread that died takes every recovery with it: reloading
        # would only refill the same empty window. Say it once, then stay quiet.
        if not obs.backend_alive:
            return self._explain_once(BlankReason.BACKEND_DEAD)

        # An outage that just ended is a new event, not a repeat of the one the
        # budget was spent on — so it always buys one more attempt. Without
        # this, a window whose reloads were used up during a long freeze would
        # sit on its explanation page while the server was healthy again.
        if obs.server_healthy and not self._was_healthy:
            self._reloads_left = max(self._reloads_left, 1)
            self._deadline = min(self._deadline, obs.now)
        self._was_healthy = obs.server_healthy

        if obs.now < self._deadline:
            return Verdict(Action.WAIT)

        if not obs.server_healthy:
            # A blocked loop is not a broken app — wait it out, but do not let
            # the user sit in front of a wordless window forever. The
            # explanation is already up in the ``notice`` case, so there is
            # nothing left to say until something changes.
            if obs.page != "notice" and obs.now - self._blank_since >= self._silent_patience_s:
                return self._explain_once(BlankReason.BACKEND_SILENT)
            return Verdict(Action.WAIT)

        if self._reloads_left > 0:
            self._reloads_left -= 1
            self._arm_after_reload(obs.now)
            return Verdict(Action.RELOAD)

        # Out of attempts. The window keeps its explanation and the button on
        # it; the watchdog keeps watching, so the next recovery still heals it.
        if obs.page == "notice":
            return Verdict(Action.WAIT)
        return self._explain_once(BlankReason.VIEW_EMPTY)

    # ---- internals ---------------------------------------------------------

    def _arm_after_reload(self, now: float) -> None:
        self._deadline = now + self._settle_s
        self._blank_since = now
        self._explained = None

    def note_action_failed(self, action: Action) -> None:
        """The window refused *action* — so it did not happen; undo the cost.

        pywebview raises on a window that is created but whose GUI loop has not
        started yet, which is exactly the window this guard is armed on (it is
        started right after ``create_window``, because a failure BEFORE
        ``webview.start`` is one of the cases it exists for). An attempt that
        never reached the window must cost neither the explanation nor a slot
        from the reload budget — otherwise the guard would spend itself on a
        window that was not there to be fixed.
        """
        self._explained = None
        if action is Action.RELOAD:
            self._reloads_left += 1
            if self._blank_since is not None:
                self._deadline = self._blank_since

    def _explain_once(self, reason: BlankReason) -> Verdict:
        """Write the explanation the first time; afterwards keep watching.

        Re-rendering the same page every second would fight the user's cursor
        and hide that nothing changed, so only the transition is an action.
        """
        if self._explained == reason:
            return Verdict(Action.WAIT, reason)
        self._explained = reason
        return Verdict(Action.EXPLAIN, reason)


# ---------------------------------------------------------------------------
# The explanation page
# ---------------------------------------------------------------------------

#: One entry per reason per language. German and Spanish are product surface
#: (AGENTS.md §1): this page is shown TO the user, and it cannot reach the
#: bundle's i18n — the bundle is exactly what failed to load. The language is
#: picked in the page from ``navigator.language``, the same way
#: ``frontend/index.html`` picks it, so both blank-window paths speak alike.
_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "title": "The window stayed empty.",
        "backend_dead": (
            "Personal Jarvis is running, but the part that serves this window "
            "has stopped. Restarting the app is the way back."
        ),
        "backend_silent": (
            "Personal Jarvis is busy and is not answering this window yet. "
            "It reloads by itself as soon as it answers again."
        ),
        "view_empty": ("Personal Jarvis is answering, but the window did not load its page."),
        "button": "Reload",
        "retrying": "Retrying…",
    },
    "de": {
        # i18n-allow: product surface, no bundle available here.
        "title": "Das Fenster ist leer geblieben.",
        "backend_dead": (
            "Personal Jarvis läuft, aber der Teil, der dieses Fenster "
            "bedient, ist gestoppt. Ein Neustart der App hilft."
        ),
        "backend_silent": (
            "Personal Jarvis ist beschäftigt und antwortet diesem Fenster "
            "noch nicht. Es lädt von selbst neu, sobald wieder Antwort kommt."
        ),
        "view_empty": (
            "Personal Jarvis antwortet, aber das Fenster hat seine Seite nicht geladen."
        ),
        "button": "Neu laden",
        "retrying": "Neuer Versuch …",
    },
    "es": {
        # i18n-allow: product surface, no bundle available here.
        "title": "La ventana se quedó vacía.",
        "backend_dead": (
            "Personal Jarvis está en marcha, pero la parte que sirve esta "
            "ventana se ha detenido. Reiniciar la aplicación es la salida."
        ),
        "backend_silent": (
            "Personal Jarvis está ocupado y aún no responde a esta ventana. "
            "Se recarga solo en cuanto vuelva a responder."
        ),
        "view_empty": ("Personal Jarvis responde, pero la ventana no cargó su página."),
        "button": "Recargar",
        "retrying": "Reintentando…",
    },
}


def _js(value: Any) -> str:
    """A Python value as a JS literal that is safe inside a ``<script>`` block.

    ``json.dumps`` alone is not: it leaves ``</script>`` intact, and the detail
    string this page prints comes from an exception message — arbitrary text
    that must never be able to close the tag it lives in. The three escapes
    below are the standard set for JSON embedded in HTML.
    """
    import json

    return (
        json.dumps(value, ensure_ascii=False)
        .replace("</", "<\\/")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_notice(
    *,
    reason: BlankReason,
    url: str,
    detail: str = "",
    theme: str = "dark",
) -> str:
    """The page that replaces a blank window: what happened, and a way out.

    Carries its own colours (from the same table the native frame is painted
    from) because there is no stylesheet to inherit — a hardcoded dark page
    would be a black rectangle inside a paper-white frame.
    """
    from jarvis.ui.theme import (
        HOLDING_PAGE_FOREGROUND,
        HOLDING_PAGE_MUTED,
        WINDOW_BACKGROUND,
    )

    key = theme if theme in WINDOW_BACKGROUND else "dark"
    bg = WINDOW_BACKGROUND[key]
    fg = HOLDING_PAGE_FOREGROUND[key]
    muted = HOLDING_PAGE_MUTED[key]

    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta name="color-scheme" content="{key}">'
        "<style>"
        f"html,body{{margin:0;height:100%;background:{bg};color:{fg};"
        "font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;"
        "display:flex;align-items:center;justify-content:center}"
        "main{text-align:center;max-width:52ch;padding:24px}"
        "h1{font-weight:600;font-size:16px;margin:0 0 10px;letter-spacing:.02em}"
        f"p{{margin:0 0 8px;color:{muted};font-size:13px;line-height:1.55}}"
        f"code{{color:{muted};font-size:12px;word-break:break-word}}"
        f"button{{margin-top:14px;padding:7px 16px;font:inherit;font-size:13px;"
        f"cursor:pointer;color:{fg};background:transparent;"
        f"border:1px solid {muted};border-radius:4px}}"
        "</style></head><body><main>"
        '<h1 id="t"></h1><p id="w"></p><p><code id="d"></code></p>'
        '<button type="button" id="b"></button>'
        "</main><script>"
        # The marker the watchdog reads back, so it knows its own page and does
        # not mistake it for a blank window (which would reload it in a loop).
        "window.__jarvisWatchdogNotice=1;"
        f"var T={_js(_TEXTS)};"
        f"var R={_js(reason.value)};"
        f"var U={_js(url)};"
        f"var D={_js(detail)};"
        "var l=(navigator.language||'en').slice(0,2);"
        "var t=T[l]||T.en;"
        "document.getElementById('t').textContent=t.title;"
        "document.getElementById('w').textContent=t[R]||t.view_empty;"
        "document.getElementById('d').textContent=D;"
        "var b=document.getElementById('b');"
        "b.textContent=t.button;"
        "b.onclick=function(){go();};"
        "function go(){b.textContent=t.retrying;b.disabled=true;location.href=U;}"
        # The page returns by itself. This is not a duplicate of the watchdog's
        # reload — it is the RELIABLE one: navigating from inside the document
        # works on a window whose earlier navigation hung, where pywebview's
        # load_url is accepted and then does nothing (measured live
        # 2026-08-20). The watchdog gets the page in; the page gets itself out.
        #
        # ``no-cors`` because this document was written straight into the view
        # and has no origin the server would answer a CORS preflight for: an
        # opaque response still proves the server is answering, which is all
        # this asks.
        "var tries=0;"
        "var timer=setInterval(function(){"
        "if(tries++>600){clearInterval(timer);return;}"
        "fetch(U,{mode:'no-cors',cache:'no-store'}).then(function(){"
        "clearInterval(timer);go();}).catch(function(){});"
        "},2000);"
        "</script></body></html>"
    )


# ---------------------------------------------------------------------------
# The watchdog thread
# ---------------------------------------------------------------------------


@dataclass
class _Slot:
    """One in-flight window call and its outcome."""

    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None


class _WindowCaller:
    """Calls into pywebview from one worker thread, with a deadline.

    Necessary because pywebview does not merely *raise* on a window that is not
    ready: before ``webview.start`` has run its GUI loop, ``evaluate_js``
    BLOCKS, waiting for a ``shown`` event that a window stuck mid-navigation
    never fires. Found live on 2026-08-20 — the watchdog thread froze on its
    very first probe and never reported anything. A guard that can be frozen by
    the thing it is guarding is not a guard.

    One worker, never more. While a call is wedged, further calls are refused
    instead of queueing behind it or getting a thread each; the wedged thread is
    left alone (it sits in a native call nobody can cancel) and the caller
    becomes usable again the moment it returns.
    """

    def __init__(self, *, timeout_s: float = 3.0) -> None:
        self._timeout_s = timeout_s
        self._work: queue.Queue[tuple[Callable[[], Any], _Slot]] = queue.Queue()
        self._lock = threading.Lock()
        self._inflight = 0
        threading.Thread(target=self._run, name="blank-window-caller", daemon=True).start()

    def call(self, fn: Callable[[], Any]) -> tuple[bool, Any]:
        """Run *fn* on the worker. Returns ``(answered, result)``.

        ``answered`` is False when the call raised, timed out, or was refused
        because an earlier one is still wedged — to this guard all three mean
        "the window did not respond", which is itself the symptom it acts on.
        """
        with self._lock:
            if self._inflight:
                return False, None
            self._inflight += 1
        slot = _Slot()
        self._work.put((fn, slot))
        if not slot.done.wait(self._timeout_s):
            return False, None
        return slot.error is None, slot.value

    def _run(self) -> None:
        while True:
            fn, slot = self._work.get()
            try:
                slot.value = fn()
            except Exception as exc:  # noqa: BLE001 — reported, never raised on
                slot.error = exc
            finally:
                slot.done.set()
                with self._lock:
                    self._inflight -= 1


class BlankWindowWatchdog:
    """Runs :class:`BlankWindowPolicy` against a live pywebview window.

    Its own daemon thread on purpose: the loop it is watching is the one that
    freezes, and the GUI thread is busy being a window. Every call into
    pywebview is guarded — a watchdog that raises has failed at the one job it
    has, which is to be there when other things break.
    """

    def __init__(
        self,
        *,
        window: Any,
        url: str,
        health_probe: Callable[[], bool],
        backend_alive: Callable[[], bool],
        failure_detail: Callable[[], str] | None = None,
        theme: Callable[[], str] | None = None,
        policy: BlankWindowPolicy | None = None,
        poll_s: float = POLL_S,
        caller: _WindowCaller | None = None,
        action_caller: _WindowCaller | None = None,
    ) -> None:
        self._window = window
        self._url = url
        self._health_probe = health_probe
        self._backend_alive = backend_alive
        self._failure_detail = failure_detail or (lambda: "")
        self._theme = theme or (lambda: "dark")
        self._policy = policy or BlankWindowPolicy()
        self._poll_s = poll_s
        # Two callers, not one. pywebview's API waits up to 20 s on an internal
        # event before it raises, and on a blank window the state probe waits
        # out every one of those 20 s (``evaluate_js`` gates on ``loaded``,
        # which a hung navigation never fires). Sharing one worker with the
        # probe therefore starved the FIX: measured live 2026-08-20, 33
        # consecutive attempts to write the explanation were dropped because
        # the worker was always mid-probe. The repair must never queue behind
        # the diagnosis.
        self._caller = caller or _WindowCaller()
        self._action_caller = action_caller or _WindowCaller()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="blank-window-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def attach(self, window: Any) -> None:
        """Point the watchdog at a newly created main window.

        The tray can reopen a main window after the first one was closed
        (``_ensure_main_window``); the replacement needs the same guard, and a
        second thread would fight the first one over the same policy.
        """
        self._window = window
        self._policy = BlankWindowPolicy()

    def detach(self) -> None:
        """Stop watching — the window is gone (closed, or being destroyed).

        Questioning a destroyed window is at best noise and at worst a call
        into a dead native handle, and "no window" is not a blank window.
        """
        self._window = None

    # ---- the loop ----------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self._poll_s):
            window = self._window
            if window is None:
                continue
            try:
                verdict = self._policy.decide(
                    Observation(
                        page=self._page_state(window),
                        backend_alive=self._safe(self._backend_alive, default=True),
                        server_healthy=self._safe(self._health_probe, default=False),
                        now=time.monotonic(),
                    )
                )
                self._apply(window, verdict)
            except Exception:  # noqa: BLE001 — never let the guard be the crash
                logger.opt(exception=True).debug("Blank-window watchdog tick failed.")

    def _apply(self, window: Any, verdict: Verdict) -> bool:
        """Carry out *verdict*. Returns whether the window accepted it.

        A window whose GUI loop has not started yet refuses every call — by
        raising, or by never returning — and that is a normal moment in this
        guard's life: it is armed right after ``create_window`` and before
        ``webview.start``, because a window that never navigates is one of the
        failures it exists for. Either refusal is reported back so the policy
        retries instead of counting it as done.
        """
        detail = ""
        if verdict.action is Action.RELOAD:
            answered, _ = self._action_caller.call(lambda: window.load_url(self._url))
        elif verdict.action is Action.EXPLAIN and verdict.reason is not None:
            detail = self._safe(self._failure_detail, default="")
            html = render_notice(
                reason=verdict.reason,
                url=self._url,
                detail=detail,
                theme=self._safe(self._theme, default="dark"),
            )
            answered, _ = self._action_caller.call(lambda: window.load_html(html))
        else:
            return True
        if not answered:
            logger.debug(
                "Blank-window watchdog: the window did not accept {}.",
                verdict.action.value,
            )
            self._policy.note_action_failed(verdict.action)
            return False
        # Logged only once it actually happened, so a window that is not there
        # yet cannot fill the log with attempts nobody saw.
        if verdict.action is Action.RELOAD:
            logger.info("Desktop window is blank — reloading it (the server answers again).")
        elif verdict.reason is not None:
            logger.warning(
                "Desktop window stayed blank ({}) — the window now says so "
                "instead of showing nothing.{}",
                verdict.reason.value,
                f" Detail: {detail}" if detail else "",
            )
        return True

    def _page_state(self, window: Any) -> PageState:
        """Ask the window what it is showing; an unanswerable window is blank.

        Every way the question can fail — ``None`` from a view with no
        document, an exception, or no answer at all within the deadline — means
        the same thing to the person looking at the frame: there is nothing
        there. Which is precisely the state this guard acts on.
        """
        answered, value = self._caller.call(lambda: window.evaluate_js(PAGE_STATE_JS))
        if answered and value in ("up", "notice"):
            return value  # type: ignore[return-value]
        return "blank"

    @staticmethod
    def _safe(probe: Callable[[], Any], *, default: Any) -> Any:
        """Run a caller-supplied probe; its failure must not stop the guard."""
        try:
            return probe()
        except Exception:  # noqa: BLE001
            return default
