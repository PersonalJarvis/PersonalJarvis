"""The desktop pet's vocabulary — ONE definition, imported everywhere.

A pet state is what the floating pet *does* on screen (its animation row). It
is derived from real Jarvis events by ``jarvis.ui.pets.state_machine`` and it
crosses Python, the sprite manifest on disk, the REST payload, TypeScript and
the i18n labels — the five-layer enum this repo has been bitten by four times
(AP-4 / BUG-008). Same remedy as ``jarvis.ui.jarvisbar.modes``: the tuples live
in a module with NO imports at all, every layer imports these names instead of
restating them, and ``tests/unit/ui/pets/test_pet_state_parity.py`` fails the
build when a layer drifts (TS mirror ``frontend/src/lib/petStates.ts``, the
``pets.states.*`` locale labels, the built-in manifests).

The ORDER of ``PET_STATES`` is also the row order of a sprite sheet: row 0 is
``idle``, row 1 ``listening`` and so on. A manifest may name another row per
state, but the template sheet and every built-in pet follow this order.

Adding a state = add it here (at the END, so existing sheets keep their rows),
give it a fallback, teach the state machine when it applies, and add the label
to every locale. Nothing else needs touching.
"""

from __future__ import annotations

#: Every animation state a pet can show, in sprite-sheet row order.
PET_STATES: tuple[str, ...] = (
    "idle",
    "listening",
    "thinking",
    "talking",
    "success",
    "error",
    "sleeping",
    "working",
    "searching",
    "held",
)

#: States that show what Jarvis is DOING inside a turn, set from tool calls:
#: ``working`` for a tool step or a running agent task, ``searching`` for a
#: lookup (web search, reading a page or a file, the wiki, memory).
ACTION_STATES: tuple[str, ...] = ("working", "searching")

#: How long a tool call keeps its action state without a newer one, in
#: seconds. A tool's result normally ends it sooner; this only bounds a call
#: whose result never arrives.
ACTION_HOLD_SECONDS: float = 12.0

#: States that play once and then hand back to the underlying state.
ONE_SHOT_STATES: tuple[str, ...] = ("success", "error")

#: How long a one-shot state stays on screen, in seconds.
ONE_SHOT_SECONDS: dict[str, float] = {"success": 1.5, "error": 2.0}

#: Idle time without any Jarvis event before the pet falls asleep, in seconds.
SLEEP_AFTER_SECONDS: float = 300.0

#: Which row to borrow when a manifest has no animation for a state. Every
#: chain ends at ``idle``, the one state a manifest must provide.
STATE_FALLBACKS: dict[str, str] = {
    "listening": "idle",
    "thinking": "listening",
    "talking": "listening",
    "success": "idle",
    "error": "idle",
    "sleeping": "idle",
    "working": "thinking",
    "searching": "working",
    "held": "listening",
}

#: States a pet may also draw "on the phone" (``phone`` rows in ``pet.json``):
#: the three a voice conversation runs through. While a call is live the
#: renderer shows these rows instead of the plain ones; a pet without them
#: simply keeps its plain rows.
CALL_STATES: tuple[str, ...] = ("listening", "thinking", "talking")

#: The pet id that shows the control strip with no figure ("None" in the UI).
NO_PET_ID: str = "none"

#: The pet a fresh install shows.
DEFAULT_PET_ID: str = "gigi"

#: Manifest format tag written into every ``pet.json``.
PET_FORMAT: str = "jarvis-pet/1"

#: Frame edge lengths a sprite sheet may use, in source pixels.
FRAME_SIZES: tuple[int, ...] = (32, 48, 64)

#: Upper bound of frames in one animation row.
MAX_FRAMES_PER_STATE: int = 8
