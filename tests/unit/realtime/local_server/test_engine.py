"""Install engine, tier ladder, preflight, and brain resolution contracts."""

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
        assert [t.key for t in measured] == ["t0-12gb"]
        for tier in tiers.TIERS:
            if not tier.measured:
                assert "pending bake-off" in tier.target_class

    def test_unmeasured_tiers_say_so_in_their_stack_sentence(self) -> None:
        sentence = tiers.describe_stack(tiers.TIERS[-1])
        assert "bake-off" in sentence


class TestBrainResolution:
    def test_prefers_the_pinned_model(self) -> None:
        chosen, note = brain_link._pick_model(
            [("llama3.1:8b", 4.9), ("qwen2.5:7b", 4.7)]
        )
        assert chosen == "qwen2.5:7b"
        assert note == ""

    def test_skips_non_chat_models(self) -> None:
        assert brain_link._pick_model([("nomic-embed-text:latest", 0.3)])[0] == ""
        assert (
            brain_link._pick_model(
                [("nomic-embed-text:latest", 0.3), ("mistral:7b", 4.4)]
            )[0]
            == "mistral:7b"
        )

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
        assert "--qwen3_tts_device cuda" in cmd
        assert "--model_name qwen2.5:7b" in cmd

    def test_cloud_command_never_carries_the_secret(self) -> None:
        brain = brain_link.BrainResolution(
            kind="cloud-openai", api_key="sk-secret", model="gpt-5.4-mini"
        )
        cmd = install.derive_launch_command(brain, memory_source="nvidia-smi")
        assert "sk-secret" not in cmd
        assert "--responses_api_api_key" not in cmd

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
