"""Unit-Tests fuer Phase A4 ``resolve_context`` (jarvis/awareness/context.py).

Spec: JARVIS_AWARENESS_PLAN.md §8 Heuristik-Reihenfolge.

Testet:
- IDE_SET (code.exe / cursor.exe / windsurf.exe) → cwd via psutil.
- Browser (chrome.exe / msedge.exe) → Hostname aus Window-Title.
- Terminal (WindowsTerminal.exe / pwsh.exe) → cwd via psutil.
- Fallback unbekannter Process → process_name.
- task_label = erste 5 Worte des Title.
- Lazy-Import von psutil + Fail-Silent bei psutil-Failures.
"""
from __future__ import annotations

from unittest.mock import patch

from jarvis.awareness.context import (
    BROWSER_SET,
    IDE_SET,
    TERMINAL_SET,
    Context,
    _hostname_from_title,
    _short_label,
    resolve_context,
)
from jarvis.awareness.state import FrameSnapshot


def _frame(
    *, title: str = "main.py", process: str = "code.exe", pid: int = 1234,
) -> FrameSnapshot:
    return FrameSnapshot(
        timestamp_ns=1_000_000_000,
        active_window_title=title,
        active_process_name=process,
        active_pid=pid,
        is_capture_allowed=True,
    )


# ---- Heuristik-Tabellen ----------------------------------------------------


def test_ide_set_contains_known_editors() -> None:
    assert "code.exe" in IDE_SET
    assert "cursor.exe" in IDE_SET
    assert "windsurf.exe" in IDE_SET


def test_browser_set_contains_known_browsers() -> None:
    assert "chrome.exe" in BROWSER_SET
    assert "firefox.exe" in BROWSER_SET
    assert "msedge.exe" in BROWSER_SET


def test_terminal_set_contains_known_shells() -> None:
    assert "WindowsTerminal.exe" in TERMINAL_SET
    assert "pwsh.exe" in TERMINAL_SET
    assert "cmd.exe" in TERMINAL_SET


# ---- IDE pathway -----------------------------------------------------------


def test_ide_resolves_cwd_via_psutil() -> None:
    """code.exe → cwd via psutil.Process.cwd()."""
    with patch("jarvis.awareness.context._safe_cwd", return_value="C:\\repo\\jarvis"):
        ctx = resolve_context(_frame(process="code.exe", title="main.py - VS Code"))
    assert isinstance(ctx, Context)
    assert ctx.project_root == "C:\\repo\\jarvis"
    assert ctx.process_name == "code.exe"


def test_ide_falls_back_to_process_name_when_cwd_fails() -> None:
    """psutil-Failure → fallback auf process_name."""
    with patch("jarvis.awareness.context._safe_cwd", return_value=None):
        ctx = resolve_context(_frame(process="code.exe"))
    assert ctx.project_root == "code.exe"


# ---- Browser pathway -------------------------------------------------------


def test_browser_resolves_url_to_hostname() -> None:
    ctx = resolve_context(_frame(
        process="chrome.exe",
        title="https://github.com/anthropic/claude — Google Chrome",
    ))
    assert ctx.project_root == "github.com"


def test_browser_resolves_domain_in_title() -> None:
    ctx = resolve_context(_frame(
        process="firefox.exe",
        title="GitHub - example.com - Mozilla Firefox",
    ))
    assert ctx.project_root == "example.com"


def test_browser_falls_back_when_no_url_in_title() -> None:
    """Title ohne Domain → fallback auf process_name."""
    ctx = resolve_context(_frame(
        process="chrome.exe",
        title="Stack Overflow - Google Chrome",
    ))
    assert ctx.project_root == "chrome.exe"


# ---- Terminal pathway ------------------------------------------------------


def test_terminal_resolves_cwd_via_psutil() -> None:
    with patch("jarvis.awareness.context._safe_cwd", return_value="C:\\workspace"):
        ctx = resolve_context(_frame(
            process="pwsh.exe",
            title="Windows PowerShell",
        ))
    assert ctx.project_root == "C:\\workspace"


def test_terminal_falls_back_when_cwd_fails() -> None:
    with patch("jarvis.awareness.context._safe_cwd", return_value=None):
        ctx = resolve_context(_frame(process="WindowsTerminal.exe"))
    assert ctx.project_root == "WindowsTerminal.exe"


# ---- Fallback pathway ------------------------------------------------------


def test_unknown_process_falls_back_to_process_name() -> None:
    """Unbekannter Process: process_name ist Fallback-Identity-Key."""
    ctx = resolve_context(_frame(
        process="random_app.exe",
        title="Random App Window",
    ))
    assert ctx.project_root == "random_app.exe"


def test_empty_process_name_uses_unknown_sentinel() -> None:
    ctx = resolve_context(_frame(process="", title="Some Window"))
    assert ctx.project_root == "unknown"


# ---- task_label -----------------------------------------------------------


def test_task_label_truncated_to_five_words() -> None:
    """Plan §8: erste 5 Worte des Window-Titles."""
    ctx = resolve_context(_frame(
        process="random_app.exe",
        title="word1 word2 word3 word4 word5 word6 word7",
    ))
    assert ctx.task_label == "word1 word2 word3 word4 word5"


def test_task_label_short_title_kept_as_is() -> None:
    ctx = resolve_context(_frame(
        process="random_app.exe",
        title="short title",
    ))
    assert ctx.task_label == "short title"


def test_short_label_helper_max_words_param() -> None:
    assert _short_label("a b c d e f", max_words=3) == "a b c"
    assert _short_label("", max_words=5) == ""
    assert _short_label("only-one") == "only-one"


# ---- Hostname parser unit-tests ------------------------------------------


def test_hostname_from_title_extracts_https_url() -> None:
    assert _hostname_from_title("https://example.com/foo") == "example.com"


def test_hostname_from_title_extracts_domain() -> None:
    assert _hostname_from_title("Some title with example.com - Brave") == "example.com"


def test_hostname_from_title_strips_browser_suffix() -> None:
    """Browser-Suffix darf nicht als Domain interpretiert werden."""
    assert _hostname_from_title("Login - Mozilla Firefox") is None


def test_hostname_from_title_returns_none_for_no_domain() -> None:
    assert _hostname_from_title("Just plain text") is None
    assert _hostname_from_title("") is None


# ---- psutil Lazy-Import & Fail-Silent -------------------------------------


def test_safe_cwd_returns_none_on_invalid_pid() -> None:
    """pid <= 0 → None ohne psutil-Aufruf."""
    from jarvis.awareness.context import _safe_cwd

    assert _safe_cwd(0) is None
    assert _safe_cwd(-1) is None


def test_safe_cwd_returns_none_on_psutil_exception() -> None:
    """psutil.Process raises (NoSuchProcess, AccessDenied, OSError, ...)
    → fallback to None ohne Crash.
    """
    from jarvis.awareness.context import _safe_cwd

    # Patch psutil-Modul Lazy-Import via sys.modules. Wenn psutil nicht
    # installiert ist, schluckt der except auch ImportError.
    with patch("jarvis.awareness.context._safe_cwd", wraps=_safe_cwd):
        # Direct call — _safe_cwd has its own try/except; we simulate
        # via patched psutil module raising an exception.
        try:
            import psutil  # noqa: PLC0415

            with patch.object(psutil, "Process") as mock_proc:
                mock_proc.side_effect = RuntimeError("nope")
                assert _safe_cwd(99999) is None
        except ImportError:
            # psutil nicht installiert → _safe_cwd liefert None via except ImportError
            assert _safe_cwd(99999) is None


# ---- Integration: A→B→A Werkzeug-Roundtrip -------------------------------


def test_a_then_b_then_a_yields_distinct_then_resumed_contexts() -> None:
    """Plan §8 Acceptance: Wechsel A→B→A liefert A's Episode-Snippet,
    nicht B's. Hier nur Resolver-Side: A und B kriegen distinkte
    project_roots, A re-resolved gibt das gleiche A wieder.
    """
    with patch("jarvis.awareness.context._safe_cwd",
               side_effect=["C:\\a", "C:\\b", "C:\\a"]):
        a_first = resolve_context(_frame(process="code.exe", title="a.py"))
        b = resolve_context(_frame(process="code.exe", title="b.py"))
        a_again = resolve_context(_frame(process="code.exe", title="a.py - again"))

    assert a_first.project_root == "C:\\a"
    assert b.project_root == "C:\\b"
    assert a_again.project_root == "C:\\a"
    # task_label aus dem zweiten A ist anders → Resolver bleibt deterministisch
    # gegen Title-Aenderung.
    assert a_again.task_label == "a.py - again"
