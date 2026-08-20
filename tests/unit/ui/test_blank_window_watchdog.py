"""A window that shows nothing must heal itself, or say why it cannot.

The page's own watchdog (``frontend/index.html``) covers the case where a page
loaded and then the app above it died. This one covers the case underneath: a
window in which NO page ever arrived, where there is no script left to notice
anything. The maintainer hit it repeatedly on 2026-08-18…20 — a titled window
filled with the theme's ground colour and nothing else.

Three ways in, all reproduced in the logs of a single day:

* the single asyncio loop froze inside a native call (``Pa_CloseStream``,
  188.3 s measured) — the socket still accepted, nothing was answered, and the
  navigation received no bytes and no error;
* the backend thread died after the port was already up (a lost ``click``
  dependency mid-boot, 13:45);
* the web view never navigated at all.

The first is recoverable and must heal without the user doing anything; the
second never heals and must be said out loud; the third is worth one reload
before it is said. These tests pin all three, plus the loops that must NOT
happen (an explanation re-rendered every second, a reload storm).
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.ui.window_watchdog import (
    Action,
    BlankReason,
    BlankWindowPolicy,
    BlankWindowWatchdog,
    Observation,
    render_notice,
)

GRACE = 20.0
SETTLE = 20.0
PATIENCE = 40.0


def _policy(**kw: Any) -> BlankWindowPolicy:
    return BlankWindowPolicy(
        grace_s=kw.pop("grace_s", GRACE),
        settle_s=kw.pop("settle_s", SETTLE),
        reload_budget=kw.pop("reload_budget", 3),
        silent_patience_s=kw.pop("silent_patience_s", PATIENCE),
    )


def _obs(
    now: float,
    *,
    page: str = "blank",
    alive: bool = True,
    healthy: bool = True,
) -> Observation:
    return Observation(
        page=page,  # type: ignore[arg-type]
        backend_alive=alive,
        server_healthy=healthy,
        now=now,
    )


# --- a healthy window is left alone ----------------------------------------


def test_a_loading_window_is_given_its_grace() -> None:
    """A cold WebView paints nothing for a few seconds — that is not a failure."""
    p = _policy()
    for t in (0.0, 5.0, 19.0):
        assert p.decide(_obs(t)).action is Action.WAIT


def test_a_live_page_is_never_touched() -> None:
    """Once our document is there, the page's own watchdog owns the problem."""
    p = _policy()
    for t in (0.0, 30.0, 300.0):
        assert p.decide(_obs(t, page="up")).action is Action.WAIT


def test_a_page_that_dies_later_gets_a_full_budget_again() -> None:
    """A window blanking after an hour of use is a new incident, not a used-up one."""
    p = _policy()
    p.decide(_obs(0.0, page="up"))
    for _ in range(3):
        p.decide(_obs(0.0, page="up"))
    # It goes blank at t=100 and the grace runs from there, not from boot.
    assert p.decide(_obs(100.0)).action is Action.WAIT
    assert p.decide(_obs(100.0 + GRACE + 1)).action is Action.RELOAD


# --- the recoverable freeze -------------------------------------------------


def test_a_frozen_server_is_waited_out_not_reloaded() -> None:
    """Reloading into a frozen loop only wastes the budget — wait for the answer."""
    p = _policy()
    v = p.decide(_obs(GRACE + 1, healthy=False))
    assert v.action is Action.WAIT


def test_the_window_reloads_itself_when_the_server_answers_again() -> None:
    """The 188 s freeze: the user does nothing and the UI comes back by itself."""
    p = _policy()
    p.decide(_obs(0.0))
    assert p.decide(_obs(30.0, healthy=False)).action is Action.WAIT
    assert p.decide(_obs(190.0, healthy=True)).action is Action.RELOAD


def test_a_long_freeze_is_explained_before_it_ends() -> None:
    """Silence has a limit: the window says what is happening rather than nothing."""
    p = _policy()
    p.decide(_obs(0.0))
    assert p.decide(_obs(PATIENCE - 1, healthy=False)).action is Action.WAIT
    v = p.decide(_obs(PATIENCE + 1, healthy=False))
    assert v.action is Action.EXPLAIN
    assert v.reason is BlankReason.BACKEND_SILENT


def test_the_explanation_gives_way_to_the_real_ui_when_the_server_returns() -> None:
    """The notice is a stand-in, not a dead end — recovery still wins."""
    p = _policy()
    p.decide(_obs(0.0))
    v = p.decide(_obs(PATIENCE + 1, healthy=False))
    assert v.action is Action.EXPLAIN
    # The notice is up now, and the server comes back.
    assert p.decide(_obs(PATIENCE + 3, page="notice", healthy=False)).action is Action.WAIT
    assert p.decide(_obs(PATIENCE + 9, page="notice", healthy=True)).action is Action.RELOAD


def test_a_reload_that_did_not_take_is_tried_again() -> None:
    """Found live: ``load_url`` can be accepted and change nothing.

    If one attempt were the whole recovery, the user would sit on the
    explanation page with a healthy server behind it.
    """
    p = _policy(reload_budget=3)
    p.decide(_obs(0.0))
    assert p.decide(_obs(GRACE + 1, healthy=False)).action is Action.WAIT
    # Server returns, first reload fires and does not take: still "notice".
    assert p.decide(_obs(50.0, page="notice", healthy=True)).action is Action.RELOAD
    assert p.decide(_obs(50.0 + SETTLE + 1, page="notice")).action is Action.RELOAD


def test_a_recovery_buys_an_attempt_even_with_an_empty_budget() -> None:
    """A freeze that outlasted the budget must not cost the window its recovery."""
    p = _policy(reload_budget=1)
    p.decide(_obs(0.0))
    assert p.decide(_obs(GRACE + 1)).action is Action.RELOAD  # budget spent
    v = p.decide(_obs(100.0, page="notice", healthy=False))
    assert v.action is Action.WAIT
    assert p.decide(_obs(101.0, page="notice", healthy=True)).action is Action.RELOAD


def test_the_notice_is_written_once_not_every_second() -> None:
    """Re-rendering each tick would fight the cursor and hide that nothing changed."""
    p = _policy()
    first = p.decide(_obs(0.0, alive=False))
    assert first.action is Action.EXPLAIN
    for t in (1.0, 2.0, 3.0, 60.0):
        assert p.decide(_obs(t, page="notice", alive=False)).action is Action.WAIT


# --- the unrecoverable death ------------------------------------------------


def test_a_dead_backend_thread_is_said_immediately_not_reloaded() -> None:
    """Nothing will serve this window again; a reload would only refill it empty."""
    p = _policy()
    v = p.decide(_obs(0.0, alive=False))
    assert v.action is Action.EXPLAIN
    assert v.reason is BlankReason.BACKEND_DEAD


def test_a_dead_backend_is_reported_even_during_the_grace_period() -> None:
    """Waiting out a grace for a thread that is already gone only delays the truth."""
    p = _policy()
    assert p.decide(_obs(1.0, alive=False)).reason is BlankReason.BACKEND_DEAD


# --- the empty view ---------------------------------------------------------


def test_a_healthy_server_and_an_empty_window_earns_a_reload() -> None:
    p = _policy()
    p.decide(_obs(0.0))
    assert p.decide(_obs(GRACE + 1)).action is Action.RELOAD


def test_reloads_are_budgeted_then_the_window_explains_itself() -> None:
    """No reload storm: three tries, then words."""
    p = _policy(reload_budget=2)
    t = 0.0
    p.decide(_obs(t))
    actions = []
    for _ in range(3):
        t += GRACE + 1
        v = p.decide(_obs(t))
        actions.append(v)
        t += 0.1
    assert [a.action for a in actions[:2]] == [Action.RELOAD, Action.RELOAD]
    assert actions[2].action is Action.EXPLAIN
    assert actions[2].reason is BlankReason.VIEW_EMPTY


def test_a_reload_is_given_time_to_settle_before_the_next_one() -> None:
    """Back-to-back reloads would out-race the browser they are trying to fix."""
    p = _policy()
    p.decide(_obs(0.0))
    assert p.decide(_obs(GRACE + 1)).action is Action.RELOAD
    assert p.decide(_obs(GRACE + 2)).action is Action.WAIT
    assert p.decide(_obs(GRACE + SETTLE + 2)).action is Action.RELOAD


# --- the explanation page ---------------------------------------------------


@pytest.mark.parametrize("reason", list(BlankReason))
def test_every_reason_renders_a_page_that_can_leave_itself(reason: BlankReason) -> None:
    html = render_notice(reason=reason, url="http://127.0.0.1:47821", theme="dark")
    # The marker the watchdog reads back, so its own page is never mistaken for
    # a blank one and reloaded in a loop.
    assert "__jarvisWatchdogNotice" in html
    # The way out, and the reason, both present without a bundle.
    assert "http://127.0.0.1:47821" in html
    assert reason.value in html


def test_the_page_brings_itself_back_without_being_clicked() -> None:
    """The reliable half of the recovery, found the hard way.

    ``load_url`` from the watchdog is accepted and then does nothing on a window
    whose first navigation hung (measured live 2026-08-20). Navigating from
    INSIDE the document works, so the page polls the server and returns by
    itself; the button is for the impatient, not the mechanism.
    """
    html = render_notice(reason=BlankReason.BACKEND_SILENT, url="http://127.0.0.1:47821")
    assert "setInterval" in html
    assert "fetch(" in html
    # no-cors: a page written straight into the view has no origin the server
    # would grant CORS to, and an opaque response still proves it answers.
    assert "no-cors" in html
    assert "location.href=U" in html
    # Bounded, so a machine left alone overnight is not polling forever.
    assert "tries++>" in html


def test_the_page_carries_the_users_theme_not_a_hardcoded_dark() -> None:
    """A black rectangle inside a paper-white frame is its own bug report."""
    from jarvis.ui.theme import WINDOW_BACKGROUND

    light = render_notice(reason=BlankReason.VIEW_EMPTY, url="http://x", theme="light")
    assert WINDOW_BACKGROUND["light"] in light
    assert WINDOW_BACKGROUND["dark"] not in light


def test_the_page_speaks_german_and_spanish_too() -> None:
    """It cannot reach the bundle's i18n — the bundle is what failed to load."""
    html = render_notice(reason=BlankReason.BACKEND_SILENT, url="http://x")
    assert "Das Fenster ist leer geblieben." in html
    assert "La ventana se quedó vacía." in html
    assert "navigator.language" in html


def test_a_detail_string_cannot_break_out_of_the_page() -> None:
    """The detail comes from an exception message — it is data, not markup."""
    html = render_notice(
        reason=BlankReason.BACKEND_DEAD,
        url="http://x",
        detail='</script><script>alert("x")</script>',
    )
    assert "</script><script>alert" not in html


# --- the thread around the policy -------------------------------------------


class _FakeWindow:
    def __init__(self, state: str = "blank") -> None:
        self.state = state
        self.loaded_urls: list[str] = []
        self.loaded_html: list[str] = []
        self.raise_on_eval = False

    def evaluate_js(self, _js: str) -> str:
        if self.raise_on_eval:
            raise RuntimeError("no document")
        return self.state

    def load_url(self, url: str) -> None:
        self.loaded_urls.append(url)

    def load_html(self, html: str) -> None:
        self.loaded_html.append(html)
        self.state = "notice"


def _watchdog(window: _FakeWindow, **kw: Any) -> BlankWindowWatchdog:
    return BlankWindowWatchdog(
        window=window,
        url=kw.pop("url", "http://127.0.0.1:47821"),
        health_probe=kw.pop("health_probe", lambda: True),
        backend_alive=kw.pop("backend_alive", lambda: True),
        failure_detail=kw.pop("failure_detail", lambda: ""),
        theme=kw.pop("theme", lambda: "dark"),
        **kw,
    )


def test_an_unanswerable_window_counts_as_blank() -> None:
    """``evaluate_js`` raising and a view with no document look the same to a user."""
    window = _FakeWindow()
    window.raise_on_eval = True
    wd = _watchdog(window)
    assert wd._page_state(window) == "blank"


def test_the_watchdog_reloads_the_configured_url() -> None:
    window = _FakeWindow()
    wd = _watchdog(window, url="http://127.0.0.1:1234")
    wd._apply(window, wd._policy.decide(_obs(0.0)))  # WAIT, still in grace
    assert window.loaded_urls == []
    from jarvis.ui.window_watchdog import Verdict

    wd._apply(window, Verdict(Action.RELOAD))
    assert window.loaded_urls == ["http://127.0.0.1:1234"]


def test_the_watchdog_writes_the_detail_it_was_given() -> None:
    window = _FakeWindow()
    wd = _watchdog(window, failure_detail=lambda: "ModuleNotFoundError: click")
    from jarvis.ui.window_watchdog import Verdict

    wd._apply(window, Verdict(Action.EXPLAIN, BlankReason.BACKEND_DEAD))
    assert window.loaded_html
    assert "ModuleNotFoundError: click" in window.loaded_html[0]


def test_a_window_that_is_not_there_yet_costs_nothing() -> None:
    """Found live: pywebview raises until ``webview.start`` has run.

    The guard is armed before that on purpose — a window that never navigates
    is one of the failures it exists for — so a refused call must cost neither
    the explanation nor a reload from the budget.
    """

    class _NotYet:
        def evaluate_js(self, _js: str) -> str:
            raise RuntimeError("Main window failed to start")

        def load_url(self, _url: str) -> None:
            raise RuntimeError("Main window failed to start")

        def load_html(self, _html: str) -> None:
            raise RuntimeError("Main window failed to start")

    window = _NotYet()
    wd = _watchdog(window, policy=_policy())  # type: ignore[arg-type]
    from jarvis.ui.window_watchdog import Verdict

    wd._policy.decide(_obs(0.0))
    assert wd._apply(window, Verdict(Action.RELOAD)) is False
    assert wd._apply(window, Verdict(Action.EXPLAIN, BlankReason.VIEW_EMPTY)) is False
    # Budget untouched: once the window is real, all three reloads are still there.
    real = _FakeWindow()
    wd._window = real
    for i in range(3):
        v = wd._policy.decide(_obs(GRACE + 1 + i * (SETTLE + 1)))
        assert v.action is Action.RELOAD, f"reload {i + 1} was already spent"


def test_a_probe_that_raises_cannot_stop_the_guard() -> None:
    """A watchdog that dies on a failing probe has failed at its only job."""

    def _boom() -> bool:
        raise RuntimeError("probe exploded")

    window = _FakeWindow()
    wd = _watchdog(window, health_probe=_boom, backend_alive=_boom)
    assert wd._safe(_boom, default=False) is False
    assert wd._safe(_boom, default=True) is True


def test_a_closed_window_is_not_a_blank_window() -> None:
    """After detach the guard has nothing to watch and must not paint anything."""
    window = _FakeWindow()
    wd = _watchdog(window)
    wd.detach()
    assert wd._window is None


def test_attaching_a_reopened_window_starts_a_fresh_incident() -> None:
    """The tray can reopen main; the replacement deserves its own full budget."""
    window = _FakeWindow()
    wd = _watchdog(window)
    wd._policy.decide(_obs(0.0))
    wd._policy.decide(_obs(GRACE + 1))  # spends a reload
    second = _FakeWindow()
    wd.attach(second)
    assert wd._window is second
    assert wd._policy.decide(_obs(0.0)).action is Action.WAIT
