"""Jarvis Board Federation Backend (Phase C).

Standalone FastAPI-Service. Speichert pro registrierter Identity (Ed25519-
Pubkey) eingehende, signierte Stats-Pushes vom lokalen Jarvis. Kein Kontakt
mit Voice-Transcripts oder Tool-Inhalten — der Server erzwingt einen PII-
Filter beim Sync-Endpoint.
"""

__version__ = "0.1.0"
