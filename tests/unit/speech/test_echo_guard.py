"""Direct pins for the shared ``SelfEchoGuard`` (BUG-084 extraction, BUG-089).

The pipeline facade stays pinned by ``test_self_echo_guard.py``; these tests
cover the guard's own contract plus the two additions the realtime session
relies on: slot-replacement registration (one deque entry per cumulative turn
transcript) and the future-dated activity stamp (playback horizon). German
fixture strings quote the runtime voice product surface (the actual Mac
transcript).
"""

from __future__ import annotations

import time

from jarvis.speech.echo_guard import SelfEchoGuard


def test_garbled_echo_fragment_is_flagged() -> None:
    guard = SelfEchoGuard()
    guard.register("Das freut mich zu hören.")  # i18n-allow: voice fixture
    assert guard.is_echo("Misch zu hören") is True  # i18n-allow: garbled echo


def test_user_answer_with_novel_word_is_kept() -> None:
    guard = SelfEchoGuard()
    guard.register(
        "Guten Morgen, bei mir läuft alles bestens. Was geht bei dir?"  # i18n-allow
    )
    assert guard.is_echo("bei mir läuft alles gut") is False  # i18n-allow


def test_slot_reregistration_replaces_instead_of_appending() -> None:
    guard = SelfEchoGuard()
    guard.register("Ich bin bereit", slot="turn:1")  # i18n-allow: voice fixture
    guard.register(
        "Ich bin bereit für den Tag heute",  # i18n-allow: voice fixture
        slot="turn:1",
    )
    assert len(guard._refs) == 1
    assert guard.is_echo("bereit für den Tag heute") is True  # i18n-allow


def test_growing_slot_snapshots_do_not_evict_other_references() -> None:
    # A cumulative turn transcript re-registers on every scrub-gate release;
    # its growing prefixes must occupy ONE entry, never flushing the canned
    # phrase reference out of the 8-slot deque.
    guard = SelfEchoGuard()
    guard.register(
        "Entschuldige, ich komme gerade nicht an mein Sprachmodell."  # i18n-allow
    )
    for step in range(20):
        guard.register(
            "Alles klar " + "sehr " * step + "gerne",  # i18n-allow: voice fixture
            slot="turn:1",
        )
    assert guard.is_echo("komme gerade nicht an mein Sprachmodell") is True  # i18n-allow


def test_future_dated_touch_keeps_guard_armed() -> None:
    # The realtime session stamps activity forward to the estimated playback
    # drain; the window check treats a future stamp as "active now".
    guard = SelfEchoGuard()
    guard.register("Das freut mich zu hören wirklich sehr")  # i18n-allow
    guard.touch(time.time_ns() + int(30e9))
    assert guard.is_echo("freut mich zu hören wirklich") is True  # i18n-allow


def test_plain_touch_cannot_pull_back_an_armed_horizon() -> None:
    guard = SelfEchoGuard()
    future = time.time_ns() + int(30e9)
    guard.touch(future)
    guard.touch()
    assert guard.activity_ns == future


def test_forced_touch_resets_the_horizon() -> None:
    # Barge-in/cancel pulls the horizon back to "now"; tests set a synthetic
    # past to lapse the window.
    guard = SelfEchoGuard()
    guard.register("Das freut mich zu hören.")  # i18n-allow: voice fixture
    guard.touch(time.time_ns() - int(60e9), force=True)
    assert guard.is_echo("freut mich zu hören") is False  # i18n-allow
