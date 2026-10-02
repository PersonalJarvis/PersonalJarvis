"""The Jarvis-owned local voice engine as a realtime provider (ADR-0037).

A thin adapter: the engine runs as one child process per app (started through
``jarvis.voice_engine.client``), and every live call is a session inside it.
``NativeLiveVoiceSession`` drives the session through the realtime contract
(``jarvis/realtime/protocol.py``); this module only translates between that
contract and the engine's wire protocol (``jarvis/voice_engine/protocol.py``).

Plugin rule: nothing from ``jarvis.*`` is imported at module import.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

log = logging.getLogger(__name__)

_INPUT_RATE = 16_000
_OUTPUT_RATE = 24_000
_READY_TIMEOUT_S = 180.0
_SELFTEST_TIMEOUT_S = 120.0
_RESTART_BACKOFF_S = (1.0, 5.0, 30.0)
#: Core load from a warm disk (plan section 7: core ready <= 15 s); scales the
#: "about N s" a refused call hears while the engine loads.
_WARM_LOAD_S = 15.0
#: Measured default for every machine class with an accelerator (plan 12.3).
DEFAULT_LLM = "qwen3.5:4b"
#: Core models the engine cannot start without (``jarvis.voice_engine.models``).
CORE_MODELS = ("silero-vad-v6", "smart-turn-v3.2", "parakeet-tdt-0.6b-v3-int8")
#: What a refused call hears and sees, per output language. ``native.py``
#: speaks ``duplex_unavailable_reason`` verbatim, so it must already be in the
#: caller's language; the card keeps the engine's own detailed reason.
_REFUSALS: dict[str, dict[str, str]] = {
    "not_set_up": {
        "en": "Local voice is not set up on this machine yet. Run its setup on the "
              "Local voice card in Settings.",
        "de": "Die lokale Stimme ist auf diesem Gerät noch nicht "  # i18n-allow
              "eingerichtet. Starte die Einrichtung in den Einstellungen "  # i18n-allow
              "auf der Karte Lokale Stimme.",  # i18n-allow
        "es": "La voz local aún no está configurada en este equipo. "  # i18n-allow
              "Inicia la configuración en la tarjeta Voz local de los "  # i18n-allow
              "Ajustes.",  # i18n-allow
    },
    "loading": {
        "en": "Local voice is still loading ({percent} %). Please try again in about "
              "{eta} seconds.",
        "de": "Die lokale Stimme lädt noch ({percent} %). Versuch es in "  # i18n-allow
              "etwa {eta} Sekunden noch einmal.",  # i18n-allow
        "es": "La voz local aún se está cargando ({percent} %). Vuelve a "  # i18n-allow
              "intentarlo en unos {eta} segundos.",  # i18n-allow
    },
    "failed": {
        "en": "Local voice could not start. The Local voice card in Settings shows why.",
        "de": "Die lokale Stimme konnte nicht starten. Die Karte Lokale "  # i18n-allow
              "Stimme in den Einstellungen zeigt den Grund.",  # i18n-allow
        "es": "La voz local no pudo arrancar. La tarjeta Voz local de los "  # i18n-allow
              "Ajustes muestra el motivo.",  # i18n-allow
    },
}
NOT_SET_UP_REASON = _REFUSALS["not_set_up"]["en"]


def refusal(kind: str, language: str, **values: object) -> str:
    """One refusal sentence in ``language`` (de/en/es; anything else is English)."""
    table = _REFUSALS[kind]
    return table.get(language, table["en"]).format(**values)


def _call_language(cfg: Any) -> str:
    """The language a call starts in, from the one authority (``turn_language``).

    A reply-language pin wins; else a pinned recognition language; else the
    app's interface language. Mirrors how the live session picks its first
    language, so a refusal is spoken in the language the call would have used.
    """
    try:
        from jarvis.core.turn_language import resolve_output_language  # noqa: PLC0415

        ui_language = str(getattr(getattr(cfg, "ui", None), "language", "") or "en")
        return resolve_output_language(
            getattr(getattr(cfg, "brain", None), "reply_language", "auto"),
            getattr(getattr(cfg, "stt", None), "language", "auto"),
            "",
            default=ui_language if ui_language in _REFUSALS["failed"] else "en",
        )
    except Exception:  # noqa: BLE001 - a refusal in English beats no refusal
        log.debug("local voice: call language unresolved; refusing in English", exc_info=True)
        return "en"


@dataclass(frozen=True, slots=True)
class _PcmChunk:
    pcm: bytes
    sample_rate: int
    timestamp_ns: int = 0
    channels: int = 1


@dataclass(frozen=True, slots=True)
class _Event:
    """Duck-typed ``RealtimeEvent`` (the plugin cannot import it at module import)."""

    type: str
    audio: _PcmChunk | None = None
    text: str | None = None
    is_final: bool = False
    shadow: bool = False
    voiced_ms: int = 0
    ms_played: int | None = None
    error: str | None = None
    recoverable: bool = False
    reconnect_advised: bool = False
    item_id: str | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    handoff_id: str | None = None
    provider_turn_id: str | None = None
    usage: dict[str, int] | None = None
    self_initiated: bool = False
    superseded: bool = False


@dataclass
class EngineSettings:
    python: str
    package_root: str | None
    home: str | None = None
    languages: list[str] = field(default_factory=lambda: ["de", "en"])
    tts: str = "pocket"
    tts_options: dict[str, Any] = field(default_factory=dict)
    llm_model: str = DEFAULT_LLM
    llm_base_url: str = "http://127.0.0.1:11434"

    @classmethod
    def from_config(cls, cfg: Any) -> EngineSettings:
        """Settings from ``[voice_engine]``, the last setup's record and defaults.

        ``cfg`` may be ``None`` (a provider built without a config): then the
        app's current configuration is read. The model is the card's explicit
        pick, else what setup chose (installed models first), else the measured
        default. No network call happens here; this runs on the call path.
        """
        if cfg is None:
            cfg = _current_config()
        section = getattr(cfg, "voice_engine", None)

        def pick(name: str, default: Any) -> Any:
            value = getattr(section, name, None) if section is not None else None
            return default if value in (None, "", [], {}) else value

        home = pick("home", os.environ.get("JARVIS_VOICE_ENGINE_HOME") or None)
        recorded = _setup_record(home)
        return cls(
            python=str(pick("python", os.environ.get("JARVIS_VOICE_ENGINE_PYTHON")
                            or _default_python(home))),
            package_root=pick("package_root", _package_root(home)),
            home=home,
            languages=list(pick("languages", ["de", "en"])),
            tts=str(pick("tts", "pocket")),
            tts_options=dict(pick("tts_options", {})),
            llm_model=str(pick("llm_model", recorded.get("llm_model") or DEFAULT_LLM)),
            llm_base_url=str(pick("llm_base_url", _ollama_root())),
        )

    def configure_message(self) -> dict[str, Any]:
        return {"type": "configure", "languages": self.languages, "tts": self.tts,
                "tts_options": self.tts_options,
                "llm": {"model": self.llm_model, "base_url": self.llm_base_url}}


def _engine_home(home: str | None) -> Path:
    from jarvis.voice_engine.paths import engine_home  # noqa: PLC0415 - plugin rule

    return Path(home) if home else engine_home()


def _package_root(home: str | None = None) -> str | None:
    """Directory the worker imports ``jarvis.voice_engine`` from.

    A source checkout runs the live source, so an edit needs no re-setup.
    A frozen build has no importable source tree; it runs the copy that
    setup placed under the engine home (plan section 4.10).
    """
    if not getattr(sys, "frozen", False):
        try:
            import jarvis  # noqa: PLC0415 - lazily, plugin rule

            root = Path(jarvis.__file__).resolve().parent.parent
            if (root / "jarvis" / "voice_engine" / "worker.py").is_file():
                return str(root)
        except (ImportError, AttributeError, TypeError):
            log.debug("local voice: no importable source tree", exc_info=True)
    copy = _engine_home(home) / "app"
    return str(copy) if (copy / "jarvis" / "voice_engine" / "worker.py").is_file() else None


def _default_python(home: str | None) -> str:
    from jarvis.voice_engine.paths import venv_python  # noqa: PLC0415 - plugin rule

    return str(venv_python(_engine_home(home)))


def _setup_record(home: str | None) -> dict[str, Any]:
    from jarvis.voice_engine.paths import read_setup_state  # noqa: PLC0415 - plugin rule

    return read_setup_state(_engine_home(home))


def _ollama_root() -> str:
    """The configured Ollama server, the one Jarvis itself starts and stops."""
    try:
        from jarvis.brain.ollama_pull import server_root  # noqa: PLC0415 - plugin rule

        return server_root()
    except Exception:  # noqa: BLE001 - an unreadable config keeps the vendor default
        log.debug("local voice: Ollama root unresolved; using the default", exc_info=True)
        return "http://127.0.0.1:11434"


def _current_config() -> Any:
    try:
        from jarvis.core.config import load_config  # noqa: PLC0415 - plugin rule

        return load_config()
    except Exception:  # noqa: BLE001 - defaults still describe a usable engine
        log.debug("local voice: config unreadable; using defaults", exc_info=True)
        return None


def engine_installed(settings: EngineSettings) -> bool:
    """The engine's Python exists and its core models are on disk."""
    if not Path(settings.python).is_file():
        return False
    try:
        from jarvis.voice_engine import models  # noqa: PLC0415 - plugin rule

        root = _engine_home(settings.home) / "models"
        return all(models.is_present(name, root) for name in CORE_MODELS)
    except (ImportError, OSError):
        # An unreadable model store is "not installed", never "ready".
        return False


def _worker_extra_env(home: Path) -> dict[str, str]:
    """Point the worker at the model cache setup filled (Pocket TTS weights)."""
    cache = home / "hf"
    return {"HF_HOME": str(cache)} if cache.is_dir() else {}


class _Engine:
    """The one worker process of this app, its state and the routing of its output."""

    def __init__(self, settings: EngineSettings) -> None:
        self.settings = settings
        self.phase = "stopped"
        self.stage = ""
        self.progress = 0.0
        self.reason = ""
        self.detail: dict[str, Any] = {}
        self._client: Any = None
        self._router: asyncio.Task[None] | None = None
        self._audio_router: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._start_lock = asyncio.Lock()
        self._sessions: dict[str, LocalVoiceSession] = {}
        self._slots: dict[int, LocalVoiceSession] = {}
        self._next_slot = 1
        self._failures = 0
        self._starting: asyncio.Task[None] | None = None
        self._selftests: list[asyncio.Future[dict[str, Any]]] = []

    async def ensure_started(self) -> None:
        async with self._start_lock:
            if self._client is not None:
                return
            from jarvis.voice_engine.client import EngineClient, worker_env  # noqa: PLC0415

            settings = self.settings
            home = _engine_home(settings.home)
            env = worker_env(
                package_root=Path(settings.package_root) if settings.package_root else None,
                home=home,
                extra=_worker_extra_env(home),
            )
            stderr = home / "worker.log"
            client = EngineClient(settings.python, env=env, stderr_path=stderr)
            self.phase, self.stage, self.reason = "starting", "process", ""
            self._ready.clear()
            await client.start()
            self._client = client
            self._router = asyncio.get_running_loop().create_task(self._route_messages(client))
            self._audio_router = asyncio.get_running_loop().create_task(self._route_audio(client))
            await client.send(settings.configure_message())

    def start_soon(self) -> None:
        """Start the worker in the background; a caller never waits for it."""
        if self._client is not None or (self._starting and not self._starting.done()):
            return
        self._starting = asyncio.get_running_loop().create_task(self._start_logged())

    async def _start_logged(self) -> None:
        try:
            await self.ensure_started()
        except (OSError, RuntimeError, TimeoutError) as exc:
            log.warning("local voice engine did not start: %s", exc)
            self.phase = "failed"
            self.reason = f"The local voice engine did not start: {exc}"
            self._ready.set()

    def reset_failures(self) -> None:
        """A user-started retry (setup, self-test) clears the crash budget."""
        self._failures = 0
        if self.phase == "failed" and self._client is None:
            self.phase, self.reason = "stopped", ""

    async def selftest(self, timeout_s: float = _SELFTEST_TIMEOUT_S) -> dict[str, Any]:
        """Run the worker's self-test: speak, hear and answer once per language."""
        if self._client is None or self.phase != "ready":
            raise RuntimeError(self.reason or "The local voice is not ready.")
        waiter: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._selftests.append(waiter)
        try:
            await self._client.send({"type": "selftest"})
            return await asyncio.wait_for(waiter, timeout_s)
        finally:
            with contextlib.suppress(ValueError):
                self._selftests.remove(waiter)

    async def wait_ready(self, timeout_s: float) -> bool:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout_s)
        except TimeoutError:
            return False
        return self.phase == "ready"

    async def _route_messages(self, client: Any) -> None:
        while True:
            message = await client.messages.get()
            kind = message.get("type")
            if kind == "_exited":
                await self._on_exit()
                return
            if kind == "selftest.result":
                for waiter in list(self._selftests):
                    if not waiter.done():
                        waiter.set_result(dict(message))
                continue
            if kind == "state":
                self.phase = str(message.get("phase", self.phase))
                self.stage = str(message.get("stage", ""))
                self.progress = float(message.get("progress") or 0.0)
                self.reason = str(message.get("reason", ""))
                if message.get("detail"):
                    self.detail = dict(message["detail"])
                if self.phase in ("ready", "failed"):
                    self._failures = 0 if self.phase == "ready" else self._failures
                    self._ready.set()
                continue
            session = self._sessions.get(str(message.get("session", "")))
            if session is not None:
                session._deliver(message)
            elif kind == "error":
                log.warning("local voice engine: %s", message.get("message"))

    async def _route_audio(self, client: Any) -> None:
        while True:
            frame = await client.audio_frames.get()
            session = self._slots.get(frame.slot)
            if session is not None:
                session._deliver_audio(frame.pcm)

    async def _on_exit(self) -> None:
        log.warning("local voice engine exited (phase %s)", self.phase)
        for session in list(self._sessions.values()):
            session._deliver({"type": "_engine_exited"})
        self._sessions.clear()
        self._slots.clear()
        self._client = None
        self._failures += 1
        self.phase = "failed" if self._failures >= len(_RESTART_BACKOFF_S) else "stopped"
        self.reason = self.reason or "The local voice engine stopped."
        for waiter in self._selftests:
            if not waiter.done():
                waiter.set_exception(RuntimeError(self.reason))
        self._ready.set()

    async def open(self, provider: LocalVoiceProvider, cfg: Any) -> LocalVoiceSession:
        await self.ensure_started()
        if not await self.wait_ready(_READY_TIMEOUT_S) or self._client is None:
            raise RuntimeError(self.reason or "The local voice is not ready.")
        slot = self._next_slot
        self._next_slot = self._next_slot % 60000 + 1
        session = LocalVoiceSession(self, slot=slot, language=getattr(cfg, "language", "en"))
        self._sessions[session.session_id] = session
        self._slots[slot] = session
        await self._client.send({
            "type": "session.open", "session": session.session_id, "slot": slot,
            "language": session.language, "instructions": getattr(cfg, "instructions", ""),
            "tools": list(getattr(cfg, "tools", ()) or ()),
            "history": list(getattr(cfg, "history", ()) or ()),
            "turn_pause_ms": getattr(cfg, "turn_pause_ms", None),
        })
        return session

    async def send(self, message: dict[str, Any]) -> None:
        if self._client is None:
            raise ConnectionError("the local voice engine is not running")
        await self._client.send(message)

    async def send_audio(self, slot: int, seq: int, pcm: bytes) -> None:
        if self._client is None:
            raise ConnectionError("the local voice engine is not running")
        await self._client.send_audio(slot, seq, pcm)

    def forget(self, session: LocalVoiceSession) -> None:
        self._sessions.pop(session.session_id, None)
        self._slots.pop(session.slot, None)

    async def stop(self) -> None:
        client, self._client = self._client, None
        for task in (self._router, self._audio_router):
            if task is not None:
                task.cancel()
        if client is not None:
            await client.close()
        self.phase = "stopped"


class LocalVoiceSession:
    """One live call inside the engine, seen through the realtime contract."""

    creates_responses_automatically = False
    isolates_response_generations = True
    supports_tool_results = True
    supports_direct_tools = True

    def __init__(self, engine: _Engine, *, slot: int, language: str) -> None:
        import uuid  # noqa: PLC0415

        self._engine = engine
        self.slot = slot
        self.language = language
        self.session_id = f"lv-{uuid.uuid4().hex[:12]}"
        self.model = engine.settings.llm_model
        self._events: asyncio.Queue[_Event | None] = asyncio.Queue()
        self._seq = 0
        self._closed = False

    # engine -> app
    def _deliver(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "speech_started":
            self._events.put_nowait(_Event(type="speech_started"))
        elif kind == "transcript.input":
            self._events.put_nowait(_Event(
                type="input_transcript", text=str(message.get("text", "")),
                is_final=bool(message.get("final")), voiced_ms=int(message.get("voiced_ms") or 0),
            ))
        elif kind == "transcript.output":
            self._events.put_nowait(_Event(type="output_transcript_delta",
                                           text=str(message.get("delta", ""))))
        elif kind == "tool.call":
            args = message.get("arguments")
            self._events.put_nowait(_Event(type="tool_call", call_id=str(message.get("call_id")),
                                           tool_name=str(message.get("name")),
                                           tool_args=args if isinstance(args, dict) else {}))
        elif kind == "interrupted":
            self._events.put_nowait(_Event(type="interrupted",
                                           self_initiated=bool(message.get("self_initiated"))))
        elif kind == "response.done":
            self._events.put_nowait(_Event(type="turn_complete"))
        elif kind == "error":
            self._events.put_nowait(_Event(type="error", error=str(message.get("message", "")),
                                           recoverable=bool(message.get("recoverable"))))
        elif kind == "_engine_exited":
            self._events.put_nowait(_Event(type="error", error="The local voice engine stopped.",
                                           recoverable=False))
            self._events.put_nowait(None)

    def _deliver_audio(self, pcm: bytes) -> None:
        self._events.put_nowait(_Event(type="audio_delta", audio=_PcmChunk(
            pcm=pcm, sample_rate=_OUTPUT_RATE, timestamp_ns=time.monotonic_ns())))

    async def receive(self) -> AsyncIterator[_Event]:
        while True:
            event = await self._events.get()
            if event is None:
                return
            yield event

    # app -> engine
    async def send_audio(self, chunk: Any) -> None:
        if self._closed:
            return
        if int(getattr(chunk, "sample_rate", _INPUT_RATE)) != _INPUT_RATE:
            raise ValueError("the local voice engine takes 16 kHz audio")
        self._seq += 1
        await self._engine.send_audio(self.slot, self._seq, bytes(chunk.pcm))

    async def request_response(self, *, required_tool: str | None = None) -> None:
        del required_tool  # the engine has no forced-tool mode
        await self._engine.send({"type": "response.request", "session": self.session_id,
                                 "language": self.language})

    async def update_session(self, *, instructions: str | None = None,
                             language: str | None = None,
                             tools: tuple[dict[str, Any], ...] | None = None,
                             turn_directive: str | None = None,
                             standing_directive: str | None = None) -> None:
        if language:
            self.language = language
        text = None
        if instructions is not None or turn_directive or standing_directive:
            text = "\n\n".join(p for p in (instructions, standing_directive, turn_directive) if p)
        await self._engine.send({"type": "session.update", "session": self.session_id,
                                 "instructions": text, "language": language,
                                 "tools": list(tools) if tools is not None else None})

    async def send_text(self, text: str) -> None:
        await self._engine.send({"type": "text", "session": self.session_id, "content": text})

    async def truncate(self, audio_end_ms: int) -> None:
        await self._engine.send({"type": "truncate", "session": self.session_id,
                                 "audio_end_ms": int(audio_end_ms)})

    async def interrupt(self, *, retire_input_entitlement: bool = False) -> None:
        del retire_input_entitlement  # one response per request; nothing to retire
        await self._engine.send({"type": "interrupt", "session": self.session_id})

    async def send_tool_result(self, call_id: str, name: str, result: dict[str, Any]) -> None:
        del name
        await self._engine.send({"type": "tool.result", "session": self.session_id,
                                 "call_id": call_id, "result": result})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(ConnectionError, OSError, RuntimeError):
            await self._engine.send({"type": "session.close", "session": self.session_id})
        self._engine.forget(self)
        self._events.put_nowait(None)


class LocalVoiceProvider:
    """Entry point class: keyless, local, native tool orchestration, browser audio."""

    name = "local-voice"
    supports_realtime = True
    native_tool_orchestration = True
    browser_audio = True
    implicit_usage_fallback_allowed = False
    eager_warm_as_fallback = False
    credential_candidates: tuple[tuple[str, str | None], ...] = ()
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE
    handshake_budget_s = 5.0
    # Plan 4.6: a small local model gets a curated direct tool set within ~2K
    # tokens; everything else goes through discover_tools/call_tool.
    tool_declaration_budget_tokens = 2_000

    _engine: ClassVar[_Engine | None] = None

    def __init__(self, settings: EngineSettings | None = None, *, language: str = "en") -> None:
        self._settings = settings
        self.duplex_unavailable_reason = ""
        #: Language of the refusal sentences (de/en/es); see ``_call_language``.
        self.language = language

    @classmethod
    def from_runtime_config(cls, cfg: Any) -> LocalVoiceProvider:
        return cls(EngineSettings.from_config(cfg), language=_call_language(cfg))

    @classmethod
    def external_login_ready(cls, cfg: Any = None) -> bool:
        """Installed means: the engine's Python exists and the core models are on disk."""
        return engine_installed(EngineSettings.from_config(cfg))

    @classmethod
    def _shared(cls, settings: EngineSettings) -> _Engine:
        previous = cls._engine
        if previous is None or previous.settings != settings:
            cls._engine = _Engine(settings)
            if previous is not None and previous._client is not None:
                # Changed settings (a new voice or model) start a new worker;
                # the old one must not linger with its models loaded.
                try:
                    asyncio.get_running_loop().create_task(previous.stop())
                except RuntimeError:
                    log.warning("local voice: a replaced engine could not be stopped "
                                "outside an event loop; it exits with the app")
        return cls._engine

    @classmethod
    def shared_engine(cls, cfg: Any = None) -> _Engine:
        """The engine this app's calls use, for the card's status and self-test."""
        return cls._shared(EngineSettings.from_config(cfg))

    @classmethod
    async def verify_activation(cls, cfg: Any) -> None:
        """Selecting the card starts the engine, so the first call finds it loaded.

        Never refuses: the card must stay selectable before setup, because
        setup runs from that card. Without an installed engine nothing starts.
        """
        if cls.external_login_ready(cfg):
            cls._shared(EngineSettings.from_config(cfg)).start_soon()

    @classmethod
    async def prespawn_transport(cls, cfg: Any) -> bool:
        if not cls.external_login_ready(cfg):
            return False
        await cls._shared(EngineSettings.from_config(cfg)).ensure_started()
        return True

    @classmethod
    async def warm_transport(cls, cfg: Any) -> bool:
        if not cls.external_login_ready(cfg):
            return False
        engine = cls._shared(EngineSettings.from_config(cfg))
        await engine.ensure_started()
        return await engine.wait_ready(_READY_TIMEOUT_S)

    async def can_open_duplex_session(self) -> bool:
        """Answer at once from the worker's state; never wait for it to load.

        A call during warm-up hears why and roughly how long within a second
        (plan section 4.9); the start itself runs in the background.
        """
        settings = self._settings or EngineSettings.from_config(None)
        engine = self._shared(settings)
        if engine.phase == "ready" and engine._client is not None:
            self.duplex_unavailable_reason = ""
            return True
        if engine._client is None:
            if not engine_installed(settings):
                self.duplex_unavailable_reason = refusal("not_set_up", self.language)
                return False
            if engine.phase != "failed":
                engine.start_soon()
        if engine.phase == "failed":
            log.warning("local voice refused a call: %s", engine.reason or "engine failed")
            self.duplex_unavailable_reason = refusal("failed", self.language)
        else:
            eta_s = max(1, round((1.0 - engine.progress) * _WARM_LOAD_S))
            self.duplex_unavailable_reason = refusal(
                "loading", self.language, percent=int(engine.progress * 100), eta=eta_s
            )
        return False

    async def open_session(self, cfg: Any) -> LocalVoiceSession:
        settings = self._settings or EngineSettings.from_config(None)
        return await self._shared(settings).open(self, cfg)
