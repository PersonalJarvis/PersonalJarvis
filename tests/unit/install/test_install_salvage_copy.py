"""install.sh salvage_reclone copy loop — user state must survive a re-clone.

The 2026-07-20 wipe: a fresh clone SHIPS a tracked seed ``wiki/`` skeleton,
so a "skip when the destination exists" guard silently never restored the
user's real vault. Contract under test (the marked block inside
``install/install.sh``):

  1. items absent from the fresh tree (data/, jarvis.toml, .env) are copied
     back verbatim;
  2. a directory existing on BOTH sides (wiki) is overlay-merged — the
     user's files land next to (and over) the seed files, never skipped;
  3. a missing backup item is simply skipped without an error.

Runs the REAL block extracted from install.sh, so the guard logic cannot
drift from what ships.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALL_SH = REPO / "install" / "install.sh"

BLOCK_BEGIN = "# --- salvage-copy begin"
BLOCK_END = "# --- salvage-copy end"

DRIVER = """#!/usr/bin/env bash
set -u
note() { printf 'NOTE|%s\\n' "$*"; }
err() { printf 'ERR|%s\\n' "$*" >&2; }
CONFIG_FILE_NAME="jarvis.toml"
salvage_copy() {
    source "$BLOCK_FILE"
}
salvage_copy
"""


def _find_bash() -> str | None:
    git = shutil.which("git")
    if git:
        for rel in ("../bin/bash.exe", "../../bin/bash.exe", "../usr/bin/bash.exe"):
            cand = (Path(git).parent / rel).resolve()
            if cand.exists():
                return str(cand)
    bash = shutil.which("bash")
    if bash and not any(t in bash.lower() for t in ("windowsapps", "system32")):
        return bash
    return None


BASH = _find_bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no usable bash on this host")


def _block_text() -> str:
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert BLOCK_BEGIN in src and BLOCK_END in src, (
        "the salvage-copy extraction markers vanished from install.sh"
    )
    return src.split(BLOCK_BEGIN, 1)[1].split(BLOCK_END, 1)[0]


def _sh_path(p: Path) -> str:
    return str(p).replace("\\", "/")


def _run_salvage(tmp_path: Path, install: Path, stale: Path) -> subprocess.CompletedProcess[str]:
    block = tmp_path / "salvage-block.sh"
    block.write_text(_block_text(), encoding="utf-8")
    driver = tmp_path / "driver.sh"
    driver.write_text(DRIVER, encoding="utf-8")
    env = {
        "BLOCK_FILE": _sh_path(block),
        "INSTALL_DIR": _sh_path(install),
        "stale_backup": _sh_path(stale),
        "PATH": "/usr/bin:/bin",
    }
    return subprocess.run(  # noqa: S603 -- test-controlled bash + script
        [BASH, str(driver)], capture_output=True, text=True, timeout=60, env=env
    )


def test_user_wiki_vault_merges_over_the_shipped_seed(tmp_path: Path) -> None:
    install = tmp_path / "install"
    stale = tmp_path / "stale"
    # The fresh clone ships the tracked wiki seed skeleton.
    (install / "wiki" / "obsidian-vault" / "00-index").mkdir(parents=True)
    (install / "wiki" / "obsidian-vault" / "schema.md").write_text("seed", encoding="utf-8")
    # The user's previous install: real vault pages + config + data + dotenv.
    (stale / "wiki" / "obsidian-vault").mkdir(parents=True)
    (stale / "wiki" / "obsidian-vault" / "Home.md").write_text("user home", encoding="utf-8")
    (stale / "wiki" / "obsidian-vault" / "schema.md").write_text("user schema", encoding="utf-8")
    (stale / "jarvis.toml").write_text("[brain]\n", encoding="utf-8")
    (stale / ".env").write_text("GROQ_API_KEY=gsk-x\n", encoding="utf-8")
    (stale / "data").mkdir()
    (stale / "data" / "credentials.json").write_text("{}", encoding="utf-8")

    proc = _run_salvage(tmp_path, install, stale)

    assert proc.returncode == 0, proc.stderr
    assert (install / "wiki" / "obsidian-vault" / "Home.md").read_text(
        encoding="utf-8"
    ) == "user home", (
        "the user's vault page must be restored even though the fresh clone "
        "already ships a wiki/ seed — the 2026-07-20 regression"
    )
    assert (install / "wiki" / "obsidian-vault" / "schema.md").read_text(
        encoding="utf-8"
    ) == "user schema", "on a conflict the user's copy wins over the seed"
    assert (install / "wiki" / "obsidian-vault" / "00-index").is_dir(), (
        "seed content the user never touched stays in place"
    )
    assert (install / "jarvis.toml").is_file()
    assert (install / ".env").is_file()
    assert (install / "data" / "credentials.json").is_file()
    assert "ERR|" not in proc.stderr


def test_missing_backup_items_are_skipped_silently(tmp_path: Path) -> None:
    install = tmp_path / "install"
    stale = tmp_path / "stale"
    install.mkdir()
    stale.mkdir()

    proc = _run_salvage(tmp_path, install, stale)

    assert proc.returncode == 0, proc.stderr
    assert "ERR|" not in proc.stderr
