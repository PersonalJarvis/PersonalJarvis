"""Kyutai Pocket TTS (100M parameters): the natural voice that runs on a CPU.

Weights come from the ungated ``kyutai/pocket-tts-without-voice-cloning``
repository through the package's own loader; voices are its predefined
embeddings, so no reference audio and no token are needed.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import numpy as np

from jarvis.voice_engine.audio import as_float_array
from jarvis.voice_engine.tts import LANGUAGE_NAMES

DEFAULT_VOICES = {"de": "juergen", "en": "alba"}


class PocketTts:
    name = "pocket"

    def __init__(
        self,
        language: str,
        *,
        voice: str | None = None,
        large: bool = False,
        threads: int | None = None,
    ) -> None:
        import torch  # noqa: PLC0415 - heavy runtime, imported on use
        from pocket_tts import TTSModel  # noqa: PLC0415

        if threads:
            torch.set_num_threads(int(threads))
        config = LANGUAGE_NAMES.get(language)
        if config is None:
            raise RuntimeError(f"Pocket TTS has no model for language {language!r}")
        if large and language != "en":
            config = f"{config}_24l"
        self._model = TTSModel.load_model(language=config)
        self._voice = self._model.get_state_for_audio_prompt(
            voice or DEFAULT_VOICES.get(language, "alba")
        )
        self.sample_rate = int(self._model.sample_rate)
        self.name = f"pocket-{config}"

    def stream(self, text: str, *, stop: threading.Event | None = None) -> Iterator[np.ndarray]:
        for chunk in self._model.generate_audio_stream(self._voice, text, stop=stop):
            yield as_float_array(chunk)
