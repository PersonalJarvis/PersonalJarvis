"""Jarvis entry point.

Usage:
    python -m jarvis                # Starts the tray app (first-run setup
                                    #   happens in the app's onboarding)
    python -m jarvis --wizard       # Terminal setup wizard (explicit opt-in,
                                    #   e.g. SSH-only hosts)
    python -m jarvis --check        # Show hardware analysis only
    python -m jarvis --plugins      # List the plugin registry
    python -m jarvis --uninstall    # Remove Jarvis from this machine (folder,
                                    #   login-autostart entry, saved API keys)
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from typing import NoReturn

# Windows Terminal defaults to cp1252 — which breaks Unicode (box-drawing,
# emojis, ✓/✗). Force utf-8 before printing anything.
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

from jarvis import __version__
from jarvis.core import config as cfg
from jarvis.core import registry
from jarvis.hardware import detection
from jarvis.ui.tray import JarvisState, JarvisTray, TrayCommand


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="Personal Jarvis — voice-driven meta-orchestrator.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--wizard", action="store_true", help="Restart the setup wizard.")
    parser.add_argument("--check", action="store_true", help="Only show the hardware analysis.")
    parser.add_argument("--plugins", action="store_true", help="List the plugin registry.")
    parser.add_argument("--debug", action="store_true", help="Debug logging + console attach.")
    # Phase 5:
    parser.add_argument("--phase5-doctor", action="store_true", dest="phase5_doctor",
                        help="Checks Phase-5 prerequisites (admin helper, "
                             "vision deps, kill hotkey, cost config).")
    parser.add_argument("--install-admin-helper", action="store_true",
                        dest="install_admin_helper",
                        help="Generates the HMAC secret + registers the admin helper shortcut.")
    parser.add_argument("--orb-doctor", action="store_true", dest="orb_doctor",
                        help="Dry-run diagnostic: where would the orb spawn? "
                             "Reads jarvis.toml + EnumDisplayMonitors, without "
                             "opening a Tk window (BUG-027 / ADR-0016).")
    parser.add_argument("--doctor", action="store_true", dest="doctor",
                        help="Completeness self-check: honestly report what is "
                             "registered & ready vs. advertised but missing "
                             "(phantom tools, dead harness config, sub-agent "
                             "worker CLI, brain provider). Exits non-zero on a "
                             "hard failure.")
    parser.add_argument(
        "--reset-onboarding",
        action="store_true",
        dest="reset_onboarding",
        help="Clear onboarding markers so the first-run guide shows again.",
    )
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="Download all voice models the current config needs, then exit. "
             "Used by the installer so the first launch has nothing left to fetch.",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove this Jarvis install from the machine: the install folder, "
             "the login-autostart entry, and the API keys saved in the OS "
             "keychain. Asks for confirmation first.",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true", dest="assume_yes",
        help="With --uninstall: skip the confirmation prompt.",
    )
    parser.add_argument(
        "--keep-keys", action="store_true", dest="keep_keys",
        help="With --uninstall: keep the saved API keys in the OS keychain.",
    )
    parser.add_argument(
        "--keep-folder", action="store_true", dest="keep_folder",
        help="With --uninstall: leave the install folder in place (used by the "
             "uninstall.ps1/.sh bootstraps, which delete it from outside the venv).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", dest="dry_run",
        help="With --uninstall: show what would be removed, change nothing.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["serve"],
        help="serve: start the headless web UI (browser/server, no desktop) — "
             "the cloud-first path for a VPS, Mac or Linux. Open the printed URL.",
    )
    return parser


def _parse_args(argv: list[str]) -> argparse.Namespace:
    return _build_parser().parse_args(argv)


def _cmd_check() -> int:
    return detection.main()


def _cmd_plugins() -> int:
    print(registry.describe())
    return 0


def _cmd_wizard() -> int:
    from jarvis.setup import wizard

    return wizard.run()


def _cmd_phase5_doctor() -> int:
    """Status check for all Phase-5 features. Shows what is enabled,
    what is missing, and what is running on defaults. No config changes.
    """
    import importlib.metadata as _md

    config = cfg.load_config()
    lines: list[str] = []
    lines.append(f"Jarvis {__version__} — Phase-5-Doctor")
    lines.append("=" * 60)

    # Entry points
    try:
        # Computer-use harness name sourced from the local action gate
        # (single home for the literal).
        from jarvis.brain.local_action_gate import HARNESS_NAME
        eps = list(_md.entry_points(group="jarvis.harness"))
        have_cu = any(ep.name == HARNESS_NAME for ep in eps)
        lines.append(f"[{'OK' if have_cu else 'FAIL'}] Harness plugin "
                      f"{HARNESS_NAME!r} in entry-points index: {have_cu}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[FAIL] Harness entry points not readable: {exc}")

    try:
        eps = list(_md.entry_points(group="jarvis.tool"))
        have_admin = any(ep.name == "dispatch-to-admin" for ep in eps)
        lines.append(f"[{'OK' if have_admin else 'FAIL'}] Tool plugin "
                      f"'dispatch-to-admin' in entry-points index: {have_admin}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[FAIL] Tool entry points not readable: {exc}")

    # Config sections
    raw = cfg._RAW_CONFIG if hasattr(cfg, "_RAW_CONFIG") else {}
    phase5_sections = [
        "vision", "computer_use", "admin_helper", "task_queue",
        "kill_switch", "cost",
    ]
    for section in phase5_sections:
        sect = raw.get(section, {}) if isinstance(raw, dict) else {}
        enabled = sect.get("enabled", False) if isinstance(sect, dict) else False
        lines.append(f"[{'ON ' if enabled else 'OFF'}] jarvis.toml:[{section}] "
                      f"enabled={enabled}")

    # Admin HMAC
    try:
        secret = cfg.get_secret("jarvis_admin_hmac", "JARVIS_ADMIN_HMAC")
        lines.append(f"[{'OK' if secret else 'MISS'}] HMAC secret "
                      f"in credential manager: {'present' if secret else 'missing'}")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[FAIL] HMAC secret check: {exc}")

    # Vision deps
    try:
        import mss  # noqa: F401
        lines.append("[OK ] mss (screenshot) importable")
    except Exception:  # noqa: BLE001
        lines.append("[FAIL] mss (screenshot) not importable")
    try:
        import pywinauto  # noqa: F401
        lines.append("[OK ] pywinauto (UIA tree) importable")
    except Exception:  # noqa: BLE001
        lines.append("[FAIL] pywinauto (UIA tree) not importable")

    _ = config  # suppress unused
    print("\n".join(lines))
    return 0


def _cmd_doctor() -> int:
    """Completeness self-check — what is registered & ready vs. advertised but
    missing. Generalises the phantom-openclaw forensic (2026-06-28): a fresh
    download can *look* complete while one dead reference makes a working feature
    appear "not installed". Exits non-zero only on a hard failure.
    """
    from jarvis.diagnostics.doctor import has_failures, run_doctor

    _ICON = {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]", "info": "[ -- ]"}

    config = cfg.load_config()
    findings = run_doctor(config)

    lines: list[str] = [f"Jarvis {__version__} — Doctor (completeness self-check)",
                        "=" * 64]
    last_cat: str | None = None
    for f in findings:
        if f.category != last_cat:
            lines.append(f"\n{f.category}:")
            last_cat = f.category
        lines.append(f"  {_ICON.get(f.status, '[ ?? ]')} {f.message}")
        if f.hint:
            lines.append(f"         → {f.hint}")

    fail = has_failures(findings)
    warn = any(f.status == "warn" for f in findings)
    lines.append("\n" + "=" * 64)
    if fail:
        lines.append("RESULT: FAIL — something advertised cannot work (see above).")
    elif warn:
        lines.append("RESULT: OK with warnings — works, but some config/prereqs "
                     "are incomplete.")
    else:
        lines.append("RESULT: OK — everything advertised is registered and ready.")

    print("\n".join(lines))
    return 1 if fail else 0


def _cmd_orb_doctor() -> int:
    """Dry-run diagnostic for orb placement (BUG-027 / ADR-0016).

    Reads the persisted orb position from jarvis.toml, enumerates current
    monitors via Win32 ``EnumDisplayMonitors``, and computes where the
    orb WOULD spawn under the current ``require_primary`` policy — all
    without opening a Tk window. Useful when the user reports "orb is
    gone" and you need to know whether the persisted pin is the cause
    before restarting Jarvis.
    """
    from pathlib import Path

    from ui.orb.drag_persistence import (
        load_allow_secondary_monitor_pin,
        load_position_from_toml,
        resolve_placement,
        screens_from_tk,
    )

    toml_path = Path(cfg.DEFAULT_CONFIG_FILE)
    lines: list[str] = []
    lines.append(f"Jarvis {__version__} — Orb-Doctor")
    lines.append("=" * 60)
    lines.append(f"Config: {toml_path}")
    lines.append("")

    persisted = load_position_from_toml(toml_path)
    if persisted is None:
        lines.append("Persisted pin: NONE (jarvis.toml missing)")
    elif not persisted.monitor:
        lines.append("Persisted pin: NONE (default anchor on next boot)")
    else:
        lines.append(
            f"Persisted pin: monitor={persisted.monitor!r} "
            f"x_relative={persisted.x_relative} "
            f"y_relative={persisted.y_relative}"
        )
    allow_secondary = load_allow_secondary_monitor_pin(toml_path)
    lines.append(f"allow_secondary_monitor_pin = {allow_secondary}")
    lines.append("")

    screens = screens_from_tk(None)
    if not screens:
        lines.append("[FAIL] EnumDisplayMonitors returned no screens.")
    else:
        lines.append(f"Monitors ({len(screens)}):")
        for s in screens:
            sx, sy, sw, sh = s.geometry
            tag = "PRIMARY" if s.is_primary else "secondary"
            lines.append(
                f"  - {s.name} [{tag}] x={sx} y={sy} w={sw} h={sh}"
            )
    lines.append("")

    if screens:
        placement = resolve_placement(
            persisted,
            screens,
            mascot_size_px=108,
            require_primary=not allow_secondary,
        )
        # Determine if the resolved monitor is primary.
        resolved_screen = next(
            (s for s in screens if s.name == placement.monitor), None
        )
        on_primary = bool(resolved_screen and resolved_screen.is_primary)
        lines.append(
            f"Resolved spawn: abs_x={placement.abs_x} abs_y={placement.abs_y} "
            f"monitor={placement.monitor!r} recovered={placement.recovered}"
        )
        lines.append(f"On primary monitor: {'YES' if on_primary else 'NO'}")
        if placement.recovered and persisted is not None and persisted.monitor:
            lines.append("")
            lines.append(
                "Note: the persisted pin would be DROPPED on next boot "
                "(BUG-027 defense). To honour a pin on a secondary monitor "
                "set `[overlay.mascot] allow_secondary_monitor_pin = true` "
                "in jarvis.toml."
            )
        elif not on_primary:
            lines.append("")
            lines.append(
                "Warning: orb will spawn on a non-primary monitor. Say "
                "'Orb zurück'"  # i18n-allow: recognized voice-trigger phrase (ADR-0016 L2)
                " or use the right-click menu to reset."
            )

    print("\n".join(lines))
    return 0


_ONBOARDING_STATE_PATH = None  # tests override; None => state.py default


def _cmd_reset_onboarding() -> int:
    from jarvis.setup import state as onb_state

    removed = onb_state.reset_onboarding(_ONBOARDING_STATE_PATH)
    onb_state.remove_setup_complete_marker(_ONBOARDING_STATE_PATH)
    print(f"Onboarding reset. Cleared keys: {removed or 'none'}; removed .setup-complete.")
    print("Next launch will show the setup guide.")
    return 0


def _cmd_uninstall(args: argparse.Namespace) -> int:
    """Remove this Jarvis install from the machine (folder + autostart + keys)."""
    from jarvis.setup import uninstall

    return uninstall.run_uninstall(
        assume_yes=args.assume_yes,
        keep_keys=args.keep_keys,
        keep_folder=args.keep_folder,
        dry_run=args.dry_run,
    )


def _cmd_install_admin_helper() -> int:
    """Generates the HMAC shared secret (if missing) in the Credential Manager."""
    try:
        from jarvis.admin.launcher import ensure_admin_secret
    except ImportError as exc:
        print(f"Admin helper launcher not importable: {exc}", file=sys.stderr)
        return 1
    try:
        secret = ensure_admin_secret()
    except Exception as exc:  # noqa: BLE001
        print(f"Admin HMAC generation failed: {exc}", file=sys.stderr)
        return 2
    print(f"Admin HMAC secret ready (length: {len(secret)} bytes).")
    print("The helper will be launched via UAC prompt on the next admin op.")
    return 0


async def _run_tray_app(debug: bool = False) -> int:
    """Tray app event loop."""
    config = cfg.load_config()
    print(f"Jarvis {__version__} started (profile: {config.profile.name}).")
    if debug:
        print(f"Config file: {cfg.DEFAULT_CONFIG_FILE}")
        print(f"Brain primary: {config.brain.primary}")
        print(f"STT: {config.stt.provider} / {config.stt.model}")
        print(f"TTS: {config.tts.provider}")

    tray = JarvisTray()
    tray.start()
    tray.set_state(JarvisState.IDLE)

    command_queue = await tray.command_stream()
    stop_event = asyncio.Event()

    # Cleanly intercept SIGINT / Ctrl+C
    def _stop_handler(*_: object) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, _stop_handler)
        signal.signal(signal.SIGTERM, _stop_handler)
    except (ValueError, AttributeError):
        # Windows + subprocess contexts sometimes do not allow signal registration
        pass

    print("Tray icon running. Right-click for menu. Quit with Ctrl+C or Tray → Quit.")

    async def _command_handler() -> None:
        while not stop_event.is_set():
            try:
                cmd: TrayCommand = await asyncio.wait_for(command_queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            if cmd.action == "quit":
                stop_event.set()
                return
            if cmd.action == "pause":
                tray.set_state(JarvisState.PAUSED)
            elif cmd.action == "resume":
                tray.set_state(JarvisState.IDLE)
            elif cmd.action == "reload_config":
                try:
                    cfg.load_config()
                    print("Config reloaded.")
                except Exception as exc:  # noqa: BLE001
                    print(f"Config reload failed: {exc}")
                    tray.set_error(str(exc))

    handler_task = asyncio.create_task(_command_handler())
    try:
        await stop_event.wait()
    finally:
        handler_task.cancel()
        tray.stop()
    print("Jarvis stopped.")
    return 0


def _run_control(argv: list[str]) -> int:
    """Forward a control invocation (``jarvis <group> ...``) to the control CLI.

    The control surface is the Typer app in ``jarvis.cli_ctl.__main__`` (also the
    ``jarvisctl`` / ``jctl`` binaries); routing it through ``jarvis`` gives one
    brand without disturbing the launcher's own argument parsing. The dynamic
    ``api`` group build is best-effort, so curated commands still work when the
    server is down.
    """
    import click

    from jarvis.cli_ctl.__main__ import build_root_command

    root = build_root_command()
    try:
        rv = root.main(args=argv, prog_name="jarvis", standalone_mode=False)
        return rv if isinstance(rv, int) else 0
    except click.exceptions.Exit as exc:
        return int(getattr(exc, "exit_code", 0) or 0)
    except click.exceptions.Abort:
        print("aborted", file=sys.stderr)
        return 1
    except click.exceptions.ClickException as exc:
        exc.show()
        return exc.exit_code
    except SystemExit as exc:  # e.g. --help / no_args_is_help
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Unified entry point: `jarvis <group> ...` (or a control-global option like
    # `--json`) drives the control CLI; bare `jarvis`, `jarvis serve`, and every
    # launcher flag (`--wizard`, `--check`, …) keep their existing behavior.
    from jarvis.cli_ctl.reserved import is_control_invocation

    if is_control_invocation(raw):
        return _run_control(raw)

    args = _parse_args(raw)

    if args.check:
        return _cmd_check()
    if args.plugins:
        return _cmd_plugins()
    if args.phase5_doctor:
        return _cmd_phase5_doctor()
    if args.orb_doctor:
        return _cmd_orb_doctor()
    if args.doctor:
        return _cmd_doctor()
    if args.install_admin_helper:
        return _cmd_install_admin_helper()
    if args.uninstall:
        return _cmd_uninstall(args)
    if args.reset_onboarding:
        return _cmd_reset_onboarding()
    if args.prefetch:
        from jarvis.setup.prefetch import prefetch_all

        return prefetch_all()
    if args.command == "serve":
        # Headless web UI — the cloud-first path (no desktop/tray). Delegates to
        # the web launcher so `jarvis serve` == `python -m jarvis.ui.web.launcher --headless`.
        from jarvis.ui.web import launcher

        return launcher.main(["--headless"])
    if _should_run_wizard(args.wizard):
        return _cmd_wizard()
    return asyncio.run(_run_tray_app(debug=args.debug))


def _should_run_wizard(wizard_flag: bool) -> bool:
    """Setup lives in the desktop/browser onboarding (first-launch guide);
    the terminal wizard is an explicit opt-in for SSH-only setups. First-run
    state deliberately does NOT factor in — a fresh install boots straight
    into the app, which shows the one-time onboarding itself."""
    return wizard_flag


def _entrypoint() -> NoReturn:
    raise SystemExit(main())


if __name__ == "__main__":
    _entrypoint()
