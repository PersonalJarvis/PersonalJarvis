"""Local voice setup: the steps, the installed-first model choice, the card status."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.core.config import JarvisConfig, VoiceEngineConfig
from jarvis.plugins.realtime.local_voice import LocalVoiceProvider
from jarvis.realtime import local_voice_setup as setup
from jarvis.voice_engine.paths import read_setup_state, venv_python
from tests.fakes.fake_voice_engine_setup import FakeSetupWorld

PACKAGE = Path(setup.__file__).resolve().parent.parent / "voice_engine"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "engine"
    monkeypatch.setenv("JARVIS_VOICE_ENGINE_HOME", str(home))
    setup._reset_for_tests()
    LocalVoiceProvider._engine = None
    yield home
    setup._reset_for_tests()
    LocalVoiceProvider._engine = None


def _world(home: Path, **kwargs) -> FakeSetupWorld:
    return FakeSetupWorld(home=home, **kwargs)


def test_setup_builds_the_environment_and_records_the_choice(_isolated_home: Path) -> None:
    world = _world(_isolated_home, installed={"qwen3.5:4b"})
    setup.run_setup_blocking(world.deps(PACKAGE))

    snapshot = setup.setup_snapshot()
    assert snapshot["error"] == "" and snapshot["running"] is False
    assert snapshot["progress"] == pytest.approx(1.0)
    venv, install, prefetch = world.commands
    assert venv[1:4] == ["venv", "--python", "3.12"]
    assert install[1:3] == ["pip", "install"]
    assert ["--torch-backend", "cpu"] == install[install.index("--torch-backend"):][:2]
    assert install[-1].endswith("requirements-engine.txt")
    assert prefetch[0] == str(venv_python(_isolated_home)) and prefetch[-2:] == ["de", "en"]
    # uv never installs into the app's own environment and keeps its Python home-local.
    assert "VIRTUAL_ENV" not in world.envs[1]
    assert world.envs[0]["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert world.envs[2]["HF_HOME"] == str(_isolated_home / "hf")
    assert world.fetched[:3] == ["silero-vad-v6", "smart-turn-v3.2", "parakeet-tdt-0.6b-v3-int8"]
    assert {"piper-de-thorsten-medium", "piper-en-ryan-medium"} <= set(world.fetched)
    assert (_isolated_home / "app" / "jarvis" / "voice_engine" / "worker.py").is_file()
    assert (_isolated_home / "app" / "jarvis" / "__init__.py").read_text(encoding="utf-8") == ""
    assert world.pulled == []  # installed first: nothing to download
    state = read_setup_state(_isolated_home)
    assert state["llm_model"] == "qwen3.5:4b" and state["tts"] == "pocket"
    assert state["requirements_sha256"] == setup.requirements_sha256()


def test_a_cpu_machine_pulls_the_small_default_when_nothing_fits(_isolated_home: Path) -> None:
    world = _world(_isolated_home, machine_class="cpu", installed={"llama3.2:3b"})
    setup.run_setup_blocking(world.deps(PACKAGE))
    assert world.pulled == ["qwen3.5:2b"]
    assert read_setup_state(_isolated_home)["llm_model"] == "qwen3.5:2b"


def test_setup_starts_ollama_once_and_fails_honestly_without_it(_isolated_home: Path) -> None:
    world = _world(_isolated_home, ollama_running=False, ollama_installed=False)
    setup.run_setup_blocking(world.deps(PACKAGE))
    snapshot = setup.setup_snapshot()
    assert world.ollama_starts == 1
    assert "not installed" in snapshot["error"]
    assert snapshot["stage"] == "llm"
    # The speech half stays installed; only the language model is missing.
    assert venv_python(_isolated_home).is_file()


def test_a_failed_voice_prefetch_degrades_to_piper(_isolated_home: Path) -> None:
    world = _world(_isolated_home, installed={"qwen3.5:4b"}, fail_command="PocketTts")
    setup.run_setup_blocking(world.deps(PACKAGE))
    snapshot = setup.setup_snapshot()
    assert snapshot["error"] == ""
    assert snapshot["warnings"] and "Piper" in snapshot["warnings"][0]


def test_a_failed_selftest_fails_setup_with_its_reason(_isolated_home: Path) -> None:
    world = _world(_isolated_home, installed={"qwen3.5:4b"},
                   selftest_report={"ok": False, "reason": "The voice could not hear itself."})
    setup.run_setup_blocking(world.deps(PACKAGE))
    assert setup.setup_snapshot()["error"] == "The voice could not hear itself."


def test_a_package_failure_stops_setup_at_that_stage(_isolated_home: Path) -> None:
    world = _world(_isolated_home, fail_command="requirements-engine.txt")
    setup.run_setup_blocking(world.deps(PACKAGE))
    snapshot = setup.setup_snapshot()
    assert snapshot["stage"] == "packages" and "fake failure" in snapshot["error"]
    assert world.fetched == []


def test_an_unsupported_machine_is_refused_before_any_download(_isolated_home: Path) -> None:
    world = _world(_isolated_home, blocked="Local voice needs macOS 14 or newer.")
    setup.run_setup_blocking(world.deps(PACKAGE))
    assert setup.setup_snapshot()["error"] == "Local voice needs macOS 14 or newer."
    assert world.commands == [] and world.fetched == [] and not _isolated_home.exists()


@pytest.mark.parametrize(
    ("kwargs", "blocked"),
    [
        ({"system": "darwin", "arch": "x86_64", "mac_version": "14.5"}, True),
        ({"system": "darwin", "arch": "arm64", "mac_version": "13.6"}, True),
        ({"system": "darwin", "arch": "arm64", "mac_version": "15.1"}, False),
        ({"system": "linux", "arch": "x86_64", "glibc": "2.17"}, True),
        ({"system": "linux", "arch": "aarch64", "glibc": "2.36"}, False),
        ({"system": "linux", "arch": "x86_64", "glibc": ""}, False),
        ({"system": "win32", "arch": "AMD64"}, False),
        ({"system": "win32", "arch": "ARM64"}, False),
    ],
)
def test_unsupported_platforms_match_the_pinned_wheels(kwargs, blocked) -> None:
    assert bool(setup.unsupported_reason(**kwargs)) is blocked


def test_requirements_pin_every_package_exactly() -> None:
    lines = [
        line.split(";")[0].strip()
        for line in setup.requirements_file().read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines and all("==" in line for line in lines)
    names = {line.split("==")[0].lower() for line in lines}
    assert {"numpy", "onnxruntime", "sherpa-onnx", "pocket-tts", "torch"} <= names


@pytest.mark.parametrize(
    ("probe", "expected"),
    [
        ((16.0, "nvidia-smi"), "nvidia"),
        ((16.0, "apple-unified"), "apple"),
        ((8.0, "linux-drm"), "gpu"),
        ((4.0, "nvidia-smi"), "cpu"),
        ((0.0, "none"), "cpu"),
    ],
)
def test_machine_class_reads_the_shared_probe(probe, expected) -> None:
    assert setup.machine_class(lambda: probe) == expected


def test_a_broken_probe_reads_as_cpu() -> None:
    def broken() -> tuple[float, str]:
        raise OSError("nvidia-smi hung")

    assert setup.machine_class(broken) == "cpu"


def test_installed_models_win_over_the_default() -> None:
    assert setup.choose_llm("nvidia", {"granite4.2:8b", "x:1b"}) == ("granite4.2:8b", True)
    # A different size of the same family is not the measured model.
    assert setup.choose_llm("nvidia", {"qwen3.5:latest", "qwen3.5:9b"}) == ("qwen3.5:4b", False)
    assert setup.choose_llm("nvidia", set()) == ("qwen3.5:4b", False)
    assert setup.choose_llm("cpu", {"qwen3.5:4b"}) == ("qwen3.5:4b", True)
    assert setup.choose_llm("cpu", set()) == ("qwen3.5:2b", False)


def test_only_measured_platforms_count_as_verified() -> None:
    assert setup.os_verified("nvidia", "windows")
    assert setup.os_verified("cpu", "linux")
    assert not setup.os_verified("apple", "macos")
    assert not setup.os_verified("nvidia", "linux")
    assert setup.expected_latency("nvidia")["basis"] == "measured"
    assert setup.expected_latency("gpu") == {"low_s": None, "high_s": None, "basis": "selftest"}


def _status(cfg, *, installed=frozenset(), error=None, machine="nvidia"):
    async def llms():
        return set(installed), error

    return asyncio.run(setup.card_status(cfg, installed_llms=llms, machine=machine))


def test_status_before_setup_names_the_next_step(_isolated_home: Path) -> None:
    status = _status(JarvisConfig(), installed={"qwen3.5:4b"})
    assert status["phase"] == "not_installed" and status["installed"] is False
    assert "setup" in status["reason"]
    assert status["llm_model"] == "qwen3.5:4b" and status["llm_source"] == "default"
    assert status["llm_installed"] is True
    assert status["voice"] == "pocket" and status["voices"] == ["pocket", "piper"]
    assert status["selftest"] is None and status["selftest_running"] is False
    assert set(status) >= {"expected_latency", "os_verified", "platform", "machine_class"}


def test_status_after_setup_is_stopped_with_the_recorded_model(_isolated_home: Path) -> None:
    world = _world(_isolated_home, installed={"granite4.2:8b"})
    setup.run_setup_blocking(world.deps(PACKAGE))
    status = _status(JarvisConfig(), installed={"granite4.2:8b"})
    assert status["installed"] is True and status["phase"] == "stopped"
    assert (status["llm_model"], status["llm_source"]) == ("granite4.2:8b", "setup")


def test_an_explicit_card_pick_wins_and_ollama_errors_stay_visible(_isolated_home: Path) -> None:
    cfg = JarvisConfig(voice_engine=VoiceEngineConfig(llm_model="gemma4:12b-it-qat", tts="piper"))
    status = _status(cfg, error="Ollama did not answer.")
    assert (status["llm_model"], status["llm_source"]) == ("gemma4:12b-it-qat", "config")
    assert status["llm_installed"] is None and status["llm_error"] == "Ollama did not answer."
    assert status["voice"] == "piper"


def test_a_stored_selftest_goes_stale_when_the_model_changes(_isolated_home: Path) -> None:
    settings = SimpleNamespace(llm_model="qwen3.5:4b", tts="pocket")
    setup.save_selftest(_isolated_home, {"ok": True, "languages": {"de": {"ok": True}},
                                         "llm": {"ok": True, "ms": 390}}, settings)
    fresh = setup.load_selftest(_isolated_home, settings)
    assert fresh["ok"] is True and fresh["stale"] is False and fresh["llm"]["ms"] == 390
    moved = setup.load_selftest(_isolated_home, SimpleNamespace(llm_model="qwen3.5:2b",
                                                                 tts="pocket"))
    assert moved["stale"] is True
    raw = json.loads((_isolated_home / "selftest.json").read_text(encoding="utf-8"))
    assert raw["fingerprint"]["engine_version"] == setup.engine_version()
