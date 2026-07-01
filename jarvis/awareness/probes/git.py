"""GitProbe — branch detection via .git/HEAD or asyncio git subprocess.

Strategy (in order):
1. Direct file read of .git/HEAD in cwd (fast, no subprocess) — PRIMARY
2. asyncio subprocess fallback ``git rev-parse --abbrev-ref HEAD`` — if (1) fails
3. None — if both fail or there is no repo

Hard Negatives §9:
- NO sync ``subprocess.run`` — everything uses ``asyncio.create_subprocess_exec``
- 200 ms hard timeout per call (``asyncio.wait_for``)
- Errors MUST NOT propagate — ``try/except`` returns ``None``
- Probe is defensive: any unknown error -> ``None`` instead of crash
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

logger = logging.getLogger(__name__)

_HEAD_FILE_TIMEOUT_S = 0.05      # 50 ms for .git/HEAD read
_SUBPROCESS_TIMEOUT_S = 0.15     # 150 ms for git subprocess
_TOTAL_BUDGET_S = 0.20           # 200 ms hard cap (Plan §9 AC)


class GitProbe:
    """Probe for the git branch of the active process cwd."""

    name: str = "git"

    async def probe(self, *, cwd: str | None, process_name: str = "") -> dict[str, Any]:
        """Returns ``{git_branch: str | None}``. No crash on any failure."""
        if cwd is None:
            return {"git_branch": None}

        # Strategy 1: .git/HEAD direct-read (fast, no subprocess)
        try:
            branch = await asyncio.wait_for(
                asyncio.to_thread(self._read_head_file, cwd),
                timeout=_HEAD_FILE_TIMEOUT_S,
            )
            if branch is not None:
                return {"git_branch": branch}
        except (TimeoutError, asyncio.CancelledError):
            return {"git_branch": None}
        except Exception:    # noqa: BLE001
            # File read failed unexpectedly — fall through to subprocess
            logger.debug("GitProbe head-file read failed", exc_info=True)

        # Strategy 2: asyncio subprocess fallback
        try:
            return {"git_branch": await asyncio.wait_for(
                self._git_subprocess(cwd),
                timeout=_SUBPROCESS_TIMEOUT_S,
            )}
        except (TimeoutError, asyncio.CancelledError):
            return {"git_branch": None}
        except Exception:    # noqa: BLE001
            logger.debug("GitProbe subprocess failed", exc_info=True)
            return {"git_branch": None}

    @staticmethod
    def _read_head_file(cwd: str) -> str | None:
        """Reads ``.git/HEAD`` directly. Returns the branch name or SHA prefix for detached HEAD.

        Handles 3 cases:
        - ``.git/HEAD`` is a regular file (standard repo)
        - ``.git`` is a FILE containing ``"gitdir: ../<actual-path>"`` (worktree repo)
        - ``.git`` does not exist (not a repo)

        Returns ``None`` on any unknown failure.
        """
        try:
            git_dir_or_file = Path(cwd) / ".git"
            if not git_dir_or_file.exists():
                return None

            # Worktree repo: .git is a file containing "gitdir: ..."
            if git_dir_or_file.is_file():
                content = git_dir_or_file.read_text(encoding="utf-8").strip()
                if content.startswith("gitdir:"):
                    raw = content[len("gitdir:"):].strip()
                    raw_path = Path(raw)
                    # Codex-MINOR-m5-Fix (2026-04-26): resolve relative gitdir
                    # against cwd instead of os.getcwd() — modern git usually
                    # writes absolute paths, but `git worktree add --relative`
                    # or submodules write a relative path.
                    actual_git_dir = (
                        raw_path if raw_path.is_absolute()
                        else (Path(cwd) / raw_path).resolve()
                    )
                    head_path = actual_git_dir / "HEAD"
                else:
                    return None
            else:
                head_path = git_dir_or_file / "HEAD"

            if not head_path.exists():
                return None

            head_content = head_path.read_text(encoding="utf-8").strip()
            if head_content.startswith("ref: refs/heads/"):
                return head_content[len("ref: refs/heads/"):]
            # detached HEAD: SHA -> return first 8 chars
            if len(head_content) >= 8:
                return head_content[:8]
            return None
        except (OSError, UnicodeDecodeError):
            return None

    async def _git_subprocess(self, cwd: str) -> str | None:
        """asyncio subprocess for ``git rev-parse --abbrev-ref HEAD``.

        Returns the branch name or ``None``. Any failure (FileNotFoundError for
        a missing git binary, non-zero exit, decode error) returns ``None``.

        Codex-BLOCKER-2-Fix (2026-04-26): on every failure path, proper
        subprocess cleanup with ``terminate -> wait_for(wait, 0.05) -> kill ->
        wait`` in finally — otherwise zombie risk on cancel. CancelledError
        is cleaned up and re-raised so that manager shutdown propagates
        correctly (instead of being silently swallowed as "no branch").
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=NO_WINDOW_CREATIONFLAGS,
            )
        except (FileNotFoundError, OSError, NotImplementedError):
            return None
        try:
            try:
                stdout, _stderr = await proc.communicate()
            except asyncio.CancelledError:
                # Cleanup + re-raise so that shutdown propagates correctly
                await self._cleanup_proc(proc)
                raise
            except OSError:
                await self._cleanup_proc(proc)
                return None
            if proc.returncode != 0:
                return None
            try:
                branch = stdout.decode().strip()
                return branch or None
            except UnicodeDecodeError:
                return None
        finally:
            # Defensive: if the process is still alive after communicate
            # (e.g. due to a race), do explicit cleanup. Idempotent.
            if proc.returncode is None:
                await self._cleanup_proc(proc)

    @staticmethod
    async def _cleanup_proc(proc: asyncio.subprocess.Process) -> None:
        """Guarantees a dead subprocess: terminate -> wait 50 ms -> kill -> wait.

        No raise — all cleanup errors are swallowed (probe defense).
        """
        try:
            proc.terminate()
        except (OSError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.05)
            return
        except (TimeoutError, OSError):
            pass
        try:
            proc.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=0.05)
        except (TimeoutError, OSError):
            pass
