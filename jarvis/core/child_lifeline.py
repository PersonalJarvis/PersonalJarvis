"""Run one POSIX child tree with a parent-owned lifetime pipe.

The parent keeps the write end open. If it crashes, the kernel closes that end,
this supervisor observes EOF, and the entire process group is killed. Normal
shutdown reaches the child through stdin and lets it exit before the supervisor.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Sequence


def _kill_process_group() -> None:
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)
    except (AttributeError, OSError):  # Hard exit is the containment fallback on this host.
        os._exit(137)


def _watch_parent(lifeline_fd: int, completed: threading.Event) -> None:
    try:
        while os.read(lifeline_fd, 1):
            pass
    except OSError:  # A closed lifeline is the parent-death signal this thread waits for.
        pass
    if not completed.is_set():
        _kill_process_group()


def run(lifeline_fd: int, command: Sequence[str]) -> int:
    """Run ``command`` and kill its process group if ``lifeline_fd`` reaches EOF."""
    if lifeline_fd < 0 or not command:
        return 2
    completed = threading.Event()
    watcher = threading.Thread(
        target=_watch_parent,
        args=(lifeline_fd, completed),
        name="jarvis-parent-lifeline",
        daemon=True,
    )
    watcher.start()
    try:
        child = subprocess.Popen(list(command), close_fds=True)  # noqa: S603
        return int(child.wait())
    except (OSError, ValueError):  # Exit 127 is the supervisor-visible spawn failure signal.
        return 127
    finally:
        completed.set()
        try:
            os.close(lifeline_fd)
        except OSError:  # The descriptor may already be closed by parent teardown.
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the fixed internal ``FD -- command`` contract."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        return 2
    try:
        lifeline_fd = int(args[0])
    except ValueError:  # Invalid internal argv is reported by the documented exit code.
        return 2
    return run(lifeline_fd, args[2:])


if __name__ == "__main__":
    raise SystemExit(main())
