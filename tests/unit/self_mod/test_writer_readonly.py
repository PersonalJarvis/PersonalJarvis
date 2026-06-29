"""Writer robustness against a read-only target (config-drift defense, BUG-010).

The drift defense sets jarvis.toml read-only to stop parallel sessions from
silently overwriting it. The AUTHORIZED atomic writer is itself part of that
defense, so it must still get through: on Windows os.replace fails with WinError
5 (Access Denied) when the target is read-only. The writer must clear the
attribute for the atomic swap and restore it afterwards — keeping the defense in
place. Live forensic 2026-06-25: every CLI/voice config write returned
"write_failed: [WinError 5] Zugriff verweigert ... jarvis.toml".
"""
from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

import pytest

from jarvis.core.config import JarvisConfig
from jarvis.core.self_mod import AtomicConfigWriter, SelfModAudit
from jarvis.core.self_mod.schema import MutationRequest

FIXTURE = Path(__file__).parent / "fixtures" / "minimal_jarvis.toml"


def _loader(path: Path) -> JarvisConfig:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return JarvisConfig.model_validate(tomllib.loads(raw.decode("utf-8")))


def _read_speed(target: Path) -> float:
    raw = target.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return tomllib.loads(raw.decode("utf-8"))["tts"]["speed"]


@pytest.fixture
def writer_and_target(tmp_path: Path):
    target = tmp_path / "jarvis.toml"
    target.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    writer = AtomicConfigWriter(
        config_path=target,
        backup_dir=tmp_path / "backups",
        audit=SelfModAudit(path=tmp_path / "audit.log"),
        config_loader=_loader,
    )
    return writer, target


def test_mutate_succeeds_when_target_is_readonly(writer_and_target) -> None:
    writer, target = writer_and_target
    os.chmod(target, stat.S_IREAD)  # config-drift defense: read-only
    assert not os.access(target, os.W_OK), "precondition: target is read-only"

    result = writer.mutate(
        MutationRequest(path="tts.speed", new_value=1.25, reason="test")
    )

    assert result.ok is True, f"write must succeed on a read-only target: {result.error_message}"
    assert _read_speed(target) == 1.25  # the value actually landed on disk
    # The drift defense is preserved: the file is read-only again afterwards.
    assert not os.access(target, os.W_OK)


def test_mutate_leaves_writable_target_writable(writer_and_target) -> None:
    # State-preserving: a normally-writable target stays writable (no surprise
    # read-only attribute introduced by the writer).
    writer, target = writer_and_target
    assert os.access(target, os.W_OK)
    result = writer.mutate(
        MutationRequest(path="tts.speed", new_value=0.9, reason="test")
    )
    assert result.ok is True
    assert os.access(target, os.W_OK)
