"""Preferences and translated labels for the native pet context menu."""

from __future__ import annotations

import logging
import sys
import tomllib
from pathlib import Path

LABELS = {
    "en": ("Open app", "Reset position", "Hide pet"),
    "de": ("App öffnen", "Position zurücksetzen", "Ausblenden"),  # i18n-allow
    "es": ("Abrir la aplicación", "Restablecer posición", "Ocultar mascota"),
    "zh": ("打开应用", "重置位置", "隐藏宠物"),
}


def preferences(path: Path | None = None) -> tuple[tuple[str, str, str], str]:
    """Read on opening, so language and shortcut edits apply without a restart."""
    from jarvis.core.config import DEFAULT_CONFIG_FILE, TriggerConfig

    language = "en"
    shortcut = TriggerConfig.model_fields["hotkey_pet_toggle"].default
    try:
        source = DEFAULT_CONFIG_FILE if path is None else path
        data = tomllib.loads(source.read_text(encoding="utf-8-sig"))
        language = str(data.get("ui", {}).get("language", "en")).lower()
        shortcut = data.get("trigger", {}).get("hotkey_pet_toggle", shortcut)
    except FileNotFoundError:
        # Fresh installs and isolated native previews use the product defaults.
        pass
    except (OSError, ValueError, AttributeError, TypeError):
        logging.getLogger(__name__).debug("Pet menu preferences unavailable", exc_info=True)
    names = {"alt": "Alt", "ctrl": "Ctrl", "shift": "Shift", "win": "Win"}
    if sys.platform == "darwin":
        names.update(alt="Option", win="Command", ctrl="Control")
    combo = "+".join(names.get(key.strip().lower(), key.strip().upper())
                     for key in str(shortcut or "").split("+") if key.strip())
    return LABELS.get(language, LABELS["en"]), combo
