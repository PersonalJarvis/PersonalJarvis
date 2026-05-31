# ADR-0004: LLM provider — Anthropic SDK with deterministic mock fallback

**Status:** Accepted
**Date:** 2026-05-26

## Context

The goal pre-decides: "LLM for ACE: Anthropic API via `$ANTHROPIC_API_KEY`; if unset, deterministic mock". The Recursive Reflector's *real* innovation per the architecture survey is the **Python REPL sandbox** — the LLM is used only to *generate Python analysis code*, not to do the analysis itself. The sandbox executes the generated code over the trace database and emits a structured Verdict. So the LLM dependency is shallow: a function `propose_analysis(trace_summary) -> code_str`.

## Decision

Define an `LLM` `Protocol`:

```python
class LLM(Protocol):
    async def complete(self, prompt: str, *, max_tokens: int = 800) -> str: ...
```

Two implementations:

1. **`AnthropicLLM`** — lazy-imports `anthropic`. Reads `ANTHROPIC_API_KEY` from env. Model: `claude-sonnet-4-6` (per parent project memory: user has no API account but Max OAuth via claude-cli — the skillbook stays library-pure and accepts the key in env if a future runner has one). Falls back to mock if the SDK import fails or the key is absent.

2. **`MockLLM`** — pattern-matches a small set of well-known prompts and returns canned Python code that the Reflector's sandbox can execute. The mock recognizes the trace-analysis prompt and returns code that:
   - reads the trace via `trace_rows`,
   - finds the last row with `status == "TIMEOUT"`,
   - extracts the actor name,
   - emits a `{"rule": {"trigger": {"actor": <name>}, "strategy": {"kind": "retry_with_delay", "delay_s": 3, "max_retries": 2}}}` JSON line on stdout.

The mock is **not a stub** — it is a real, deterministic implementation that satisfies the Reflector's contract and is exercised by the capstone test. The "deterministic mock" terminology comes from the goal directly.

## Consequences

- Capstone passes without `ANTHROPIC_API_KEY`.
- Real-LLM runs are possible by exporting the key and installing the `[llm]` extra.
- The Reflector's sandbox-execution logic is unchanged regardless of LLM provider; the LLM is a thin replaceable adapter.

## Alternatives considered

- **OpenAI SDK**: same shape; we could have either. Goal specified Anthropic.
- **Local LLM via Ollama**: viable but adds runtime weight and undermines the "library, no servers" stance.
