"""Managed llama.cpp ``llama-server`` — the keyless local brain.

Jarvis must answer with no API key at all. The ``local-openai`` card already
speaks to any OpenAI-compatible server; what was missing is a server nobody
has to start by hand. This module owns exactly that:

* **Discovery** — a ``llama-server`` binary under ``<user_data_dir>/llama/bin``
  (what ``scripts/install_local_brain.py`` downloads) or on ``PATH``, plus the
  ``*.gguf`` files in ``<user_data_dir>/llama/models``. Missing either one is
  not an error: the feature is simply absent (capability probe, never a name).
* **Router mode** — the server runs with ``--models-preset`` and
  ``--models-max 1``: every GGUF appears in ``/v1/models`` (so the card's model
  picker switches models from the UI) and at most ONE model is resident, which
  is the rule for a 4 GB card.
* **VRAM-aware offload** — per model, full GPU offload when the free VRAM holds
  weights + context + margin, otherwise ``--fit`` lets llama.cpp keep what fits
  on the GPU and the rest in RAM/CPU. A warm-up that fails (OOM) steps down
  full -> fit -> CPU and restarts, so a small card degrades instead of dying.
* **Wiring** — once healthy, ``[brain.providers."local-openai"].base_url`` is
  pinned to the served port through ``config_writer`` (AP-7), which also makes
  the brain manager's last-resort local stage live.

Nothing here runs on the boot critical path (AP-26): :func:`schedule_boot`
starts a background task. The engine lives in its own process, so no native
inference state is shared with any other caller (AP-24).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

PROVIDER_ID = "local-openai"
DEFAULT_PORT = 18181
#: 32K, not 8K: Jarvis's own system prompt plus tool surface measured ~21.7K
#: tokens live (2026-09-18), so an 8K server refused every turn. It matches the
#: 32K the ``local-openai`` brain declares, which the manager's tool fitter uses.
DEFAULT_CTX = 32768
BOOT_DELAY_S = 4.0
HEALTH_TIMEOUT_S = 40.0
WARM_TIMEOUT_S = 180.0

#: Measured on an RTX 3050 4 GB with a 2.6 GB Q4_K_M 4B model at 32K context,
#: q8_0 KV cache, one slot: resident usage was weights + ~880 MB (KV cache +
#: compute buffers). The margin keeps room for the desktop compositor.
_RUNTIME_OVERHEAD_MB = 900
_SAFETY_MARGIN_MB = 250

#: Offload tiers, strongest first. ``full`` = every layer on the GPU,
#: ``fit`` = llama.cpp's own fitter keeps what fits, ``cpu`` = no GPU at all.
OFFLOAD_TIERS: tuple[str, ...] = ("full", "fit", "cpu")

_CACHE_RAM_MB = 1536

#: Seconds after the server is ready before the prompt-cache prefill runs:
#: the app's own boot (voice models, the 3D scene) competes for the GPU for
#: about a minute, and a prefill during that window runs 5x slower.
PREWARM_DELAY_S = 45.0


def llama_home() -> Path:
    return user_data_dir() / "llama"


def models_dir() -> Path:
    return llama_home() / "models"


def find_binary() -> Path | None:
    """The ``llama-server`` executable, managed install first, then ``PATH``."""
    exe = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    managed = llama_home() / "bin" / exe
    if managed.is_file():
        return managed
    found = shutil.which("llama-server")
    return Path(found) if found else None


def list_models(directory: Path | None = None) -> list[Path]:
    """Chat GGUFs, largest last. Vision projectors (``mmproj*``) are not chat models."""
    root = directory or models_dir()
    if not root.is_dir():
        return []
    files = [
        p
        for p in root.glob("*.gguf")
        if p.is_file() and not p.name.lower().startswith("mmproj")
    ]
    return sorted(files, key=lambda p: (p.stat().st_size, p.name))


def free_vram_mb() -> int | None:
    """Free memory of the first NVIDIA GPU, or None when there is no probe-able GPU."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=NO_WINDOW_CREATIONFLAGS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.info("llama-server: nvidia-smi probe failed (%s) — assuming no GPU.", exc)
        return None
    first = (out.stdout or "").strip().splitlines()
    try:
        return int(first[0].strip()) if first else None
    except ValueError:
        return None


def _settled_free_vram_mb(model_bytes: int, *, samples: int = 3, gap_s: float = 1.0) -> int | None:
    """Free VRAM, re-sampled while it reads too low for a full offload.

    A process that just exited (the previous server on an app restart) hands
    its memory back to the driver a moment later; one early reading put a
    model that fits on the ``fit`` tier (live 2026-09-18: 3325 MB read, 3880 MB
    a second later). The best of a few readings is what is actually free.
    """
    best = free_vram_mb()
    for _ in range(samples - 1):
        if best is None or choose_tier(model_bytes, best) == "full":
            break
        time.sleep(gap_s)
        again = free_vram_mb()
        if again is not None:
            best = max(best, again)
    return best


def choose_tier(model_bytes: int, free_mb: int | None) -> str:
    """The strongest offload tier this model can use with ``free_mb`` of VRAM."""
    if free_mb is None:
        return "cpu"
    need = model_bytes // (1024 * 1024) + _RUNTIME_OVERHEAD_MB + _SAFETY_MARGIN_MB
    if free_mb >= need:
        return "full"
    # Less than ~1 GB free makes partial offload slower than plain CPU.
    return "fit" if free_mb >= 1024 else "cpu"


def _tier_lines(tier: str) -> list[str]:
    if tier == "full":
        return ["n-gpu-layers = 99"]
    if tier == "fit":
        return ["fit = on", f"fit-target = {_SAFETY_MARGIN_MB + 262}"]
    return ["n-gpu-layers = 0"]


def render_presets(models: list[Path], tiers: dict[str, str], ctx: int = DEFAULT_CTX) -> str:
    """The ``--models-preset`` INI: one section per model, id = file stem."""
    blocks: list[str] = []
    for path in models:
        lines = [
            f"[{path.stem}]",
            f"model = {path}",
            f"ctx-size = {ctx}",
            "flash-attn = on",
            # One slot owns the whole window; a quantized KV cache halves it.
            "parallel = 1",
            "cache-type-k = q8_0",
            "cache-type-v = q8_0",
            # Host-RAM prompt cache: lets a side call (title, summary) run
            # without evicting the main turn prefix. The default is 8 GB,
            # half of a 16 GB machine; 1.5 GB holds several 32K prefixes.
            f"cache-ram = {_CACHE_RAM_MB}",
            # Hybrid models (Qwen3.5's recurrent layers) cannot rewind to an
            # arbitrary position, only to a checkpoint. The default spacing
            # (8192) made any mid-prompt divergence a full re-prefill.
            "checkpoint-min-step = 1024",
            # Voice turns want the answer, not a visible chain of thought.
            "reasoning = off",
            *_tier_lines(tiers.get(path.stem, "cpu")),
        ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - loopback only
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> Any:
    req = urllib.request.Request(  # noqa: S310 - loopback only
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class ServerState:
    base_url: str
    port: int
    models: list[str]
    tiers: dict[str, str]


class LlamaServer:
    """One managed ``llama-server`` router process."""

    def __init__(self, *, port: int = DEFAULT_PORT, ctx: int = DEFAULT_CTX) -> None:
        self._port = port
        self._ctx = ctx
        self._proc: subprocess.Popen[bytes] | None = None
        self._log_handle: Any = None
        self._tiers: dict[str, str] = {}
        self._adopted = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def is_running(self) -> bool:
        if self._adopted:
            # Not our child: the only signal we have is the server answering.
            return self._healthy()
        return self._proc is not None and self._proc.poll() is None

    def _healthy(self) -> bool:
        try:
            return str(_get_json(f"{self.base_url}/health", 2.0).get("status")) == "ok"
        except (OSError, ValueError, urllib.error.URLError):
            return False

    def _spawn(self, binary: Path, models: list[Path]) -> None:
        home = llama_home()
        preset = home / "presets.ini"
        preset.write_text(render_presets(models, self._tiers, self._ctx), encoding="utf-8")
        log_path = home / "llama-server.log"
        self._log_handle = open(log_path, "ab")  # noqa: SIM115 - lives as long as the process
        cmd = [
            str(binary),
            "--host", "127.0.0.1",
            "--port", str(self._port),
            "--models-preset", str(preset),
            "--models-max", "1",
        ]
        log.info("llama-server: starting %s (tiers %s)", " ".join(cmd), self._tiers)
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, managed binary
            cmd,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(binary.parent),
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )

    def _wait_healthy(self) -> bool:
        deadline = time.monotonic() + HEALTH_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if self._healthy():
                return True
            time.sleep(0.5)
        return False

    def _warm(self, model_id: str) -> bool:
        """Load ``model_id`` with a one-token turn — the real OOM test."""
        try:
            _post_json(
                f"{self.base_url}/v1/chat/completions",
                {
                    "model": model_id,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                },
                WARM_TIMEOUT_S,
            )
            return True
        except (OSError, ValueError, urllib.error.URLError) as exc:
            log.warning("llama-server: warm-up of %s failed: %s", model_id, exc)
            return False

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._log_handle is not None:
            with contextlib.suppress(OSError):
                self._log_handle.close()
            self._log_handle = None

    def start(self, *, warm_model: str | None = None) -> ServerState | None:
        """Start (or adopt) the server and warm one model. Blocking; call off-loop."""
        binary = find_binary()
        models = list_models()
        if binary is None or not models:
            log.info(
                "llama-server: not installed (binary=%s, models=%d) — local brain absent.",
                binary, len(models),
            )
            return None
        if self.is_running() and self._healthy():
            return self._state(models)
        if not _port_free(self._port):
            if self._healthy():
                # A server from a previous run of this app still owns the port.
                log.info("llama-server: adopting the server already on %s", self.base_url)
                self._adopted = True
                return self._state(models)
            self._port = _pick_free_port()
        self._adopted = False
        free = _settled_free_vram_mb(max(p.stat().st_size for p in models))
        self._tiers = {p.stem: choose_tier(p.stat().st_size, free) for p in models}
        target = warm_model if warm_model in self._tiers else models[0].stem
        for _attempt in range(len(OFFLOAD_TIERS)):
            self._spawn(binary, models)
            if not self._wait_healthy():
                log.warning("llama-server: did not become healthy; see llama-server.log")
                self.stop()
                return None
            if self._warm(target):
                log.info(
                    "llama-server: ready on %s — %s on tier %s (free VRAM %s MB)",
                    self.base_url, target, self._tiers[target], free,
                )
                return self._state(models)
            current = self._tiers[target]
            if current == OFFLOAD_TIERS[-1]:
                break
            self._tiers[target] = OFFLOAD_TIERS[OFFLOAD_TIERS.index(current) + 1]
            log.warning(
                "llama-server: stepping %s down from %s to %s and restarting",
                target, current, self._tiers[target],
            )
            self.stop()
        self.stop()
        return None

    def _state(self, models: list[Path]) -> ServerState:
        return ServerState(
            base_url=self.base_url,
            port=self._port,
            models=[p.stem for p in models],
            tiers=dict(self._tiers),
        )


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


_server: LlamaServer | None = None


def server() -> LlamaServer:
    global _server
    if _server is None:
        _server = LlamaServer()
    return _server


def _configured_model(cfg: Any) -> str | None:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    block = providers.get(PROVIDER_ID) if isinstance(providers, dict) else None
    model = str(getattr(block, "model", "") or "").strip() if block is not None else ""
    return model or None


def _configured_base_url(cfg: Any) -> str:
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    block = providers.get(PROVIDER_ID) if isinstance(providers, dict) else None
    return str(getattr(block, "base_url", "") or "").strip().rstrip("/")


def _wire(cfg: Any, state: ServerState) -> None:
    """Pin the card's base URL to the served port (only when it differs)."""
    current = _configured_base_url(cfg)
    if current and current != state.base_url and not current.startswith(
        ("http://127.0.0.1", "http://localhost")
    ):
        # The user points the card at a remote server — theirs, not ours.
        log.info("llama-server: card points at %s — leaving it untouched.", current)
        return
    if current == state.base_url:
        return
    from jarvis.core.config_writer import set_provider_base_url

    set_provider_base_url(PROVIDER_ID, state.base_url)
    from jarvis.core.config import clear_config_cache

    clear_config_cache()
    log.info("llama-server: %s base_url pinned to %s", PROVIDER_ID, state.base_url)


def enabled(cfg: Any) -> bool:
    """Opt-out switch: ``[brain.providers."local-openai"].managed_server = false``."""
    providers = getattr(getattr(cfg, "brain", None), "providers", None) or {}
    block = providers.get(PROVIDER_ID) if isinstance(providers, dict) else None
    extra = getattr(block, "model_extra", None) or {}
    return bool(extra.get("managed_server", True))


async def ensure_started(cfg: Any) -> ServerState | None:
    if not enabled(cfg):
        log.info("llama-server: managed server disabled in config.")
        return None
    srv = server()
    state = await asyncio.to_thread(srv.start, warm_model=_configured_model(cfg))
    if state is not None:
        await asyncio.to_thread(_wire, cfg, state)
    return state


_tasks: set[asyncio.Task[None]] = set()

WATCH_INTERVAL_S = 15.0
MAX_RESTARTS = 5


async def _watch(cfg_fn: Any) -> None:
    """Restart the server when its process dies; give up after MAX_RESTARTS.

    Only the process handle is polled — no socket per tick. Each restart waits
    longer than the last, so a model that crashes on load cannot spin.
    """
    restarts = 0
    while restarts < MAX_RESTARTS:
        await asyncio.sleep(WATCH_INTERVAL_S)
        srv = server()
        if srv.is_running():
            continue
        restarts += 1
        await asyncio.sleep(WATCH_INTERVAL_S * restarts)
        log.warning("llama-server: process gone — restart %d/%d", restarts, MAX_RESTARTS)
        try:
            await ensure_started(cfg_fn())
        except Exception:  # noqa: BLE001 - logged; the next tick retries
            log.warning("llama-server: restart failed", exc_info=True)
    log.error("llama-server: gave up after %d restarts; see llama-server.log", MAX_RESTARTS)


def schedule_boot(
    cfg_fn: Any,
    *,
    delay_s: float = BOOT_DELAY_S,
    on_ready: Any = None,
) -> asyncio.Task[None]:
    """Boot hook: start the managed server in the background. Never raises.

    ``on_ready`` is an optional coroutine function run once, PREWARM_DELAY_S
    after the server is healthy (the prompt-cache prefill).
    """

    async def _boot() -> None:
        await asyncio.sleep(delay_s)
        try:
            state = await ensure_started(cfg_fn())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a missing local brain is a log line, not a boot error
            log.warning("llama-server: boot start failed", exc_info=True)
            return
        if state is None:
            return
        if on_ready is not None:
            await asyncio.sleep(PREWARM_DELAY_S)
            try:
                await on_ready()
            except Exception:  # noqa: BLE001 - an optimisation; the watchdog still runs
                log.info("llama-server: on-ready hook failed", exc_info=True)
        await _watch(cfg_fn)

    task = asyncio.create_task(_boot(), name="llama-server-boot")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


def shutdown() -> None:
    if _server is not None:
        _server.stop()


__all__ = [
    "OFFLOAD_TIERS",
    "PROVIDER_ID",
    "LlamaServer",
    "ServerState",
    "choose_tier",
    "enabled",
    "ensure_started",
    "find_binary",
    "free_vram_mb",
    "list_models",
    "llama_home",
    "render_presets",
    "schedule_boot",
    "server",
    "shutdown",
]
