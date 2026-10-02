"""A fake machine for the local voice setup (``jarvis/realtime/local_voice_setup.py``).

Records every command, download and pull, and leaves the same files behind the
real steps would (the venv interpreter, the model directories), so setup and
the status read can be driven end to end in a temporary engine home without
uv, the network or Ollama.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.realtime.local_voice_setup import SetupDeps, SetupError
from jarvis.voice_engine import models
from jarvis.voice_engine.paths import venv_python


@dataclass
class FakeSetupWorld:
    home: Path
    machine_class: str = "nvidia"
    installed: set[str] = field(default_factory=set)
    ollama_running: bool = True
    ollama_installed: bool = True
    configured_llm: str = ""
    voice: str = "pocket"
    languages: list[str] = field(default_factory=lambda: ["de", "en"])
    fail_command: str = ""
    #: Non-empty: this machine cannot run the engine (see ``unsupported_reason``).
    blocked: str = ""
    selftest_report: dict[str, Any] | None = field(default_factory=lambda: {"ok": True})
    commands: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str]] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    ollama_starts: int = 0

    def ensure_uv(self, root: Path) -> str:
        root.mkdir(parents=True, exist_ok=True)
        return str(root / "uv")

    def run(self, cmd: list[str], env: dict[str, str], timeout: int) -> None:
        del timeout
        self.commands.append(list(cmd))
        self.envs.append(dict(env))
        if self.fail_command and self.fail_command in " ".join(cmd):
            raise SetupError(f"fake failure in {self.fail_command}")
        if cmd[1:2] == ["venv"]:
            python = venv_python(self.home)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")

    def fetch_model(self, name: str, root: Path,
                    progress: Callable[[str, int, int], None]) -> None:
        self.fetched.append(name)
        progress(name, 1, 2)
        path = models.model_path(name, root)
        if models.REGISTRY[name].is_archive:
            path.mkdir(parents=True, exist_ok=True)
            (path / "model.onnx").write_bytes(b"x")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        progress(name, 2, 2)

    def installed_llms(self) -> tuple[set[str], str | None]:
        if not self.ollama_running:
            return set(), "Ollama did not answer."
        return set(self.installed), None

    def start_ollama(self) -> tuple[bool, str]:
        self.ollama_starts += 1
        if not self.ollama_installed:
            return False, "Ollama is not installed — install it first."
        self.ollama_running = True
        return True, "started"

    def pull_llm(self, model: str, progress: Callable[[float], None]) -> None:
        self.pulled.append(model)
        progress(50.0)
        self.installed.add(model)

    def selftest(self) -> dict[str, Any]:
        return dict(self.selftest_report or {})

    def deps(self, package_source: Path) -> SetupDeps:
        return SetupDeps(
            home=self.home,
            ensure_uv=self.ensure_uv,
            run=self.run,
            fetch_model=self.fetch_model,
            installed_llms=self.installed_llms,
            start_ollama=self.start_ollama,
            pull_llm=self.pull_llm,
            machine=lambda: self.machine_class,
            configured_llm=lambda: self.configured_llm,
            configured_voice=lambda: self.voice,
            languages=lambda: list(self.languages),
            selftest=self.selftest if self.selftest_report is not None else None,
            unsupported=lambda: self.blocked,
            package_source=package_source,
        )
