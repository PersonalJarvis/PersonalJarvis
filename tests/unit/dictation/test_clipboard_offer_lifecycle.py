"""Clipboard offers must not outlive a timeout or leak their owning thread."""
from __future__ import annotations

import threading

from jarvis.platform import clipboard_offer as mod


class EventOffer(mod.ClipboardOffer):
    def __init__(self, *, delayed=False):
        super().__init__("test")
        self.delayed = delayed
        self.runs = 0
        self.offered = False

    def _pump(self):
        self.runs += 1
        if self.delayed:
            self._stop_requested.wait(0.2)
        if self._stop_requested.is_set():
            return
        self.offered = True
        self._ok = True
        self._ready.set()
        self._stop_requested.wait(2)


def test_start_and_stop_are_idempotent(monkeypatch):
    monkeypatch.setattr(mod, "available", lambda: True)
    offer = EventOffer()
    assert offer.start()
    thread = offer._thread
    assert offer.start()
    assert offer._thread is thread
    assert offer.runs == 1
    offer.stop()
    offer.stop()
    assert not thread.is_alive()
    assert offer._thread is None
    assert not offer.start()


def test_start_timeout_cancels_before_delayed_worker_can_take_clipboard(monkeypatch):
    monkeypatch.setattr(mod, "available", lambda: True)
    offer = EventOffer(delayed=True)
    assert not offer.start(timeout_s=0.001)
    assert offer._stop_requested.is_set()
    assert offer._done.wait(1)
    assert not offer.offered
    assert offer._thread is None


def test_stop_timeout_keeps_handle_for_later_join(monkeypatch):
    monkeypatch.setattr(mod, "available", lambda: True)
    release = threading.Event()
    offer = EventOffer()

    def pump():
        offer._ok = True
        offer._ready.set()
        release.wait(1)

    offer._pump = pump
    try:
        assert offer.start()
        thread = offer._thread
        offer.stop(timeout_s=0)
        assert offer._thread is thread
    finally:
        release.set()
        offer.stop()
    assert not thread.is_alive()
    assert offer._thread is None


def test_read_wait_wakes_when_offer_loses_ownership():
    offer = EventOffer()
    offer.lost_ownership = True
    assert offer.wait_for_read(exclude_pids=set(), after_s=0, timeout_s=10) is None


class OwnedClipboard:
    def __init__(self, owner):
        self.owner = owner
        self.locked = False
        self.emptied = False

    def OpenClipboard(self, hwnd):
        self.locked = True
        return True

    def GetClipboardOwner(self):
        assert self.locked
        return self.owner

    def EmptyClipboard(self):
        assert self.locked
        self.emptied = True
        return True

    def CloseClipboard(self):
        self.locked = False


def test_restore_cannot_overwrite_a_new_owner_even_with_same_text():
    clipboard = OwnedClipboard(owner=456)
    offer = EventOffer()
    offer._restore_text = "previous"
    writes = []
    offer._restore_owned_text(clipboard, 123, lambda text=None: writes.append(text))
    assert not writes
    assert not clipboard.emptied
    assert not clipboard.locked
    assert not offer.restored


def test_restore_checks_and_writes_under_the_same_lock():
    clipboard = OwnedClipboard(owner=123)
    offer = EventOffer()
    offer._restore_text = "previous"
    writes = []

    def render(text=None):
        assert clipboard.locked
        writes.append(text)
        return True

    offer._restore_owned_text(clipboard, 123, render)
    assert writes == ["previous"]
    assert offer.restored
    assert offer._text == "previous"
    assert not clipboard.locked


def test_failed_restore_republishes_transcript_before_unlocking():
    clipboard = OwnedClipboard(owner=123)
    offer = EventOffer()
    offer._restore_text = "previous"
    writes = []

    def render(text=None):
        assert clipboard.locked
        writes.append(text)
        return text is None

    offer._restore_owned_text(clipboard, 123, render)
    assert writes == ["previous", None]
    assert not offer.restored
    assert not clipboard.locked


class NativeFunction:
    """ctypes-callable stand-in retaining the signatures assigned by the pump."""
    def __init__(self, call=lambda *args: 0):
        self.call = call
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.call(*args)


class NativeLibrary:
    def __init__(self, **calls):
        self.calls = {name: NativeFunction(call) for name, call in calls.items()}

    def __getattr__(self, name):
        return self.calls.setdefault(name, NativeFunction())


def test_stop_dispatched_during_message_drain_never_enters_one_second_wait(monkeypatch):
    """Exercise the actual native pump with a stop arriving inside DispatchMessage."""
    import ctypes

    offer = mod.ClipboardOffer("test")
    waits = []
    callbacks = []
    drained = False

    def register_class(pointer):
        callbacks.append(pointer._obj.lpfnWndProc)
        return 1

    def peek_message(pointer, *args):
        nonlocal drained
        if drained:
            return 0
        drained = True
        pointer._obj.message = mod._WM_APP_STOP
        return 1

    def dispatch_message(pointer):
        return callbacks[0](123, pointer._obj.message, 0, 0)

    user32 = NativeLibrary(
        RegisterClassW=register_class,
        CreateWindowExW=lambda *args: 123,
        OpenClipboard=lambda *args: 1,
        PeekMessageW=peek_message,
        DispatchMessageW=dispatch_message,
        IsWindow=lambda *args: 1,
        MsgWaitForMultipleObjectsEx=lambda *args: waits.append(args[2]),
    )
    kernel32 = NativeLibrary(GetModuleHandleW=lambda *args: 1)
    monkeypatch.setattr(mod, "available", lambda: True)
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", lambda value: None, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **kwargs: user32 if name == "user32" else kernel32,
        raising=False,
    )

    offer._pump()

    assert offer._stop_requested.is_set()
    assert waits == []
    assert offer._hwnd == 0
