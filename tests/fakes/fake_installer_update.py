"""Fakes for the two edges of ``jarvis.core.installer_update``.

The updater talks to exactly two things it cannot own in a test: the network
(:class:`FakeAssetFetcher`) and the operating system's process table
(:class:`FakeCommandRunner`). Replacing those two makes the whole flow —
including the macOS and Linux handovers — runnable on any OS, offline, without
installing anything.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FakeAssetFetcher", "FakeCommandRunner", "RunResult"]


def _write(dest: Path, payload: bytes) -> None:
    """Blocking write, kept out of the async body (ruff ASYNC240)."""
    dest.write_bytes(payload)


@dataclass
class FakeAssetFetcher:
    """Serves a checksum manifest and one installer payload from memory.

    ``fail_text`` / ``fail_download`` raise the given exception instead, which is
    how the transport-error branches are exercised without a socket.
    """

    manifest: str = ""
    payload: bytes = b""
    fail_text: Exception | None = None
    fail_download: Exception | None = None
    text_urls: list[str] = field(default_factory=list)
    download_urls: list[str] = field(default_factory=list)
    progress_ticks: list[tuple[int, int | None]] = field(default_factory=list)

    async def get_text(self, url: str, *, max_bytes: int) -> str:
        self.text_urls.append(url)
        if self.fail_text is not None:
            raise self.fail_text
        if len(self.manifest.encode("utf-8")) > max_bytes:
            raise AssertionError("the fake manifest exceeds the caller's cap")
        return self.manifest

    async def download(
        self,
        url: str,
        dest: Path,
        *,
        max_bytes: int,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> int:
        self.download_urls.append(url)
        if self.fail_download is not None:
            raise self.fail_download
        from jarvis.core.installer_update import InstallerUpdateError

        if len(self.payload) > max_bytes:
            # Mirrors the real streaming guard: the partial file is left for the
            # caller to discard, exactly as httpx would leave it.
            _write(dest, self.payload[:max_bytes])
            raise InstallerUpdateError(
                f"installer download exceeded {max_bytes} bytes - refusing it"
            )
        # Emit the payload in slices so a test sees a real ramp, not one 100%
        # tick — the shape a progress bar is actually built against.
        total = len(self.payload)
        if on_progress is not None:
            step = max(1, total // 4) if total else 1
            for written in range(0, total, step):
                on_progress(written, total)
                self.progress_ticks.append((written, total))
        _write(dest, self.payload)
        if on_progress is not None:
            on_progress(total, total)
            self.progress_ticks.append((total, total))
        return total


@dataclass(frozen=True)
class RunResult:
    """What a faked command "returned"."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass
class FakeCommandRunner:
    """Records commands instead of running them.

    ``on_run`` lets a test give one command a side effect — mounting a DMG, for
    instance, is modelled by creating the app bundle inside the mountpoint the
    caller passed on the command line.
    """

    results: dict[str, RunResult] = field(default_factory=dict)
    on_run: Callable[[Sequence[str]], None] | None = None
    spawn_error: OSError | None = None
    ran: list[list[str]] = field(default_factory=list)
    spawned: list[list[str]] = field(default_factory=list)

    def run(self, command: Sequence[str], *, timeout_s: float) -> tuple[int, str, str]:
        recorded = list(command)
        self.ran.append(recorded)
        if self.on_run is not None:
            self.on_run(recorded)
        result = self._result_for(recorded)
        return result.returncode, result.stdout, result.stderr

    def spawn_detached(self, command: Sequence[str]) -> None:
        if self.spawn_error is not None:
            raise self.spawn_error
        self.spawned.append(list(command))

    def _result_for(self, command: Sequence[str]) -> RunResult:
        # Keyed by the sub-command ("attach", "detach") when there is one, else
        # by the executable, so a test can steer hdiutil's two calls apart.
        for key in (
            " ".join(command[:2]),
            command[1] if len(command) > 1 else "",
            command[0],
        ):
            if key and key in self.results:
                return self.results[key]
        return RunResult()
