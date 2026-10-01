"""Both channel forms, alternate encodings, and connection-local installation."""

from types import SimpleNamespace

import asyncssh
import pytest
from asyncssh.constants import MSG_CHANNEL_OPEN, MSG_CHANNEL_OPEN_CONFIRMATION
from asyncssh.packet import PacketDecodeError, SSHPacket, String, UInt32

from jarvis.computers.ssh_packet_guard import guarded_client_factory, install_channel_guard


def packet_for(kind, size):
    prefix = String("session") if kind == MSG_CHANNEL_OPEN else UInt32(7)
    return SSHPacket(prefix + UInt32(8) + UInt32(1024) + UInt32(size))


def connection_for(calls, *, version=b"test", compressed=False):
    def original(connection, kind, packet_id, packet):
        calls.append((kind, packet_id, packet.get_remaining_payload()))

    return SimpleNamespace(
        _packet_handlers={MSG_CHANNEL_OPEN: original, MSG_CHANNEL_OPEN_CONFIRMATION: original},
        _client_version=version,
        _server_version=version,
        _compressor=compressed,
    )


@pytest.mark.parametrize("kind", [MSG_CHANNEL_OPEN, MSG_CHANNEL_OPEN_CONFIRMATION])
@pytest.mark.parametrize(
    ("size", "version", "compressed"), [(0, b"test", False), (1, b"dropbear", True)]
)
def test_invalid_effective_size_is_rejected_before_dispatch(kind, size, version, compressed):
    calls = []
    connection = connection_for(calls, version=version, compressed=compressed)
    install_channel_guard(connection)
    with pytest.raises(asyncssh.ProtocolError, match="packet size"):
        connection._packet_handlers[kind](connection, kind, 1, packet_for(kind, size))
    assert calls == []


@pytest.mark.parametrize("kind", [MSG_CHANNEL_OPEN, MSG_CHANNEL_OPEN_CONFIRMATION])
def test_valid_packet_reaches_the_original_handler_unchanged(kind):
    calls = []
    connection = connection_for(calls)
    source_handlers = connection._packet_handlers
    install_channel_guard(connection)
    packet = packet_for(kind, 1)
    raw = packet.get_remaining_payload()
    connection._packet_handlers[kind](connection, kind, 9, packet)
    assert calls == [(kind, 9, raw)]
    assert connection._packet_handlers is not source_handlers
    source_handlers[kind](connection, kind, 10, packet_for(kind, 0))
    assert calls[-1][1] == 10, "another connection's dispatch table is not patched"


def test_truncated_packet_is_not_reinterpreted_as_valid():
    connection = connection_for([])
    install_channel_guard(connection)
    with pytest.raises(PacketDecodeError):
        connection._packet_handlers[MSG_CHANNEL_OPEN](
            connection, MSG_CHANNEL_OPEN, 0, SSHPacket(b"")
        )


def test_unknown_dispatch_shape_fails_closed():
    with pytest.raises(asyncssh.ProtocolError, match="unavailable"):
        install_channel_guard(SimpleNamespace(_packet_handlers={}))


def test_reinstall_is_idempotent_and_owner_callbacks_are_preserved():
    calls = []

    class Owner(asyncssh.SSHClient):
        def connection_made(self, connection):
            calls.append("connected")

        def password_auth_requested(self):
            calls.append("password offered")

    connection = connection_for([])
    client = guarded_client_factory(Owner)()
    client.connection_made(connection)
    table = connection._packet_handlers
    install_channel_guard(connection)
    assert connection._packet_handlers is table
    client.password_auth_requested()
    assert calls == ["connected", "password offered"]


def test_missing_guard_capability_aborts_before_owner_callback():
    calls = []
    connection = SimpleNamespace(_packet_handlers={}, abort=lambda: calls.append("aborted"))
    with pytest.raises(asyncssh.ProtocolError):
        guarded_client_factory()().connection_made(connection)
    assert calls == ["aborted"]
