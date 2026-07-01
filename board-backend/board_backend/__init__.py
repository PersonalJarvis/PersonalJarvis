"""Jarvis Board Federation Backend (Phase C).

Standalone FastAPI service. Stores, per registered identity (Ed25519
pubkey), incoming signed stats pushes from the local Jarvis. No contact
with voice transcripts or tool content — the server enforces a PII
filter at the sync endpoint.
"""

__version__ = "0.1.0"
