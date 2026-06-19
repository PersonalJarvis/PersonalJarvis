"""Cross-platform login autostart — the 7th cross-platform port.

Public API:
    resolve_launch_spec(cfg)        -> LaunchSpec   (how to relaunch at login)
    make_autostart_manager(caps)    -> AutostartManager
    reconcile_autostart(cfg, caps)  -> AutostartStatus  (self-healing boot hook)

Design: ``docs/superpowers/specs/2026-05-30-cross-platform-autostart-design.md``.

``reconcile_autostart`` is the self-healing boot hook. It runs once during the
launcher boot (off the voice critical path) and reconciles the on-disk autostart
entry with ``cfg.autostart.enabled``:

    enabled=True  + entry missing/stale  -> install   (self-heal path drift)
    enabled=True  + entry current        -> no-op
    enabled=False + entry present         -> uninstall
    headless / unsupported host           -> no-op

It NEVER raises — autostart must not block or crash boot (AD-6 spirit). The
default ``cfg.autostart.enabled = True`` means the first boot after this feature
ships finds no entry and installs it, which is the "apply to the current install
now" behaviour. The Settings toggle is the intended off-switch; a manually
deleted entry is recreated next boot while the toggle stays on (by design).
"""

from __future__ import annotations

import logging

from .command import LAUNCHER_MODULE, resolve_launch_spec
from .factory import make_autostart_manager
from .protocol import AutostartManager, AutostartStatus, LaunchSpec

log = logging.getLogger(__name__)


def reconcile_autostart(cfg: object, caps: object | None = None) -> AutostartStatus:
    """Self-heal the autostart entry against ``cfg.autostart.enabled``.

    ``caps`` defaults to the cached host capability snapshot. Never raises: any
    failure is logged and reported in the returned status' ``detail``.
    """
    try:
        from jarvis.platform.capabilities import detect_capabilities

        capabilities = caps or detect_capabilities()
        manager = make_autostart_manager(capabilities)  # type: ignore[arg-type]
        spec = resolve_launch_spec(cfg)

        autostart_cfg = getattr(cfg, "autostart", None)
        enabled = bool(getattr(autostart_cfg, "enabled", True))

        current = manager.status(spec)
        if not current.supported:
            log.debug("Autostart reconcile: unsupported host — %s", current.detail)
            return current

        if enabled:
            if not current.installed or not current.matches_spec:
                result = manager.install(spec)
                log.info("Autostart reconcile: installed/refreshed (%s)", result.detail)
                return result
            log.debug("Autostart reconcile: already current — no-op")
            return current

        # enabled is False
        if current.installed:
            result = manager.uninstall()
            log.info("Autostart reconcile: removed per config")
            return result
        log.debug("Autostart reconcile: disabled and absent — no-op")
        return current
    except Exception as exc:  # noqa: BLE001 — must never block/crash boot (AD-6)
        log.warning("Autostart reconcile failed (ignored): %s", exc)
        return AutostartStatus(
            supported=False,
            installed=False,
            matches_spec=False,
            entry_path=None,
            detail=f"Autostart reconcile error (ignored): {exc}.",
        )


__all__ = [
    "AutostartManager",
    "AutostartStatus",
    "LaunchSpec",
    "LAUNCHER_MODULE",
    "resolve_launch_spec",
    "make_autostart_manager",
    "reconcile_autostart",
]
