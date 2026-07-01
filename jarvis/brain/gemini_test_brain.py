"""Fictitious test brain based on Gemini (for speech-pipeline validation only).

This is NOT a production brain plugin (the real brain plugins in
`jarvis/plugins/brain/` build a different instance). This is a simple
`BrainCallback` that exercises the pipeline hook with something that behaves
like a real LLM — so the user can test the entire system end-to-end.

Model-ID fallback: tries `gemini-3.1-pro` first, then older stable models,
then Flash. The first model that responds wins.
"""
from __future__ import annotations

import logging
from typing import Any

from jarvis.core import config as cfg

log = logging.getLogger("jarvis.brain.test")


# Order determines preference. Flash BEFORE Pro because:
#   - For a 1-2-sentence voice assistant Flash is fast + cost-efficient.
#   - Pro models (2.5 Pro) have mandatory "thinking" that consumes max_output_tokens
#     — visible text only appears with a sufficiently large budget.
MODEL_CANDIDATES: tuple[str, ...] = (
    "gemini-3.1-pro",       # if available, Preview
    "gemini-2.5-flash",     # stable, low-latency, priority for voice
    "gemini-2.5-pro",       # fallback with thinking
)


SYSTEM_PROMPT = (
    "You are JARVIS — a personal voice assistant on Windows 11, running in a "
    "voice interface. You MUST always respond in ENGLISH, always concisely "
    "(at most 2 short sentences), always with a polished British butler tone: "
    "measured, formal, witty, never servile. "
    "Address the user as 'Ruben'. NEVER an honorific such as 'Sir' or "
    "'boss', and never a fictional owner's name — Mandat-A1 (Audit "
    "F-AUDIT-1, 2026-04-29). No bullet points, no "
    "markdown, no emojis — your text is spoken directly. If the user makes "
    "small talk, reply with dry elegance. If they ask a question, answer "
    "directly.\n\n"
    "**CRITICAL HANGUP RULE**: If the user wants to end the conversation "
    "(typical signals regardless of language: 'hang up', 'goodbye', 'bye', "
    "'stop', 'exit', 'quit', 'shut down', 'that's all', 'thanks jarvis', "
    "'good night', 'ciao', 'auflegen', 'tschüss' — even if Whisper transcribes "  # i18n-allow: German hangup-trigger vocabulary the LLM must match against user speech
    "it strangely like 'offleging'), reply EXACTLY AND EXCLUSIVELY with the "
    "string: \"Goodbye, Ruben.\" (without quotes, exactly 15 characters). "
    "This is the hangup signal — the pipeline detects this response and ends "
    "the call. No additional words, no explanation, only 'Goodbye, Ruben.'"
)


# Magic hangup signal — matched exactly against brain response.
# Audit F-AUDIT-1: old variant "Goodbye, Sir." (13 chars) was migrated on
# 2026-04-29 to "Goodbye, Ruben." (15 chars). The pipeline hangup matcher
# (jarvis/speech/pipeline.py) accepts both forms via normalized-equals —
# see hangup tests for backward compat.
HANGUP_SIGNAL = "Goodbye, Ruben."


class GeminiTestBrain:
    """Callable wrapper — injected as `brain_callback=GeminiTestBrain()`."""

    def __init__(
        self,
        model: str | None = None,
        system_prompt: str = SYSTEM_PROMPT,
        temperature: float = 0.7,
    ) -> None:
        self._override_model = model
        self._resolved_model: str | None = None
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._client: Any = None

    def _resolve_api_key(self) -> str:
        for env_var in ("GEMINI_API_KEY", "GOOGLE_AIStudio_API_KEY", "GOOGLE_API_KEY"):
            val = cfg.get_secret(env_var.lower(), env_fallback=env_var)
            if val:
                return val
        raise RuntimeError(
            "Gemini API key not found. Set GEMINI_API_KEY or "
            "GOOGLE_AIStudio_API_KEY in .env / Credential Manager."
        )

    def _ensure_client(self) -> None:
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self._resolve_api_key())

    def _resolve_model(self) -> str:
        """On the first call, find the best available model alias."""
        if self._resolved_model:
            return self._resolved_model
        if self._override_model:
            self._resolved_model = self._override_model
            return self._resolved_model

        # Short ping call to each candidate model. The first that responds
        # successfully is cached.
        from google import genai  # noqa: F401
        for candidate in MODEL_CANDIDATES:
            try:
                self._client.models.generate_content(
                    model=candidate, contents="ok"
                )
                self._resolved_model = candidate
                log.info("Test brain: using model '%s'", candidate)
                return candidate
            except Exception as exc:  # noqa: BLE001
                log.warning("Model '%s' not available (%s) — trying next.",
                            candidate, type(exc).__name__)
        raise RuntimeError(
            f"No Gemini model available. Tried: {MODEL_CANDIDATES}"
        )

    async def __call__(self, user_text: str) -> str:
        """Brain callback signature: async (str) -> str."""
        import asyncio
        self._ensure_client()
        model = self._resolve_model()
        return await asyncio.to_thread(self._generate_sync, model, user_text)

    def _generate_sync(self, model: str, user_text: str) -> str:
        from google.genai import types
        assert self._client is not None
        # 1024-token budget — enough for thinking models (2.5 Pro) to still
        # produce visible text output.
        resp = self._client.models.generate_content(
            model=model,
            contents=user_text,
            config=types.GenerateContentConfig(
                system_instruction=self._system_prompt,
                temperature=self._temperature,
                max_output_tokens=1024,
            ),
        )
        text = (resp.text or "").strip()
        if text:
            return text

        # Debug aid: if empty, log the finish_reason
        finish = None
        try:
            finish = resp.candidates[0].finish_reason
        except Exception:  # noqa: BLE001
            pass
        log.warning("Brain returned empty response (model=%s, finish=%s)",
                    model, finish)
        return "Entschuldige, ich habe gerade nichts zu sagen."
