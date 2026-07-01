"""Overlay entry point. Later phases fill in the ``setup_*()`` bodies."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

from .transparency import set_per_monitor_dpi_awareness

logger = logging.getLogger(__name__)


def setup_dpi_awareness() -> None:
    """Per-monitor DPI. MUST run before QApplication — Plan §12.3."""
    set_per_monitor_dpi_awareness()


def _setup_app_identity_and_icon() -> Optional["object"]:
    """Pin the taskbar identity and resolve the Jarvis icon path.

    Runs before ``QApplication`` so the AUMID is in place when Qt registers
    its window class. Returns the resolved icon path (or None) — the caller
    feeds it into ``QApplication.setWindowIcon`` once the app exists.

    All steps are best-effort: this subprocess is cosmetic infrastructure,
    a missing icon must never crash the overlay.
    """
    if sys.platform != "win32":
        return None
    try:
        from jarvis.ui.icon_utils import (
            ensure_windows_app_identity,
            project_icon_path,
        )
    except Exception:  # noqa: BLE001
        # Running standalone outside the Jarvis source tree (smoke tests).
        return None

    ensure_windows_app_identity()
    ico = project_icon_path()
    return ico if ico.is_file() else None


def setup_windows(app, config, state_machine=None, effects_bridge=None) -> list:
    """Edge-Glow windows, one per screen.

    Plan §12.1 + §12.5: one window per screen when ``all_monitors=True``,
    otherwise only primary. We hook hotplug into the ``MonitorManager``.

    ``state_machine`` (optional, new in 9.3): passed through to
    ``EdgeGlowWindow``, which exposes a ``StateBridge`` over QWebChannel.
    Each window subscribes independently — the StateMachine's subscriber
    list is thread-safe.

    ``effects_bridge`` (optional, new in 9.5): simultaneously registered
    as ``effectsBridge`` on the QWebChannel. Same instance per window —
    all windows fire against the same bridge, and the renderer per
    window subscribes independently.
    """
    from PySide6.QtGui import QGuiApplication

    from .monitors import MonitorManager
    from .window_glow import EdgeGlowWindow

    windows: list[EdgeGlowWindow] = []
    primary = QGuiApplication.primaryScreen()

    if config.all_monitors:
        targets = list(QGuiApplication.screens())
    elif primary is not None:
        targets = [primary]
    else:
        targets = []

    for screen in targets:
        win = EdgeGlowWindow(
            screen,
            hide_from_capture=config.hide_from_capture,
            state_machine=state_machine,
            effects_bridge=effects_bridge,
        )
        win.show()
        windows.append(win)

    def _on_screen_added(screen) -> None:
        if not config.all_monitors:
            return
        win = EdgeGlowWindow(
            screen,
            hide_from_capture=config.hide_from_capture,
            state_machine=state_machine,
            effects_bridge=effects_bridge,
        )
        win.show()
        windows.append(win)

    def _on_screen_removed(screen) -> None:
        for w in list(windows):
            # ``w.screen()`` can return None after removal — compare defensively.
            try:
                if w.screen() is screen:
                    w.close()
                    windows.remove(w)
            except RuntimeError:  # pragma: no cover — window already gone
                if w in windows:
                    windows.remove(w)

    manager = MonitorManager(_on_screen_added, _on_screen_removed)
    manager.attach()
    # Park the manager on the app object so it doesn't get GC'd.
    app._overlay_monitor_manager = manager  # type: ignore[attr-defined]

    return windows


def setup_ipc(app, config, on_message=None) -> Any:
    """WS client on a dedicated asyncio thread.

    The Qt event loop and asyncio coexist here via a worker thread
    instead of qasync — that avoids an extra dependency and makes the
    path easier to test (asyncio can be started/stopped independently
    of the Qt loop).

    Plan §10.5: reconnect logic + heartbeat are fully encapsulated in
    ``WSClient.run()``. We park the thread + loop on the ``app`` object
    so nothing gets GC'd and the shutdown hook can set the stop event.

    ``on_message`` (optional, new in 9.3): async callback that receives
    every validated IPC envelope. setup_state_machine() passes
    ``EventRouter.handle`` through here — it's sync, we wrap it there.
    """
    import threading

    from .ipc_ws import WSClient

    ports = list(range(config.ws_port, config.ws_port_range_max + 1))
    client = WSClient(
        host=config.ws_host,
        ports=ports,
        heartbeat_interval_s=float(config.heartbeat_interval_s),
        heartbeat_timeout_s=float(config.heartbeat_timeout_s),
        on_message=on_message,
    )

    loop = asyncio.new_event_loop()
    run_task: dict[str, Any] = {}

    def _thread_entry() -> None:
        asyncio.set_event_loop(loop)
        run_task["task"] = loop.create_task(client.run())
        try:
            loop.run_forever()
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_entry, name="overlay-ipc", daemon=True)
    thread.start()

    def _shutdown() -> None:
        if loop.is_closed():
            return
        fut = asyncio.run_coroutine_threadsafe(client.aclose(), loop)
        try:
            fut.result(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2.0)

    if hasattr(app, "aboutToQuit"):
        app.aboutToQuit.connect(_shutdown)

    handle = {
        "client": client,
        "thread": thread,
        "loop": loop,
        "shutdown": _shutdown,
    }
    app._overlay_ipc = handle  # type: ignore[attr-defined]
    return handle


def setup_state_machine(app, config) -> Any:
    """State machine + EventRouter. Phase 9.3.

    Returns ``{"machine": StateMachine, "router": EventRouter,
    "on_message": Callable}``. The ``on_message`` coroutine is passed
    through to ``setup_ipc``; the ``machine`` goes to ``setup_windows``,
    so each ``EdgeGlowWindow`` can build its own QWebChannel bridge.

    Plan AD-8: state logic lives in the overlay process. The router is
    the only component that knows the wire format — the StateMachine
    stays IPC-agnostic.
    """
    from .event_router import EventRouter
    from .state import StateMachine

    machine = StateMachine()
    router = EventRouter(machine)

    async def on_message(envelope: Any) -> None:
        # Router.handle is sync and fast (dict lookup + locks). We call
        # it directly — no await needed.
        try:
            router.handle(envelope)
        except Exception:  # noqa: BLE001
            logger.exception("EventRouter.handle raised on %s", type(envelope).__name__)

    handle = {
        "machine": machine,
        "router": router,
        "on_message": on_message,
    }
    app._overlay_state = handle  # type: ignore[attr-defined]
    return handle


def setup_effects(app, config, router=None) -> Any:
    """Action effects: cursor SHM reader + ripple/action bridge. Phase 9.5.

    Returns ``{"bridge": EffectsBridge, "shm_thread": Optional[Thread],
    "shm_reader": Optional[CursorShmReader], "shm_stop": Event}``.

    Lifecycle:
      - ``EffectsBridge`` is instantiated immediately, parked on ``app``,
        and attached to all windows by the caller via
        ``setup_windows(effects_bridge=...)``.
      - ``router.add_click_hook/add_action_started_hook/...`` are hooked
        so IPC events flow through the bridge to the JS side.
      - When ``config.cursor_trail_enabled`` and a ``shm_cursor_name``
        string becomes known via config IPC (from Hauptjarvis), a 60-Hz
        reader thread starts and emits ``cursorMoved`` signals. Phase
        9.5 polls the config block once at startup — dynamic reconfig
        arrives with 9.7+.
    """
    import threading

    from .window_glow import EffectsBridge

    bridge = EffectsBridge()

    # Hook wiring to EventRouter: pass every IPC event through.
    if router is not None:
        router.add_click_hook(
            lambda env: bridge.emit_click(
                env.payload.x,
                env.payload.y,
                _monitor_str_to_idx(env.payload.monitor),
                env.payload.button,
            )
        )
        router.add_action_started_hook(
            lambda env: bridge.emit_action_started(
                env.payload.kind, env.payload.duration_hint_ms
            )
        )
        router.add_action_ended_hook(lambda env: bridge.emit_action_ended())
        # Cursor via WS fallback (Plan §11.5) — when SHM is off,
        # CursorEnvelope comes through and we forward it.
        router.add_cursor_hook(
            lambda env: bridge.emit_cursor(env.payload.x, env.payload.y)
        )

    # SHM reader thread — only when the trail is enabled AND a name is
    # available. The name typically comes via config IPC from Hauptjarvis.
    # Phase 9.5 starts the thread DEFERRED via the shm_attach() helper below.
    handle = {
        "bridge": bridge,
        "shm_thread": None,
        "shm_reader": None,
        "shm_stop": threading.Event(),
        "shm_attach": None,
    }

    def shm_attach(name: str, hz: int = 60) -> bool:
        """Attaches to an existing SHM block and starts the 60-Hz
        reader thread. Returns False if the block is missing."""
        from .cursor_shm import CursorShmReader

        try:
            reader = CursorShmReader.attach(name)
        except FileNotFoundError:
            logger.warning("SHM block %r not found — cursor-trail disabled", name)
            return False

        period = 1.0 / max(1, hz)
        stop = handle["shm_stop"]

        def _run() -> None:
            while not stop.is_set():
                try:
                    frame = reader.read()
                    if frame is not None:
                        bridge.emit_cursor(frame.x, frame.y)
                except Exception:  # noqa: BLE001
                    logger.debug("SHM read error", exc_info=True)
                if stop.wait(timeout=period):
                    return

        thread = threading.Thread(target=_run, name="cursor-shm-reader", daemon=True)
        thread.start()
        handle["shm_thread"] = thread
        handle["shm_reader"] = reader
        return True

    handle["shm_attach"] = shm_attach

    def _shutdown() -> None:
        handle["shm_stop"].set()
        thread = handle["shm_thread"]
        if thread is not None:
            thread.join(timeout=2.0)
        reader = handle["shm_reader"]
        if reader is not None:
            reader.close()

    if hasattr(app, "aboutToQuit"):
        app.aboutToQuit.connect(_shutdown)

    app._overlay_effects = handle  # type: ignore[attr-defined]
    return handle


def _monitor_str_to_idx(monitor: str) -> int:
    """ClickPayload.monitor is a string (free-form). If parseable as
    int -> use directly; otherwise default to 0. Phase-9.7 mascot
    wiring can refine this once real monitor IDs come in."""
    if not monitor:
        return 0
    try:
        return int(monitor)
    except (TypeError, ValueError):
        return 0


def setup_mascot(app, config, state_machine=None, toml_path=None, ipc=None) -> Any:
    """Mascot window. Phase 9.6.

    Returns ``{"window": MascotWindow | None, "position": ResolvedPlacement | None}``.

    If ``config.mascot_enabled`` is False, no window is created
    (Plan §13.5 — the TOML toggle disables the mascot entirely).

    Position is determined via ``mascot_position.resolve_placement``
    from the persisted ``[overlay.mascot]``; recovery when the monitor
    is gone falls back to primary (Plan §13.4 step 3).

    Plus: ``hideRequested`` / ``resetRequested`` signals are wired to
    sensible defaults (hide -> close, reset -> primary default position).
    """
    if not getattr(config, "mascot_enabled", True):
        logger.info("Mascot disabled via config.mascot_enabled=false")
        handle = {"window": None, "position": None}
        app._overlay_mascot = handle  # type: ignore[attr-defined]
        return handle

    from pathlib import Path

    from .mascot_position import (
        DEFAULT_X_RELATIVE,
        DEFAULT_Y_RELATIVE,
        MascotPosition,
        load_position_from_toml,
        resolve_placement,
        save_position_to_toml,
        screens_from_qt,
    )
    from .window_mascot import MascotWindow

    mascot_section = getattr(config, "mascot", None)
    size_px = getattr(mascot_section, "size_px", 160) if mascot_section else 160
    snap_px = getattr(mascot_section, "snap_to_edges_px", 16) if mascot_section else 16

    # Load position — default is jarvis.toml in the working dir. Tests
    # and the Hauptjarvis bootstrap can pass toml_path explicitly.
    config_path = Path(toml_path) if toml_path is not None else Path("jarvis.toml")
    persisted = load_position_from_toml(config_path)
    if persisted is None and mascot_section is not None:
        # Fallback: values from the in-memory config (e.g. first start
        # without a TOML edit).
        persisted = MascotPosition(
            monitor=getattr(mascot_section, "position_monitor", "") or "",
            x_relative=getattr(mascot_section, "position_x_relative", DEFAULT_X_RELATIVE),
            y_relative=getattr(mascot_section, "position_y_relative", DEFAULT_Y_RELATIVE),
        )

    placement = resolve_placement(persisted, screens_from_qt(), mascot_size_px=size_px)
    if placement.recovered:
        logger.warning(
            "mascot.position_recovered=primary_fallback (persisted monitor missing)"
        )

    def _save(pos: MascotPosition) -> None:
        try:
            save_position_to_toml(config_path, pos)
        except Exception:  # noqa: BLE001
            logger.exception("save mascot position failed")

    win = MascotWindow(
        initial_x=placement.abs_x,
        initial_y=placement.abs_y,
        monitor_name=placement.monitor,
        size_px=size_px,
        snap_tolerance_px=snap_px,
        hide_from_capture=getattr(config, "hide_from_capture", True),
        state_machine=state_machine,
        on_position_saved=_save,
    )
    win.hideRequested.connect(win.close)

    def _reset_position() -> None:
        from PySide6.QtGui import QGuiApplication

        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        geo = primary.availableGeometry()
        new_x = geo.x() + DEFAULT_X_RELATIVE
        new_y = geo.y() + DEFAULT_Y_RELATIVE
        win.move(new_x, new_y)
        _save(MascotPosition(
            monitor=primary.name(),
            x_relative=DEFAULT_X_RELATIVE,
            y_relative=DEFAULT_Y_RELATIVE,
        ))

    win.resetRequested.connect(_reset_position)

    # Wire the doubleClick-mute signal upstream over the WS bridge. The
    # mascot fires a single MascotEventEnvelope(kind="mute_toggle"); main
    # jarvis owns the actual mute state and decides what the new flag
    # value should be (idempotent toggle). The local sprite already
    # flipped its opacity for optimistic feedback.
    if ipc is not None:
        client = ipc.get("client")
        loop = ipc.get("loop")
        if client is not None and loop is not None:
            from .schema import MascotEventEnvelope, MascotEventPayload

            def _on_mute_toggle_requested() -> None:
                envelope = MascotEventEnvelope(
                    payload=MascotEventPayload(kind="mute_toggle"),
                )
                try:
                    asyncio.run_coroutine_threadsafe(
                        client.send(envelope), loop
                    )
                except RuntimeError:
                    logger.debug("mute_toggle send: ipc loop closed")
                except Exception:  # noqa: BLE001
                    logger.exception("mute_toggle send failed")

            win.muteToggleRequested.connect(_on_mute_toggle_requested)

    win.show()

    handle = {"window": win, "position": placement}
    app._overlay_mascot = handle  # type: ignore[attr-defined]
    return handle


def setup_throttling(app, config, state_machine=None, windows=None) -> Any:
    """Fullscreen detection + power monitor + throttler wiring. Phase 9.7.

    Inputs:
      - ``state_machine`` (required for the throttler — it subscribes
        to state changes for idle reset).
      - ``windows`` (optional, list of EdgeGlowWindow instances).
        The throttler subscriber calls each window's
        ``set_view_visible`` method on a hide timeout.

    Returns::

        {
          "throttler": Throttler,
          "fullscreen": FullscreenDetector,
          "power": PowerMonitor,
          "shutdown": Callable[[], None],
        }

    If no StateMachine is supplied: only fullscreen+power, no
    throttler. (e.g. headless tests.)
    """
    from PySide6.QtCore import QTimer

    from .fullscreen_detect import FullscreenDetector
    from .power import PowerMonitor
    from .throttler import (
        DEFAULT_FPS_ACTIVE,
        DEFAULT_FPS_BURST,
        DEFAULT_FPS_IDLE,
        DEFAULT_HIDE_TIMEOUT_S,
        DEFAULT_IDLE_TIMEOUT_S,
        Throttler,
    )

    # Qt cross-thread marshalling: FullscreenDetector and PowerMonitor run
    # on their own daemon threads. Their callbacks must NOT touch Qt
    # widgets directly (setVisible, transition_to_*) — Qt hard-asserts
    # with STATUS_BREAKPOINT 0x80000003 and the subprocess crashes. We
    # route all worker callbacks via QTimer.singleShot(0, ...) onto the
    # Qt main thread that holds QApplication.exec().
    def _post_to_main(fn) -> None:
        QTimer.singleShot(0, fn)

    handle: dict[str, Any] = {
        "throttler": None,
        "fullscreen": None,
        "power": None,
        "shutdown": lambda: None,
    }

    throttler: Optional[Throttler] = None
    if state_machine is not None:
        throttler = Throttler(
            state_machine,
            fps_idle=int(getattr(config, "fps_idle", DEFAULT_FPS_IDLE)),
            fps_active=int(getattr(config, "fps_active", DEFAULT_FPS_ACTIVE)),
            fps_burst=int(getattr(config, "fps_burst", DEFAULT_FPS_BURST)),
            idle_timeout_s=float(
                getattr(config, "idle_timeout_s", DEFAULT_IDLE_TIMEOUT_S)
            ),
            hide_timeout_s=float(
                getattr(config, "hide_timeout_s", DEFAULT_HIDE_TIMEOUT_S)
            ),
        )
        handle["throttler"] = throttler

        # Window visibility toggling: throttler-snapshot.should_hide_view
        # is mirrored onto each Edge-Glow view (WebView setVisible).
        # MUST be marshalled onto the Qt main thread — the throttler can
        # call the subscriber synchronously from a worker thread (the
        # crash stack shows Detector → state_machine → throttler.recompute
        # → _on_throttle).
        if windows is not None:
            def _on_throttle(snapshot) -> None:
                visible = not snapshot.should_hide_view

                def _apply() -> None:
                    for w in windows:
                        if hasattr(w, "set_view_visible"):
                            try:
                                w.set_view_visible(visible)
                            except Exception:  # noqa: BLE001
                                logger.debug(
                                    "set_view_visible failed", exc_info=True
                                )

                _post_to_main(_apply)

            throttler.subscribe(_on_throttle)

    # Fullscreen detection. Toggles the HIDDEN state.
    # The FullscreenDetector callback runs on its own daemon thread —
    # state_machine.transition_* publishes synchronously to subscribers,
    # which in turn touch Qt widgets. Route the entire logic through
    # _post_to_main onto the Qt main thread.
    def _on_fullscreen(status) -> None:
        if state_machine is None:
            return

        def _apply() -> None:
            if status.should_hide:
                state_machine.transition_to_hidden(reason="timeout")
            else:
                # Recovery: back to IDLE if HIDDEN was previously caused
                # by fullscreen. If HIDDEN was set manually, don't
                # override it — we currently can't distinguish the two,
                # so we keep the conservative path: only switch to IDLE
                # if currently HIDDEN.
                if state_machine.state.value == "hidden":
                    from .state import OverlayState

                    state_machine.transition_to(
                        OverlayState.IDLE, reason="timeout"
                    )
            if throttler is not None:
                throttler.set_fullscreen_should_hide(status.should_hide)

        _post_to_main(_apply)

    fullscreen = FullscreenDetector(
        callback=_on_fullscreen,
        ignore_busy_state=bool(getattr(config, "ignore_busy_state", False)),
    )
    fullscreen.start()
    handle["fullscreen"] = fullscreen

    # Power monitor. Halves FPS on battery.
    # PowerMonitor.callback runs on its own worker thread; throttler.set_on_battery
    # publishes synchronously to the _on_throttle subscriber (see above). Also
    # marshalled onto the Qt main thread here, otherwise the same crash risk
    # applies on an AC/DC switch.
    def _on_power(status) -> None:
        if throttler is None:
            return

        def _apply() -> None:
            throttler.set_on_battery(status.on_battery)

        _post_to_main(_apply)

    power = PowerMonitor(callback=_on_power)
    power.start()
    handle["power"] = power

    def _shutdown() -> None:
        fullscreen.stop()
        power.stop()
        if throttler is not None:
            throttler.shutdown()

    if hasattr(app, "aboutToQuit"):
        app.aboutToQuit.connect(_shutdown)
    handle["shutdown"] = _shutdown

    app._overlay_throttle = handle  # type: ignore[attr-defined]
    return handle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="overlay")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--ws-port", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.self_test:
        from .self_test import run as run_self_test

        return run_self_test()

    setup_dpi_awareness()  # before QApplication

    # AUMID before QApplication so the taskbar group is fixed from the first
    # window registration (otherwise Qt's class inherits the python.exe icon).
    ico_path = _setup_app_identity_and_icon()

    from PySide6.QtWidgets import QApplication

    from .config import OverlayConfig

    config = OverlayConfig()
    app = QApplication(sys.argv)
    if ico_path is not None:
        try:
            from PySide6.QtGui import QIcon

            app.setWindowIcon(QIcon(str(ico_path)))
        except Exception:  # noqa: BLE001
            # Cosmetic only; never block the overlay on icon issues.
            pass

    # Wiring order: state machine first (no side effect besides object
    # construction), then effects (attaches hooks to the router), then
    # windows with machine + bridge, then IPC with the on_message
    # callback. This way there's no phase where IPC is already delivering
    # events but no subscriber is attached yet.
    _state = setup_state_machine(app, config)
    _effects = setup_effects(app, config, router=_state["router"])
    _windows = setup_windows(
        app,
        config,
        state_machine=_state["machine"],
        effects_bridge=_effects["bridge"],
    )
    _ipc = setup_ipc(app, config, on_message=_state["on_message"])
    _mascot = setup_mascot(app, config, state_machine=_state["machine"], ipc=_ipc)
    _throttle = setup_throttling(
        app, config, state_machine=_state["machine"], windows=_windows
    )

    if args.smoke:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(5000, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
