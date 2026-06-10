"""Throwaway spike — does `openclaw agent --model xai/grok-<m>` write a file?

De-risks Wave B2 of the sub-agent reliability rebuild: re-enabling the OpenClaw
worker so a *selected* non-claude/non-codex provider (grok/openai/openrouter)
actually runs as a file-writing agent instead of silently falling back to Claude.

Reuses the surviving machinery (provider_chain + provider_map). Exit 0 if the
file was written; non-zero (or SKIP) otherwise. Prints the verdict.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.core.config import get_secret, load_config  # noqa: E402
from jarvis.missions.isolation.env import build_worker_env  # noqa: E402
from jarvis.missions.workers.process_utils import (  # noqa: E402
    worker_creationflags as _win32_creationflags,
)
from jarvis.missions.workers.provider_chain import (  # noqa: E402
    _build_openclaw_cmd,
    _resolve_worker_argv_prefix,
)
from jarvis.missions.worker_runtime.provider_map import (  # noqa: E402
    to_provider_slug,
)

PROMPT = (
    "Create a new text file named spike_ok.txt in the current working directory "
    "whose entire contents are exactly the single line: GROK-OK\n"
    "Do nothing else."
)


async def main() -> int:
    key = get_secret("grok_api_key", env_fallback="GROK_API_KEY") or get_secret(
        "xai_api_key", env_fallback="XAI_API_KEY"
    )
    if not key:
        print("[SKIP] no grok/xai key configured")
        return 0

    cfg = load_config()
    providers = getattr(cfg.brain, "providers", {}) or {}
    pcfg = providers.get("grok")
    model = (getattr(pcfg, "deep_model", None) or getattr(pcfg, "model", None)
             or "grok-4.3")
    slug = to_provider_slug("grok")  # -> "xai"

    with tempfile.TemporaryDirectory(prefix="oc-spike-") as tmp:
        mission_dir = Path(tmp)
        worktree = mission_dir / "wt"
        worktree.mkdir()

        # The REAL worker env: seeds the plugin-skills location (avoids the
        # EPERM symlink crash on non-admin Windows), sets MISSION_STATE_DIR,
        # injects XAI_API_KEY/GROK_API_KEY. This is what the live workers use.
        env = build_worker_env(run_dir=mission_dir, xai_api_key=key)

        cmd = _build_openclaw_cmd(
            PROMPT,
            binary=_resolve_worker_argv_prefix(),
            session_id=str(uuid.uuid4()),
            openclaw_slug=slug,
            model=str(model),
            timeout_s=120,
        )
        print(f"[..] spawning: openclaw agent --model {slug}/{model} (cwd={worktree})")
        flags = _win32_creationflags() if sys.platform == "win32" else 0
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(worktree), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=flags,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=180.0)
        except FileNotFoundError as exc:
            print(f"[SKIP] openclaw binary not found: {exc}")
            return 0
        except asyncio.TimeoutError:
            print("[FAIL] openclaw spike timed out (180s)")
            return 1

        wrote = (worktree / "spike_ok.txt").exists()
        content = ""
        if wrote:
            content = (worktree / "spike_ok.txt").read_text(encoding="utf-8", errors="replace").strip()
        print(f"[..] exit={proc.returncode} file_written={wrote} content={content!r}")
        print(f"[..] stdout[:400]={out.decode('utf-8','replace')[:400]!r}")
        print(f"[..] stderr[:400]={err.decode('utf-8','replace')[:400]!r}")
        if wrote:
            print("[OK] OpenClaw grok spike WROTE a file — B2 path is viable.")
            return 0
        print("[FAIL] OpenClaw grok spike did NOT write a file.")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
