"""Realtime (full-duplex speech-to-speech) orchestrator package.

Nothing heavy is imported at module load (AP-26): the OpenAI SDK is imported
lazily inside the provider adapter. Use ``build_realtime_session`` from
``jarvis.realtime.factory`` to construct a session.
"""
