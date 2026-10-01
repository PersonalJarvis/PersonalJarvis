"""The Jarvis X overlay sidecar — everything Jarvis X draws on the desktop.

- ``renderer`` + ``__main__`` — a PySide6 process (``python -m
  jarvis.jarvisx.overlay``): the dimmed region-selection overlay, the flash
  and fly-into-the-corner thumbnail cards (the appshot visual language, but
  clickable and optionally persistent), and the recording border plus the
  "Stop" pill. PySide6 is imported only inside that process.
- ``controller`` — main-process glue: spawns the sidecar on first use, sends
  commands, turns the sidecar's events (card clicked, stop clicked, region
  selected) into calls on the Jarvis X service.
- ``protocol`` — the JSON-lines vocabulary between the two.

A separate process from the Computer-Use indicator on purpose: that sidecar
is strictly click-through and command-only, while this one takes mouse input
and reports back.
"""
