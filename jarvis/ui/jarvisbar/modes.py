"""The bar's coarse-mode vocabulary — ONE definition, imported everywhere.

A coarse mode is what the bridge asks the bar to *be* (``show(mode=...)``); the
rendered look is derived from it plus live audio by ``renderer.visual_mode``.

Why this module exists at all: the tuple used to live in ``renderer`` and was
hand-copied into ``subprocess_overlay`` because that module is a pure IPC proxy
and must not drag numpy/PIL into the parent process. A hand-copied enum is the
five-layer drift pattern this repo has been bitten by four times (AP-4 /
BUG-008) — and it duly drifted: ``"dictate"`` was handled by ``visual_mode`` and
``resolve_click`` for weeks while every surface silently dropped it, because it
was never added to the tuple. So the vocabulary now lives in a module with NO
imports at all: every layer (renderer, the Tk surface, the Qt surface, the IPC
proxy, the click resolver) imports these names instead of restating them, and
``tests/unit/ui/jarvisbar/test_mode_parity.py`` fails the build if any file in
the package re-hardcodes them.

Adding a mode = add it here, teach ``renderer.visual_mode`` what it looks like,
and decide in ``interaction.resolve_click`` what a click on it does. Nothing
else needs touching.
"""
from __future__ import annotations

#: Voice-session modes, driven by the supervisor state machine.
VOICE_MODES: tuple[str, ...] = ("idle", "listen", "speak", "think")

#: Dictation modes. Dictation runs OUTSIDE the voice state machine (it raises no
#: ``SystemStateChanged``), so it gets its own modes rather than borrowing a
#: voice one — reusing ``listen``/``think`` would re-arm the close-X hang-up and
#: the mute zone on a bar with no session behind it, which is exactly the
#: silent-hangup trap ``resolve_click`` exists to prevent.
#:
#: - ``dictate``               — recording: the user is speaking, level is live.
#: - ``dictate_transcribing``  — recording stopped, the transcription is running.
DICTATION_MODES: tuple[str, ...] = ("dictate", "dictate_transcribing")

#: Attention modes: something the user ASKED for did not happen, and they are
#: owed a reason. The bar carries no text, so without a look of its own a
#: refusal reached only a log file the desktop app cannot display — pressing the
#: dictation shortcut with a live voice conversation did nothing at all, on
#: screen or anywhere else. This mode is that missing answer.
#:
#: - ``notice`` — a brief, self-clearing "that did not happen" look. It is NOT a
#:   dictation mode and must never be one: a dictation mode claims the
#:   microphone is recording, which on a REFUSED dictation is the opposite of
#:   the truth. Like the dictation modes it is inert to clicks, because a
#:   transient message is not a control.
NOTICE_MODES: tuple[str, ...] = ("notice",)

#: Every mode a surface accepts. Anything else is dropped by ``show()``.
MODES: tuple[str, ...] = VOICE_MODES + DICTATION_MODES + NOTICE_MODES
