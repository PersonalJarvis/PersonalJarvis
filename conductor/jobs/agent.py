"""AgentHandler — single-turn LLM call for three providers.

v0.1 is single-turn: no tool use, no multi-step. That covers 80% of
agent jobs ("summarize this log", "write a standup", "analyze this
metric"). Tool use arrives in v0.2 as its own RunStep loop.

Provider env vars:
- ``anthropic`` → ``ANTHROPIC_API_KEY``
- ``openai``    → ``OPENAI_API_KEY``
- ``ollama``    → ``OLLAMA_HOST`` (default http://localhost:11434)

Cost tracking: ``input_tokens`` + ``output_tokens`` are extracted from
the provider response. For USD cost we'd need per-model pricing
tables — v0.2. Currently only token counts in metrics.
"""
from __future__ import annotations

import os
import time
from typing import Any

from .base import HandlerResult


class AgentHandler:
    async def execute(
        self,
        spec: Any,
        input_data: dict[str, Any],
    ) -> HandlerResult:
        user_prompt = _expand_template(spec.user_prompt, input_data)
        system = spec.system_prompt or ""

        if spec.provider == "gemini":
            return await self._run_gemini(
                spec.model, system, user_prompt,
                spec.max_output_tokens, spec.temperature,
            )
        if spec.provider == "anthropic":
            return await self._run_anthropic(
                spec.model, system, user_prompt,
                spec.max_output_tokens, spec.temperature,
            )
        if spec.provider == "openai":
            return await self._run_openai(
                spec.model, system, user_prompt,
                spec.max_output_tokens, spec.temperature,
            )
        if spec.provider == "ollama":
            return await self._run_ollama(
                spec.model, system, user_prompt, spec.temperature,
            )
        return HandlerResult(
            success=False, output="", exit_code=-1,
            error=f"unknown provider: {spec.provider}",
        )

    # ------------------------------------------------------------------

    async def _run_gemini(
        self, model: str, system: str, user: str,
        max_tokens: int, temperature: float,
    ) -> HandlerResult:
        """Single-turn Google AI Studio call via the google-genai SDK.

        Key is tried in order: GEMINI_API_KEY, GOOGLE_AIStudio_
        API_KEY, GOOGLE_API_KEY. Default model when empty: ``gemini-3.1-pro``.
        """
        api_key: str | None = None
        for env_var in ("GEMINI_API_KEY", "GOOGLE_AIStudio_API_KEY",
                         "GOOGLE_API_KEY"):
            val = os.environ.get(env_var)
            if val:
                api_key = val
                break
        if not api_key:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=(
                    "No Gemini API key set — "
                    "run setx GEMINI_API_KEY <key> and restart your terminal. "
                    "Get a key at: https://aistudio.google.com/apikey"
                ),
            )

        try:
            from google import genai
        except ImportError:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error="google-genai package missing: pip install google-genai",
            )

        effective_model = model or "gemini-3.1-pro"
        start = time.perf_counter()
        try:
            client = genai.Client(api_key=api_key)
            config: dict[str, Any] = {
                "temperature": temperature,
                "max_output_tokens": max_tokens,
            }
            if system:
                config["system_instruction"] = system
            resp = await client.aio.models.generate_content(
                model=effective_model,
                contents=[{"role": "user", "parts": [{"text": user}]}],
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - start) * 1000)
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"{type(exc).__name__}: {exc}",
                metrics={"duration_ms": duration_ms, "provider": "gemini",
                         "model": effective_model},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        text = ""
        # google-genai: resp.text is the consolidated text, or extract
        # manually from candidates[0].content.parts.
        text = getattr(resp, "text", None) or ""
        if not text:
            for cand in getattr(resp, "candidates", None) or []:
                content_obj = getattr(cand, "content", None)
                if content_obj is None:
                    continue
                for part in getattr(content_obj, "parts", None) or []:
                    part_text = getattr(part, "text", None)
                    if part_text:
                        text += part_text

        usage = getattr(resp, "usage_metadata", None)
        in_tokens = int(getattr(usage, "prompt_token_count", 0) or 0) if usage else 0
        out_tokens = int(getattr(usage, "candidates_token_count", 0) or 0) if usage else 0
        finish_reason = None
        cands = getattr(resp, "candidates", None) or []
        if cands:
            finish_reason = str(getattr(cands[0], "finish_reason", "") or "")

        return HandlerResult(
            success=True, output=text.strip(), exit_code=0,
            metrics={
                "duration_ms": duration_ms,
                "provider": "gemini",
                "model": effective_model,
                "input_tokens": in_tokens,
                "output_tokens": out_tokens,
                "finish_reason": finish_reason,
            },
        )

    # ------------------------------------------------------------------

    # ------------------------------------------------------------------

    async def _run_anthropic(
        self, model: str, system: str, user: str,
        max_tokens: int, temperature: float,
    ) -> HandlerResult:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error="ANTHROPIC_API_KEY not set — run setx ANTHROPIC_API_KEY <key>",
            )
        try:
            import anthropic
        except ImportError:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error="anthropic package missing: pip install anthropic",
            )

        start = time.perf_counter()
        try:
            client = anthropic.AsyncAnthropic(api_key=api_key)
            messages = [{"role": "user", "content": user}]
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            msg = await client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"{type(exc).__name__}: {exc}",
                metrics={"duration_ms": int((time.perf_counter() - start) * 1000)},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        text = ""
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", None) == "text":
                text += block.text
        usage = getattr(msg, "usage", None)
        metrics = {
            "duration_ms": duration_ms,
            "provider": "anthropic",
            "model": model,
            "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
            "stop_reason": getattr(msg, "stop_reason", None),
        }
        return HandlerResult(
            success=True, output=text, exit_code=0, metrics=metrics,
        )

    # ------------------------------------------------------------------

    async def _run_openai(
        self, model: str, system: str, user: str,
        max_tokens: int, temperature: float,
    ) -> HandlerResult:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error="OPENAI_API_KEY not set",
            )
        try:
            import openai
        except ImportError:
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error="openai package missing: pip install openai",
            )

        start = time.perf_counter()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        try:
            client = openai.AsyncOpenAI(api_key=api_key)
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"{type(exc).__name__}: {exc}",
                metrics={"duration_ms": int((time.perf_counter() - start) * 1000)},
            )

        duration_ms = int((time.perf_counter() - start) * 1000)
        choice = resp.choices[0] if resp.choices else None
        text = choice.message.content if choice and choice.message else ""
        usage = resp.usage
        metrics = {
            "duration_ms": duration_ms,
            "provider": "openai",
            "model": model,
            "input_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
            "finish_reason": choice.finish_reason if choice else None,
        }
        return HandlerResult(
            success=True, output=text or "", exit_code=0, metrics=metrics,
        )

    # ------------------------------------------------------------------

    async def _run_ollama(
        self, model: str, system: str, user: str, temperature: float,
    ) -> HandlerResult:
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        import httpx

        payload: dict[str, Any] = {
            "model": model,
            "prompt": user,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system:
            payload["system"] = system

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(f"{host}/api/generate", json=payload)
        except Exception as exc:  # noqa: BLE001
            return HandlerResult(
                success=False, output="", exit_code=-1,
                error=f"ollama-connect: {exc}",
            )
        duration_ms = int((time.perf_counter() - start) * 1000)
        if r.status_code >= 400:
            return HandlerResult(
                success=False, output="", exit_code=r.status_code,
                error=f"ollama HTTP {r.status_code}: {r.text[:200]}",
                metrics={"duration_ms": duration_ms},
            )
        try:
            data = r.json()
        except Exception:  # noqa: BLE001
            return HandlerResult(
                success=False, output=r.text[:500], exit_code=-1,
                error="ollama: non-JSON response",
                metrics={"duration_ms": duration_ms},
            )
        metrics = {
            "duration_ms": duration_ms,
            "provider": "ollama",
            "model": model,
            "input_tokens": data.get("prompt_eval_count", 0),
            "output_tokens": data.get("eval_count", 0),
        }
        return HandlerResult(
            success=True, output=data.get("response", ""), exit_code=0,
            metrics=metrics,
        )


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------

import re  # noqa: E402

_TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


def _expand_template(s: str, input_data: dict[str, Any]) -> str:
    """``{{input.X}}`` → value from the input dict."""
    def repl(m: re.Match[str]) -> str:
        token = m.group(1).strip()
        if token.startswith("input."):
            key = token[len("input."):]
            v = input_data.get(key, "")
            return str(v)
        return m.group(0)
    return _TEMPLATE_RE.sub(repl, s)
