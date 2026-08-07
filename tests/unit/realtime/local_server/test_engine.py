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
        assert brain_link._pick_model(["llama3.1:8b", "qwen2.5:7b"]) == "qwen2.5:7b"

    def test_skips_non_chat_models(self) -> None:
        assert brain_link._pick_model(["nomic-embed-text:latest"]) == ""
        assert brain_link._pick_model(["nomic-embed-text:latest", "mistral:7b"]) == "mistral:7b"

    def test_running_ollama_with_model_is_fully_local(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_tags", lambda base, timeout: ["qwen2.5:7b"])
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "ollama"
        assert resolution.ok
        assert resolution.base_url.endswith("/v1")
        assert resolution.model == "qwen2.5:7b"

    def test_empty_ollama_blocks_with_pull_action(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_tags", lambda base, timeout: [])
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "blocked"
        assert any("ollama pull" in action for action in resolution.actions)

    def test_no_ollama_with_key_goes_cloud_with_honest_note(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_tags", lambda base, timeout: None)
        monkeypatch.setattr(brain_link, "_openai_key", lambda: "sk-test")
        resolution = brain_link.resolve_brain()
        assert resolution.kind == "cloud-openai"
        assert "OpenAI key" in resolution.note
        assert resolution.base_url == ""  # server default; key stays out of commands

    def test_nothing_available_blocks_with_actions(self, monkeypatch) -> None:
        monkeypatch.setattr(brain_link, "_ollama_tags", lambda base, timeout: None)
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
            lambda: brain_link.BrainResolution(
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
            lambda: brain_link.BrainResolution(kind="blocked", note="no brain", actions=("x",)),
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
