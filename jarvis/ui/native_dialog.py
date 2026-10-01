"""Native modal dialogs for the moments the app has no window of its own.

A GUI launch is mute on all three desktop platforms: Windows ``pythonw`` has no
standard streams at all, a macOS ``.app`` and a Linux ``.desktop`` with
``Terminal=false`` bury stderr in Console.app and the journal. When the launcher
or the relauncher must tell the user something BEFORE a window exists — the app
refused to start, an earlier instance is stuck, a restart never came back — the
only surface left is the operating system's own message box.

Windows uses Win32 directly; macOS uses ``osascript``, which every install has;
Linux tries the two desktop dialog helpers in turn and honestly gives up if the
distro ships neither (stderr and the log still carry the reason). A headless
host never shows anything: ``ask_yes_no`` answers ``False`` there, which every
caller treats as "do nothing".

Every function is best-effort and never raises — a dialog that cannot be shown
must not turn a startup problem into a crash.
"""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Callable


def _run_helper(
    cmd: list[str], *, dismiss: threading.Event | None = None
) -> tuple[bool, int]:
    """Run a dialog helper; ``(ran, returncode)``. ``ran=False`` → not installed.

    With ``dismiss``, setting that event closes the helper's dialog and the
    call returns a non-zero code, which every caller reads as "no".
    """
    import subprocess

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    if dismiss is None:
        try:
            completed = subprocess.run(  # noqa: S603 — fixed argv, no shell
                cmd,
                check=False,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (OSError, ValueError):  # no dialog tool here: the caller falls back
            return False, -1
        return True, int(getattr(completed, "returncode", 0) or 0)
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd, creationflags=NO_WINDOW_CREATIONFLAGS
        )
    except (OSError, ValueError):  # no dialog tool here: the caller falls back
        return False, -1
    while proc.poll() is None:
        if dismiss.wait(0.2):
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:  # ignored terminate: kill is the answer
                proc.kill()
            return True, -1
    return True, int(proc.returncode or 0)


def _dismiss_message_box_when_set(
    thread_id: int, dismiss: threading.Event, done: threading.Event
) -> None:
    """Answer "No" on this thread's message box once ``dismiss`` is set.

    ``MessageBoxW`` blocks the thread that shows it, so the close has to come
    from outside: the box is a ``#32770`` dialog owned by that thread, and a
    posted ``WM_COMMAND(IDNO)`` ends it exactly like the button would. The
    post repeats until the box is gone, which also covers a dismissal that
    arrives before the box has been created.
    """
    import ctypes
    from ctypes import wintypes

    # A private WinDLL instance: argtypes on the shared ctypes.windll.user32
    # would leak into every other caller in the process.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumThreadWindows.argtypes = [wintypes.DWORD, enum_proc, wintypes.LPARAM]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    wm_command, idno = 0x0111, 7

    def _answer_no(hwnd, _lparam):
        name = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(hwnd, name, 32)
        if name.value == "#32770":
            user32.PostMessageW(hwnd, wm_command, idno, 0)
        return True

    callback = enum_proc(_answer_no)
    while not done.is_set():
        if dismiss.wait(0.2):
            user32.EnumThreadWindows(thread_id, callback, 0)
            done.wait(0.2)


def _applescript_literal(text: str) -> str:
    """Escape for an AppleScript string literal: backslash first, then the quote."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _linux_has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def show_error_dialog(title: str, message: str) -> None:
    """Show a modal error box; silently does nothing where none can appear."""
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            import ctypes

            mb_iconerror = 0x10
            ctypes.windll.user32.MessageBoxW(None, message, title, mb_iconerror)
        return
    if sys.platform == "darwin":
        _run_helper(
            [
                "osascript",
                "-e",
                f'display dialog "{_applescript_literal(message)}" with title '
                f'"{_applescript_literal(title)}" buttons {{"OK"}} default button "OK" '
                "with icon stop",
            ]
        )
        return
    if not _linux_has_display():
        return  # no graphical session — a dialog has nowhere to appear
    for cmd in (
        ["zenity", "--error", f"--title={title}", f"--text={message}"],
        ["kdialog", "--title", title, "--error", message],
    ):
        ran, _ = _run_helper(cmd)
        if ran:
            return


def ask_yes_no(
    title: str,
    message: str,
    *,
    dismiss: threading.Event | None = None,
    _run: Callable[..., tuple[bool, int]] = _run_helper,
) -> bool:
    """Modal Yes/No question; ``True`` only on an explicit Yes.

    Anything else — No, the box closed, no dialog helper, no display, any
    error — is ``False``, so callers can only ever take the destructive branch
    on a real click. ``_run`` is injectable for tests (the helper is otherwise
    a real process that blocks on a real click).

    ``dismiss`` lets the caller withdraw the question: once that event is set
    the box closes and the answer is ``False``. The caller tells a withdrawal
    from a "No" by looking at its own event.
    """
    if dismiss is not None and dismiss.is_set():
        return False
    if sys.platform == "win32":
        done = threading.Event()
        try:
            import ctypes

            if dismiss is not None:
                threading.Thread(
                    target=_dismiss_message_box_when_set,
                    args=(int(ctypes.windll.kernel32.GetCurrentThreadId()), dismiss, done),
                    name="jarvis-dialog-dismiss",
                    daemon=True,
                ).start()
            mb_yesno, mb_iconquestion, mb_defbutton2, idyes = 0x4, 0x20, 0x100, 6
            result = ctypes.windll.user32.MessageBoxW(
                None, message, title, mb_yesno | mb_iconquestion | mb_defbutton2
            )
            return int(result) == idyes and not (dismiss is not None and dismiss.is_set())
        except Exception:  # noqa: BLE001 — no box → no consent
            return False
        finally:
            done.set()

    def _ask(cmd: list[str]) -> tuple[bool, int]:
        return _run(cmd) if dismiss is None else _run(cmd, dismiss=dismiss)

    if sys.platform == "darwin":
        ran, code = _ask(
            [
                "osascript",
                "-e",
                f'display dialog "{_applescript_literal(message)}" with title '
                f'"{_applescript_literal(title)}" buttons {{"No", "Yes"}} '
                'default button "No" with icon caution',
                "-e",
                'if button returned of result is "Yes" then return 0',
                "-e",
                "error number 1",
            ]
        )
        # osascript exits 0 only when the script returned normally (Yes);
        # "No" raises error 1 and a cancelled dialog raises -128.
        return ran and code == 0
    if not _linux_has_display():
        return False
    for cmd in (
        ["zenity", "--question", f"--title={title}", f"--text={message}", "--default-cancel"],
        ["kdialog", "--title", title, "--warningyesno", message],
    ):
        ran, code = _ask(cmd)
        if ran:
            return code == 0  # both helpers exit 0 for Yes, 1 for No
    return False
