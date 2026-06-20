"""Browser-microphone/speaker voice bridge (B2, DEEP-DIVE-AUDIT-2026-06-19).

The headless / €5-VPS voice path: a browser's AudioWorklet streams raw int16 PCM
over a WebSocket, the server runs the same STT -> Brain -> TTS turn loop the
telephony bridge uses (stdlib audioop only, NEVER sounddevice), and streams the
TTS PCM straight back as binary WS frames for Web Audio playback. This closes the
cloud-first gap where a browser user on a headless server could only text-chat.
"""
