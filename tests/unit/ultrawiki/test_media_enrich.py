"""Turning a picture into words — and refusing to invent any.

The failure this file exists to prevent: a text-only model, handed an image it
never received, writing a confident description of a photo it cannot see. That
fiction would be indexed as memory and be indistinguishable from a real one.
Everything else here is ordinary plumbing; the ``NO_IMAGE_RECEIVED`` and
chain-filter tests are the load-bearing ones.

Offline throughout — no provider is contacted, and the registry is a fake.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.ultrawiki import media_enrich
from jarvis.ultrawiki.media_enrich import (
    CANNOT_SEE_MARKER,
    EnrichResult,
    describe_image,
    transcribe_recording,
    vision_chain,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


class _FakeClass:
    def __init__(self, supports_vision: bool) -> None:
        self.supports_vision = supports_vision


class _FakeRegistry:
    """Stands in for the brain provider registry, with declared capabilities."""

    def __init__(self, providers: dict[str, bool]) -> None:
        self._providers = providers

    def available(self) -> list[str]:
        return sorted(self._providers)

    def get_class(self, name: str) -> Any:
        return _FakeClass(self._providers[name])


class _Cfg:
    class ultrawiki:  # noqa: N801 — mirrors the config attribute path
        distill_provider = ""

    class brain:  # noqa: N801
        primary = "seeing"

    class stt:  # noqa: N801
        provider = ""


@pytest.fixture
def all_credential_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat every registered provider as having a usable credential."""
    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(
        chain_mod, "credential_ready_wiki_providers", lambda **kwargs: set(kwargs["available"])
    )
    monkeypatch.setattr(chain_mod, "_cheap_model_for", lambda name: "", raising=False)


# ---------------------------------------------------------------------------
# The chain: capability, never a name
# ---------------------------------------------------------------------------


def test_only_providers_that_declare_vision_reach_the_chain(all_credential_ready):
    registry = _FakeRegistry({"seeing": True, "blind": False, "also-seeing": True})
    names = [name for name, _model in vision_chain(_Cfg(), registry)]
    assert "blind" not in names
    assert set(names) == {"seeing", "also-seeing"}


def test_a_provider_that_will_not_load_is_treated_as_blind(all_credential_ready):
    class _Broken(_FakeRegistry):
        def get_class(self, name: str) -> Any:
            if name == "broken":
                raise RuntimeError("plugin import failed")
            return super().get_class(name)

    registry = _Broken({"seeing": True, "broken": True})
    assert [name for name, _ in vision_chain(_Cfg(), registry)] == ["seeing"]


async def test_no_seeing_provider_means_an_honest_reason_not_a_guess(
    all_credential_ready,
):
    registry = _FakeRegistry({"blind": False})
    result = await describe_image(PNG, filename="a.png", cfg=_Cfg(), registry=registry)
    assert result.ok is False
    assert result.text == ""
    assert "images" in result.reason
    # Retryable: connecting a capable provider later must drain the backlog.
    assert result.retryable is True


# ---------------------------------------------------------------------------
# The one that matters: never store a description of an unseen image
# ---------------------------------------------------------------------------


async def test_a_model_that_admits_it_saw_no_image_is_rejected(
    all_credential_ready, monkeypatch: pytest.MonkeyPatch
):
    """The prompt gives the model an escape hatch; this is it being honoured.

    Without this check the reply would be stored verbatim — and a model that
    answers the marker is precisely one that would otherwise have invented a
    photo.
    """
    seen: list[Any] = []

    async def _fake_complete(**kwargs: Any) -> Any:
        # Mirror the real helper: run validate(), and report failure when no
        # attempt passes it.
        validate = kwargs["validate"]
        aggregate = kwargs["aggregate"]
        seen.append(kwargs["request"])
        answer = aggregate(f"{CANNOT_SEE_MARKER}")
        return None if not validate(answer) else (answer, "seeing")

    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)

    registry = _FakeRegistry({"seeing": True})
    result = await describe_image(PNG, filename="a.png", cfg=_Cfg(), registry=registry)
    assert result.ok is False
    assert result.text == ""
    assert seen, "the provider was never called"


async def test_an_empty_or_trivial_answer_is_rejected(all_credential_ready, monkeypatch):
    async def _fake_complete(**kwargs: Any) -> Any:
        validate, aggregate = kwargs["validate"], kwargs["aggregate"]
        answer = aggregate("ok")
        return None if not validate(answer) else (answer, "seeing")

    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)
    result = await describe_image(
        PNG, filename="a.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
    )
    assert result.ok is False


async def test_a_real_description_comes_back_with_its_provider(
    all_credential_ready, monkeypatch
):
    async def _fake_complete(**kwargs: Any) -> Any:
        aggregate = kwargs["aggregate"]
        answer = aggregate(
            "A photo of two people on a beach at sunset, holding surfboards.\n"
            "Text: Malibu 2019"
        )
        assert kwargs["validate"](answer)
        return answer, "seeing"

    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)
    result = await describe_image(
        PNG, filename="a.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
    )
    assert result.ok is True
    assert "surfboards" in result.text
    assert result.provider == "seeing"


async def test_the_image_actually_reaches_the_request(all_credential_ready, monkeypatch):
    """A request without the image attached would be the silent version of the
    same bug: the model answers about nothing and sounds fine."""
    captured: dict[str, Any] = {}

    async def _fake_complete(**kwargs: Any) -> Any:
        captured["request"] = kwargs["request"]
        answer = kwargs["aggregate"]("A photograph of a mountain range at dawn.")
        return answer, "seeing"

    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)
    await describe_image(
        PNG, filename="peak.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
    )
    message = captured["request"].messages[0]
    assert len(message.images) == 1
    assert message.images[0].mime == "image/png"
    assert message.images[0].data_b64


# ---------------------------------------------------------------------------
# Limits and empties
# ---------------------------------------------------------------------------


async def test_an_oversized_picture_is_refused_permanently(all_credential_ready):
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (media_enrich.MAX_IMAGE_BYTES + 1)
    result = await describe_image(
        huge, filename="huge.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
    )
    assert result.ok is False
    assert result.retryable is False, "retrying an oversized file forever is a loop"


async def test_an_empty_file_is_refused_permanently(all_credential_ready):
    result = await describe_image(
        b"", filename="a.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
    )
    assert result.ok is False
    assert result.retryable is False


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------


class _FileCapableSTT:
    async def transcribe_container(self, data: bytes, *, filename: str = "") -> Any:
        class _T:
            text = f"transcript of {filename}"

        return _T()


class _MicOnlySTT:
    provider_name = "local-whisper"


async def test_a_recording_is_transcribed_when_the_provider_takes_a_file():
    result = await transcribe_recording(
        b"OggS-audio", filename="PTT-0003.opus", cfg=_Cfg(), stt=_FileCapableSTT()
    )
    assert result.ok is True
    assert "PTT-0003.opus" in result.text


async def test_a_microphone_only_provider_says_so_by_name():
    """"No speech recognition" would be a lie when one is configured."""
    result = await transcribe_recording(
        b"OggS-audio", filename="note.opus", cfg=_Cfg(), stt=_MicOnlySTT()
    )
    assert result.ok is False
    assert "local-whisper" in result.reason
    assert result.retryable is True


async def test_a_provider_that_raises_never_breaks_the_lane():
    class _Broken:
        async def transcribe_container(self, data: bytes, *, filename: str = "") -> Any:
            raise RuntimeError("upstream refused")

    result = await transcribe_recording(
        b"OggS-audio", filename="note.opus", cfg=_Cfg(), stt=_Broken()
    )
    assert result.ok is False
    assert "upstream refused" in result.reason


async def test_silence_is_a_permanent_outcome_not_a_retry():
    class _Silent:
        async def transcribe_container(self, data: bytes, *, filename: str = "") -> Any:
            class _T:
                text = "   "

            return _T()

    result = await transcribe_recording(
        b"OggS-audio", filename="note.opus", cfg=_Cfg(), stt=_Silent()
    )
    assert result.ok is False
    assert result.retryable is False


def test_enrich_result_defaults_to_retryable():
    """A new failure mode must default to "try again", never to "give up"."""
    assert EnrichResult().retryable is True
