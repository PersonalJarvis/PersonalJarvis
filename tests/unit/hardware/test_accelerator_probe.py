"""The accelerator probe itself — the one every other test stubs out.

`usable_accelerator_gb` decides the fit verdict on every local-model card, and
until now nothing exercised it: each consumer monkeypatched it to a fixed pair,
so the function that PRODUCES those pairs had no coverage on any platform. The
NVIDIA leg was verified live on one box; the Apple-Silicon branch and the
"no accelerator I can read" fallback had never been executed by a test at all.

Every probe here is driven through injected fakes — no GPU, no registry, no
sysfs and no Ollama server is required, so the same assertions run on a
headless Linux container as on the maintainer's Windows box.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.hardware import detection


@pytest.fixture(autouse=True)
def _fresh_memo() -> None:
    """The probe is memoised process-wide; each test starts from cold."""
    detection._reset_for_tests()


def _no_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection, "_detect_nvidia_gpus", lambda: [])


def _no_vendor_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detection, "_ollama_reported_gb", lambda: 0.0)
    monkeypatch.setattr(detection, "_windows_registry_vram_gb", lambda: 0.0)
    monkeypatch.setattr(detection, "_linux_drm_vram_gb", lambda: 0.0)


# ── Unit parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("15.9", "GiB", 15.9),
        ("15.9", "GB", 15.9),
        ("16384", "MiB", 16.0),
        ("1", "TiB", 1024.0),
        ("1048576", "KiB", 1.0),
    ],
)
def test_every_unit_ollama_prints_lands_in_gib(value: str, unit: str, expected: float) -> None:
    assert detection._gib(value, unit) == pytest.approx(expected)


@pytest.mark.parametrize(("value", "unit"), [("15.9", "furlongs"), ("not-a-number", "GiB")])
def test_an_unreadable_figure_is_no_answer_not_a_guess(value: str, unit: str) -> None:
    """0 means "I could not read it" and the caller falls through to the next
    source; inventing a number here would put a wrong figure on a fit verdict."""
    assert detection._gib(value, unit) == 0.0


# ── Ollama's own startup record ──────────────────────────────────────────


def _log_with(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str) -> None:
    log_file = tmp_path / "server.log"
    log_file.write_text(text, encoding="utf-8")
    monkeypatch.setattr(detection, "_ollama_log_paths", lambda: [log_file])


def test_it_reads_the_card_ollama_itself_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Verified against Ollama 0.33.1 on 2026-08-28: the figure this line
    carries matched nvidia-smi and the Windows registry to three digits."""
    _log_with(
        monkeypatch,
        tmp_path,
        'time=2026-08-28 level=INFO msg="inference compute" id=0 library=CUDA '
        'compute=12.0 name=CUDA0 description="NVIDIA GeForce RTX 5070 Ti" '
        'driver=13.3 type=discrete total="15.9 GiB" available="14.7 GiB"\n',
    )
    assert detection._ollama_reported_gb() == pytest.approx(15.9)


@pytest.mark.parametrize("library", ["ROCm", "Metal", "CUDA", "Vulkan"])
def test_the_line_is_read_whatever_vendor_produced_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, library: str
) -> None:
    """The point of this source: one pattern covers an AMD, an Apple and an
    NVIDIA machine, because the server did the vendor detection already."""
    _log_with(
        monkeypatch,
        tmp_path,
        f'msg="inference compute" id=0 library={library} total="24.0 GiB" available="23.1 GiB"\n',
    )
    assert detection._ollama_reported_gb() == pytest.approx(24.0)


def test_the_largest_device_wins_never_the_sum(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inference runs on ONE GPU, so two 8 GB cards are an 8 GB machine."""
    _log_with(
        monkeypatch,
        tmp_path,
        'msg="inference compute" id=0 library=ROCm total="8.0 GiB"\n'
        'msg="inference compute" id=1 library=ROCm total="12.0 GiB"\n',
    )
    assert detection._ollama_reported_gb() == pytest.approx(12.0)


def test_a_rotated_or_missing_log_is_simply_no_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The line is written once per server start and rotates away with the log,
    so absence is the normal case, not a failure."""
    monkeypatch.setattr(detection, "_ollama_log_paths", lambda: [tmp_path / "gone.log"])
    assert detection._ollama_reported_gb() == 0.0
    _log_with(monkeypatch, tmp_path, "")
    assert detection._ollama_reported_gb() == 0.0


# ── Source precedence ────────────────────────────────────────────────────


def test_nvidia_is_asked_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """It is the leg verified on real hardware; a best-effort source must not
    displace it."""
    monkeypatch.setattr(
        detection, "_detect_nvidia_gpus", lambda: [detection.GPUInfo(name="RTX", vram_mb=16384)]
    )
    monkeypatch.setattr(detection, "_ollama_reported_gb", lambda: 99.0)
    assert detection.usable_accelerator_gb() == (16.0, "nvidia-smi")


def test_an_amd_card_is_read_where_nvidia_smi_sees_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect this source exists for: a 16 GB Radeon used to report 0, and
    every fit verdict fell back to the system-RAM rule."""
    _no_nvidia(monkeypatch)
    monkeypatch.setattr(detection, "_ollama_reported_gb", lambda: 16.0)
    assert detection.usable_accelerator_gb() == (16.0, "ollama-runtime")


def test_the_os_inventory_answers_when_no_server_has_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first-run box has no Ollama log yet, and the fit verdicts are needed
    before anything is downloaded. Patched at the source-selection seam so the
    assertion holds on whichever OS runs the suite."""
    _no_nvidia(monkeypatch)
    monkeypatch.setattr(detection, "_vendor_neutral_gb", lambda: (12.0, "windows-registry"))
    assert detection.usable_accelerator_gb() == (12.0, "windows-registry")


def test_the_os_inventory_is_only_consulted_after_the_server_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server's own figure is the budget it will USE; the OS inventory only
    reports what the card HAS, so it is the weaker of the two."""
    monkeypatch.setattr(detection, "_ollama_reported_gb", lambda: 10.0)
    monkeypatch.setattr(detection, "_windows_registry_vram_gb", lambda: 16.0)
    monkeypatch.setattr(detection, "_linux_drm_vram_gb", lambda: 16.0)
    assert detection._vendor_neutral_gb() == (10.0, "ollama-runtime")


def test_apple_silicon_reports_its_unified_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GPU shares system memory there, so RAM is the figure — the branch
    that had never been executed by a test."""
    _no_nvidia(monkeypatch)
    _no_vendor_sources(monkeypatch)
    monkeypatch.setattr(detection.sys, "platform", "darwin")
    monkeypatch.setattr(detection.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(detection, "_detect_ram", lambda: (32768, 16384))
    assert detection.usable_accelerator_gb() == (32.0, "apple-unified")


def test_a_metal_budget_beats_the_ram_guess_on_a_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS gives the GPU only part of the unified memory. When Ollama has
    already named the real budget, that is the better answer than total RAM."""
    _no_nvidia(monkeypatch)
    monkeypatch.setattr(detection, "_ollama_reported_gb", lambda: 21.3)
    monkeypatch.setattr(detection.sys, "platform", "darwin")
    monkeypatch.setattr(detection.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(detection, "_detect_ram", lambda: (32768, 16384))
    assert detection.usable_accelerator_gb() == (21.3, "ollama-runtime")


def test_a_box_with_nothing_readable_says_so_and_does_not_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 with source "none" means UNKNOWN accelerator, never "no memory":
    system RAM still runs the model, and the fit verdict says exactly that."""
    _no_nvidia(monkeypatch)
    _no_vendor_sources(monkeypatch)
    monkeypatch.setattr(detection.sys, "platform", "linux")
    assert detection.usable_accelerator_gb() == (0.0, "none")


def test_a_failing_source_never_breaks_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A locked-down registry or an unreadable log is a normal machine."""

    def _boom() -> float:
        raise OSError("permission denied")

    _no_nvidia(monkeypatch)
    monkeypatch.setattr(detection, "_ollama_reported_gb", _boom)
    monkeypatch.setattr(detection.sys, "platform", "linux")
    assert detection.usable_accelerator_gb() == (0.0, "none")


# ── The realtime speech stack narrows the same probe ──────────────────────


def test_the_voice_stack_refuses_a_card_it_has_no_backend_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared probe now reads AMD and Intel cards for the local-model fit
    verdicts. The realtime speech stack maps any non-NVIDIA source to `mps`, so
    letting one through would clear the memory floor and then launch with a
    torch device that does not exist on the box — worse than the honest
    "no supported accelerator" refusal it gives today (os-parity P-28)."""
    from jarvis.realtime.local_server import preflight

    monkeypatch.setattr(
        "jarvis.hardware.detection.usable_accelerator_gb", lambda: (16.0, "windows-registry")
    )
    assert preflight._usable_accelerator_gb() == (0.0, "none")


@pytest.mark.parametrize(("source", "gb"), [("nvidia-smi", 16.0), ("apple-unified", 32.0)])
def test_the_voice_stack_still_takes_the_two_sources_it_can_drive(
    monkeypatch: pytest.MonkeyPatch, source: str, gb: float
) -> None:
    from jarvis.realtime.local_server import preflight

    monkeypatch.setattr("jarvis.hardware.detection.usable_accelerator_gb", lambda: (gb, source))
    assert preflight._usable_accelerator_gb() == (gb, source)
