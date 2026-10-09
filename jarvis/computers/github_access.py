"""Share this PC's GitHub login with the coding agents on one computer.

Portions adapted from pingdotgg/t3code @ 12069ee (apps/web
GitHubRoutingSettings.tsx: per-machine GitHub sharing with the trust warning
next to the switch), MIT License, Copyright (c) 2026 T3 Tools Inc. Full text:
third_party/t3code/LICENSE.

An agent working in tmux on a server has to clone and push without this PC
being on. On an explicit click, :func:`share` writes this PC's GitHub token
(the app's GitHub connection, else the GitHub CLI's login) to
:data:`remote_os.GITHUB_ENV_FILE` on the server — readable by that login
only, sourced by every agent launcher — and points git's credential helper
for ``https://github.com`` at it, so ``git push`` and ``gh`` both work there.
The token itself never enters ``~/.gitconfig``, a command line or a log.

The token carries the user's whole GitHub access; the UI says so beside the
switch. :func:`unshare` deletes the file and the helper again.
"""

from __future__ import annotations

import logging
import shlex
import time

from jarvis.computers import remote_os
from jarvis.computers.models import Computer
from jarvis.computers.service import ComputerError, get_service
from jarvis.computers.ssh import SshError

log = logging.getLogger(__name__)

_HELPER_KEY = "credential.https://github.com.helper"
#: Reads the token from the environment the launcher set; holds no secret.
HELPER = (
    '!f() { test "$1" = get && echo username=x-access-token && echo "password=${GH_TOKEN}"; }; f'
)


def _local_token() -> tuple[str, str]:
    from jarvis.agentic_ide import github_link

    found = github_link.credential()
    if found is None:
        raise ComputerError(
            "GitHub is not connected on this PC. Connect it under Plugins, GitHub, or log in "
            "with the GitHub CLI (gh auth login), then share it again.",
            status=409,
            kind="github_missing",
        )
    return found.token, found.source


def env_file_body(token: str) -> str:
    quoted = shlex.quote(token)
    return (
        "# Written by Personal Jarvis: the GitHub login this PC shared with the agents here.\n"
        f"export GH_TOKEN={quoted}\n"
        f"export GITHUB_TOKEN={quoted}\n"
    )


def helper_script(on: bool) -> str:
    """The git configuration step; removes only the helper this module set."""
    key = shlex.quote(_HELPER_KEY)
    ours = shlex.quote(HELPER)
    if on:
        return (
            f"git config --global --unset-all {key} >/dev/null 2>&1\n"
            f"git config --global --add {key} ''\n"  # drop helpers inherited from system config
            f"git config --global --add {key} {ours}\n"
        )
    return (
        f'if [ "$(git config --global --get-all {key} 2>/dev/null | tail -n 1)" = {ours} ]; then\n'
        f"  git config --global --unset-all {key}\n"
        "fi\n"
        f'rm -f "$HOME/{remote_os.GITHUB_ENV_FILE}"\n'
    )


async def share(computer_id: str) -> Computer:
    """Write this PC's GitHub login to the computer (explicit user action)."""
    token, source = _local_token()
    service = get_service()
    async with service.session(computer_id) as session:
        try:
            host = await remote_os.remote_host(computer_id, session)
            await remote_os.upload_text(
                session, host, remote_os.GITHUB_ENV_FILE, env_file_body(token), private=True
            )
            result = await remote_os.run_script(session, host, helper_script(True), timeout_s=30)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
    if result.exit_status not in (0, None):
        log.warning(
            "computers: git credential helper not set on %s (exit %s)",
            computer_id,
            result.exit_status,
        )
        raise ComputerError(
            "The GitHub login was saved, but git could not be configured there. "
            "Install git on that computer (Agents tab), then share again.",
            status=502,
            kind="git_missing",
        )
    log.info(
        "computers: shared the GitHub login (%s) with %s at the user's request", source, computer_id
    )
    return _mark(computer_id, time.time())


async def unshare(computer_id: str) -> Computer:
    """Remove the shared login and the helper from the computer again."""
    service = get_service()
    async with service.session(computer_id) as session:
        try:
            host = await remote_os.remote_host(computer_id, session)
            await remote_os.run_script(session, host, helper_script(False), timeout_s=30)
        except SshError as exc:
            raise ComputerError(exc.message, status=502, kind=exc.kind) from exc
    log.info("computers: removed the shared GitHub login from %s", computer_id)
    return _mark(computer_id, None)


def _mark(computer_id: str, when: float | None) -> Computer:
    updated = get_service()._store.update(  # noqa: SLF001 — the service owns the record
        computer_id, lambda row: row.model_copy(update={"github_shared_at": when})
    )
    if updated is None:
        raise ComputerError("This computer does not exist.", status=404)
    return updated
