"""Reject SSH channel parameters which would stop the send loop making progress.

AsyncSSH 2.24.0 still accepts zero packet sizes despite its advisory metadata.
Keep the fix local to each Jarvis connection, before any channel can be used.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def install_channel_guard(connection: Any) -> None:
    """Wrap both peer-controlled channel-open paths without consuming their packet."""
    import asyncssh
    from asyncssh.constants import MSG_CHANNEL_OPEN, MSG_CHANNEL_OPEN_CONFIRMATION
    from asyncssh.packet import SSHPacket

    if getattr(connection, "_jarvis_channel_guard", False):
        return
    handlers = getattr(connection, "_packet_handlers", None)
    kinds = (MSG_CHANNEL_OPEN, MSG_CHANNEL_OPEN_CONFIRMATION)
    if not isinstance(handlers, Mapping) or any(not callable(handlers.get(k)) for k in kinds):
        raise asyncssh.ProtocolError("SSH channel parameter validation is unavailable")
    guarded = dict(handlers)
    for kind in kinds:
        original = handlers[kind]

        def checked(conn, packet_type, packet_id, packet, *, original=original, kind=kind):
            probe = SSHPacket(packet.get_remaining_payload())
            if kind == MSG_CHANNEL_OPEN:
                probe.get_string()
                probe.get_uint32()
                probe.get_uint32()
                version = getattr(conn, "_client_version", None)
            else:
                probe.get_uint32()
                probe.get_uint32()
                probe.get_uint32()
                version = getattr(conn, "_server_version", None)
            size = probe.get_uint32()
            if not isinstance(version, bytes) or not hasattr(conn, "_compressor"):
                raise asyncssh.ProtocolError("SSH channel parameter validation is unavailable")
            # AsyncSSH subtracts one for compressed Dropbear channels. A wire
            # value of one is therefore another spelling of the same zero.
            effective = size - int(b"dropbear" in version and bool(conn._compressor))
            if effective <= 0:
                raise asyncssh.ProtocolError("Invalid maximum SSH packet size")
            return original(conn, packet_type, packet_id, packet)

        guarded[kind] = checked
    connection._packet_handlers = guarded
    connection._jarvis_channel_guard = True


def guarded_client_factory(base_type: Any = None) -> Any:
    """Preserve authentication callbacks while installing the guard before traffic."""
    import asyncssh

    class GuardedClient(base_type or asyncssh.SSHClient):
        def connection_made(self, connection: Any) -> None:
            try:
                install_channel_guard(connection)
                super().connection_made(connection)
            except Exception:
                connection.abort()
                raise

    return GuardedClient
