"""Keep the details of a Windows Forms error after its dialog is dismissed.

WinForms catches UI callback failures inside its managed message loop. Neither
``sys.excepthook`` nor ``threading.excepthook`` sees them. Subscribe on the GUI
thread (pywebview's synchronous ``before_show`` event), not the Python main
thread. Keep the normal Continue/Quit dialog; logging is not crash recovery.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

log = logging.getLogger(__name__)
_handlers: dict[int, tuple[Any, Any]] = {}


def _report_exception(exception: Any) -> None:
    from jarvis.core.redact import safe_preview

    # ToString includes managed inner exceptions and stack traces. Python's
    # traceback formatter cannot reconstruct those from a pythonnet proxy.
    detail = safe_preview(str(exception.ToString()), max_chars=32_768)
    log.critical("Unhandled Windows Forms UI exception:\n%s", detail)


def _present_exception(forms: Any, exception: Any) -> None:
    dialog = forms.ThreadExceptionDialog(exception)
    try:
        result = dialog.ShowDialog()
    finally:
        dialog.Dispose()
    if result == forms.DialogResult.Abort:
        # Run normal window-closing hooks, including Jarvis's bounded shutdown.
        forms.Application.Exit()


def _subscribe(forms: Any, thread_id: int) -> None:
    """Install once per managed UI thread; release delegates on ThreadExit."""
    if thread_id in _handlers:
        return

    def on_exception(_sender: Any, args: Any) -> None:
        try:
            _report_exception(args.Exception)
        except Exception:  # noqa: BLE001 — reporting must not hide the original dialog
            log.exception("Could not record the Windows Forms exception")
        _present_exception(forms, args.Exception)

    def on_exit(_sender: Any, _args: Any) -> None:
        # ThreadExit is process-wide; another GUI thread must keep its handler.
        from System.Threading import Thread  # type: ignore[import-not-found]

        if int(Thread.CurrentThread.ManagedThreadId) != thread_id:
            return
        forms.Application.ThreadException -= on_exception
        forms.Application.ThreadExit -= on_exit
        _handlers.pop(thread_id, None)

    forms.Application.ThreadException += on_exception
    try:
        forms.Application.ThreadExit += on_exit
    except Exception:
        forms.Application.ThreadException -= on_exception
        raise
    # Hold the Python callables for the full managed delegate lifetime.
    _handlers[thread_id] = (on_exception, on_exit)
    log.info("Windows Forms UI error logging installed")


def _install_on_gui_thread() -> None:
    try:
        # The real window already loaded WinForms; importing here adds no CLR
        # dependency to Linux, macOS, the API server, or Python module import.
        import System.Windows.Forms as forms  # type: ignore[import-not-found]
        from System.Threading import Thread  # type: ignore[import-not-found]

        _subscribe(forms, int(Thread.CurrentThread.ManagedThreadId))
    except Exception:  # noqa: BLE001 — retain the framework's default error handling
        log.exception("Windows Forms error logging could not be installed")


def register_winforms_error_logging(window: Any) -> None:
    """Arm logging before the first shown window, without loading .NET early."""
    if sys.platform != "win32":
        return
    before_show = getattr(window.events, "before_show", None)
    if before_show is None:
        log.warning("Windows Forms error logging needs a synchronous before_show event")
        return
    before_show += _install_on_gui_thread
