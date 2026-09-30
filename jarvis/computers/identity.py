"""Jarvis's own SSH key pair — the one key every connected computer trusts.

Generated once on first use (Ed25519), the private half stored through
``set_secret`` (OS keyring, or the 0600 file fallback on a headless box) and
never written anywhere else. The public half is what the user pastes into a
hosting panel, what cloud-init plants into a new VM, and what the
"install key with password" step appends to ``~/.ssh/authorized_keys``.
"""

from __future__ import annotations

import logging
import platform
import threading
from typing import TYPE_CHECKING, Any

from jarvis.core.config import get_secret, set_secret

if TYPE_CHECKING:
    import asyncssh

log = logging.getLogger(__name__)

#: Keyring slot of the private key (OpenSSH PEM text).
PRIVATE_KEY_SECRET = "computers_ssh_private_key"  # noqa: S105 — a slot name, not a secret

_LOCK = threading.Lock()


class IdentityError(RuntimeError):
    """The key pair could not be created or stored."""


def _comment() -> str:
    host = platform.node().strip() or "desktop"
    return f"personal-jarvis@{host}"


def private_key() -> asyncssh.SSHKey:
    """Return the key pair, creating and storing it on first use."""
    import asyncssh

    with _LOCK:
        pem = get_secret(PRIVATE_KEY_SECRET, env_fallback="JARVIS_COMPUTERS_SSH_KEY")
        if pem:
            try:
                return asyncssh.import_private_key(pem)
            except (asyncssh.KeyImportError, ValueError) as exc:
                # A damaged slot must not silently mint a new key: every
                # computer that trusts the old one would lock us out.
                raise IdentityError(
                    "Jarvis's SSH key in the keyring is damaged and cannot be read."
                ) from exc
        key = asyncssh.generate_private_key("ssh-ed25519", comment=_comment())
        text = key.export_private_key("openssh").decode("utf-8")
        if not set_secret(PRIVATE_KEY_SECRET, text):
            raise IdentityError("The new SSH key could not be saved to the keyring.")
        log.info("computers: created Jarvis's SSH key pair")
        return key


def public_key_line() -> str:
    """The OpenSSH public key line (``ssh-ed25519 AAAA... comment``)."""
    return private_key().export_public_key("openssh").decode("utf-8").strip()


def describe() -> dict[str, Any]:
    """Public key plus fingerprint for the UI; creates the key when missing."""
    from jarvis.computers.ssh import authorize_key_command

    key = private_key()
    public = key.export_public_key("openssh").decode("utf-8").strip()
    return {
        "public_key": public,
        "fingerprint": key.get_fingerprint("sha256"),
        "algorithm": key.get_algorithm(),
        # Run once on the server (hosting web console or any SSH session) for
        # a server that lets in known keys only.
        "install_command": authorize_key_command(public),
    }
