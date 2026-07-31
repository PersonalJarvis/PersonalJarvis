"""``[dictation].paste_chord`` accepts a recorded combination, honestly.

Jarvis does not paste. It asks whatever application is in front to paste, by
sending a synthetic chord — and an application that does not bind that chord
ignores it silently. That is fine for the curated chords (they mean "paste"
somewhere real), and it is the whole problem for a chord the user recorded:
there is no error and nothing to read back, so reporting ``inserted`` would be
a lie in the one module whose entire docstring is about not lying.

So the vocabulary widened in two directions at once and both are pinned here:
the config field accepts a combo, and the delivery report gains ``paste_sent``
for the case where the keystroke went out and the result is unknown.
"""

from __future__ import annotations

import pytest

import jarvis.platform
import jarvis.platform.clipboard  # noqa: F401  (binds the package attribute)
from jarvis.core.config import DictationConfig
from jarvis.dictation.insert import (
    CUSTOM_CHORD_KEYS,
    CUSTOM_CHORD_MODIFIERS,
    PASTE_CHORDS,
    normalize_paste_chord,
    paste_chord_is_curated,
    resolve_paste_chord,
)
from jarvis.dictation.outcomes import DICTATION_OUTCOMES


def _install_fake_clipboard(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    """Put ``fake`` where ``insert_text`` actually looks for the clipboard.

    ``jarvis.dictation.insert`` reaches the module with ``from jarvis.platform
    import clipboard``, which reads the ATTRIBUTE off the already-imported
    ``jarvis.platform`` package and only falls back to ``sys.modules`` when
    that attribute is missing. Replacing the ``sys.modules`` entry alone
    therefore works exactly once -- in a run where nothing has imported the
    real module yet -- and is silently bypassed as soon as any earlier test
    binds the attribute. That is worse than a flaky assertion: the double
    would be ignored and the test would drive the REAL system clipboard of
    whoever ran the suite. Patch the attribute the code reads.
    """
    monkeypatch.setattr(jarvis.platform, "clipboard", fake)


# ----------------------------------------------------------------------
# What the field accepts
# ----------------------------------------------------------------------


@pytest.mark.parametrize("name", ["auto", *PASTE_CHORDS])
def test_every_curated_chord_is_still_accepted(name: str) -> None:
    """Widening the field must not cost the values people already have."""
    assert DictationConfig(paste_chord=name).paste_chord == name


def test_a_recorded_combination_is_accepted_and_canonicalized() -> None:
    cfg = DictationConfig(paste_chord="SHIFT + Ctrl + Insert")
    assert cfg.paste_chord == "ctrl+shift+insert"


def test_a_recorded_combination_that_spells_a_curated_chord_folds_into_it() -> None:
    """Otherwise two spellings of Ctrl+V would report different outcomes."""
    assert DictationConfig(paste_chord="ctrl+v").paste_chord == "ctrl_v"
    assert DictationConfig(paste_chord="v+ctrl+shift").paste_chord == "ctrl_shift_v"


def test_an_unusable_value_falls_back_to_auto_instead_of_raising() -> None:
    """AP-16: a hand-edited config must never fail to load."""
    assert DictationConfig(paste_chord="nonsense").paste_chord == "auto"
    assert DictationConfig(paste_chord="ctrl+dragon").paste_chord == "auto"
    assert DictationConfig(paste_chord="").paste_chord == "auto"


def test_a_modifier_only_combination_is_refused() -> None:
    """Ctrl+Shift cannot paste anything — there is no key to send."""
    value, problem = normalize_paste_chord("ctrl+shift")
    assert value == "auto"
    assert problem


def test_the_rejection_is_a_sentence_a_person_can_act_on() -> None:
    _, problem = normalize_paste_chord("ctrl+dragon")
    assert "dragon" in problem
    assert problem.endswith(".")


def test_a_key_only_one_platform_can_send_is_refused() -> None:
    """The token vocabulary is the INTERSECTION of the two actuator backends.

    ``numpad3`` resolves on Windows and raises ``Unknown key`` on Linux/macOS,
    which would turn every paste on those hosts into a clipboard fallback —
    accepted here it would be a shortcut that works on one machine only.
    """
    assert "numpad3" not in CUSTOM_CHORD_KEYS
    assert DictationConfig(paste_chord="ctrl+numpad3").paste_chord == "auto"


def test_the_token_vocabulary_has_no_overlap_between_keys_and_modifiers() -> None:
    """A token that is both would parse differently depending on lookup order."""
    assert not set(CUSTOM_CHORD_MODIFIERS) & set(CUSTOM_CHORD_KEYS)


# ----------------------------------------------------------------------
# What gets sent
# ----------------------------------------------------------------------


def test_a_custom_chord_resolves_to_its_own_key_list() -> None:
    label, keys = resolve_paste_chord("ctrl+shift+insert")
    assert label == "ctrl+shift+insert"
    assert keys == ["ctrl", "shift", "insert"]


def test_an_unknown_chord_still_falls_back_to_the_platform_default() -> None:
    assert resolve_paste_chord("nonsense") == resolve_paste_chord("auto")


def test_only_the_curated_chords_are_treated_as_known_to_mean_paste() -> None:
    for name in PASTE_CHORDS:
        assert paste_chord_is_curated(name) is True
    assert paste_chord_is_curated("ctrl+shift+insert") is False


# ----------------------------------------------------------------------
# The honest report
# ----------------------------------------------------------------------


def test_paste_sent_is_part_of_the_outcome_vocabulary() -> None:
    """It reaches the history entry and the UI, so it is a five-layer value."""
    assert "paste_sent" in DICTATION_OUTCOMES


def test_a_custom_chord_reports_paste_sent_and_keeps_the_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.dictation import insert as insert_mod

    class _Clipboard:
        def __init__(self) -> None:
            self.content = "something the user had copied"

        def read_text(self) -> str:
            return self.content

        def write_text(self, text: str) -> bool:
            self.content = text
            return True

    class _Actuator:
        def __init__(self) -> None:
            self.combos: list[list[str]] = []

        def key_combo(self, keys: list[str]) -> None:
            self.combos.append(list(keys))

    clipboard = _Clipboard()
    actuator = _Actuator()
    _install_fake_clipboard(monkeypatch, clipboard)
    monkeypatch.setattr(
        insert_mod, "describe_target", lambda: insert_mod.TargetReport(True, "", "")
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.get_actuator", lambda *a, **k: actuator, raising=False
    )

    result = insert_mod.insert_text(
        "dictated text",
        paste_chord="ctrl+shift+insert",
        delay_ms=0,
        delay_after_ms=0,
    )

    assert result.status == "paste_sent"
    assert actuator.combos == [["ctrl", "shift", "insert"]]
    # The transcript is deliberately LEFT on the clipboard: if the chord landed
    # nowhere, restoring the old content would delete the only copy the user
    # can still reach.
    assert result.clipboard_holds_text is True
    assert result.clipboard_restored is False
    assert clipboard.content == "dictated text"
    assert result.ok is True
    assert result.detail


def test_a_curated_chord_still_reports_inserted_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The honesty change must not cost the normal path its clean report."""
    from jarvis.dictation import insert as insert_mod

    class _Clipboard:
        def __init__(self) -> None:
            self.content = "previous"
            # Every write is recorded so the closing assertion cannot pass
            # vacuously: a double that is never reached also never changes
            # ``content``, which is precisely the end state this test wants.
            self.writes: list[str] = []

        def read_text(self) -> str:
            return self.content

        def write_text(self, text: str) -> bool:
            self.content = text
            self.writes.append(text)
            return True

    class _Actuator:
        def key_combo(self, keys: list[str]) -> None:
            return None

    clipboard = _Clipboard()
    _install_fake_clipboard(monkeypatch, clipboard)
    monkeypatch.setattr(
        insert_mod, "describe_target", lambda: insert_mod.TargetReport(True, "", "")
    )
    monkeypatch.setattr(
        "jarvis.cu.actuate.get_actuator", lambda *a, **k: _Actuator(), raising=False
    )

    result = insert_mod.insert_text(
        "dictated text", paste_chord="ctrl_v", delay_ms=0, delay_after_ms=0
    )

    assert result.status == "inserted"
    assert result.clipboard_restored is True
    assert clipboard.writes == ["dictated text", "previous"]
    assert clipboard.content == "previous"
