"""A malicious peer must not freeze the event loop; normal SSH remains usable."""

from __future__ import annotations

import asyncssh
import pytest

from jarvis.computers.ssh_packet_guard import guarded_client_factory

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(10)]


class LocalServer(asyncssh.SSHServer):
    def begin_auth(self, username):
        return False


async def echo(process):
    try:
        process.stdout.write(await process.stdin.read())
        process.exit(0)
    except (asyncssh.Error, OSError):
        # The malicious-packet cases deliberately close the transport first.
        return


@pytest.mark.parametrize(
    ("size", "dropbear", "accepted"),
    [(32768, False, True), (2, True, True), (0, False, False), (1, True, False)],
)
async def test_peer_packet_size_cannot_stall_a_real_connection(size, dropbear, accepted):
    compression = ["zlib@openssh.com"] if dropbear else ["none"]
    host_key = asyncssh.generate_private_key("ssh-ed25519")
    listener = await asyncssh.listen(
        "127.0.0.1",
        0,
        server_factory=LocalServer,
        server_host_keys=[host_key],
        server_version="dropbear_test" if dropbear else "test_server",
        process_factory=echo,
        max_pktsize=size,
        compression_algs=compression,
    )
    try:
        async with asyncssh.connect(
            "127.0.0.1",
            port=listener.get_port(),
            known_hosts=([asyncssh.import_public_key(host_key.export_public_key())], [], []),
            config=[],
            agent_path=None,
            client_keys=[],
            client_factory=guarded_client_factory(),
            compression_algs=compression,
        ) as connection:
            if accepted:
                result = await connection.run("echo", input="verified data", check=True)
                assert result.stdout == "verified data"
            else:
                with pytest.raises(asyncssh.Error):
                    await connection.run("echo", input="must never stall", check=True)
    finally:
        listener.close()
        await listener.wait_closed()
