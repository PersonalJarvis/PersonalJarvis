"""Process DPI-awareness declaration ladder — Per-Monitor Aware V2 first.

Window-centric Computer-Use requires the process to be declared
PER_MONITOR_AWARE_V2 on Windows: without it, window rects and monitor
metrics are DPI-virtualized per thread and the capture disagrees with the
input space on mixed-DPI desktops. The ladder must try, in order:

1. ``user32.SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`` (1703+),
2. ``shcore.SetProcessDpiAwareness(2)`` (8.1+, V1),
3. ``user32.SetProcessDPIAware()`` (system-aware last resort).

The ladder is tested with an injected fake ``windll`` so it runs on any OS.
"""
from __future__ import annotations

import ctypes
from types import SimpleNamespace

import pytest

from jarvis.core import win32_dpi


class _Fn:
    """Callable recording its calls; attribute-assignable like a ctypes fn."""

    def __init__(self, result=1, raises: type[Exception] | None = None):
        self.result = result
        self.raises = raises
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        if self.raises is not None:
            raise self.raises("unavailable")
        return self.result


class _NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _windll(*, v2=None, shcore=None, aware=None):
    user32 = _NS(
        SetProcessDpiAwarenessContext=v2 if v2 is not None else _Fn(),
        SetProcessDPIAware=aware if aware is not None else _Fn(),
    )
    if v2 is None:
        user32.SetProcessDpiAwarenessContext = _Fn()
    shc = _NS(SetProcessDpiAwareness=shcore if shcore is not None else _Fn(0))
    return _NS(user32=user32, shcore=shc)


def test_v2_context_is_declared_first():
    v2 = _Fn(result=1)
    shcore = _Fn(result=0)
    fake = _windll(v2=v2, shcore=shcore)
    assert win32_dpi._apply_process_awareness(fake) == "per_monitor_v2"
    assert len(v2.calls) == 1
    (arg,) = v2.calls[0]
    assert arg.value == ctypes.c_void_p(-4).value
    assert shcore.calls == []


def test_falls_back_to_shcore_per_monitor_v1():
    v2 = _Fn(result=0)  # refused (e.g. already set differently)
    shcore = _Fn(result=0)
    fake = _windll(v2=v2, shcore=shcore)
    assert win32_dpi._apply_process_awareness(fake) == "per_monitor"
    assert len(shcore.calls) == 1


def test_shcore_access_denied_reports_the_effective_awareness():
    fake = _windll(v2=_Fn(result=0), shcore=_Fn(result=-2147024891))
    fake.user32.GetThreadDpiAwarenessContext = _Fn(result=123)
    fake.user32.AreDpiAwarenessContextsEqual = _Fn(result=0)
    fake.user32.GetAwarenessFromDpiAwarenessContext = _Fn(result=2)
    assert win32_dpi._apply_process_awareness(fake) == "per_monitor"


def test_shcore_access_denied_does_not_invent_per_monitor_awareness():
    fake = _windll(v2=_Fn(result=0), shcore=_Fn(result=-2147024891))
    fake.user32.GetThreadDpiAwarenessContext = _Fn(result=123)
    fake.user32.AreDpiAwarenessContextsEqual = _Fn(result=0)
    fake.user32.GetAwarenessFromDpiAwarenessContext = _Fn(result=1)
    assert win32_dpi._apply_process_awareness(fake) == "system"


def test_shcore_access_denied_prefers_actual_process_awareness():
    fake = _windll(v2=_Fn(result=0), shcore=_Fn(result=-2147024891))

    def get_process_awareness(_process, awareness):
        awareness._obj.value = 1
        return 0

    fake.shcore.GetProcessDpiAwareness = get_process_awareness
    assert win32_dpi._apply_process_awareness(fake) == "system"


def test_missing_v2_symbol_falls_through_cleanly():
    fake = _windll(v2=_Fn(raises=AttributeError), shcore=_Fn(result=0))
    assert win32_dpi._apply_process_awareness(fake) == "per_monitor"


def test_last_resort_is_system_aware():
    aware = _Fn(result=1)
    fake = _windll(
        v2=_Fn(raises=AttributeError),
        shcore=_Fn(raises=OSError),
        aware=aware,
    )
    fake.user32.SetProcessDPIAware = aware
    assert win32_dpi._apply_process_awareness(fake) == "system"
    assert len(aware.calls) == 1


def test_total_failure_reports_none():
    fake = _windll(
        v2=_Fn(raises=AttributeError),
        shcore=_Fn(raises=OSError),
        aware=_Fn(raises=OSError),
    )
    fake.user32.SetProcessDPIAware = _Fn(raises=OSError)
    assert win32_dpi._apply_process_awareness(fake) == "none"


def test_ensure_dpi_awareness_is_idempotent_and_safe():
    # Behavioural contract only: repeated calls never raise on any host.
    win32_dpi.ensure_dpi_awareness()
    win32_dpi.ensure_dpi_awareness()


def test_per_monitor_context_restores_the_previous_thread_context(monkeypatch):
    calls: list[int | None] = []

    def set_context(context):
        calls.append(context.value)
        return 123

    monkeypatch.setattr(win32_dpi, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(win32_dpi, "_DPI_AWARENESS_SET", True)
    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=SimpleNamespace(SetThreadDpiAwarenessContext=set_context)),
        raising=False,
    )

    with win32_dpi.per_monitor_dpi_context():
        assert calls == [ctypes.c_void_p(-4).value]

    assert calls == [ctypes.c_void_p(-4).value, 123]

    calls.clear()
    with pytest.raises(RuntimeError), win32_dpi.per_monitor_dpi_context():
        raise RuntimeError("probe")
    assert calls == [ctypes.c_void_p(-4).value, 123]
