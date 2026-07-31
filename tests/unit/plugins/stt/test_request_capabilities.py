"""What ONE transcription request may contain, and what a 400 teaches us.

The defect these lock: every hosted transcription plugin here posted the SAME
body regardless of model. ``whisper-1`` answers ``response_format =
"verbose_json"``; ``gpt-4o-transcribe`` — the newer, genuinely multilingual
model a user would pick to fix mixed-language dictation — rejects that value
with HTTP 400 and transcribes nothing. Picking the better model therefore made
speech recognition stop working, with an error nobody could act on.

Two mechanisms, tested here end to end: a per-model declared shape, and a
refusal treated as EVIDENCE rather than as a failure.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jarvis.plugins.stt.capabilities import (
    FULL_SHAPE,
    UNIVERSAL_SHAPE,
    RequestShape,
    declared_shape,
    is_model_rejection,
    remember_shape,
    reset_learned_shapes,
    resolve_shape,
    shape_after_rejection,
)
from jarvis.plugins.stt.groq_api import GroqWhisperAPI
from jarvis.plugins.stt.openai_api import OpenAIWhisperAPI
from jarvis.plugins.stt.openrouter_stt import OpenRouterSTT


@pytest.fixture(autouse=True)
def _forget_learned_shapes():
    """Each test starts from what is DECLARED, never from a sibling's lesson."""
    reset_learned_shapes()
    yield
    reset_learned_shapes()


def _silent_pcm(seconds: float = 0.2) -> bytes:
    return b"\x00\x00" * int(16_000 * seconds)


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# The declared shape
# ---------------------------------------------------------------------------


class TestWhatAModelIsAssumedToAccept:
    def test_a_whisper_checkpoint_keeps_the_rich_response_format(self):
        """Segment timings feed the confidence figure; Whisper has always
        answered them, on every vendor that hosts it."""
        for model in (
            "whisper-1",
            "whisper-large-v3",
            "openai/whisper-large-v3-turbo",
            "distil-whisper-large-v3-en",
        ):
            assert declared_shape(model).verbose_json is True, model
            assert declared_shape(model).response_format == "verbose_json"

    def test_a_newer_transcription_model_starts_on_the_universal_subset(self):
        """The whole bug: ``verbose_json`` is not universal, and assuming it
        costs the utterance instead of a confidence number."""
        for model in (
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "openai/gpt-4o-transcribe",
            "google/chirp-3",
            "mistralai/voxtral-mini-transcribe",
        ):
            assert declared_shape(model).verbose_json is False, model
            assert declared_shape(model).response_format == "json"

    def test_an_unknown_model_is_never_assumed_to_accept_more(self):
        """A model released tomorrow must degrade to "works", not to a 400."""
        assert declared_shape("some-vendor/model-nobody-has-seen") == UNIVERSAL_SHAPE
        assert declared_shape("") == UNIVERSAL_SHAPE
        assert declared_shape(None) == UNIVERSAL_SHAPE

    def test_the_optional_fields_start_permitted_everywhere(self):
        """Only the response format is genuinely divisive; language, prompt and
        temperature are accepted far more widely, and a refusal narrows them."""
        shape = declared_shape("gpt-4o-transcribe")
        assert shape.language and shape.prompt and shape.temperature


# ---------------------------------------------------------------------------
# A refusal is evidence
# ---------------------------------------------------------------------------


class TestLearningFromARefusal:
    def test_a_response_format_complaint_drops_exactly_that_field(self):
        narrowed = shape_after_rejection(
            FULL_SHAPE,
            "Invalid value: 'verbose_json'. Supported values are: 'json' and 'text'.",
        )
        assert narrowed == RequestShape(verbose_json=False)

    def test_each_optional_field_can_be_named_and_dropped(self):
        assert shape_after_rejection(
            FULL_SHAPE, "Unrecognized request argument supplied: temperature"
        ) == RequestShape(temperature=False)
        assert shape_after_rejection(
            FULL_SHAPE, "prompt is not supported with this model"
        ) == RequestShape(prompt=False)
        assert shape_after_rejection(
            FULL_SHAPE, "language is not supported by this endpoint"
        ) == RequestShape(language=False)

    def test_an_unrelated_refusal_teaches_nothing(self):
        """The safety of the whole mechanism: a retry ladder that stripped a
        field off every 400 would reduce the request to nothing and report the
        least informative error it could find."""
        for message in (
            "Incorrect API key provided",
            "Audio file is too short",
            "You exceeded your current quota",
            "",
        ):
            assert shape_after_rejection(FULL_SHAPE, message) is None, message

    def test_a_field_already_dropped_cannot_be_dropped_again(self):
        """Otherwise a service repeating its complaint spins the caller."""
        once = shape_after_rejection(FULL_SHAPE, "response_format is invalid")
        assert once is not None
        assert shape_after_rejection(once, "response_format is invalid") is None

    def test_what_was_learned_survives_for_the_process(self):
        remember_shape("openai-api", "gpt-4o-transcribe", RequestShape(verbose_json=False))
        assert resolve_shape("openai-api", "gpt-4o-transcribe").verbose_json is False
        # Another model on the same vendor is untouched — the lesson is about
        # the MODEL, which is where the difference actually lives.
        assert resolve_shape("openai-api", "whisper-1").verbose_json is True

    def test_two_lessons_narrow_rather_than_overwrite(self):
        """Concurrent calls each learning a different rejection must end with
        BOTH fields dropped, not with whichever finished last."""
        remember_shape("x", "m", RequestShape(verbose_json=False))
        remember_shape("x", "m", RequestShape(temperature=False))
        shape = resolve_shape("x", "m")
        assert shape.verbose_json is False
        assert shape.temperature is False

    def test_the_lesson_is_never_written_to_disk(self):
        """A capability verdict nobody re-probes is AP-25's sticky cache in
        miniature: one bad minute would pin a model across every restart."""
        reset_learned_shapes()
        assert resolve_shape("openai-api", "gpt-4o-transcribe") == UNIVERSAL_SHAPE


class TestRecognisingAModelRefusal:
    def test_the_usual_wordings_are_recognised(self):
        for message in (
            "The model `gpt-4o-transcribe` does not exist",
            '{"error":{"code":"model_not_found"}}',
            "Unknown model: whisper-9",
            "invalid model id",
        ):
            assert is_model_rejection(message), message

    def test_an_ordinary_failure_is_not_a_model_refusal(self):
        for message in ("Incorrect API key provided", "rate limit reached", ""):
            assert not is_model_rejection(message), message


# ---------------------------------------------------------------------------
# The plugins actually do it
# ---------------------------------------------------------------------------


_OK_VERBOSE = {"text": "hallo welt", "language": "de", "segments": []}
_OK_PLAIN = {"text": "hallo welt"}


class TestTheOpenAIShapedPluginsAdapt:
    @pytest.mark.asyncio
    async def test_a_rejected_response_format_is_retried_as_plain_json(self):
        """The reported failure, end to end: picking gpt-4o-transcribe used to
        make dictation return nothing at all."""
        seen: list[dict[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            fields = _multipart_fields(request)
            seen.append(fields)
            if fields.get("response_format") == "verbose_json":
                return httpx.Response(
                    400,
                    json={"error": {"message": "Invalid value: 'verbose_json'"}},
                )
            return httpx.Response(200, json=_OK_PLAIN)

        # Declared shape says json already, so force the rich one to prove the
        # retry: a whisper id on a service that has stopped accepting it.
        stt = OpenAIWhisperAPI(
            api_key="k", model="whisper-1", http_client=_mock_client(handler)
        )
        try:
            result = await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert result.text == "hallo welt"
        assert [f["response_format"] for f in seen] == ["verbose_json", "json"]

    @pytest.mark.asyncio
    async def test_the_retry_uploads_the_audio_again(self):
        """httpx consumes the buffer it is handed, so a retry that reuses the
        same file tuple posts zero bytes — a silent, total data loss."""
        sizes: list[int] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            fields = _multipart_fields(request)
            sizes.append(len(request.content))
            if fields.get("response_format") == "verbose_json":
                return httpx.Response(400, json={"error": {"message": "response_format"}})
            return httpx.Response(200, json=_OK_PLAIN)

        stt = OpenAIWhisperAPI(
            api_key="k", model="whisper-1", http_client=_mock_client(handler)
        )
        try:
            await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert len(sizes) == 2
        # The retry is within a few header bytes of the first attempt.
        assert abs(sizes[0] - sizes[1]) < 200

    @pytest.mark.asyncio
    async def test_a_refused_model_falls_back_to_the_default_one(self):
        """A pin the account cannot call is a configuration problem, not a
        reason to leave the user unable to dictate."""
        models: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            fields = _multipart_fields(request)
            models.append(fields["model"])
            if fields["model"] != "whisper-1":
                return httpx.Response(
                    400, json={"error": {"code": "model_not_found"}}
                )
            return httpx.Response(200, json=_OK_PLAIN)

        stt = OpenAIWhisperAPI(
            api_key="k",
            model="gpt-4o-transcribe-preview-nobody-has",
            http_client=_mock_client(handler),
        )
        try:
            result = await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert result.text == "hallo welt"
        assert models[-1] == "whisper-1"
        assert stt.last_used_model == "whisper-1"

    @pytest.mark.asyncio
    async def test_an_unrelated_400_is_still_an_error(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": {"message": "Audio too short"}})

        stt = OpenAIWhisperAPI(api_key="k", http_client=_mock_client(handler))
        try:
            with pytest.raises(RuntimeError):
                await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

    @pytest.mark.asyncio
    async def test_groq_adapts_the_same_way(self):
        seen: list[dict[str, str]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            fields = _multipart_fields(request)
            seen.append(fields)
            if "temperature" in fields:
                return httpx.Response(
                    400, json={"error": {"message": "Unrecognized argument: temperature"}}
                )
            return httpx.Response(200, json=_OK_VERBOSE)

        stt = GroqWhisperAPI(api_key="k", http_client=_mock_client(handler))
        try:
            result = await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert result.text == "hallo welt"
        assert "temperature" in seen[0]
        assert "temperature" not in seen[-1]


class TestTheGatewayPluginAdapts:
    @pytest.mark.asyncio
    async def test_a_rejected_bias_prompt_is_dropped_and_the_call_retried(self):
        bodies: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if "provider" in body:
                return httpx.Response(
                    400, json={"error": {"message": "prompt is not supported"}}
                )
            return httpx.Response(200, json={"text": "hola mundo"})

        stt = OpenRouterSTT(
            api_key="k", prompt="Nova", http_client=_mock_client(handler)
        )
        try:
            result = await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert result.text == "hola mundo"
        assert "provider" in bodies[0]
        assert "provider" not in bodies[-1]

    @pytest.mark.asyncio
    async def test_a_rejected_temperature_is_dropped_and_the_call_retried(self):
        bodies: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if "temperature" in body:
                return httpx.Response(
                    400, json={"error": {"message": "temperature is not supported"}}
                )
            return httpx.Response(200, json={"text": "hola mundo"})

        stt = OpenRouterSTT(
            api_key="k", model="google/chirp-3", http_client=_mock_client(handler)
        )
        try:
            result = await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert result.text == "hola mundo"
        assert "temperature" in bodies[0]
        assert "temperature" not in bodies[-1]

    @pytest.mark.asyncio
    async def test_the_audio_survives_every_retry(self):
        """The gateway takes base64 in the body, so the same class of bug is
        possible here: a retry must carry the recording, not an empty string."""
        bodies: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            if "temperature" in body:
                return httpx.Response(400, json={"error": {"message": "temperature"}})
            return httpx.Response(200, json={"text": "ok"})

        stt = OpenRouterSTT(api_key="k", http_client=_mock_client(handler))
        try:
            await stt.transcribe_pcm(_silent_pcm())
        finally:
            await stt.aclose()

        assert len(bodies) == 2
        assert bodies[0]["input_audio"]["data"] == bodies[1]["input_audio"]["data"]
        assert bodies[1]["input_audio"]["data"]


def _multipart_fields(request: httpx.Request) -> dict[str, str]:
    """The non-file multipart fields of a transcription upload, as strings.

    Parsed rather than mocked so the tests fail if the plugin stops sending a
    field it is supposed to send.
    """
    raw = request.content
    fields: dict[str, str] = {}
    for part in raw.split(b"\r\n--"):
        if b'name="' not in part or b'filename="' in part:
            continue
        name = part.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
        try:
            value = part.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n", 1)[0]
        except IndexError:  # pragma: no cover — malformed part
            continue
        fields[name] = value.decode(errors="replace")
    return fields
