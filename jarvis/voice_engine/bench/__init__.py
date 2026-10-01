"""Phase P0 measurement bench for the local voice engine.

Run inside the engine's own environment, from a checkout::

    PYTHONPATH=<repo> PYTHONUTF8=1 python -m jarvis.voice_engine.bench env
    ... python -m jarvis.voice_engine.bench stt|tts|turn|llm|tools|e2e|memory
    ... python -m jarvis.voice_engine.bench summary

Every suite writes one JSON file to ``paths.results_dir()`` that records the
machine, the load on it, the configuration and every individual measurement.
Speech input is synthetic (rendered by the engine's own TTS), which makes
transcription accuracy optimistic and turn detection pessimistic; the report
says so next to each number.
"""
