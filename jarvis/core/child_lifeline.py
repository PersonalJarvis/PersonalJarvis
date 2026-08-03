"""Run one POSIX child tree with a parent-owned lifetime pipe.

The parent keeps the write end open. If it crashes, the kernel closes that end,
this supervisor observes EOF, and the entire process group is killed. Normal
shutdown reaches the child through stdin and lets it exit before the supervisor.

``--keep-fd`` forwards an inherited descriptor THROUGH this supervisor into the
real child. Without it the supervisor was the end of the line for every
descriptor above stdio: ``Popen`` closes them at exec, so a lock a caller
believed it had handed to the child was in fact held only here. The two
processes die together today, which is why that never surfaced as a bug — but
the guarantee the caller relies on ("the child holds this lock") was not true,
and the first change that lets this supervisor exit first would turn it into two
processes writing one profile with nothing left to stop them.
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


def run(
    lifeline_fd: int,
    command: Sequence[str],
    keep_fds: Sequence[int] = (),
) -> int:
    """Run ``command`` and kill its process group if ``lifeline_fd`` reaches EOF.

    ``keep_fds`` are descriptors this process inherited that must stay open in
    the real child as well (a held lock, for example). They are re-declared on
    the inner spawn because ``close_fds`` would otherwise drop them at exec.
    """
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
        child = subprocess.Popen(  # noqa: S603
            list(command),
            close_fds=True,
            pass_fds=tuple(keep_fds),
        )
        return int(child.wait())
    except (OSError, ValueError):  # Exit 127 is the supervisor-visible spawn failure signal.
        return 127
    finally:
        completed.set()
        try:
            os.close(lifeline_fd)
        except OSError:  # The descriptor may already be closed by parent teardown.
            pass


def _parse_keep_fds(args: Sequence[str]) -> tuple[list[int], int]:
    """Read the leading ``--keep-fd N`` pairs; return them and the next index."""
    keep_fds: list[int] = []
    index = 1
    while index + 1 < len(args) and args[index] == "--keep-fd":
        keep_fds.append(int(args[index + 1]))
        index += 2
    return keep_fds, index


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the internal ``FD [--keep-fd N ...] -- command`` contract.

    The ``--keep-fd`` pairs are optional, so an argv built before they existed
    still parses to exactly the old behaviour.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3:
        return 2
    try:
        lifeline_fd = int(args[0])
        keep_fds, separator = _parse_keep_fds(args)
    except ValueError:  # Invalid internal argv is reported by the documented exit code.
        return 2
    if separator >= len(args) or args[separator] != "--":
        return 2
    command = args[separator + 1 :]
    if not command or any(descriptor < 0 for descriptor in keep_fds):
        return 2
    return run(lifeline_fd, command, keep_fds)


if __name__ == "__main__":
    raise SystemExit(main())
