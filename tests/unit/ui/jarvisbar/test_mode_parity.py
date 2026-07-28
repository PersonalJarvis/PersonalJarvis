"""Every bar layer must agree on the SAME coarse-mode vocabulary.

This is the AP-4 / BUG-008 enum-drift guard for the bar. The vocabulary used to
be a tuple in ``renderer`` that ``subprocess_overlay`` hand-copied, and it duly
drifted: ``"dictate"`` was handled by ``visual_mode`` and ``resolve_click`` for
weeks while every surface silently dropped it, so the dictation bar never
appeared on any OS. There is now ONE definition
(``jarvis.ui.jarvisbar.modes``) and these tests fail the build if any layer
restates it, stops accepting a mode, or leaves one without a defined look and a
defined click behaviour.

Deliberately derived: the expectations iterate ``modes.MODES`` rather than
listing modes, so ADDING a mode automatically extends the coverage instead of
silently escaping it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from jarvis.ui.jarvisbar import interaction, modes, renderer
from jarvis.ui.jarvisbar import subprocess_overlay as subprocess_mod
from jarvis.ui.jarvisbar.null_overlay import NullOverlay
from jarvis.ui.jarvisbar.overlay import JarvisBarOverlay

_PACKAGE_DIR = Path(renderer.__file__).resolve().parent


# --------------------------------------------------------------------------
# One definition, imported everywhere
# --------------------------------------------------------------------------
def test_the_vocabulary_has_exactly_one_definition() -> None:
    """Nothing may restate the tuple — every layer imports the same object."""
    assert renderer.MODES is modes.MODES
    assert renderer.DICTATION_MODES is modes.DICTATION_MODES
    assert subprocess_mod._MODES is modes.MODES
    assert interaction.DICTATION_MODES is modes.DICTATION_MODES


def test_no_module_hardcodes_a_mode_tuple() -> None:
    """A future hand-copied mirror is a build failure, not a bug report.

    Scans the package for a literal tuple/list of mode strings outside the one
    module that is allowed to define them. This is what makes the guard survive
    a new surface written by someone who never read this file.
    """
    literal = re.compile(r"""\(\s*["']idle["']\s*,\s*["']listen["']""")
    offenders: list[str] = []
    for path in sorted(_PACKAGE_DIR.glob("*.py")):
        if path.name == "modes.py":
            continue
        if literal.search(path.read_text(encoding="utf-8")):
            offenders.append(path.name)
    assert not offenders, (
        "these modules restate the coarse-mode vocabulary instead of importing "
        f"jarvis.ui.jarvisbar.modes: {offenders}"
    )


def test_dictation_modes_are_part_of_the_vocabulary() -> None:
    for mode in modes.DICTATION_MODES:
        assert mode in modes.MODES
    # The four voice modes are untouched — no existing behaviour may shift.
    assert modes.VOICE_MODES == ("idle", "listen", "speak", "think")
    assert modes.DICTATION_MODES == ("dictate", "dictate_transcribing")


# --------------------------------------------------------------------------
# Every surface accepts every mode
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mode", modes.MODES)
def test_the_tk_surface_accepts_every_mode(mode: str) -> None:
    bar = JarvisBarOverlay(persistent=False, accent="#abcdef")
    bar.show(mode)
    assert bar._mode == mode


def test_the_tk_surface_still_rejects_an_unknown_mode() -> None:
    bar = JarvisBarOverlay(persistent=False, accent="#abcdef")
    bar.show("listen")
    bar.show("bogus")
    assert bar._mode == "listen"


@pytest.mark.parametrize("mode", modes.MODES)
def test_the_ipc_proxy_accepts_and_forwards_every_mode(mode: str) -> None:
    """The macOS proxy must put every mode on the wire, or the feature is
    Windows/Linux-only — a silent OS-parity hole (CLAUDE.md §3)."""
    proxy = subprocess_mod.SubprocessBarOverlay.__new__(
        subprocess_mod.SubprocessBarOverlay
    )
    sent: list[dict] = []
    proxy._send = sent.append  # type: ignore[method-assign]
    proxy._persistent_flag = False
    proxy._visible = False
    proxy._mode = "idle"

    proxy.show(mode)

    assert proxy._mode == mode
    assert sent == [{"op": "show", "mode": mode}]
    assert proxy._visible is (mode != "idle")


@pytest.mark.parametrize("mode", modes.MODES)
def test_the_host_protocol_forwards_every_mode_verbatim(mode: str) -> None:
    """The companion-process protocol carries no mode list of its own."""
    from jarvis.ui.jarvisbar import host

    class _Surface:
        def __init__(self) -> None:
            self.shown: list[str] = []

        def show(self, mode: str = "listen") -> None:
            self.shown.append(mode)

    surface = _Surface()
    assert host.dispatch(surface, {"op": "show", "mode": mode}) is True
    assert surface.shown == [mode]


@pytest.mark.parametrize("mode", modes.MODES)
def test_the_null_surface_swallows_every_mode(mode: str) -> None:
    NullOverlay().show(mode)  # must never raise on any mode


# --------------------------------------------------------------------------
# Every mode has a defined look and a defined click behaviour
# --------------------------------------------------------------------------
@pytest.mark.parametrize("mode", modes.MODES)
def test_every_mode_resolves_to_a_renderable_look(mode: str) -> None:
    for seconds_since_audible in (0.0, 99.0):
        for playback in (False, True):
            look = renderer.visual_mode(
                mode, seconds_since_audible, hold_s=0.4, playback_active=playback
            )
            assert look in ("idle", "speak", "think")


@pytest.mark.parametrize("mode", modes.MODES)
def test_every_mode_has_a_pill_size(mode: str) -> None:
    w, h = renderer.target_pill_size(mode, hovered=False)
    assert w > 0 and h > 0


@pytest.mark.parametrize("mode", modes.MODES)
def test_every_mode_resolves_a_click(mode: str) -> None:
    action = interaction.resolve_click(400, 800, mode, hovered=True, pill_w=400)
    assert action in ("hangup", "mute", "talk", "none")
