"""The model the user picked has to reach the provider that transcribes.

The defect: the STT model picker wrote ``[stt] model`` and the factory never
forwarded it, so every cloud recognizer ran on its own hardcoded default. A
user who selected a genuinely multilingual model to fix mixed-language
dictation changed nothing at all — the classic AP-31 shape, a switch whose
value is ignored.

The repair is a per-provider slot rather than one global string, because
``[stt] model`` holds a faster-whisper CHECKPOINT name: forwarding that one
value to whichever provider happened to be selected would have posted
``large-v3-turbo`` to Groq on a fresh install and broken speech recognition for
everyone who never touched the picker.
"""

from __future__ import annotations

from typing import Any

import jarvis.core.config as cfg
import jarvis.plugins.stt as stt_pkg
from jarvis.core.config import STTConfig


class _FakeCloud:
    """A cloud provider that records exactly what it was constructed with."""

    name = "openrouter-stt"
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeCloud.last_kwargs = kwargs


class _FakeOnDevice:
    name = "nemotron-local"
    runs_on_device = True
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOnDevice.last_kwargs = kwargs


def _keyed(monkeypatch) -> None:
    """Pretend every credential slot resolves, so the key-aware factory keeps
    the configured provider instead of crossing families (AP-22)."""
    monkeypatch.setattr(cfg, "get_secret_any", lambda candidates: "key")


def _build(monkeypatch, stt_cfg: STTConfig, cls: type) -> None:
    _keyed(monkeypatch)
    monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: cls)
    stt_pkg.build_stt_from_config(stt_cfg)


# ---------------------------------------------------------------------------
# resolve_stt_model — the precedence, on its own
# ---------------------------------------------------------------------------


class TestWhichModelAProviderIsGiven:
    def test_a_per_provider_pin_is_what_gets_sent(self):
        conf = STTConfig(
            provider="openrouter-stt",
            models={"openrouter-stt": "openai/gpt-4o-transcribe"},
        )
        assert (
            stt_pkg.resolve_stt_model(conf, "openrouter-stt")
            == "openai/gpt-4o-transcribe"
        )

    def test_the_local_checkpoint_name_never_leaks_to_a_hosted_api(self):
        """The trap this design exists to avoid: ``[stt] model`` defaults to a
        faster-whisper checkpoint, and Groq has never heard of it."""
        conf = STTConfig(provider="groq-api", model="large-v3-turbo")
        assert stt_pkg.resolve_stt_model(conf, "groq-api") == ""
        assert stt_pkg.resolve_stt_model(conf, "openai-api") == ""

    def test_a_pin_for_another_provider_is_not_borrowed(self):
        conf = STTConfig(
            provider="groq-api", models={"openai-api": "gpt-4o-transcribe"}
        )
        assert stt_pkg.resolve_stt_model(conf, "groq-api") == ""

    def test_an_unusable_config_answers_the_working_value(self):
        """A test double or a stale install must never break the build."""

        class _Bare:
            provider = "groq-api"

        assert stt_pkg.resolve_stt_model(_Bare(), "groq-api") == ""
        assert stt_pkg.resolve_stt_model(STTConfig(), "") == ""


# ---------------------------------------------------------------------------
# The factory forwards it
# ---------------------------------------------------------------------------


class TestTheFactoryForwardsThePick:
    def test_a_pinned_cloud_model_reaches_the_provider(self, monkeypatch):
        _build(
            monkeypatch,
            STTConfig(
                provider="openrouter-stt",
                models={"openrouter-stt": "openai/gpt-4o-transcribe"},
            ),
            _FakeCloud,
        )
        assert _FakeCloud.last_kwargs["model"] == "openai/gpt-4o-transcribe"

    def test_without_a_pin_the_provider_keeps_its_own_default(self, monkeypatch):
        """No pin must mean "your default", never an empty model id."""
        _build(monkeypatch, STTConfig(provider="openrouter-stt"), _FakeCloud)
        assert "model" not in _FakeCloud.last_kwargs

    def test_temperature_zero_is_forwarded_by_default(self, monkeypatch):
        """A transcription is a measurement: the same audio has to come back
        the same way twice."""
        _build(monkeypatch, STTConfig(provider="openrouter-stt"), _FakeCloud)
        assert _FakeCloud.last_kwargs["temperature"] == 0.0

    def test_a_configured_temperature_is_honoured(self, monkeypatch):
        _build(
            monkeypatch, STTConfig(provider="openrouter-stt", temperature=0.2), _FakeCloud
        )
        assert _FakeCloud.last_kwargs["temperature"] == 0.2

    def test_an_on_device_recognizer_is_not_handed_cloud_options(self, monkeypatch):
        """It configures its own decoding; offering it these only earns a
        misleading "this plugin is out of date" warning."""
        _build(
            monkeypatch,
            STTConfig(provider="nemotron-local", bias_prompt="Ruben"),
            _FakeOnDevice,
        )
        for cloud_only in ("temperature", "prompt", "timeout_s"):
            assert cloud_only not in _FakeOnDevice.last_kwargs

    def test_a_provider_that_predates_the_kwargs_still_builds(self, monkeypatch):
        """A third-party plugin written before these existed must not fall all
        the way through to a local engine a base install does not ship."""

        class _Old:
            name = "openrouter-stt"
            last_kwargs: dict[str, Any] = {}

            def __init__(self, *, language: str | None = None, **rest: Any) -> None:
                if rest:
                    raise TypeError(f"unexpected keyword arguments: {sorted(rest)}")
                _Old.last_kwargs = {"language": language}

        _build(
            monkeypatch,
            STTConfig(
                provider="openrouter-stt",
                language="de",
                models={"openrouter-stt": "openai/gpt-4o-transcribe"},
            ),
            _Old,
        )
        assert _Old.last_kwargs == {"language": "de"}

    def test_a_fallback_provider_is_built_with_its_own_pin(self, monkeypatch):
        """A cross-family fallback that silently dropped the user's model would
        transcribe their words differently the moment it took over."""
        _keyed(monkeypatch)
        monkeypatch.setattr(stt_pkg, "_load_provider_class", lambda name: _FakeCloud)
        conf = STTConfig(
            provider="groq-api",
            models={
                "groq-api": "whisper-large-v3",
                "openai-api": "gpt-4o-transcribe",
            },
        )
        stt_pkg.build_named_stt_provider("openai-api", conf)
        assert _FakeCloud.last_kwargs["model"] == "gpt-4o-transcribe"
