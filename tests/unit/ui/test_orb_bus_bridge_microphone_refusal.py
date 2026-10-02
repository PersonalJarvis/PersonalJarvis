"""A dictation refused for the microphone names the switch and where to flip it.

The native bar has no permission card (that lives in the web window), so the one
sentence a refused dictation shows there has to carry the answer: which switch,
and where. On macOS the reason ``microphone_unavailable`` is the microphone
permission, so the bridge shows a localized sentence naming System Settings >
Privacy & Security > Microphone instead of the pipeline's generic English one.
A refusal the user cannot fix by switching it on (restricted, unavailable, run from
outside the installed app) keeps the permission layer's own sentence.
Other reasons, and other operating systems, keep the event's own detail.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — deliberate try-import (top-level `ui` discovery quirk)
    from ui.orb import bus_bridge as bridge_mod  # type: ignore[import-not-found]
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not importable in this pytest pythonpath — run from repo root.",
        allow_module_level=True,
    )

from jarvis.core.events import DictationRefused  # noqa: E402
from jarvis.platform.permission_service import user_detail_for  # noqa: E402
from jarvis.platform.permissions import PermissionId  # noqa: E402

_PIPELINE_DETAIL = "Microphone access is not ready — check the microphone permission."


class _FakeBus:
    def subscribe(self, *_a, **_k) -> None:
        pass


class _Orb:
    def __init__(self) -> None:
        self.transcripts: list[str] = []

    def show(self, mode: str = "listen") -> None: ...
    def hide(self) -> None: ...
    def set_level(self, level: float) -> None: ...
    def play_animation(self, name: str, **_kw) -> None: ...
    def stop_animation(self, name: str) -> None: ...

    def show_listening_transcript(self, text: str = "", duration_ms: int = 0) -> None:
        self.transcripts.append(text)


def _bridge(orb: _Orb, language: str = "en") -> OrbBusBridge:
    return OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=orb,
        hide_on_idle=True,
        idle_animations_enabled=False,
        language=language,
    )


async def _refuse(bridge: OrbBusBridge, orb: _Orb, reason: str) -> str:
    await bridge._on_dictation_refused(DictationRefused(reason=reason, detail=_PIPELINE_DETAIL))
    # Stop the stand-down timer the refusal arms so it cannot outlive the test.
    bridge._cancel_dictation_standdown()
    return orb.transcripts[-1]


def test_the_table_covers_every_interface_language() -> None:
    assert set(bridge_mod.MICROPHONE_REFUSAL_TEXT) == set(bridge_mod.PET_STATUS_LANGUAGES)


@pytest.mark.parametrize(
    ("language", "switch", "place"),
    [
        ("en", "Personal Jarvis", "Privacy & Security > Microphone"),
        ("de", "Personal Jarvis", "Datenschutz & Sicherheit > Mikrofon"),  # i18n-allow
        ("es", "Personal Jarvis", "Privacidad y seguridad > Micrófono"),  # i18n-allow
    ],
)
async def test_macos_microphone_refusal_names_the_switch_and_the_place(
    monkeypatch: pytest.MonkeyPatch, language: str, switch: str, place: str
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    orb = _Orb()
    shown = await _refuse(_bridge(orb, language), orb, "microphone_unavailable")

    assert switch in shown and place in shown
    assert shown == bridge_mod.MICROPHONE_REFUSAL_TEXT[language]
    assert shown != _PIPELINE_DETAIL


@pytest.mark.parametrize(
    ("reason", "kwargs"),
    [
        ("restricted", {}),
        ("unavailable", {}),
        ("not_determined", {"outside_app": True, "launched_as_bundle": False}),
        ("not_determined", {"outside_app": True, "launched_as_bundle": True}),
    ],
)
async def test_a_refusal_the_user_cannot_switch_keeps_the_permission_layers_sentence(
    monkeypatch: pytest.MonkeyPatch, reason: str, kwargs: dict[str, bool]
) -> None:
    """Restricted (MDM), unavailable (no GUI session) and outside-the-installed-app are
    not fixed by "switch on Personal Jarvis in System Settings": the service's own
    sentence stays on the bar for those."""
    monkeypatch.setattr(sys, "platform", "darwin")
    sentence = user_detail_for(PermissionId.MICROPHONE, reason, **kwargs)
    orb = _Orb()
    bridge = _bridge(orb)

    await bridge._on_dictation_refused(
        DictationRefused(reason="microphone_unavailable", detail=sentence)
    )
    bridge._cancel_dictation_standdown()

    assert orb.transcripts[-1] == sentence
    assert orb.transcripts[-1] != bridge_mod.MICROPHONE_REFUSAL_TEXT["en"]


@pytest.mark.parametrize(
    ("reason", "kwargs"),
    [("not_determined", {}), ("not_determined", {"asking": True}), ("denied", {})],
)
async def test_an_undecided_or_denied_microphone_still_gets_the_localized_table(
    monkeypatch: pytest.MonkeyPatch, reason: str, kwargs: dict[str, bool]
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    orb = _Orb()
    bridge = _bridge(orb)

    await bridge._on_dictation_refused(
        DictationRefused(
            reason="microphone_unavailable",
            detail=user_detail_for(PermissionId.MICROPHONE, reason, **kwargs),
        )
    )
    bridge._cancel_dictation_standdown()

    assert orb.transcripts[-1] == bridge_mod.MICROPHONE_REFUSAL_TEXT["en"]


async def test_an_unknown_language_falls_back_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    orb = _Orb()
    bridge = _bridge(orb)
    monkeypatch.setattr(bridge, "_status_language", lambda: "fr")

    assert (
        await _refuse(bridge, orb, "microphone_unavailable")
        == (bridge_mod.MICROPHONE_REFUSAL_TEXT["en"])
    )


@pytest.mark.parametrize("platform_name", ["win32", "linux"])
async def test_other_operating_systems_keep_the_pipelines_sentence(
    monkeypatch: pytest.MonkeyPatch, platform_name: str
) -> None:
    """The reason also means "the desktop window is not visible" off macOS, and the
    System Settings path would be wrong there."""
    monkeypatch.setattr(sys, "platform", platform_name)
    orb = _Orb()

    assert await _refuse(_bridge(orb), orb, "microphone_unavailable") == _PIPELINE_DETAIL


async def test_other_refusal_reasons_are_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    orb = _Orb()

    assert await _refuse(_bridge(orb), orb, "no_stt") == _PIPELINE_DETAIL
