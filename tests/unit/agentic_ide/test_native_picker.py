"""The system folder window: offered where it works, silent where it does not.

Two things must hold on every machine, and the second one is the reason this
feature is allowed to exist at all:

* Where a desktop is present, the window opens and the chosen folder comes back
  intact — including folders whose names carry accents, which is where the
  first version broke.
* Where one is not (a headless VPS, an SSH session, a container), the feature
  reports itself unavailable in plain language and the REST browser stays the
  whole story. It never raises, never blocks, and never offers a button that
  cannot deliver.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from jarvis.agentic_ide import native_picker


class _Completed:
    """Stand-in for a finished helper process."""

    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _marked(path: str) -> str:
    return f"noise before\n{native_picker._BEGIN}\n{path}\n{native_picker._END}\ntrailing\n"


# --------------------------------------------------------------------------- #
# Availability                                                                #
# --------------------------------------------------------------------------- #
def test_a_headless_linux_box_says_so_in_plain_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    probe = native_picker.support()

    assert probe.available is False
    assert probe.backend is None
    assert probe.reason and "desktop session" in probe.reason


def test_a_linux_desktop_without_a_dialog_program_names_the_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(native_picker.shutil, "which", lambda _name: None)

    probe = native_picker.support()

    assert probe.available is False
    # A dead end is not an answer: the message says what to install.
    assert probe.reason and "zenity" in probe.reason


def test_a_linux_desktop_with_zenity_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        native_picker.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None
    )

    assert native_picker.support() == native_picker.PickerSupport(available=True, backend="zenity")


def test_choosing_on_a_headless_box_fails_without_running_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no helper process may be started without a display")

    monkeypatch.setattr(native_picker.subprocess, "run", _explode)

    result = native_picker.choose_folder()

    assert result.path is None
    assert result.error


# --------------------------------------------------------------------------- #
# The answer                                                                  #
# --------------------------------------------------------------------------- #
def test_the_chosen_folder_comes_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "zenity")
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        native_picker.subprocess, "run", lambda *_a, **_k: _Completed(stdout=str(tmp_path))
    )

    assert native_picker.choose_folder().path == str(tmp_path)


def test_a_folder_name_with_accents_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first version decoded the helper's output with the console codepage
    and took its own reader thread down on the first accented character. Most of
    the world names folders in something other than plain ASCII, so this is an
    ordinary folder, not an edge case.

    Written as escapes so the assertion is about ENCODING rather than about any
    one language, and so this file stays plain ASCII on disk.
    """
    folder = tmp_path / "\u00c5ngstr\u00f6m caf\u00e9 \u00f1o\u00f1o"
    folder.mkdir()
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "powershell")
    )
    monkeypatch.setattr(sys, "platform", "win32")

    seen: dict[str, object] = {}

    def _run(*_args: object, **kwargs: object) -> _Completed:
        seen.update(kwargs)
        return _Completed(stdout=_marked(str(folder)))

    monkeypatch.setattr(native_picker.subprocess, "run", _run)

    assert native_picker.choose_folder().path == str(folder)
    # The decoding is pinned, not inherited from whatever locale the app booted in.
    assert seen["encoding"] == "utf-8"


def test_the_helpers_own_chatter_is_not_mistaken_for_an_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "powershell")
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        native_picker.subprocess,
        "run",
        lambda *_a, **_k: _Completed(stdout=f"Gtk-WARNING: something\n{_marked(str(tmp_path))}"),
    )

    assert native_picker.choose_folder().path == str(tmp_path)


def test_cancelling_is_an_outcome_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "zenity")
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(native_picker.subprocess, "run", lambda *_a, **_k: _Completed(returncode=1))

    result = native_picker.choose_folder()

    assert result.cancelled is True
    assert result.error is None
    assert result.path is None


def test_a_window_left_open_is_closed_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dialog nobody answers must be recoverable, not merely bounded — which
    is the whole reason it runs as a separate process (AP-24)."""
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "zenity")
    )
    monkeypatch.setattr(sys, "platform", "linux")

    def _timeout(*_a: object, **_k: object) -> None:
        raise subprocess.TimeoutExpired(cmd="zenity", timeout=1)

    monkeypatch.setattr(native_picker.subprocess, "run", _timeout)

    result = native_picker.choose_folder(timeout=1)

    assert result.path is None
    assert result.error and "too long" in result.error


def test_a_folder_deleted_while_the_window_was_open_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "zenity")
    )
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        native_picker.subprocess,
        "run",
        lambda *_a, **_k: _Completed(stdout=str(tmp_path / "gone")),
    )

    result = native_picker.choose_folder()

    assert result.path is None
    assert result.error and "not a folder" in result.error


def test_a_missing_helper_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "zenity")
    )
    monkeypatch.setattr(sys, "platform", "linux")

    def _missing(*_a: object, **_k: object) -> None:
        raise OSError("zenity vanished")

    monkeypatch.setattr(native_picker.subprocess, "run", _missing)

    assert native_picker.choose_folder().error


# --------------------------------------------------------------------------- #
# The command                                                                 #
# --------------------------------------------------------------------------- #
def test_the_start_folder_is_dropped_when_it_no_longer_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale start folder must not stop the window from opening."""
    monkeypatch.setattr(
        native_picker, "support", lambda: native_picker.PickerSupport(True, "kdialog")
    )
    monkeypatch.setattr(sys, "platform", "linux")
    seen: list[list[str]] = []

    def _run(argv: list[str], **_k: object) -> _Completed:
        seen.append(argv)
        return _Completed(stdout=str(tmp_path))

    monkeypatch.setattr(native_picker.subprocess, "run", _run)
    native_picker.choose_folder(start=str(tmp_path / "deleted-yesterday"))

    assert seen and str(tmp_path / "deleted-yesterday") not in seen[0]


def test_the_windows_command_runs_in_a_single_threaded_apartment() -> None:
    """`-STA` is not optional: the shell dialogs refuse to run without it."""
    if sys.platform != "win32":
        pytest.skip("the Windows command is only built on Windows")
    argv, _env = native_picker._command(None, "powershell")
    assert "-STA" in argv
    assert "-NoProfile" in argv
