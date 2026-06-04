"""Guided live sign-off probe runner (cross-platform Wave 4, sub-task 4.1; AD-3).

This is a thin **operator aide**, not an automated test. Waves 1-3 prove all six
platform ports green in CI, but four behaviors *cannot* be proven on a headless
runner (AD-3): a real AX/AT-SPI accessibility tree captured from a live app, the
Orb's actual transparency, global-hotkey *capture* (keys arriving from the OS),
and an elevation prompt. This script brackets the human-in-the-loop step for each
of the six ports: it constructs the relevant seam via its ``jarvis.platform``
factory, runs the part of the action it *can* run on the current OS, and prints
exactly what a human operator should observe to record a verdict in
``docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md``.

It deliberately does **not** automate the OS permission prompt (impossible) and
makes **no** claim that a GUI/permission behavior passed — that is the operator's
call after watching the screen. Honest labelling (AD-3) is the whole point.

Usage::

    python scripts/crossplatform/signoff_probe.py --list
    python scripts/crossplatform/signoff_probe.py --feature terminal
    python scripts/crossplatform/signoff_probe.py --feature applaunch
    python scripts/crossplatform/signoff_probe.py --feature ax
    python scripts/crossplatform/signoff_probe.py --feature orb
    python scripts/crossplatform/signoff_probe.py --feature hotkey
    python scripts/crossplatform/signoff_probe.py --feature admin

``--list`` runs on **any** OS without raising and prints the probe catalog. A
``--feature`` run only *acts* on the matching OS; on the wrong OS (or with a
missing capability) it prints what is needed and exits cleanly — it never fakes a
result and never raises.

Output language: English (CLAUDE.md Output-Language Policy).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

# cp1252 is the Windows console default (CLAUDE.md "Windows specifics"); this
# script prints check-mark / arrow glyphs, so force UTF-8 to avoid a
# UnicodeEncodeError on the maintainer's box.
try:  # pragma: no cover - depends on the console stream type
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except (AttributeError, ValueError):  # pragma: no cover
    pass


# ----------------------------------------------------------------------
# Catalog — one entry per feature x OS GUI/permission behavior (AD-3).
# This is data only, so ``--list`` works on every OS with zero imports of the
# jarvis package.
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeEntry:
    """One catalog row: a (feature x OS) behavior the operator signs off on."""

    feature: str  # the --feature key
    title: str  # human label
    target_os: str  # the OS the live behavior must be observed on
    ci_provable: bool  # True if CI can fully prove this (no live sign-off needed)
    behavior: str  # what the operator should observe / record


_CATALOG: tuple[ProbeEntry, ...] = (
    # --- Terminal (CI-provable, EK-4) -----------------------------------
    ProbeEntry(
        feature="terminal",
        title="PTY terminal — spawn shell + echo round-trip",
        target_os="macOS / Linux (CI-provable; Windows here)",
        ci_provable=True,
        behavior=(
            "make_pty_backend() spawns the OS-default shell; an `echo` round-trips "
            "back through the str<->bytes seam with no mojibake; exitstatus == 0. "
            "Fully provable on the ubuntu/macos runners (real PTY, EK-4) — no live "
            "GUI sign-off needed."
        ),
    ),
    # --- App-launch (CI-provable resolution; live launch is light) ------
    ProbeEntry(
        feature="applaunch",
        title="App-launch — resolve an app name to a launch target",
        target_os="macOS / Linux (resolution CI-provable; Windows here)",
        ci_provable=True,
        behavior=(
            "resolve_app_launch_target(name) maps a spoken/typed name to a per-OS "
            "LaunchTarget (open_a on macOS, xdg_open/executable on Linux, "
            "startfile/executable on Windows) and refuses a hallucinated name. "
            "Resolution is CI-verified; the *actual* process launch is a light live "
            "check on the real device."
        ),
    ),
    # --- UI-element-click (AX/AT-SPI) — live sign-off gated -------------
    ProbeEntry(
        feature="ax",
        title="UI-element-click — AX accessibility tree (macOS)",
        target_os="macOS (Accessibility permission granted)",
        ci_provable=False,
        behavior=(
            "Grant System Settings > Privacy & Security > Accessibility, then "
            "make_ui_tree_source() returns an AXTreeSource whose observe() yields "
            "non-empty UIANodes with canonical roles for the foreground app; a "
            "click_element by name lands. Revoke the grant and confirm the English "
            "onboarding message + pixel-path fallback (AD-13). Needs a real macOS "
            "desktop + a granted permission — not reachable in CI."
        ),
    ),
    ProbeEntry(
        feature="ax",
        title="UI-element-click — AT-SPI accessibility tree (Linux)",
        target_os="Linux (python3-pyatspi + AT-SPI bus up)",
        ci_provable=False,
        behavior=(
            "apt install python3-pyatspi gir1.2-atspi-2.0, ensure the AT-SPI bus is "
            "up, then make_ui_tree_source() returns an AtspiTreeSource with a "
            "non-empty tree; confirm the bus-unavailable degrade message + pixel "
            "fallback. pyatspi is distro-packaged (AD-14), not a pip extra — needs "
            "a real Linux desktop session."
        ),
    ),
    # --- Orb overlay — live sign-off gated (transparency) ---------------
    ProbeEntry(
        feature="orb",
        title="Orb overlay — transparent surface (macOS)",
        target_os="macOS (display present)",
        ci_provable=False,
        behavior=(
            "make_overlay_surface() returns a TkColorKeyOverlay; launch the desktop "
            "app and confirm a *transparent* orb (no opaque magenta/black backing "
            "box) that visibly changes to LISTENING then back to IDLE. A "
            "transparency mask cannot be proven on a headless runner."
        ),
    ),
    ProbeEntry(
        feature="orb",
        title="Orb overlay — best-effort transparency + tray fallback (Linux)",
        target_os="Linux (X11 compositor; Wayland/headless -> tray)",
        ci_provable=False,
        behavior=(
            "On a Linux compositor, make_overlay_surface() returns a "
            "LinuxBestEffortOverlay; confirm transparency. On Wayland / no "
            "compositor it returns a TrayOnlySurface — confirm a state-colored tray "
            "icon (IDLE/LISTENING/THINKING/SPEAKING), never an opaque magenta box, "
            "never a crash."
        ),
    ),
    # --- Hotkey capture — live sign-off gated ---------------------------
    ProbeEntry(
        feature="hotkey",
        title="Hotkey capture — global combo (macOS)",
        target_os="macOS (Input-Monitoring granted)",
        ci_provable=False,
        behavior=(
            "Grant Input-Monitoring, then make_hotkey_backend() returns a "
            "PynputBackend that actually *receives* the configured combo "
            "(ctrl+right_alt+j). Confirm the 'registered but zero events -> grant "
            "Input-Monitoring' hint fires when the grant is missing (AD-8). Key "
            "arrival from the OS cannot be proven in CI."
        ),
    ),
    ProbeEntry(
        feature="hotkey",
        title="Hotkey capture — global combo (Linux X11) / Wayland no-op",
        target_os="Linux X11 (capture) / Wayland (no-op + log)",
        ci_provable=False,
        behavior=(
            "On X11, make_hotkey_backend() returns a PynputBackend that captures "
            "the combo. On Wayland it returns a NoopBackend that logs once 'global "
            "hotkey unavailable on Wayland by OS design; lean on the wake word' and "
            "no-ops — the wake word still works (AD-8)."
        ),
    ),
    # --- Admin / elevation — never CI-E2E by design ---------------------
    ProbeEntry(
        feature="admin",
        title="Admin/elevation — auth prompt + privileged op (macOS)",
        target_os="macOS (Touch-ID/password sheet)",
        ci_provable=False,
        behavior=(
            "make_elevator() returns a MacAuthElevator; a brew/launchctl op through "
            "the UnixSocketTransport peer-cred path raises the Touch-ID/password "
            "sheet and, on approval, completes via an argv list (never a shell "
            "string). Confirm NullElevator refusal on a box with no auth. "
            "Interactive auth is never CI-testable end-to-end (AD-3)."
        ),
    ),
    ProbeEntry(
        feature="admin",
        title="Admin/elevation — polkit/sudo prompt + privileged op (Linux)",
        target_os="Linux (polkit pkexec / sudo)",
        ci_provable=False,
        behavior=(
            "make_elevator() returns a PolkitElevator (pkexec) raising the polkit "
            "dialog; an apt/systemctl/ufw op completes through the validated-argv "
            "HMAC core. Confirm the SudoElevator fallback and the NullElevator "
            "headless refusal (the €5-VPS path). Interactive auth is never "
            "CI-testable end-to-end (AD-3)."
        ),
    ),
)

_FEATURES: tuple[str, ...] = ("ax", "orb", "hotkey", "admin", "terminal", "applaunch")


# ----------------------------------------------------------------------
# --list
# ----------------------------------------------------------------------


def _print_catalog() -> None:
    """Print the full probe catalog. Runs on ANY OS without raising (AD-3)."""
    print("Cross-platform live sign-off probe catalog (Wave 4, AD-3)")
    print("=" * 70)
    print(
        "One entry per feature x OS GUI/permission behavior. 'CI-provable' rows "
        "need no live sign-off;\nthe rest need a real macOS/Linux device + the "
        "noted permission — they cannot be faked here.\n"
    )
    for feat in _FEATURES:
        rows = [e for e in _CATALOG if e.feature == feat]
        print(f"--feature {feat}")
        for e in rows:
            badge = "CI-provable" if e.ci_provable else "live-sign-off-gated"
            print(f"  - {e.title}")
            print(f"      target OS : {e.target_os}")
            print(f"      verify    : {badge}")
            print(f"      observe   : {e.behavior}")
        print()
    print("=" * 70)
    print(
        "Run `--feature <name>` on the matching real device to bracket the manual "
        "step.\nRecord each verdict in "
        "docs/plans/cross-platform-mac-linux/SIGNOFF-LOG.md."
    )


# ----------------------------------------------------------------------
# Shared header for a --feature run
# ----------------------------------------------------------------------


def _host_header(feature: str) -> str:
    """One-line host/platform banner; imports jarvis.platform lazily."""
    from jarvis.platform import detect_platform
    from jarvis.platform.capabilities import detect_capabilities

    plat = detect_platform()
    caps = detect_capabilities()
    return (
        f"[probe: {feature}] host platform = {plat} | "
        f"has_pty={caps.has_pty} has_hotkey={caps.has_hotkey} "
        f"has_ax_tree={caps.has_ax_tree} has_overlay={caps.has_overlay} "
        f"has_elevation={caps.has_elevation} display_present={caps.display_present} "
        f"is_wayland={caps.is_wayland} ax_permission_granted={caps.ax_permission_granted}"
    )


def _needs_real_desktop(feature: str) -> None:
    """Print the honest 'needs a real macOS/Linux desktop + permission' note."""
    print(
        f"\nThis behavior ({feature}) is a live-sign-off-gated GUI/permission "
        "behavior (AD-3).\nIt needs a real macOS/Linux desktop + the noted "
        "permission and a human watching the screen.\nIt cannot be proven on this "
        "host or in CI. Construct-only checks below; record the verdict manually "
        "in SIGNOFF-LOG.md."
    )


# ----------------------------------------------------------------------
# Per-feature probes — each constructs the seam factory and runs what it can.
# ----------------------------------------------------------------------


def _probe_terminal() -> None:
    """Terminal (CI-provable): spawn the OS shell and echo round-trip."""
    from jarvis.terminal.backend import make_pty_backend
    from jarvis.terminal.shells import discover_shells

    backend = make_pty_backend()
    print(f"make_pty_backend() -> {type(backend).__name__}")

    shells = discover_shells()
    if not shells:
        print(
            "No interactive shell discovered on this host — the terminal would "
            "degrade with a logged English message. (Operator: confirm the "
            "degrade, not a crash.)"
        )
        return
    shell = shells[0]
    print(f"OS-default shell = {shell.id}  argv={shell.argv}")

    marker = "hello-jarvis-signoff"
    try:
        handle = backend.spawn(tuple(shell.argv), None, 80, 24)
    except RuntimeError as exc:
        # NullPtyBackend / no toolchain — the graceful degrade (AD-6).
        print(f"spawn refused gracefully (degrade, not a crash): {exc}")
        return
    try:
        import time

        # Drive an echo and read it back through the str-facing seam.
        if sys.platform == "win32":
            handle.write(f"echo {marker}\r\n")
        else:
            handle.write(f"echo {marker}\n")
        collected = ""
        deadline = time.time() + 5.0
        while time.time() < deadline and marker not in collected:
            try:
                collected += handle.read(4096)
            except (EOFError, OSError):
                break
            time.sleep(0.05)
        if marker in collected:
            print(
                f"PTY round-trip OK: captured '{marker}' from the shell (str seam "
                "holds, no mojibake)."
            )
        else:
            print(
                "PTY spawned but the echo marker was not captured in 5s — inspect "
                "the read-loop on this host (not a crash; report for sign-off)."
            )
    finally:
        try:
            handle.terminate(force=True)
        except Exception as exc:  # noqa: BLE001
            print(f"(terminate cleanup note: {exc})")


def _probe_applaunch() -> None:
    """App-launch (CI-provable resolution): resolve names + reject a fake."""
    from jarvis.plugins.tool.app_resolver import resolve_app_launch_target

    samples = ["calculator", "terminal", "code", "Flibbertyglop-nonexistent-app"]
    print("resolve_app_launch_target() on representative names:")
    for name in samples:
        target = resolve_app_launch_target(name)
        print(f"  {name!r:42} -> kind={target.kind!r} value={target.value!r}")
    print(
        "\nResolution is CI-verified. The *actual* process launch (a new window "
        "appearing) is a light live check on the real device — observe a browser/"
        "calculator window open, and confirm the nonsense name is refused with a "
        "spoken English explanation rather than silently attempted."
    )


def _probe_ax() -> None:
    """UI-element-click (live-gated): construct the source, describe the sign-off."""
    from jarvis.vision.tree_factory import make_ui_tree_source

    source = make_ui_tree_source()
    print(f"make_ui_tree_source() -> {type(source).__name__}")
    cls = type(source).__name__
    if cls == "AXTreeSource":
        print(
            "macOS AX source constructed. OPERATOR: grant Accessibility, bring an "
            "app to the foreground, and confirm observe() returns non-empty "
            "UIANodes with canonical roles; then revoke and confirm the onboarding "
            "message + pixel fallback (AD-13)."
        )
    elif cls == "AtspiTreeSource":
        print(
            "Linux AT-SPI source constructed. OPERATOR: with the AT-SPI bus up + "
            "python3-pyatspi installed, confirm observe() returns a non-empty tree; "
            "then stop the bus and confirm the degrade message + pixel fallback."
        )
    else:  # NullUITreeSource or UIATreeSource(Windows)
        print(
            "This host returned a "
            f"{cls} (no live AX/AT-SPI tree to sign off here). The named-element "
            "click on macOS/Linux must be observed on a real desktop with the "
            "permission granted."
        )
    _needs_real_desktop("ax")


def _probe_orb() -> None:
    """Orb (live-gated): construct the surface, describe the transparency sign-off."""
    from jarvis.overlay.surface import make_overlay_surface

    surface = make_overlay_surface()
    cls = type(surface).__name__
    print(f"make_overlay_surface() -> {cls}")
    if cls == "TkColorKeyOverlay":
        print(
            "Transparent-overlay surface constructed (Windows/macOS path). "
            "OPERATOR: on macOS, launch the desktop app and confirm a *transparent* "
            "orb (no opaque magenta/black box) cycling IDLE->LISTENING->...->IDLE."
        )
    elif cls == "LinuxBestEffortOverlay":
        print(
            "Linux best-effort transparent surface constructed. OPERATOR: on a "
            "compositor confirm transparency; verify it degrades to the tray on "
            "Wayland / no compositor."
        )
    elif cls == "TrayOnlySurface":
        print(
            "Tray-only floor surface constructed (headless/Wayland/no-Tk). "
            "OPERATOR: confirm a state-colored tray icon shows the four states — "
            "never an opaque magenta box, never a crash (this is the graceful "
            "degrade, a PASS for the contract)."
        )
    else:
        print(f"Unexpected surface type {cls} — record for sign-off.")
    _needs_real_desktop("orb")


def _probe_hotkey() -> None:
    """Hotkey (live-gated): construct the backend, describe the capture sign-off."""
    from jarvis.trigger.backends import make_hotkey_backend

    backend = make_hotkey_backend()
    cls = type(backend).__name__
    print(f"make_hotkey_backend() -> {cls}")
    if cls == "PynputBackend":
        print(
            "pynput backend constructed (macOS / Linux-X11). OPERATOR: grant "
            "Input-Monitoring (macOS), press the configured combo, and confirm the "
            "press is *captured* (Jarvis enters LISTENING). Confirm the 'registered "
            "but zero events' hint fires when the grant is missing (AD-8)."
        )
    elif cls == "NoopBackend":
        print(
            "Noop backend constructed (Wayland or no pynput). OPERATOR: confirm the "
            "combo does nothing but logs once 'global hotkey unavailable on Wayland "
            "by OS design; lean on the wake word' — and the wake word still works. "
            "This is the graceful degrade (a PASS for the contract)."
        )
    else:  # GlobalHotkeysBackend (Windows)
        print(
            f"This host returned {cls} (Windows). The macOS/Linux live capture must "
            "be observed on a real desktop — key arrival from the OS is not "
            "provable in CI."
        )
    _needs_real_desktop("hotkey")


def _probe_admin() -> None:
    """Admin/elevation (live-gated): construct the seams, describe the prompt sign-off."""
    from jarvis.admin.elevator import make_elevator
    from jarvis.admin.transport import make_admin_transport

    elevator = make_elevator()
    transport = make_admin_transport()
    print(f"make_elevator()         -> {type(elevator).__name__}")
    print(f"make_admin_transport()  -> {type(transport).__name__}")
    ecls = type(elevator).__name__
    if ecls == "NullElevator":
        print(
            "NullElevator selected — this host has no elevation mechanism (the "
            "headless €5-VPS path). OPERATOR: confirm a privileged op returns a "
            "typed AdminResponse(success=False, ...) with the English 'no elevation "
            "mechanism available' message, never silently running and never "
            "crashing (the graceful degrade, a PASS for the contract)."
        )
    elif ecls in ("MacAuthElevator", "PolkitElevator", "SudoElevator", "UacElevator"):
        print(
            f"{ecls} selected. OPERATOR: trigger an authorized privileged op and "
            "confirm the OS auth prompt (Touch-ID/password sheet / polkit dialog / "
            "UAC) appears, and on approval the op completes via an argv list (never "
            "a shell string). Interactive auth is never CI-testable end-to-end "
            "(AD-3) — record the verdict manually."
        )
    else:
        print(f"Unexpected elevator type {ecls} — record for sign-off.")
    _needs_real_desktop("admin")


_PROBES = {
    "terminal": _probe_terminal,
    "applaunch": _probe_applaunch,
    "ax": _probe_ax,
    "orb": _probe_orb,
    "hotkey": _probe_hotkey,
    "admin": _probe_admin,
}


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="signoff_probe.py",
        description=(
            "Guided cross-platform live sign-off probe runner (Wave 4, AD-3). "
            "Brackets the manual GUI/permission step for each of the six ports; "
            "never automates a permission prompt and never fakes a verdict."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        action="store_true",
        help="Print the probe catalog (runs on any OS, never raises).",
    )
    group.add_argument(
        "--feature",
        choices=_FEATURES,
        help="Construct the named seam's factory and bracket its sign-off step.",
    )
    args = parser.parse_args(argv)

    if args.list:
        _print_catalog()
        return 0

    print(_host_header(args.feature))
    print("-" * 70)
    probe = _PROBES[args.feature]
    try:
        probe()
    except Exception as exc:  # noqa: BLE001
        # A probe must never crash the operator's session; surface the failure as
        # a recordable note (AD-6 "no sys.platform branch ever raises").
        print(
            f"\n[probe note] the {args.feature} probe hit an exception while "
            f"bracketing the manual step: {exc!r}\nThis is a note for the "
            "sign-off log, not a verdict — the live behavior is judged by the "
            "operator on a real device."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
