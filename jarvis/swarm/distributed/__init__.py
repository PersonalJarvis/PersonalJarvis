"""Optional PostgreSQL, Redis Streams and S3 Swarm storage.

Importing this module loads no optional driver and opens no connection. All
credentials belong to the trusted control plane, never to worker handles.
"""

from .config import DistributedConfig, DistributedSecrets


def create_distributed_registry(config, secrets, *, clock=None):
    """Create a lazy registry; ``provision()`` validates explicitly enabled setup."""
    from .registry import PostgresTeamRegistry

    return PostgresTeamRegistry(config, secrets, **({"clock": clock} if clock else {}))


__all__ = ["DistributedConfig", "DistributedSecrets", "create_distributed_registry"]
