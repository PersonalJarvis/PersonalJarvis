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


class _StreamingBrain:
    def __init__(self, text: str = "", *, fail: bool = False) -> None:
        self._text = text
        self._fail = fail

    async def complete(self, request: Any):  # noqa: ARG002
        from jarvis.core.protocols import BrainDelta

        if self._fail:
            raise RuntimeError("provider stream failed")
        yield BrainDelta(content=self._text)
        yield BrainDelta(finish_reason="stop")


class _StreamingRegistry(_FakeRegistry):
    def __init__(self, responses: dict[str, str | Exception]) -> None:
        super().__init__({name: True for name in responses})
        self._responses = responses
        self.tried: list[str] = []

    def instantiate(self, name: str, **kwargs: Any) -> Any:  # noqa: ARG002
        self.tried.append(name)
        response = self._responses[name]
        if isinstance(response, Exception):
            return _StreamingBrain(fail=True)
        return _StreamingBrain(response)


async def _aggregate_text(kwargs: dict[str, Any], text: str) -> Any:
    """Feed text through the same async ``BrainDelta`` stream as live brains."""
    from jarvis.core.protocols import BrainDelta

    async def _stream():
        yield BrainDelta(content=text)

    return await kwargs["aggregate"](_stream())


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


def test_known_blind_model_is_swapped_for_a_seeing_sibling(
    all_credential_ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.brain import model_catalog

    monkeypatch.setattr(
        model_catalog,
        "model_capabilities",
        lambda provider, model: {"vision": provider == "seeing" and model != "text-only"},
    )
    monkeypatch.setattr(
        model_catalog, "pick_fast_vision_model", lambda provider: f"{provider}-vision"
    )
    import jarvis.memory.wiki.curator_llm as curator_mod

    monkeypatch.setattr(curator_mod, "_cheap_model_for", lambda provider: "text-only")

    registry = _FakeRegistry({"seeing": True})
    assert vision_chain(_Cfg(), registry) == [("seeing", "seeing-vision")]


def test_known_blind_model_without_a_seeing_sibling_is_excluded(
    all_credential_ready, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.brain import model_catalog

    monkeypatch.setattr(
        model_catalog,
        "model_capabilities",
        lambda _provider, _model: {"vision": False},
    )
    monkeypatch.setattr(model_catalog, "pick_fast_vision_model", lambda _provider: None)
    import jarvis.memory.wiki.curator_llm as curator_mod

    monkeypatch.setattr(curator_mod, "_cheap_model_for", lambda _provider: "text-only")

    assert vision_chain(_Cfg(), _FakeRegistry({"seeing": True})) == []


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
        seen.append(kwargs["request"])
        answer = await _aggregate_text(kwargs, CANNOT_SEE_MARKER)
        return None if validate(answer) else (answer, "seeing")

    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)

    registry = _FakeRegistry({"seeing": True})
    result = await describe_image(PNG, filename="a.png", cfg=_Cfg(), registry=registry)
    assert result.ok is False
    assert result.text == ""
    assert seen, "the provider was never called"


async def test_an_empty_or_trivial_answer_is_rejected(all_credential_ready, monkeypatch):
    async def _fake_complete(**kwargs: Any) -> Any:
        validate = kwargs["validate"]
        answer = await _aggregate_text(kwargs, "ok")
        return None if validate(answer) else (answer, "seeing")

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
        answer = await _aggregate_text(
            kwargs,
            "A photo of two people on a beach at sunset, holding surfboards.\n"
            "Text: Malibu 2019"
        )
        assert kwargs["validate"](answer) is None
        return answer, "seeing"

    import jarvis.memory.wiki.provider_chain as chain_mod

    monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)
    result = await describe_image(
        PNG, filename="a.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
    )
    assert result.ok is True
    assert "surfboards" in result.text
    assert result.provider == "seeing"


async def test_live_chain_consumes_the_async_provider_stream(
    all_credential_ready,
) -> None:
    """Regression for the live six-provider ``TypeError`` failure.

    The media adapter used a synchronous ``for`` loop over the providers'
    ``AsyncIterator[BrainDelta]``. Mocking the fallback helper hid that contract
    mismatch, so this test deliberately crosses the real helper boundary.
    """
    registry = _StreamingRegistry({"seeing": "A photograph of a mountain range at dawn."})

    result = await describe_image(PNG, filename="peak.png", cfg=_Cfg(), registry=registry)

    assert result.ok is True
    assert result.provider == "seeing"
    assert "mountain range" in result.text
    assert registry.tried == ["seeing"]


async def test_the_image_actually_reaches_the_request(all_credential_ready, monkeypatch):
    """A request without the image attached would be the silent version of the
    same bug: the model answers about nothing and sounds fine."""
    captured: dict[str, Any] = {}

    async def _fake_complete(**kwargs: Any) -> Any:
        captured["request"] = kwargs["request"]
        answer = await _aggregate_text(kwargs, "A photograph of a mountain range at dawn.")
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


# ---------------------------------------------------------------------------
# Reading a picture, not just describing it
# ---------------------------------------------------------------------------
#
# A description answers "what does this look like". For the pictures a real
# folder is full of - screenshots, scanned pages, photographed whiteboards -
# the content IS the text, and a paragraph about "a screenshot of an
# application window" is unsearchable by anything the user would actually
# type. So the reading comes first and verbatim, and the description is the
# short second half.


class TestPictureText:
    async def test_the_prompt_asks_for_the_text_before_the_description(self):
        """Order is load-bearing: a truncated answer must keep the TEXT."""
        prompt = media_enrich.image_prompt()
        assert prompt.index("TEXT") < prompt.index("DESCRIPTION")
        assert "verbatim" in prompt.lower()

    async def test_the_prompt_offers_a_way_to_say_there_is_no_text(self):
        """Without an explicit token, a model invents captions for a sunset."""
        assert media_enrich.NO_TEXT_MARKER in media_enrich.image_prompt()

    def test_a_two_block_answer_splits_into_text_and_description(self):
        answer = (
            "TEXT:\n"
            "Invoice 2026-114\nTotal due: 48,20 EUR\n\n"
            "DESCRIPTION:\n"
            "A scanned invoice on white paper."
        )
        parsed = media_enrich.parse_image_answer(answer)
        assert parsed.text == "Invoice 2026-114\nTotal due: 48,20 EUR"
        assert parsed.description == "A scanned invoice on white paper."

    def test_the_no_text_marker_yields_a_description_only(self):
        answer = (
            f"TEXT:\n{media_enrich.NO_TEXT_MARKER}\n\n"
            "DESCRIPTION:\nTwo people on a beach at sunset."
        )
        parsed = media_enrich.parse_image_answer(answer)
        assert parsed.text == ""
        assert "beach" in parsed.description

    def test_an_answer_without_headings_counts_as_the_description(self):
        """Models drop the scaffolding sometimes; that must not lose content."""
        parsed = media_enrich.parse_image_answer("A mountain range at dawn.")
        assert parsed.text == ""
        assert parsed.description == "A mountain range at dawn."

    def test_prose_before_the_first_heading_survives_as_description(self):
        """Some models answer description-first whatever the prompt asks.

        Everything ahead of the first heading has to be kept: dropping it
        would silently lose the entire description for those models.
        """
        parsed = media_enrich.parse_image_answer(
            "A photo of two people on a beach, holding surfboards.\n"
            "TEXT: Malibu 2019"
        )
        assert parsed.text == "Malibu 2019"
        assert "surfboards" in parsed.description

    def test_the_stored_body_leads_with_the_words_that_were_read(self):
        """Search hits the beginning hardest, and the text is the real content."""
        parsed = media_enrich.parse_image_answer(
            "TEXT:\nQuarterly ledger reconciliation\n\n"
            "DESCRIPTION:\nA screenshot of a spreadsheet."
        )
        body = parsed.as_body()
        assert body.index("Quarterly ledger") < body.index("screenshot")

    def test_a_picture_with_no_text_still_stores_its_description(self):
        parsed = media_enrich.parse_image_answer(
            f"TEXT:\n{media_enrich.NO_TEXT_MARKER}\n\nDESCRIPTION:\nA red bicycle."
        )
        assert parsed.as_body().strip() == "A red bicycle."

    async def test_a_screenshot_answer_keeps_all_of_its_text(
        self, all_credential_ready, monkeypatch
    ):
        """End to end: what the model read must survive into the result."""
        lines = "\n".join(f"Row {n}: telescope maintenance" for n in range(40))

        async def _fake_complete(**kwargs: Any) -> Any:
            answer = await _aggregate_text(
                kwargs,
                f"TEXT:\n{lines}\n\nDESCRIPTION:\nA screenshot of a table."
            )
            assert kwargs["validate"](answer) is None
            return answer, "seeing"

        import jarvis.memory.wiki.provider_chain as chain_mod

        monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)
        result = await describe_image(
            PNG, filename="shot.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
        )
        assert result.ok is True
        assert result.text.count("telescope maintenance") == 40
        assert result.meta.get("read_chars", 0) > 0

    async def test_a_blind_model_answer_is_still_refused(
        self, all_credential_ready, monkeypatch
    ):
        """The invented-photo guard must survive the new answer shape."""

        async def _fake_complete(**kwargs: Any) -> Any:
            answer = await _aggregate_text(kwargs, f"TEXT:\n{CANNOT_SEE_MARKER}")
            assert kwargs["validate"](answer)
            return None

        import jarvis.memory.wiki.provider_chain as chain_mod

        monkeypatch.setattr(chain_mod, "complete_with_fallback", _fake_complete)
        result = await describe_image(
            PNG, filename="a.png", cfg=_Cfg(), registry=_FakeRegistry({"seeing": True})
        )
        assert result.ok is False


class TestWhichPicturesAreWorthReading:
    """A model call per file is the expensive part; most files are not worth it.

    Measured on a real Desktop: 218,419 of the audio files were one-second
    wake-word debug clips under a program data folder, and the image side has
    the same shape - icons, sprites and cache thumbnails outnumber the photos.
    """

    def test_a_tiny_icon_is_not_worth_a_model_call(self):
        skip = media_enrich.skip_reason_for_image("icon.png", size_bytes=900)
        assert skip
        assert "small" in skip.lower()

    def test_an_ordinary_screenshot_is_worth_reading(self):
        assert media_enrich.skip_reason_for_image("shot.png", size_bytes=400_000) == ""

    def test_a_picture_inside_a_program_data_folder_is_skipped(self):
        skip = media_enrich.skip_reason_for_image(
            "Personal Jarvis/data/wake_debug/frame.png", size_bytes=400_000
        )
        assert skip
        assert "data" in skip.lower() or "program" in skip.lower()

    def test_a_cache_or_build_folder_is_skipped_too(self):
        for folder in ("cache", "dist", "build", "node_modules", "__pycache__"):
            skip = media_enrich.skip_reason_for_image(
                f"project/{folder}/img.png", size_bytes=400_000
            )
            assert skip, folder

    def test_a_users_own_pictures_folder_is_never_skipped(self):
        for path in ("Pictures/holiday.jpg", "Desktop/scan.png", "Documents/x.jpeg"):
            assert media_enrich.skip_reason_for_image(path, size_bytes=400_000) == "", path

    def test_the_folder_match_is_case_blind(self):
        assert media_enrich.skip_reason_for_image("App/Data/x.png", size_bytes=400_000)

    def test_a_folder_merely_containing_the_word_is_not_skipped(self):
        """'data' as a path SEGMENT, never as a substring of a real name."""
        assert media_enrich.skip_reason_for_image(
            "my-database-notes/diagram.png", size_bytes=400_000
        ) == ""
