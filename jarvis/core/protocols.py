"""Plugin contracts for all interchangeable components.

Each plugin slot is declared here as a ``typing.Protocol`` — structural typing
so that plugins do not need to import anything from the Jarvis package.
Only ``runtime_checkable`` protocols allow ``isinstance()`` checks during
discovery.

Streaming is first-class: every Brain/STT/TTS/Harness response is an
``AsyncIterator``. Non-streaming providers yield exactly one element.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

# ----------------------------------------------------------------------
# Audio Data-Types
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class AudioChunk:
    """Raw PCM audio with sample rate and timestamp."""
    pcm: bytes
    sample_rate: int
    timestamp_ns: int
    channels: int = 1


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """Audio device (input or output)."""
    index: int
    name: str
    is_input: bool
    is_output: bool
    default_sample_rate: float
    channels: int


# ----------------------------------------------------------------------
# Speech Data-Types
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Transcript:
    """STT result, optionally including segments and language information."""
    text: str
    language: str  # "de", "en", "auto-detected" oder konkret
    confidence: float  # 0.0–1.0
    is_partial: bool = False
    segments: tuple[dict[str, Any], ...] = field(default_factory=tuple)


# ----------------------------------------------------------------------
# Brain Data-Types
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ImageBlock:
    """Multimodal image input for brain providers.

    ``data_b64`` is base64-encoded raw bytes (no data-URI prefix).
    Provider adapters are responsible for API-specific wrapping
    (Anthropic image-block, Gemini inline_data, OpenAI image_url).
    ``source_hash`` is an observation hash for logging/deduplication and is
    not forwarded to the LLM.
    """
    mime: str
    data_b64: str
    source_hash: str = ""


@dataclass(frozen=True, slots=True)
class BrainMessage:
    """A message in the message log (user/assistant/system/tool)."""
    role: Literal["user", "assistant", "system", "tool"]
    content: str | list[dict[str, Any]]
    tool_call_id: str | None = None
    name: str | None = None
    images: tuple[ImageBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class BrainRequest:
    """Request to a brain provider."""
    messages: tuple[BrainMessage, ...]
    tools: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    system: str | None = None
    temperature: float = 0.7
    max_tokens: int = 4096
    stream: bool = True


@dataclass(frozen=True, slots=True)
class BrainDelta:
    """A stream chunk from the brain: text, tool call, or finish signal."""
    content: str | None = None
    tool_call: dict[str, Any] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None  # input_tokens, output_tokens, cache_hit_tokens


# ----------------------------------------------------------------------
# Harness Data-Types
# ----------------------------------------------------------------------

RiskTier = Literal["safe", "monitor", "ask", "block"]


@dataclass(frozen=True, slots=True)
class HarnessTask:
    """Task for a sub-agent harness (OpenClaw, Codex, OI, …)."""
    prompt: str
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    timeout_s: int = 600
    risk_tier: RiskTier = "monitor"
    allow_computer_use: bool = False


@dataclass(frozen=True, slots=True)
class HarnessResult:
    """Result of a harness invocation stream."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    artifacts: tuple[str, ...] = field(default_factory=tuple)
    cost_usd: float = 0.0
    duration_ms: int = 0
    is_final: bool = False  # letztes Element im Stream


# ----------------------------------------------------------------------
# Tool Data-Types
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Context passed to a tool at execution time."""
    trace_id: UUID
    user_utterance: str
    config: dict[str, Any]
    memory_read: Any  # MemoryStore read-only handle
    approved_by: str | None = None  # "auto" | "user" | None (falls tier=safe)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result of a tool execution."""
    success: bool
    output: Any
    error: str | None = None
    artifacts: tuple[str, ...] = field(default_factory=tuple)


# ----------------------------------------------------------------------
# Protocols
# ----------------------------------------------------------------------

@runtime_checkable
class WakeWordProvider(Protocol):
    """Detects wake words in the audio stream."""

    name: str
    supported_keywords: tuple[str, ...]

    async def start(self) -> None:
        """Initialise the model and open the audio stream."""
        ...

    async def stop(self) -> None:
        """Stop cleanly and release resources."""
        ...

    async def stream(self) -> AsyncIterator[float]:
        """Yield confidence values (0.0–1.0). Values above the threshold indicate a wake word."""
        ...


@runtime_checkable
class TurnDetector(Protocol):
    """End-of-turn detection (Phase L.2, Plan §6.1).

    Three implementation classes:
      * ``smart_turn_v3``   — Pipecat ``LocalSmartTurnAnalyzerV3`` (semantic, ~12 ms CPU).
      * ``silero_only``     — silence-based endpointing (plan B / last resort).
      * ``flux_integrated`` — no-op; end-of-turn comes from the STT provider (Deepgram Flux EOT).

    The default in ``jarvis.toml`` is ``flux_integrated`` (Plan AD-L-4 variant A).
    When the STT provider delivers no integrated EOT, the pipeline owner falls back
    to ``smart_turn_v3`` (or ``silero_only`` when Pipecat is not installed).

    Audio input: 16 kHz mono PCM (see AP-L-16 — Smart Turn v3 is hard-coded to
    16 kHz Whisper feature extraction).
    """

    name: str
    supports_semantic: bool   # True = uses phoneme/prosody model, False = silence-only

    async def start(self) -> None:
        """Initialise the model (lazy load)."""
        ...

    async def stop(self) -> None:
        """Release resources."""
        ...

    async def detect_end_of_turn(self, audio: AsyncIterator[AudioChunk]) -> bytes:
        """Consume PCM frames until end of turn is detected.

        Returns the completed utterance as raw int16 PCM bytes (concatenation of all
        frames up to EOT). A ``flux_integrated`` provider may return ``b''``
        immediately because the STT plugin already handles EOT.
        """
        ...


@runtime_checkable
class STTProvider(Protocol):
    """Speech-to-text (local or cloud)."""

    name: str
    supports_streaming: bool

    async def transcribe(self, audio: AsyncIterator[AudioChunk]) -> Transcript:
        """Full transcription after the input ends."""
        ...

    async def stream_transcribe(
        self, audio: AsyncIterator[AudioChunk]
    ) -> AsyncIterator[Transcript]:
        """Inkrementelle Transkription mit partials + final."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    """Text-to-speech (local or cloud)."""

    name: str
    supports_streaming: bool

    async def synthesize(
        self, text: str, voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Synthesise audio, yielding chunks for streaming playback.

        ``language_code`` (BCP-47, e.g. ``"de-DE"``) is the per-turn output
        language the pipeline resolves once via ``resolve_output_language`` and
        passes to every provider so a multilingual TTS model pins the
        pronunciation language instead of guessing it per word (the 2026-06-19
        "Juni, Boss" code-switch forensic). ``None`` means unpinned/auto-detect.
        Every shipped provider (gemini-flash, grok-voice, cartesia, elevenlabs,
        fallback) accepts it; the keyword is part of the structural contract.
        """
        ...

    def list_voices(self, language: str | None = None) -> list[str]:
        """Available voices (optionally filtered by language)."""
        ...


@runtime_checkable
class Brain(Protocol):
    """LLM-Provider (Claude, Gemini, OpenAI, OpenRouter, Grok, …)."""

    name: str
    context_window: int
    supports_tools: bool
    supports_vision: bool

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        """Stream a response. When stream=False everything is in the first delta."""
        ...

    def estimate_cost(self, req: BrainRequest) -> float:
        """Rough cost estimate in USD (0.0 for local models)."""
        ...


@runtime_checkable
class Harness(Protocol):
    """Sub-Agent-Framework (OpenClaw, Codex, Open Interpreter, MCP)."""

    name: str
    version: str
    supports_versions: str  # PEP 440 specifier, z.B. ">=2.0,<3.0"

    async def health(self) -> bool:
        """Check whether the harness is available and callable."""
        ...

    async def invoke(self, task: HarnessTask) -> AsyncIterator[HarnessResult]:
        """Start a task, streaming progress and the final result."""
        ...

    async def cancel(self) -> None:
        """Cancel the running task."""
        ...


@runtime_checkable
class Tool(Protocol):
    """Einzelne Jarvis-Action (open_app, type_text, search_web, …)."""

    name: str
    schema: dict[str, Any]  # JSON-Schema für LLM-Tool-Use
    description: str
    risk_tier: RiskTier
    # Optional extension (recognized by RiskTierEvaluator via getattr, NOT part
    # of the structural Protocol check): a tool with mixed actions may define
    #   def risk_tier_for_args(self, args: dict[str, Any]) -> RiskTier
    # to refine its tier per call (e.g. gmail: reads "safe", send "ask"). The
    # returned value is validated against the RiskTier vocabulary; blacklist and
    # whitelist still take priority. Returning an unknown value falls back to
    # the static ``risk_tier`` above.

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        """Execute the action."""
        ...


@runtime_checkable
class MemoryStore(Protocol):
    """Persistent-Memory-Layer (SQLite, ChromaDB, …)."""

    name: str

    async def put(self, namespace: str, key: str, value: dict[str, Any]) -> None:
        """Write or overwrite an entry."""
        ...

    async def get(self, namespace: str, key: str) -> dict[str, Any] | None:
        """Read an entry (None if not present)."""
        ...

    async def search(
        self, namespace: str, query: str, k: int = 5
    ) -> list[tuple[str, dict[str, Any], float]]:
        """Semantic or FTS search; returns (key, value, score) tuples."""
        ...

    async def forget(self, namespace: str, key: str) -> None:
        """Delete an entry."""
        ...


# ----------------------------------------------------------------------
# Channel Data-Types (forward-declared via string annotations in Protocol)
# ----------------------------------------------------------------------
# The concrete dataclasses `ChannelMessage` and `ChannelSession` live in
# `jarvis.channels.base` — the protocol references them via string forward refs
# so that `jarvis.core` does not need to import from the channels layer.


@runtime_checkable
class ChannelAdapter(Protocol):
    """Bidirectional message channel (web UI, Telegram, WhatsApp, …).

    See the Phase-1a spec (OpenClaw pattern).
    """

    name: str

    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def send_message(self, msg: ChannelMessage) -> None: ...  # noqa: F821
    async def broadcast_event(self, event: Event) -> None: ...  # noqa: F821
    async def messages(self) -> AsyncIterator[ChannelMessage]: ...  # noqa: F821
    async def sessions(self) -> list[ChannelSession]: ...  # noqa: F821


# ----------------------------------------------------------------------
# Plugin-Groups (für Registry/Discovery)
# ----------------------------------------------------------------------

PLUGIN_GROUPS: tuple[str, ...] = (
    "jarvis.wakeword",
    "jarvis.stt",
    "jarvis.tts",
    "jarvis.brain",
    "jarvis.harness",
    "jarvis.tool",
    "jarvis.channel",  # NEW
)


# ======================================================================
# Phase 5 — Vision / Control / Cost
# ======================================================================
# These protocols are new in Phase 5. They are infrastructure contracts (not
# plugin-discovered via entry_points) but are declared as runtime_checkable
# protocols so that tests can work with fakes against the interface rather than
# against concrete implementation types.


# ----------------------------------------------------------------------
# Vision Data-Types (Phase 5 — Capability 1)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class UIANode:
    """A single node from the UIAutomation tree (after pruning, see ADR-0002).

    Fields are intentionally short — the entire tree is serialised to the LLM,
    so every byte counts.
    """
    role: str                        # "Button", "Edit", "MenuItem", ...
    name: str                        # UIA Name-Property
    automation_id: str = ""          # AutomationId (stabiler als Name)
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h
    enabled: bool = True
    parent_index: int = -1           # Index in der flachen Nodes-Liste
    value: str = ""                  # L3: current text of an editable control
    is_password: bool = False        # secure/password edit -> redact, never read
    focused: bool = False            # holds keyboard focus (post-click verify)


@dataclass(frozen=True, slots=True)
class Observation:
    """Ergebnis eines Vision-Observe-Calls — enthält Screenshot-Referenz und
    gepruneden UIA-Tree. Eine Observation ist die Input-Einheit für den
    Plan-Observe-Act-Verify-Loop.
    """
    trace_id: UUID
    timestamp_ns: int
    screenshot_path: str | None       # Pfad zum Screenshot-Blob (PNG) oder None
    screenshot_hash: str              # SHA256 des PNG-Inhalts (für Cache)
    nodes: tuple[UIANode, ...] = field(default_factory=tuple)
    window_title: str = ""
    active_pid: int = 0
    source: Literal["full", "screenshot_only", "ui_tree_only"] = "full"
    pruning_stats: dict[str, int] = field(default_factory=dict)  # nodes_before/after, depth_used
    # (left, top, width, height) of the monitor this screenshot was ACTUALLY
    # captured from (virtual-desktop pixels). The click-coordinate resolution must
    # use THIS — not a separate GetForegroundWindow/MonitorFromWindow lookup that
    # can pick a different monitor on a mixed-DPI / multi-monitor desktop and make
    # every click miss (live 2026-06-28: 150% primary + 100% secondary). (0,0,0,0)
    # = unknown (older sources / non-screenshot observes) -> caller falls back.
    monitor_geom: tuple[int, int, int, int] = (0, 0, 0, 0)


@runtime_checkable
class VisionSource(Protocol):
    """Liefert Observations — entweder als reiner Screenshot, reiner UIA-Tree
    oder als Kombination. Der `VisionEngine` entscheidet pro Frage, welche
    Source billiger/robuster ist (Mandat Capability 1).
    """

    name: str
    kind: Literal["screenshot", "ui_tree", "composite"]

    async def observe(
        self,
        *,
        cancel_token: CancelToken | None = None,
        window_title_filter: str | None = None,
    ) -> Observation:
        """Fängt einen einzelnen Observation-Snapshot ab."""
        ...

    async def close(self) -> None:
        """Gibt Ressourcen frei (Handles, Memory-Caches)."""
        ...


# ----------------------------------------------------------------------
# CancelToken (Phase 5 — Capability 5, Kill-Switch-Propagation, ADR-0004)
# ----------------------------------------------------------------------

@runtime_checkable
class CancelToken(Protocol):
    """Propagiert Abbruch-Signale durch die Async-Hierarchie.

    Kein `asyncio.Event` direkt, weil wir zusätzlich `reason` und einen
    stabilen Polling-Pfad brauchen. Die konkrete Implementierung liegt in
    `jarvis.control.cancel`.
    """

    def cancel(self, reason: str) -> None: ...
    def is_cancelled(self) -> bool: ...

    @property
    def reason(self) -> str | None: ...

    async def wait_until_cancelled(self) -> None:
        """Blockiert, bis `cancel()` aufgerufen wurde."""
        ...


# ----------------------------------------------------------------------
# CostMeter (Phase 5 — Capability 5, Cost-Circuit-Breaker, ADR-0006)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CostRecord:
    """Ein atomarer Kosten-Eintrag für einen Brain-Delta."""
    trace_id: UUID
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cache_hit: int
    usd: float
    timestamp_ns: int


@runtime_checkable
class CostMeter(Protocol):
    """Akkumuliert Kosten pro trace_id und prüft gegen Task-/Tagesbudget.

    Wird vom `BrainManager` nach jedem `BrainDelta` mit Usage gefüttert.
    Bei Overrun wird via `CancelToken.cancel(reason=...)` gestoppt, siehe
    ADR-0006.
    """

    name: str

    def start(self, trace_id: UUID, provider: str, model: str) -> None: ...
    def add(self, record: CostRecord) -> None: ...
    def total_for(self, trace_id: UUID) -> float: ...
    def total_today(self) -> float: ...
    def over_task_budget(self, trace_id: UUID) -> bool: ...
    def over_daily_budget(self) -> bool: ...
    def close(self, trace_id: UUID) -> None: ...


# ----------------------------------------------------------------------
# Intent-Classification (Phase 5 — CL-3, Tiered Routing)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IntentClassification:
    """Ergebnis des RouterBrain-Klassifikators.

    Drei Routing-Klassen (siehe Plan §6 CL-3, Welle-4-Migration):
    - `trivial`         → Hauptjarvis antwortet direkt (Smalltalk, Bestätigung).
    - `direct_action`   → Hauptjarvis ruft ein First-Class-Tool auf (bash,
                          screenshot, multi_spawn).
    - `spawn_worker`  → Task geht verbatim an die OpenClaw-Bridge via
                          Mission-Manager. (vorher ``spawn_sub_jarvis``.)
    """
    intent: Literal["trivial", "direct_action", "spawn_worker"]
    confidence: float                       # 0.0–1.0
    suggested_tool: str | None = None       # für direct_action: "bash" | "screenshot" | ...
    rationale: str = ""                     # 1-Satz-Begründung fürs Debug-Log


@runtime_checkable
class IntentClassifier(Protocol):
    """Klassifiziert eine User-Utterance in eine der drei Routing-Klassen.

    Implementierungen liegen in `jarvis.brain.router` (RouterBrain nutzt
    Tool-Choice des Low-Latency-Modells als implizite Klassifikation) — das
    Protocol erlaubt alternative Fakes in Tests und spätere Heuristik-Varianten
    ohne Extra-LLM-Call.
    """

    name: str

    async def classify(
        self, utterance: str, *, ctx: ExecutionContext
    ) -> IntentClassification:
        """Klassifiziert `utterance` und liefert Intent + Konfidenz."""
        ...
