# Optimistic Execution v2 — "The Awakening" (real AI + SSE)

The v1 prototype proved the *pattern* in-process with dummies. **v2 makes it a
real, networked service:** a FastAPI server, real provider-agnostic LLM calls,
Server-Sent Events (SSE) to any client, and HTTP VAD endpoints driving the "Oops"
loop. v1 is left untouched in `../optimistic-execution-v1/prototype/`.

Still **zero new hard dependencies beyond what was already installed** (FastAPI,
httpx, sse-starlette, python-dotenv) — in particular the LLM call is a thin
`httpx` POST to an **OpenAI-compatible** endpoint, so it works with **local
Ollama/LM Studio and any cloud provider** alike, configured entirely via `.env`.
No `litellm`, no `openai` SDK, no GPU/OS-specific code (keeps the maintainer's
production Jarvis env undisturbed; honours the cloud-first "no new hard dep"
doctrine).

## Quickstart

```bash
# 1. Configure the provider (a working .env pointing at local Ollama is included)
cp .env.example .env        # edit to taste; defaults to Ollama qwen2.5:7b

# 2. Start the server
python -m optimistic.server          # http://127.0.0.1:8008

# 3. In another terminal, talk to it
python demo_client.py "Erstelle eine kurze Erklaerung von Optimistic Execution."
python demo_client.py --oops          # the missing-info self-correction scenario

# 4. Run the test suite (mock LLM, no network, no Ollama needed)
python -m pytest tests/ -q            # 201 passed

# 5. Optional: observable live smoke test against the running server
python -u verify_live.py
```

## Definition of Done — verified live against Ollama

```
[health] {'ok': True, 'backend': 'http', 'model': 'qwen2.5:7b', 'base_url': 'http://localhost:11434/v1'}
[POST] status=200 ack_latency=331.4ms ack='Geht klar, ich kümmere mich drum.'
[sse:ack] {'text': 'Geht klar, ich kümmere mich drum.'}
[sse:worker_started] {'mission_id': '2d54594e'}
[sse:answer] {'text': 'Optimistisches Ausführen ist ein Verfahren, das ... zur Reduktion von Zeitverzögerungen führen kann.'}
[RESULT] PASS — real LLM answer over SSE
```

The POST returns the **instant ACK** (Main Jarvis); the **real LLM answer**
(qwen2.5:7b) arrives **asynchronously over SSE** (the Heavy-Duty Worker). The
`--oops` run shows the invisible failure → organic, scrubbed correction surfacing
at the VAD turn boundary.

## Architecture

```
HTTP client (web/mobile/test)
   │  POST /api/utterance {text, session_id}
   ▼
FastAPI ── Talker.handle_utterance ──► instant ACK in the HTTP response   [DoD part 1]
   │            │ publish AckEmitted + MissionSpawn (in-process EventBus — still the queue, AD-OE2)
   │            ▼
   │       HeavyDutyWorker ── httpx POST /v1/chat/completions (OpenAI-compatible) ──► WorkerCompleted
   │            │                                              │ (failure) WorkerCorrectionNeeded (invisible)
   │  GET /api/stream (SSE) ◄── SSEHub (subscribe_all) ────────┘
   │      ack · worker_started · answer        [DoD part 2: real LLM answer, async over SSE]
   │
   └─ POST /api/vad/speech_started | speech_ended ──► flush per-session Oops → "correction" SSE event
```

## Module map (Δ from v1)

| File | Status | Role |
|---|---|---|
| `optimistic/config.py` | **new** | `LLMSettings` + `load_settings()` — all provider config from `.env`. |
| `optimistic/llm.py` | **new** | `complete()` — provider-agnostic OpenAI-compatible call via httpx; `mock` backend for tests. |
| `optimistic/worker.py` | **rewritten** | Calls the real LLM instead of a dummy; one retry → `NETWORK_ERROR`; gmail missing-info pre-check. |
| `optimistic/sse.py` | **new** | `SSEHub` (per-session fan-out) + `GET /api/stream`. |
| `optimistic/vad.py` | **new** | `VADRegistry` + `POST /api/vad/speech_started|ended` (no PyAudio — HTTP-driven). |
| `optimistic/oops.py` | **rewritten** | Per-session correction buffers; `flush(session_id)` at the turn boundary. |
| `optimistic/talker.py` | upgraded | Threads `session_id` through every event. |
| `optimistic/server.py` | **new** | `create_app()` wiring + `/api/utterance`, `/api/health`. |
| `optimistic/events.py` | extended | Every event gains `session_id`; `event_to_wire()` JSON serialiser. |
| `optimistic/{bus,router,registry,tools}.py` | as v1 | (tools trimmed: `SmartTool` removed, `check_missing_info` added.) |
| `demo_client.py`, `verify_live.py` | **new** | Live HTTP clients. |
| `tests/` | extended | 201 tests; SSE tested via a direct-ASGI streaming client (httpx.ASGITransport buffers SSE). |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/utterance` | `{text, session_id}` → instant `{ack}` (Main Jarvis). |
| GET | `/api/stream?session_id=` | SSE: `ack`, `worker_started`, `answer`, `correction`. |
| POST | `/api/vad/speech_started` | Mark the session speaking (corrections held back). |
| POST | `/api/vad/speech_ended` | Turn boundary → flush organic corrections (response + SSE). |
| GET | `/api/health` | `{ok, backend, model, base_url}`. |

## Notes & limits

- **Routing heuristic (from v1):** a command routes to the worker only if it hits
  a tool trigger (mail/termin/…) or an action verb (`erstelle/such/zeig/…`).
  Open questions like "Erkläre X" fall to the smalltalk path (a canned reply).
  Use an action verb to exercise the LLM, or extend `registry.ACTION_VERBS`.
- **Tests never touch the network** — they use the `mock` LLM backend and a direct
  ASGI streaming client. CI-safe, hardware-agnostic.
- **Switch provider** by editing `.env` only (e.g. point `LLM_BASE_URL` at Groq,
  OpenAI, OpenRouter, or a Gemini OpenAI-compatible endpoint and set `LLM_API_KEY`).
