# Prototype Contracts — v2 "The Awakening" (real AI + SSE)

v2 turns the in-process v1 prototype into a **networked** system: a FastAPI
server, real (provider-agnostic) LLM calls, Server-Sent Events (SSE) to clients,
and HTTP VAD endpoints driving the "Oops" loop. The in-process `EventBus` stays
as the internal backbone (no external broker — AD-OE2); SSE is only the client
transport.

```
HTTP client (web/mobile/test)
   │  POST /api/utterance {text, session_id}
   ▼
FastAPI server ── Talker.handle_utterance ──► returns INSTANT ack in the HTTP response   [DoD part 1]
   │                     │ publish AckEmitted + MissionSpawn (in-process EventBus)
   │                     ▼
   │              HeavyDutyWorker ── real LLM call (httpx, OpenAI-compatible) ──► WorkerCompleted
   │                     │                                              │ (failure) WorkerCorrectionNeeded
   │  GET /api/stream (SSE) ◄── SSEHub (subscribe_all on the bus) ──────┘
   │      streams: ack, worker_started, answer            [DoD part 2: real LLM answer arrives async over SSE]
   │
   └─ POST /api/vad/speech_started | speech_ended ──► flush per-session Oops corrections into the SSE stream
```

## HARD CONSTRAINTS (every module)
1. **Standard library + already-installed packages ONLY.** Allowed third-party:
   `fastapi`, `httpx`, `sse_starlette`, `python-dotenv` (`import dotenv`),
   `pytest`. **Do NOT `pip install` anything** (esp. NOT `litellm`/`openai` —
   the real LLM call is a thin `httpx` POST to an OpenAI-compatible endpoint).
   No `import jarvis.*`.
2. **Hardware-agnostic / config-driven.** No hardcoded GPU, CUDA, OS path,
   provider, model, URL, or key anywhere. Everything via `.env` → `config.py`.
   Refuse any OS/GPU-specific code.
3. **English** for all code/comments/logs. (Spoken correction text is German.)
4. **TDD, strictly.** Test first → watch it fail → implement → green. Use
   `asyncio.run(...)` in sync tests (no `pytest-asyncio`). For HTTP/SSE tests use
   `httpx.ASGITransport(app=...)` in-process (no real network, no real LLM).
5. Tests must NOT require Ollama, a network, or any key — use `backend="mock"`
   for the LLM and `httpx.MockTransport` to simulate the HTTP path.
6. Bus handlers are `async def` and must never raise out (swallowed by the bus).
7. Touch only your assigned files. Run your tests from the v2 directory and
   report the pytest output.

## Shared contract already provided (DO NOT MODIFY)
- `optimistic/events.py`: as v1, **plus** every event now has `session_id: str =
  "default"`, and there is `event_to_wire(ev) -> dict` (JSON-safe: class name in
  `type`, UUID→str, enum→value). Carry `session_id=ev.session_id` and
  `trace_id=ev.trace_id` through every event you publish in response to another.
- `optimistic/registry.py`, `optimistic/bus.py`, `optimistic/router.py`: unchanged from v1.

## FakeBus for unit tests (copy into your test file when you don't want the real bus)
```python
class FakeBus:
    def __init__(self):
        self.published = []; self._subs = {}; self._all = []
    def subscribe(self, event_type, handler):
        self._subs.setdefault(event_type, []).append(handler)
    def subscribe_all(self, handler):
        self._all.append(handler)
    async def publish(self, event):
        self.published.append(event)
        for et, hs in self._subs.items():
            if isinstance(event, et):
                for h in hs: await h(event)
        for h in self._all: await h(event)
```

---

## SUB-AGENT 1 — Hardware-Agnostic AI Backend
Files: `optimistic/config.py` (new), `optimistic/llm.py` (new), `optimistic/worker.py` (rewrite), `optimistic/tools.py` (trim).
Tests: `tests/test_config.py` (new), `tests/test_llm.py` (new), `tests/test_worker.py` (rewrite), `tests/test_tools.py` (rewrite).

### `optimistic/config.py`
```python
@dataclass(frozen=True)
class LLMSettings:
    backend: str          # "http" | "mock"
    base_url: str
    model: str
    api_key: str | None
    timeout: float
    system_prompt: str | None
    @property
    def use_mock(self) -> bool: ...   # backend == "mock"

def load_settings(env: dict[str, str] | None = None) -> LLMSettings
    # Reads dotenv (call dotenv.load_dotenv() if env is None) then os.environ,
    # or the passed `env` dict. Keys + defaults:
    #   LLM_BACKEND="http", LLM_BASE_URL="http://localhost:11434/v1",
    #   LLM_MODEL="qwen2.5:7b", LLM_API_KEY=None (empty string -> None),
    #   LLM_TIMEOUT=120.0, LLM_SYSTEM_PROMPT=None.
    # Pure config — no network, no GPU/OS code.
```

### `optimistic/llm.py`
```python
class LLMError(Exception): ...
async def complete(prompt: str, *, settings: LLMSettings, system: str | None = None) -> str
    # settings.use_mock -> return a DETERMINISTIC, instant, non-empty string that
    #   echoes the prompt, e.g. f"[mock:{settings.model}] {prompt[:120]}".  No network.
    # else -> httpx POST f"{settings.base_url}/chat/completions" with OpenAI body:
    #   {"model": settings.model, "stream": False,
    #    "messages": ([{"role":"system","content":system}] if system else []) +
    #                [{"role":"user","content":prompt}]}
    #   headers Authorization: Bearer <api_key> when api_key set.
    #   httpx.AsyncClient(timeout=settings.timeout). Raise LLMError on any
    #   exception or non-2xx. Return choices[0].message.content.
    # Hardware-agnostic: only base_url/model/key are used.
```
Test the HTTP path with `httpx.MockTransport` (no real network): assert request body shape + parsing of a fake OpenAI response, and `LLMError` on a 500.

### `optimistic/worker.py` (rewrite)
```python
class HeavyDutyWorker:
    def __init__(self, bus, settings: LLMSettings)
    @property
    def in_flight(self) -> int
    async def drain(self) -> None
```
- `__init__`: `bus.subscribe(MissionSpawn, self._on_mission_spawn)`.
- `_on_mission_spawn(ev)`: schedule `asyncio.create_task(self._run(ev))`, track it, **return immediately** (delegation stays instant).
- `_run(ev)`:
  1. publish `WorkerStarted(mission_id=ev.mission_id, tool_name=ev.tool_name, session_id=ev.session_id, trace_id=ev.trace_id)`
  2. `logging.getLogger("optimistic.worker").info("Heavy-Duty-Worker processing task %s: %s", ev.mission_id, ev.command)`
  3. recoverable pre-check: `if ev.tool_name == "gmail":` `miss = check_missing_info(ev.command, ev.context)`; if `miss` → publish `WorkerCorrectionNeeded(reason=miss[0], detail=miss[1], mission_id, command, session_id, trace_id)` and return.
  4. else: `try: result = await llm.complete(ev.command, settings=self._settings, system=self._settings.system_prompt)` → publish `WorkerCompleted(result=result, mission_id, session_id, trace_id)`.
  5. `except LLMError`: ONE retry; on second failure publish `WorkerCorrectionNeeded(reason=CorrectionReason.NETWORK_ERROR, detail=str(exc), mission_id, command, session_id, trace_id)`. Never let `_run` raise out.

### `optimistic/tools.py` (trim)
- Keep `DumbTool` + `get_dumb_tool` unchanged.
- Keep `MissingInfoError` (optional) and ADD:
  `def check_missing_info(command: str, context: dict) -> tuple[CorrectionReason, str] | None`
  (move the v1 gmail recipient logic here: extract the first capitalised name after
  the first word; if present and not in `context.get("contacts", {})` case-insensitively,
  return `(CorrectionReason.MISSING_INFO, f"no email address on file for {name}")`, else `None`).
- **Remove `SmartTool`** (the worker uses `llm.complete` now). Update `tests/test_tools.py` accordingly.

---

## SUB-AGENT 2 — Async Event-Stream / SSE
Files: `optimistic/sse.py` (new). Tests: `tests/test_sse.py` (new).

### `optimistic/sse.py`
```python
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

class SSEHub:
    def __init__(self, bus): ...   # bus.subscribe_all(self._on_event)
    async def push(self, session_id: str, event_name: str, data: dict) -> None
        # enqueue an SSE message {event: event_name, data: json.dumps(data)} for every
        # client queue currently subscribed to session_id.
    def stream(self, session_id: str) -> EventSourceResponse
        # register a fresh asyncio.Queue for this session, return an EventSourceResponse
        # whose generator yields queued messages (dict with "event" + "data" keys),
        # plus a periodic comment/ping; unregister the queue on disconnect (finally).

def build_sse_router(hub: SSEHub) -> APIRouter
    # GET /api/stream?session_id=default  ->  hub.stream(session_id)
```
Auto-stream policy in `_on_event(ev)` — map bus events to SSE event names and push
via the same queues:
- `AckEmitted` → `"ack"`  data `{"text": ev.text}`
- `WorkerStarted` → `"worker_started"`  data `{"mission_id": ev.mission_id}`
- `WorkerCompleted` → `"answer"`  data `{"text": ev.result, "mission_id": ev.mission_id}`
- **`WorkerCorrectionNeeded` → NOT streamed** (it is invisible until the VAD flush;
  the orchestrator pushes a `"correction"` event explicitly via `hub.push`).
- Everything routes by `ev.session_id`.

### `tests/test_sse.py` (httpx ASGITransport, no real network)
Build a tiny app: `app = FastAPI(); bus = EventBus(); hub = SSEHub(bus); app.include_router(build_sse_router(hub))`.
Cover: a client streaming `session_id="s1"` receives an `"answer"` SSE event after
`await bus.publish(WorkerCompleted(result="hi", session_id="s1"))`; an event for
`"s2"` is NOT delivered to the `"s1"` stream; `hub.push("s1","correction",{"text":"x"})`
reaches the `"s1"` client. Pattern: start the stream read as an `asyncio.Task`,
`await asyncio.sleep(0.05)` to let it subscribe, publish, then read with a timeout.
You own making this streaming-read pattern robust — the orchestrator's E2E reuses it.

---

## SUB-AGENT 3 — VAD endpoints + per-session Oops
Files: `optimistic/vad.py` (new), `optimistic/oops.py` (rewrite to per-session).
Tests: `tests/test_vad.py` (new), `tests/test_oops.py` (rewrite).

### `optimistic/oops.py` (rewrite — per session, no speaking-flag here)
```python
class OopsProtocol:
    def __init__(self, bus): ...   # bus.subscribe(WorkerCorrectionNeeded, self._on_correction)
    def pending(self, session_id: str = "default") -> list[WorkerCorrectionNeeded]
    def injected_context(self, session_id: str = "default") -> list[str]
    def flush(self, session_id: str = "default") -> list[str]
        # return [self.phrase(ev) for ev in pending(session)], then clear that session's buffer.
    def phrase(self, ev: WorkerCorrectionNeeded) -> str   # organic German + _scrub (keep v1 logic)
```
- `_on_correction(ev)` appends to a per-`session_id` buffer (invisible injection; no speech).
- `phrase`/`_scrub`: keep v1 behaviour (MISSING_INFO names the recipient; regex scrub
  removes tool names `gmail|calendar|drive|mcp`, markdown, extra whitespace; no LLM).

### `optimistic/vad.py`
```python
from fastapi import APIRouter
class VADRegistry:
    def speech_started(self, session_id: str) -> None
    def speech_ended(self, session_id: str) -> None
    def is_speaking(self, session_id: str) -> bool

def build_vad_router(registry: VADRegistry, on_turn_boundary) -> APIRouter
    # on_turn_boundary: async callable (session_id:str) -> list[str]
    # POST /api/vad/speech_started  body {"session_id": "..."} -> registry.speech_started; {"ok": True, "speaking": True}
    # POST /api/vad/speech_ended    body {"session_id": "..."} -> registry.speech_ended;
    #        corrections = await on_turn_boundary(session_id); {"ok": True, "speaking": False, "corrections": corrections}
```
`on_turn_boundary` is injected (the orchestrator wires it to: flush Oops for that
session + push each phrase to SSE as a `"correction"` event). Keep `vad.py`
independent — do NOT import `oops`/`sse`; only call the injected callback.
For request bodies use a Pydantic model or `dict = Body(...)` — `session_id` defaults to `"default"`.

### Tests
- `tests/test_oops.py` (rewrite): a published `WorkerCorrectionNeeded(MISSING_INFO, detail "...Max", session_id="s1")` lands in `pending("s1")` and not in `pending("s2")`; `flush("s1")` returns one organic phrase that contains "max" and not "gmail"/backtick, then `pending("s1")` is empty.
- `tests/test_vad.py` (httpx ASGITransport): build an app with `build_vad_router(VADRegistry(), fake_cb)` where `fake_cb` records calls and returns `["correction X"]`. `POST /api/vad/speech_started` → `is_speaking` true, `fake_cb` NOT called. `POST /api/vad/speech_ended` → `fake_cb` called once with the session, response `corrections == ["correction X"]`.

---

## Orchestrator (me) after you finish
Writes `optimistic/talker.py` (adds `session_id`), `optimistic/server.py`
(`create_app(settings)` wiring bus+hub+worker+oops+vad+talker, `POST /api/utterance`,
`GET /api/health`), `demo_client.py`, and the integration tests
(`test_acceptance.py`, `test_latency.py`, `test_e2e_oops.py` rewrites + the
server-level `test_e2e_phase2.py`). Build your modules so those pass with the
mock backend, and so a live run against Ollama returns a real answer over SSE.
