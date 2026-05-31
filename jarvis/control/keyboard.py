"""Keyboard wrappers with overlay trigger hook. Plan §8.2.

Choke point per Plan §8.3: all PC-action plugins should route through
``jarvis.control.keyboard`` instead of calling ``pyautogui`` directly.

``pyautogui`` is imported LAZILY inside function bodies so that
``jarvis.control`` remains importable without pyautogui installed
(headless tests).
"""

from __future__ import annotations

from jarvis.overlay import ActionKind, overlay_action_sync


@overlay_action_sync(ActionKind.TYPING)
def typewrite(text: str, *, interval: float = 0.0) -> None:
    """Wrapper around ``pyautogui.typewrite``.

    Counts as an interactive PC action — the typing indicator (bottom-edge
    sweep) should react to each key press.
    """
    import pyautogui  # lazy

    pyautogui.typewrite(text, interval=interval)


@overlay_action_sync(ActionKind.HOTKEY)
def hotkey(*keys: str) -> None:
    """Wrapper around ``pyautogui.hotkey``.

    Counts as an interactive PC action — a hotkey is a single event;
    Plan §8.2 maps it to TYPING state (without a sweep burst).
    """
    import pyautogui  # lazy

    pyautogui.hotkey(*keys)


@overlay_action_sync(ActionKind.TYPING)
def press(key: str, *, presses: int = 1) -> None:
    """Wrapper around ``pyautogui.press``.

    Counts as an interactive PC action — a single key press is treated like
    typewrite.
    """
    import pyautogui  # lazy

    pyautogui.press(key, presses=presses)
