"""Install engine, tier ladder, preflight, and brain resolution contracts."""

import json
from pathlib import Path

import pytest

from jarvis.realtime.local_server import brain_link, install, preflight, tiers


class TestTierLadder:
    def test_below_floor_returns_none(self) -> None:
        assert tiers.pick_tier(0.0) is None
        assert tiers.pick_tier(11.9) is None

    def test_floor_and_boundaries(self) -> None:
        assert tiers.pick_tier(12.0) is not None
        assert tiers.pick_tier(12.0).key == "t0-12gb"
        # Real cards report slightly under their marketing size (a 16 GB
        # RTX measures ~15.9 GiB), so thresholds carry an engineering
        # allowance — the marketing-16GB card must land in the 16 GB tier.
        assert tiers.pick_tier(15.9).key == "t1-16gb"
        assert tiers.pick_tier(16.0).key == "t1-16gb"
        assert tiers.pick_tier(30.5).key == "t1-16gb"
        assert tiers.pick_tier(31.5).key == "t2-32gb"
        assert tiers.pick_tier(700.0).key == "t4-128gb"

    def test_only_measured_tiers_may_claim_measured(self) -> None:
        # Mandate: a tier flips to measured only with a recorded bake-off.
        measured = [t for t in tiers.TIERS if t.measured]
        assert measured == []
        for tier in tiers.TIERS:
            if not tier.measured:
                assert "pending bake-off" in tier.target_class

    def test_unmeasured_tiers_say_so_in_their_stack_sentence(self) -> None:
        sentence = tiers.describe_stack(tiers.TIERS[-1])
        assert "bake-off" in sentence


class TestBrainResolution:
    def test_prefers_the_pinned_model(self) -> None:
        chosen, note = brain_link._pick_model(
            [("qwen3.5:9b", 6.6), ("qwen3.5:4b", 3.4)]
        )
        assert chosen == "qwen3.5:4b"
        assert note == ""

    def test_skips_non_chat_models(self) -> None:
        assert brain_link._pick_model([("nomic-embed-text:latest", 0.3)])[0] == ""
        assert (
            brain_link._pick_model(
                [("nomic-embed-text:latest", 0.3), ("mistral:7b", 4.4)]
            )[0]
            == "mistral:7b"
        )

    def test_skips_embedding_families_without_the_word_embed(self) -> None:
        """A live picker offered ``bge-m3`` as a voice brain (2026-08-09):
        the embedding families whose NAME carries no "embed" slipped the
        filter and would answer every spoken turn with a vector."""
        for tag in (
            "bge-m3:latest",
            "gte-large:latest",
            "e5-mistral:latest",
            "all-minilm:latest",
            "paraphrase-multilingual:latest",
        ):
            assert brain_link._pick_model([(tag, 1.1)])[0] == "", tag

    def test_skips_cloud_tags_on_the_fully_local_path(self) -> None:
        """A ``:cloud`` tag runs on Ollama's servers behind the user's
        account — offering it would make the resolver's own "Fully local"
        sentence a lie and add a sign-in the local path never asked for."""
        assert brain_link._pick_model([("kimi-k2.5:cloud", 0.0)])[0] == ""
        # The local sibling of the same family stays perfectly usable.
        assert brain_link._pick_model([("kimi-k2.5:9b", 6.0)])[0] == "kimi-k2.5:9b"

    def test_a_configured_model_wins_when_it_fits(self) -> None:
        chosen, note = brain_link._pick_model(
            [("qwen2.5:7b", 4.7), ("mistral:7b", 4.4)],
            preferred_model="mistral:7b",
            usable_gb=16.0,
        )
        assert chosen == "mistral:7b"
        assert note == ""

    def test_an_oversized_model_is_skipped_with_the_reason(self) -> None:
        """A 24 GB tag on a 16 GB card would OOM at the first turn next to
        the TTS; the resolver must pick a fitting model and SAY why."""
        chosen, note = brain_link._pick_model(
            [("nemotron-cascade-2:latest", 24.0), ("qwen2.5:7b", 4.7)],
            preferred_model="nemotron-cascade-2:latest",
            usable_gb=16.0,
        )
        assert chosen == "qwen2.5:7b"
        assert "does not fit" in note

    def test_unknown_sizes_never_veto(self) -> None:
        """A tags payload without sizes must behave exactly as before the
        fit check existed (fail-open)."""
        chosen, _note = brain_link._pick_model(
            [("mystery-model:latest", 0.0)], usable_gb=16.0
        )
        assert chosen == "mystery-model:latest"

    def test_brain_choices_annotate_fit_and_installed_state(self, monkeypatch) -> None:
        """The picker's data: installed tags first with the SAME fit rule the
        resolver applies, then curated not-yet-installed recommendations."""
        monkeypatch.setattr(
            brain_link,
            "_ollama_models",
            lambda base, timeout: [
                ("qwen3.5:4b", 3.4),
                ("nemotron-cascade-2:latest", 24.0),
                ("nomic-embed-text:latest", 0.3),  # never a voice brain
            ],
        )
        payload = brain_link.list_brain_choices(
            usable_gb=16.0, current_model="qwen3.5:4b"
        )
        assert payload["reachable"] is True
        by_id = {entry["id"]: entry for entry in payload["models"]}
        assert "nomic-embed-text:latest" not in by_id
        assert by_id["qwen3.5:4b"]["installed"] is True
        assert by_id["qwen3.5:4b"]["fits"] is True
        assert by_id["qwen3.5:4b"]["current"] is True
        assert by_id["qwen3.5:4b"]["recommended"] is True
        assert by_id["nemotron-cascade-2:latest"]["fits"] is False
        assert "does not fit" in by_id["nemotron-cascade-2:latest"]["note"]
        # Curated chat entries that are not installed join the list.
        assert any(
            not entry["installed"] for entry in payload["models"]
        ), "curated recommendations must be offered for download"

    def test_brain_choices_survive_an_unreachable_ollama(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_models", lambda base, timeout: None)
        payload = brain_link.list_brain_choices(usable_gb=16.0)
        assert payload["reachable"] is False
        # Curated entries still render so the user sees what COULD run here.
        assert payload["models"]

    def test_running_ollama_with_model_is_fully_local(self, monkeypatch) -> None:
        monkeypatch.setattr(
            brain_link, "_ollama_models", lambda base, timeout: [("qwen2.5:7b", 4.7)]
        )
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "ollama"
        assert resolution.ok
        assert resolution.base_url.endswith("/v1")
        assert resolution.model == "qwen2.5:7b"

    def test_empty_ollama_blocks_with_pull_action(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_models", lambda base, timeout: [])
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "blocked"
        assert any("ollama pull" in action for action in resolution.actions)

    def test_no_ollama_with_key_goes_cloud_with_honest_note(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_models", lambda base, timeout: None)
        monkeypatch.setattr(brain_link, "_openai_key", lambda: "sk-test")
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "cloud-openai"
        assert "OpenAI key" in resolution.note
        assert resolution.base_url == ""  # server default; key stays out of commands

    def test_nothing_available_blocks_with_actions(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_models", lambda base, timeout: None)
        monkeypatch.setattr(brain_link, "_openai_key", lambda: "")
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "blocked"
        assert len(resolution.actions) == 2


class TestPreflight:
    def test_below_floor_blocks_with_cloud_pointer(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (4.0, "nvidia-smi"))
        report = preflight.run_preflight(tmp_path)
        assert not report.ok
        assert "minimum" in report.blocker
        assert any("cloud" in a.lower() for a in report.actions)
        assert report.tier is None

    def test_full_disk_blocks_before_downloading(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
        monkeypatch.setattr(preflight, "_disk_free_gb", lambda root: 1.0)
        report = preflight.run_preflight(tmp_path)
        assert not report.ok
        assert "disk" in report.blocker.lower()

    def test_ok_report_carries_tier_stack_and_brain(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
        monkeypatch.setattr(preflight, "_disk_free_gb", lambda root: 100.0)
        monkeypatch.setattr(
            preflight,
            "resolve_brain",
            lambda **kwargs: brain_link.BrainResolution(
                kind="ollama", base_url="http://127.0.0.1:11434/v1", model="qwen2.5:7b"
            ),
        )
        report = preflight.run_preflight(tmp_path)
        assert report.ok
        assert report.tier is not None and report.tier.key == "t1-16gb"
        assert report.stack_sentence
        payload = preflight.report_payload(report)
        assert payload["tier"]["key"] == "t1-16gb"  # type: ignore[index]

    def test_blocked_brain_blocks_the_preflight(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (16.0, "nvidia-smi"))
        monkeypatch.setattr(preflight, "_disk_free_gb", lambda root: 100.0)
        monkeypatch.setattr(
            preflight,
            "resolve_brain",
            lambda **kwargs: brain_link.BrainResolution(
                kind="blocked", note="no brain", actions=("x",)
            ),
        )
        report = preflight.run_preflight(tmp_path)
        assert not report.ok
        assert report.blocker == "no brain"


class TestDeriveLaunchCommand:
    def _brain(self) -> brain_link.BrainResolution:
        return brain_link.BrainResolution(
            kind="ollama",
            base_url="http://127.0.0.1:11434/v1",
            api_key="ollama",
            model="qwen2.5:7b",
        )

    def test_ollama_command_carries_endpoint_and_placeholder_key(self) -> None:
        cmd = install.derive_launch_command(self._brain(), memory_source="nvidia-smi")
        assert "--responses_api_base_url http://127.0.0.1:11434/v1" in cmd
        assert "--responses_api_api_key ollama" in cmd
        assert "--llm_backend chat-completions" in cmd
        assert "--responses_api_reasoning_effort none" in cmd
        assert "--qwen3_tts_device cuda" in cmd
        assert "--model_name qwen2.5:7b" in cmd
        assert "--ws_host 127.0.0.1" in cmd
        assert "--no_enable_live_transcription" in cmd
        assert "--min_silence_ms 320" in cmd
        assert "--smart_turn_incomplete_delay_ms 2000" in cmd
        assert "--unanswered_reopen_ms 2000" in cmd

    def test_cloud_command_never_carries_the_secret(self) -> None:
        brain = brain_link.BrainResolution(
            kind="cloud-openai", api_key="sk-secret", model="gpt-5.4-mini"
        )
        cmd = install.derive_launch_command(brain, memory_source="nvidia-smi")
        assert "sk-secret" not in cmd
        assert "--responses_api_api_key" not in cmd
        assert "--llm_backend chat-completions" not in cmd
        assert "--responses_api_reasoning_effort" not in cmd
        assert "--no_enable_live_transcription" in cmd
        assert "--smart_turn_incomplete_delay_ms 2000" in cmd

    def test_apple_unified_memory_maps_to_mps(self) -> None:
        cmd = install.derive_launch_command(self._brain(), memory_source="apple-unified")
        assert "--qwen3_tts_device mps" in cmd


class TestServerStatusFailClosed:
    def test_nothing_installed_reports_not_ready(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        status = install.server_status()
        assert status["ready"] is False
        assert status["sentence"] == "Managed server not installed."

    def test_partial_install_names_whats_missing(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        python = install._venv_python()
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        status = install.server_status()
        assert status["ready"] is False
        assert "missing" in str(status["sentence"])
        components = status["components"]
        assert components["venv"] is True  # type: ignore[index]
        assert components["patch"] is False  # type: ignore[index]

    def test_corrupt_smoke_marker_is_not_readiness(self, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        python = install._venv_python()
        entrypoint = install._server_entrypoint()
        python.parent.mkdir(parents=True)
        python.write_bytes(b"")
        entrypoint.write_bytes(b"")
        install._smoke_marker().write_text("", encoding="utf-8")
        monkeypatch.setattr(install, "patch_state", lambda path: "patched")

        status = install.server_status()

        assert status["ready"] is False
        assert status["components"]["smoke_boot"] is False  # type: ignore[index]

    def test_smoke_marker_must_match_the_current_patch_version(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        install.install_root().mkdir(parents=True)
        install._smoke_marker().write_text(
            '{"schema": 1, "patch_version": "old", "at": 1, '
            '"tier": "t1-16gb", "brain": "ollama", "preflight": {}}',
            encoding="utf-8",
        )
        assert install._smoke_marker_valid() is False

    def test_owned_live_runtime_repairs_a_missing_smoke_marker(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """A schema upgrade must not demand a destructive multi-GB reinstall.

        A ready server owned by this exact patched install is stronger smoke
        evidence than the marker that an older Jarvis version wrote.
        """
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        install._venv_python().parent.mkdir(parents=True)
        install._venv_python().write_bytes(b"")
        install._server_entrypoint().write_bytes(b"")
        monkeypatch.setattr(install, "patch_state", lambda path: "patched")
        monkeypatch.setattr(
            supervisor,
            "status",
            lambda base_url: {"ready": True, "owned": True},
        )
        report = preflight.PreflightReport(
            ok=True,
            usable_gb=16.0,
            memory_source="nvidia-smi",
            disk_free_gb=100.0,
            tier=tiers.TIERS[1],
            stack_sentence="x",
            brain=brain_link.BrainResolution(kind="ollama", model="qwen2.5:7b"),
        )
        monkeypatch.setattr(install, "run_preflight", lambda root: report)

        assert install.repair_smoke_marker_from_live_runtime(
            "http://127.0.0.1:8765"
        )

        payload = json.loads(install._smoke_marker().read_text(encoding="utf-8"))
        assert payload["schema"] == install._SMOKE_MARKER_SCHEMA
        assert payload["patch_version"] == install.PATCH_TARGET_VERSION
        assert payload["repair_source"] == "owned-live-runtime"
        assert install._smoke_marker_valid()

    def test_foreign_runtime_cannot_repair_the_smoke_marker(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        install._venv_python().parent.mkdir(parents=True)
        install._venv_python().write_bytes(b"")
        install._server_entrypoint().write_bytes(b"")
        monkeypatch.setattr(install, "patch_state", lambda path: "patched")
        monkeypatch.setattr(
            supervisor,
            "status",
            lambda base_url: {"ready": True, "owned": False},
        )

        assert not install.repair_smoke_marker_from_live_runtime(
            "http://127.0.0.1:8765"
        )
        assert not install._smoke_marker().exists()

    def test_smoke_marker_repair_refuses_during_another_lifecycle_operation(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))

        class _BusyLifecycle:
            def __enter__(self) -> bool:
                return False

            def __exit__(self, *args: object) -> None:
                return None

        monkeypatch.setattr(
            supervisor,
            "lifecycle_guard",
            lambda: _BusyLifecycle(),
        )
        monkeypatch.setattr(
            install,
            "_repair_smoke_marker_from_live_runtime_unlocked",
            lambda base_url: (_ for _ in ()).throw(
                AssertionError("repair must not race an install")
            ),
        )

        assert not install.repair_smoke_marker_from_live_runtime(
            "http://127.0.0.1:8765"
        )

    def test_stale_config_earns_the_repair_sentence(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Install files gone while jarvis.toml still points a launch command
        at them (live 2026-08-08): the card must say "repair", not the
        misleading "not installed"."""
        import json

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        command = str(
            install.install_root() / "venv" / "Scripts" / "speech-to-speech.exe"
        )
        quoted = f'"{command}" --mode realtime'
        cfg = tmp_path / "jarvis.toml"
        cfg.write_text(
            "[brain.providers.local-realtime]\n"
            f"launch_command = {json.dumps(quoted)}\n",
            encoding="utf-8",
        )
        import jarvis.core.config as config_module

        monkeypatch.setattr(config_module, "resolve_config_path", lambda: cfg)
        status = install.server_status()
        assert status["ready"] is False
        assert status["stale"] is True
        assert "reinstall to repair" in str(status["sentence"])

    def test_a_foreign_launch_command_is_never_called_stale(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """A bring-your-own command (docker, another tree) must keep the
        plain "not installed" sentence — staleness is a MANAGED concept."""
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        cfg = tmp_path / "jarvis.toml"
        cfg.write_text(
            '[brain.providers.local-realtime]\nlaunch_command = "docker run my-server"\n',
            encoding="utf-8",
        )
        import jarvis.core.config as config_module

        monkeypatch.setattr(config_module, "resolve_config_path", lambda: cfg)
        status = install.server_status()
        assert status["stale"] is False
        assert status["sentence"] == "Managed server not installed."

    def test_uninstall_refuses_while_running(self, monkeypatch) -> None:
        class _Alive:
            @staticmethod
            def is_alive() -> bool:
                return True

        monkeypatch.setattr(install._STATE, "thread", _Alive())
        ok, message = install.uninstall()
        assert not ok
        assert "running" in message


class TestSnapshot:
    def test_snapshot_shape(self) -> None:
        snap = install.snapshot()
        assert set(snap) == {"phase", "percent", "detail", "error", "running", "log_tail"}


class TestReviewFixes:
    """Contracts pinned by the 2026-08-07 code review."""

    def test_posix_install_steps_start_a_killable_process_group(self, monkeypatch) -> None:
        captured: dict[str, object] = {}

        class _Process:
            stdout: list[str] = []

            def wait(self, timeout: int) -> int:
                return 0

        def popen(*args, **kwargs):
            captured.update(kwargs)
            return _Process()

        monkeypatch.setattr(install.os, "name", "posix")
        monkeypatch.setattr(install.subprocess, "Popen", popen)
        install._run(["python", "-m", "pip", "install", "package"], timeout=30)
        assert captured["start_new_session"] is True

    def test_failed_reinstall_invalidates_the_previous_smoke_marker(
        self, monkeypatch, tmp_path
    ) -> None:
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        install.install_root().mkdir(parents=True)
        install._venv_python().parent.mkdir(parents=True)
        install._venv_python().write_bytes(b"")
        install._write_smoke_marker(
            {
                "schema": install._SMOKE_MARKER_SCHEMA,
                "patch_version": install.PATCH_TARGET_VERSION,
                "at": 1.0,
                "tier": "t1-16gb",
                "brain": "ollama",
                "preflight": {},
            }
        )
        report = preflight.PreflightReport(
            ok=True,
            usable_gb=16.0,
            memory_source="nvidia-smi",
            disk_free_gb=100.0,
            tier=tiers.TIERS[1],
            stack_sentence="x",
            brain=brain_link.BrainResolution(kind="ollama", model="qwen2.5:7b"),
        )
        monkeypatch.setattr(install, "run_preflight", lambda root: report)
        monkeypatch.setattr(
            supervisor,
            "_stop_owned_unlocked",
            lambda **kwargs: (False, "no owned server process found"),
        )

        def failed_step(*args, **kwargs):
            raise RuntimeError("dependency install failed")

        monkeypatch.setattr(install, "_run", failed_step)
        install._run_install()

        assert not install._smoke_marker().exists()
        assert install.snapshot()["phase"] == "error"

    def test_install_repairs_live_proof_without_stopping_the_server(
        self, monkeypatch, tmp_path
    ) -> None:
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(install, "_smoke_marker_valid", lambda: False)
        monkeypatch.setattr(
            install,
            "_repair_smoke_marker_from_live_runtime_unlocked",
            lambda base_url: True,
        )
        monkeypatch.setattr(
            install,
            "_read_smoke_marker_payload",
            lambda: {"brain": "ollama"},
        )

        def forbidden(*args, **kwargs):
            raise AssertionError("a healthy server must not be stopped for marker repair")

        monkeypatch.setattr(install, "run_preflight", forbidden)
        monkeypatch.setattr(supervisor, "_stop_owned_unlocked", forbidden)

        install._run_install_guarded("ollama")

        state = install.snapshot()
        assert state["phase"] == "done"
        assert state["percent"] == 100
        assert "proof repaired" in str(state["detail"])

    def test_smoke_boot_always_tears_down_its_child(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        install.install_root().mkdir(parents=True)

        class _Process:
            pid = 4711
            returncode = None

            def poll(self):
                return None

        process = _Process()
        monkeypatch.setattr(install.subprocess, "Popen", lambda *args, **kwargs: process)
        monkeypatch.setattr(
            "jarvis.realtime.local_server.supervisor.probe_runtime",
            lambda *args, **kwargs: {
                "size": 1,
                "in_use": 0,
                "available": 1,
                "stuck": 0,
            },
        )
        killed: list[object] = []
        monkeypatch.setattr(
            install, "_kill_tree", lambda proc: killed.append(proc) or True
        )
        monkeypatch.setattr(
            "jarvis.realtime.local_server.smoke.probe_voice_roundtrip_sync",
            lambda *args, **kwargs: {"ok": True},
        )

        install._smoke_boot(
            "serve --mode realtime",
            brain_link.BrainResolution(kind="ollama", model="qwen2.5:7b"),
        )

        assert killed == [process]

    def test_smoke_boot_fails_if_its_child_survives_teardown(
        self, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        install.install_root().mkdir(parents=True)

        class _Process:
            pid = 4711
            returncode = None

            def poll(self):
                return None

        monkeypatch.setattr(
            install.subprocess,
            "Popen",
            lambda *args, **kwargs: _Process(),
        )
        monkeypatch.setattr(
            "jarvis.realtime.local_server.supervisor.probe_runtime",
            lambda *args, **kwargs: {
                "size": 1,
                "in_use": 0,
                "available": 1,
                "stuck": 0,
            },
        )
        monkeypatch.setattr(install, "_kill_tree", lambda proc: False)
        monkeypatch.setattr(
            "jarvis.realtime.local_server.smoke.probe_voice_roundtrip_sync",
            lambda *args, **kwargs: {"ok": True},
        )

        with pytest.raises(RuntimeError, match="survived teardown"):
            install._smoke_boot(
                "serve --mode realtime",
                brain_link.BrainResolution(kind="ollama", model="qwen2.5:7b"),
            )

    def test_install_holds_the_server_lifecycle_lease_for_its_body(
        self, monkeypatch, tmp_path
    ) -> None:
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        outcomes: list[str] = []

        def guarded_body(confirmed_brain: str = "") -> None:
            outcomes.append(
                supervisor.ensure_running(
                    launch_command="serve",
                    base_url="http://127.0.0.1:8765",
                    reason="lease-test",
                )
            )

        monkeypatch.setattr(install, "_run_install_guarded", guarded_body)
        install._run_install()
        assert outcomes == ["refused:spawn-in-progress"]

    def test_uninstall_holds_the_server_lifecycle_lease_for_its_body(
        self, monkeypatch, tmp_path
    ) -> None:
        from jarvis.realtime.local_server import supervisor

        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        outcomes: list[str] = []

        def guarded_body() -> tuple[bool, str]:
            outcomes.append(
                supervisor.ensure_running(
                    launch_command="serve",
                    base_url="http://127.0.0.1:8765",
                    reason="lease-test",
                )
            )
            return True, "done"

        monkeypatch.setattr(install, "_uninstall_guarded", guarded_body)
        assert install.uninstall() == (True, "done")
        assert outcomes == ["refused:spawn-in-progress"]

    def test_install_root_is_absolute_without_env(self, monkeypatch) -> None:
        monkeypatch.delenv("JARVIS_DATA_DIR", raising=False)
        assert install.install_root().is_absolute()

    def test_multi_gpu_uses_largest_device_not_the_sum(self, monkeypatch) -> None:
        from jarvis.hardware import detection

        monkeypatch.setattr(
            detection,
            "_detect_nvidia_gpus",
            lambda: [detection.GPUInfo("A", 8192), detection.GPUInfo("B", 8192)],
        )
        usable, source = preflight._usable_accelerator_gb()
        assert source == "nvidia-smi"
        assert usable == pytest.approx(8.0)  # two 8 GB cards are an 8 GB machine

    def test_brain_kind_change_fails_the_install(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        report = preflight.PreflightReport(
            ok=True,
            usable_gb=16.0,
            memory_source="nvidia-smi",
            disk_free_gb=100.0,
            tier=tiers.TIERS[0],
            stack_sentence="x",
            brain=brain_link.BrainResolution(kind="cloud-openai", model="gpt-5.4-mini"),
        )
        monkeypatch.setattr(install, "run_preflight", lambda root: report)
        install._run_install("ollama")
        snap = install.snapshot()
        assert snap["phase"] == "error"
        assert "changed since your confirmation" in str(snap["error"])

    def test_uninstall_clears_only_the_managed_launch_command(self, tmp_path) -> None:
        from jarvis.core.config_writer import clear_local_realtime_launch_command

        cfg = tmp_path / "jarvis.toml"
        managed = tmp_path / "data" / "local_realtime"
        entrypoint = (managed / "venv" / "s.exe").as_posix()
        cfg.write_text(
            "[brain.providers.local-realtime]\n"
            f"launch_command = \"'{entrypoint}' --mode realtime\"\n"
            'base_url = "http://localhost:8765"\n',
            encoding="utf-8",
        )
        clear_local_realtime_launch_command(only_if_under=str(managed), path=cfg)
        text = cfg.read_text(encoding="utf-8")
        assert 'launch_command = ""' in text
        assert 'base_url = ""' in text

    def test_clear_spares_a_bring_your_own_command(self, tmp_path) -> None:
        from jarvis.core.config_writer import clear_local_realtime_launch_command

        cfg = tmp_path / "jarvis.toml"
        cfg.write_text(
            "[brain.providers.local-realtime]\n"
            'launch_command = "/opt/myserver/run --mode realtime"\n'
            'base_url = "http://192.168.1.5:9000"\n',
            encoding="utf-8",
        )
        clear_local_realtime_launch_command(
            only_if_under=str(tmp_path / "data" / "local_realtime"), path=cfg
        )
        text = cfg.read_text(encoding="utf-8")
        assert "/opt/myserver/run" in text
        assert "192.168.1.5:9000" in text

    def test_tolerant_rmtree_survives_vanishing_files(self, tmp_path) -> None:
        root = tmp_path / "tree"
        (root / "sub").mkdir(parents=True)
        (root / "sub" / "a.txt").write_text("x", encoding="utf-8")
        install._rmtree_tolerant(root)
        assert not root.exists()
        # Idempotent on an already-gone tree.
        install._rmtree_tolerant(root)


class TestBrainSetupChain:
    """Single-click promise: a confirmed local install sets up its own brain."""

    @staticmethod
    def _blocked_report() -> preflight.PreflightReport:
        return preflight.PreflightReport(
            ok=False,
            blocker="No brain available: neither a running Ollama nor an OpenAI key.",
            usable_gb=16.0,
            memory_source="nvidia-smi",
            disk_free_gb=100.0,
            tier=tiers.TIERS[0],
            brain=brain_link.BrainResolution(kind="blocked", note="no brain"),
        )

    def test_confirmed_ollama_runs_the_brain_setup(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        reports = [self._blocked_report(), self._blocked_report()]
        monkeypatch.setattr(install, "run_preflight", lambda root: reports.pop(0))
        calls: list[str] = []
        monkeypatch.setattr(
            install, "_setup_local_brain", lambda: calls.append("setup")
        )
        install._run_install("ollama")
        # Setup ran; the still-blocked re-check then failed honestly (which
        # also keeps this test off the heavy venv/pip tail).
        assert calls == ["setup"]
        assert install.snapshot()["phase"] == "error"

    def test_unconfirmed_blocked_brain_fails_without_setup(
        self, monkeypatch, tmp_path
    ) -> None:
        """Nothing installs without the user's explicit confirmation."""
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        monkeypatch.setattr(
            install, "run_preflight", lambda root: self._blocked_report()
        )
        calls: list[str] = []
        monkeypatch.setattr(
            install, "_setup_local_brain", lambda: calls.append("setup")
        )
        install._run_install("")
        assert calls == []
        assert install.snapshot()["phase"] == "error"

    def test_floor_blocked_never_runs_brain_setup(self, monkeypatch, tmp_path) -> None:
        """A machine under the VRAM floor cannot be fixed by installing
        Ollama — the honest hardware blocker must win."""
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        floor_blocked = preflight.PreflightReport(
            ok=False,
            blocker="under the minimum",
            usable_gb=4.0,
            memory_source="nvidia-smi",
            disk_free_gb=100.0,
        )
        monkeypatch.setattr(install, "run_preflight", lambda root: floor_blocked)
        calls: list[str] = []
        monkeypatch.setattr(
            install, "_setup_local_brain", lambda: calls.append("setup")
        )
        install._run_install("ollama")
        assert calls == []
        assert "minimum" in str(install.snapshot()["error"])

    def test_disk_blocker_never_downloads_a_brain_model(
        self, monkeypatch, tmp_path
    ) -> None:
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        disk_blocked = preflight.PreflightReport(
            ok=False,
            blocker="not enough free disk",
            usable_gb=16.0,
            memory_source="nvidia-smi",
            disk_free_gb=1.0,
            tier=tiers.TIERS[0],
        )
        monkeypatch.setattr(install, "run_preflight", lambda root: disk_blocked)
        calls: list[str] = []
        monkeypatch.setattr(
            install, "_setup_local_brain", lambda: calls.append("setup")
        )

        install._run_install("ollama")

        assert calls == []
        assert "disk" in str(install.snapshot()["error"])

    def test_setup_pulls_the_preferred_model_when_missing(self, monkeypatch) -> None:
        from jarvis.brain import ollama_runtime

        monkeypatch.setattr(
            ollama_runtime, "ensure_runtime_blocking", lambda: (True, "running")
        )
        monkeypatch.setattr(
            install,
            "resolve_brain_for_install",
            lambda: brain_link.BrainResolution(kind="blocked", note="no model"),
        )
        pulled: list[str] = []
        monkeypatch.setattr(
            install, "_pull_brain_model", lambda model: pulled.append(model)
        )
        install._setup_local_brain()
        assert pulled == [brain_link._PREFERRED_MODELS[0]]

    def test_setup_skips_the_pull_when_a_brain_already_resolves(
        self, monkeypatch
    ) -> None:
        from jarvis.brain import ollama_runtime

        monkeypatch.setattr(
            ollama_runtime, "ensure_runtime_blocking", lambda: (True, "running")
        )
        monkeypatch.setattr(
            install,
            "resolve_brain_for_install",
            lambda: brain_link.BrainResolution(kind="ollama", model="qwen2.5:7b"),
        )
        pulled: list[str] = []
        monkeypatch.setattr(
            install, "_pull_brain_model", lambda model: pulled.append(model)
        )
        install._setup_local_brain()
        assert pulled == []

    def test_setup_raises_the_honest_platform_reason(self, monkeypatch) -> None:
        from jarvis.brain import ollama_runtime

        monkeypatch.setattr(
            ollama_runtime,
            "ensure_runtime_blocking",
            lambda: (False, "passwordless sudo is not available here"),
        )
        with pytest.raises(RuntimeError, match="sudo"):
            install._setup_local_brain()

    def test_payload_flags_the_fixable_brain(self) -> None:
        payload = preflight.report_payload(self._blocked_report())
        assert payload["brain_fixable"] is True
        floor_blocked = preflight.PreflightReport(
            ok=False, blocker="x", usable_gb=4.0, memory_source="nvidia-smi"
        )
        assert preflight.report_payload(floor_blocked)["brain_fixable"] is False

    def test_the_preferred_model_is_in_the_curated_pull_list(self) -> None:
        """The auto-pull target and the download panel must agree — two
        curated lists drifting apart ships a model the panel disowns."""
        from jarvis.brain.ollama_pull import RECOMMENDED_MODELS

        curated = {entry.id for entry in RECOMMENDED_MODELS}
        assert brain_link._PREFERRED_MODELS[0] in curated


class TestCrossPlatformHonesty:
    """Contracts from the 2026-08-08 hardening plan (Wave 4)."""

    def test_unsupported_gpu_gets_the_vendor_sentence_not_zero_gb(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """"0 GB accelerator memory" is factually wrong on an AMD/Intel box
        (and on a headless NVIDIA host without nvidia-smi); the blocker must
        name the real situation."""
        monkeypatch.setattr(preflight, "_usable_accelerator_gb", lambda: (0.0, "none"))
        report = preflight.run_preflight(tmp_path)
        assert not report.ok
        assert "No supported accelerator" in report.blocker
        assert "0 GB" not in report.blocker

    @pytest.mark.skipif(
        __import__("os").name == "nt", reason="exercises the POSIX venv layout"
    )
    def test_site_packages_globs_the_venv_not_the_host(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """POSIX: the venv may have been created by a different Python than
        the one running Jarvis; the real directory wins over the host's
        sys.version_info guess."""
        monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
        site = install.install_root() / "venv" / "lib" / "python3.9" / "site-packages"
        site.mkdir(parents=True)
        assert install._site_packages() == site

    def test_launch_model_rewrite_changes_only_the_model_token(
        self, tmp_path: Path
    ) -> None:
        from jarvis.core.config_writer import update_local_realtime_launch_model

        managed = tmp_path / "data" / "local_realtime"
        entrypoint = (managed / "venv" / "s.exe").as_posix()
        cfg = tmp_path / "jarvis.toml"
        cfg.write_text(
            "[brain.providers.local-realtime]\n"
            f"launch_command = \"'{entrypoint}' --mode realtime "
            "--model_name qwen2.5:7b "
            '--responses_api_base_url http://127.0.0.1:11434/v1"\n',
            encoding="utf-8",
        )
        changed = update_local_realtime_launch_model(
            "llama3.1:8b", only_if_under=str(managed), path=cfg
        )
        assert changed is True
        text = cfg.read_text(encoding="utf-8")
        assert "--model_name llama3.1:8b" in text
        assert "qwen2.5:7b" not in text
        assert "--responses_api_base_url http://127.0.0.1:11434/v1" in text
        assert "--mode realtime" in text

    def test_launch_model_rewrite_spares_a_bring_your_own_command(
        self, tmp_path: Path
    ) -> None:
        from jarvis.core.config_writer import update_local_realtime_launch_model

        cfg = tmp_path / "jarvis.toml"
        original = '[brain.providers.local-realtime]\nlaunch_command = "/opt/run --model_name x"\n'
        cfg.write_text(original, encoding="utf-8")
        changed = update_local_realtime_launch_model(
            "y", only_if_under=str(tmp_path / "data" / "local_realtime"), path=cfg
        )
        assert changed is False
        assert "--model_name x" in cfg.read_text(encoding="utf-8")

    def test_launch_model_rewrite_is_a_noop_without_the_flag(
        self, tmp_path: Path
    ) -> None:
        from jarvis.core.config_writer import update_local_realtime_launch_model

        cfg = tmp_path / "jarvis.toml"
        cfg.write_text(
            '[brain.providers.local-realtime]\nlaunch_command = "serve --mode realtime"\n',
            encoding="utf-8",
        )
        assert update_local_realtime_launch_model("m", path=cfg) is False

    def test_command_root_match_is_case_sensitive_on_posix(self) -> None:
        """Lowercasing both sides everywhere made two DISTINCT case-sensitive
        POSIX paths compare equal; only Windows folds case."""
        from jarvis.core import config_writer

        assert not config_writer._command_references_root(
            '"/home/User/Data/tree/run" --x', "/home/user/data/tree", windows=False
        )
        assert config_writer._command_references_root(
            '"/home/user/data/tree/run" --x', "/home/user/data/tree", windows=False
        )
        assert config_writer._command_references_root(
            '"C:\\Data\\Tree\\run.exe" --x', "c:\\data\\tree", windows=True
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
