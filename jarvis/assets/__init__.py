"""Bundled binary assets shipped with the Jarvis package.

Currently:
- ``wakeword/``: the word-agnostic openWakeWord feature backbones
  (melspectrogram, embedding) that a user-trained custom wake model needs to
  load offline. NO named wake model ships (design 2026-07-07). Loaded via
  :func:`bundled_wakeword_models` from the openWakeWord provider.
- ``vad/``: the Silero VAD ONNX model (MIT-licensed, ~2.2 MB) that powers
  end-of-speech detection (:mod:`jarvis.audio.vad`). Bundled so the core voice
  loop closes a turn on every install profile without the redundant
  ``silero-vad`` pip package or torch. The model is run through base CPU
  ``onnxruntime``. Loaded via
  :func:`bundled_silero_vad_model`.
- ``icons/``: the desktop/taskbar icon (the Gigi ghost mascot), in two formats:
  ``jarvis.ico`` for every Win32 icon surface (window class icon, AUMID icon,
  Start-Menu + autostart shortcut, taskbar name) and ``jarvis.png`` for the
  Linux XDG ``.desktop`` ``Icon=`` key (most Linux desktops cannot render
  ``.ico``). Bundled so both can be found regardless of how the package was
  installed. Loaded via :func:`bundled_app_icon` / :func:`bundled_app_icon_png`.
  The ``.ico`` is byte-identical to the build-tool copy at
  ``<repo-root>/assets/icons/jarvis.ico`` (kept in sync by
  ``tests/unit/ui/test_icon_identity.py``).

Future bundles (e.g. packaged voice clips) live under this package.
"""
from __future__ import annotations

from pathlib import Path

_WAKEWORD_DIR = Path(__file__).resolve().parent / "wakeword"
_WAKEWORD_FILES = {
    "melspec": "melspectrogram.onnx",
    "embedding": "embedding_model.onnx",
}

_VAD_DIR = Path(__file__).resolve().parent / "vad"
_SILERO_VAD_FILE = "silero_vad.onnx"

_ICONS_DIR = Path(__file__).resolve().parent / "icons"
_APP_ICON_FILE = "jarvis.ico"
_APP_ICON_PNG_FILE = "jarvis.png"


def bundled_wakeword_models() -> dict[str, Path] | None:
    """Return absolute paths to the bundled openWakeWord backbone assets.

    Returns ``None`` when either required file is missing (partial checkout).
    The caller (``openwakeword_provider``) then hands a bare custom-model path
    to openWakeWord, which resolves backbones from its own package resources.
    No named wake model ships (design 2026-07-07).

    Keys: ``melspec`` (preprocessing), ``embedding`` (shared backbone) — both
    word-agnostic; they carry no wake vocabulary of their own.
    """
    if not _WAKEWORD_DIR.is_dir():
        return None
    resolved: dict[str, Path] = {}
    for key, filename in _WAKEWORD_FILES.items():
        path = _WAKEWORD_DIR / filename
        if not path.is_file():
            return None
        resolved[key] = path
    return resolved


def bundled_silero_vad_model() -> Path | None:
    """Return the absolute path to the bundled Silero VAD ONNX model, or ``None``.

    ``None`` means the required package asset is missing, for example in an
    incomplete source checkout. Normal wheels and frozen builds include it.

    Bundling this ~2.2 MB MIT model in-repo is what makes end-of-speech detection
    work out-of-the-box on a fresh base install — without it the voice loop can
    hear a wake word but never close the utterance. The model remains torch-free
    and is executed through the CPU ONNX Runtime path in ``vad.py``.
    """
    path = _VAD_DIR / _SILERO_VAD_FILE
    return path if path.is_file() else None


def bundled_app_icon() -> Path | None:
    """Return the absolute path to the bundled ``jarvis.ico``, or ``None``.

    ``None`` only when the file is missing (partial checkout). Shipping the icon
    *inside* the package — rather than at ``<repo-root>/assets/icons/`` where the
    build-tool copy lives — is what makes the Windows taskbar/titlebar icon work
    on a fresh install no matter how it was installed. The legacy repo-root path
    resolves only for a run *from the project folder* (``parents[2]`` == repo
    root); a real ``pip install`` puts the package under ``site-packages`` where
    that repo-root ``assets/`` does not exist, so every Win32 icon surface (class
    icon, AUMID icon, Start-Menu shortcut, taskbar name) silently fell back to
    the ``pythonw.exe`` Python logo. The in-package copy always ships with the
    code (``package-data`` glob ``assets/**/*``). Same fix class as the bundled
    Silero VAD model above.
    """
    return _instance_icon(_APP_ICON_FILE)


def _instance_icon(default_name: str) -> Path | None:
    """The icon file for the running *instance* (``jarvis.core.instance``).

    The dev app carries a DEV-badged copy (``jarvis-dev.ico`` / ``.png``,
    rendered by ``scripts/make_dev_icon.py``) so its taskbar, dock and tray
    entries are told apart from the default app's at a glance. A missing badged
    file falls back to the default icon rather than to no icon at all.
    """
    from jarvis.core.instance import current_instance

    identity = current_instance()
    if not identity.is_default:
        badged = _ICONS_DIR / Path(identity.icon_file_name).with_suffix(
            Path(default_name).suffix
        )
        if badged.is_file():
            return badged
    path = _ICONS_DIR / default_name
    return path if path.is_file() else None


def bundled_app_icon_png() -> Path | None:
    """Return the absolute path to the bundled ``jarvis.png``, or ``None``.

    The Linux counterpart to :func:`bundled_app_icon`. Linux desktops read the
    autostart/menu entry's icon from the ``.desktop`` ``Icon=`` key, and most of
    them (and the XDG icon cache) cannot decode a Windows ``.ico`` — they need a
    PNG (or SVG). Without a bundled PNG the ``.desktop`` entry, and therefore the
    taskbar/dock button of the running window, falls back to the generic
    interpreter icon (``python3``) — the Linux face of the same "shows Python,
    not Jarvis" report. Resolved fresh from the installed package so the absolute
    path baked into the ``.desktop`` is correct on any install layout.
    """
    return _instance_icon(_APP_ICON_PNG_FILE)


_MUSIC_PLAYER_ICON_FILE = "music-player.ico"


def bundled_music_player_icon() -> Path | None:
    """Return the absolute path to the background music player's window icon.

    The player is its own ``pythonw`` process (``jarvis.platform.music_player_host``),
    so without an icon of its own its title bar and taskbar button show the
    Python logo (maintainer, 2026-08-22: "a window with the Python logo — make
    it proper"). The icon is the YouTube Music mark with the Jarvis mascot as a
    badge, so the window reads as "YouTube Music, driven by Jarvis" at a
    glance. ``None`` only when the file is missing (partial checkout).
    """
    path = _ICONS_DIR / _MUSIC_PLAYER_ICON_FILE
    return path if path.is_file() else None


__all__ = [
    "bundled_wakeword_models",
    "bundled_silero_vad_model",
    "bundled_app_icon",
    "bundled_app_icon_png",
    "bundled_music_player_icon",
]
