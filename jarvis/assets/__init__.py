"""Bundled binary assets shipped with the Jarvis package.

Currently:
- ``wakeword/``: the word-agnostic openWakeWord feature backbones
  (melspectrogram, embedding) that a user-trained custom wake model needs to
  load offline. NO named wake model ships (design 2026-07-07). Loaded via
  :func:`bundled_wakeword_models` from the openWakeWord provider.
- ``vad/``: the Silero VAD ONNX model (MIT-licensed, ~2.2 MB) that powers
  end-of-speech detection (:mod:`jarvis.audio.vad`). Bundled so the core voice
  loop closes a turn on a base install too: the ``silero-vad`` pip package (which
  drags torch) lives only in the ``[local-voice]`` extra, but the model file
  itself is torch-free and run via base ``onnxruntime``. Loaded via
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

    ``None`` when the file is missing (partial checkout / slim install); the
    caller (:meth:`jarvis.audio.vad.SileroEndpointer._ensure_model`) then falls
    back to locating the model inside the installed ``silero-vad`` pip package.

    Bundling this ~2.2 MB MIT model in-repo is what makes end-of-speech detection
    work out-of-the-box on a fresh base install — without it the voice loop can
    hear a wake word but never close the utterance, because ``silero-vad`` is an
    opt-in extra. The bundled file is byte-identical to the pip package's model,
    so the torch-free onnxruntime inference in ``vad.py`` is unchanged.
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
    path = _ICONS_DIR / _APP_ICON_FILE
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
    path = _ICONS_DIR / _APP_ICON_PNG_FILE
    return path if path.is_file() else None


__all__ = [
    "bundled_wakeword_models",
    "bundled_silero_vad_model",
    "bundled_app_icon",
    "bundled_app_icon_png",
]
