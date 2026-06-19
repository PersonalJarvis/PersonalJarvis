"""Optimistic Execution prototype — a self-contained, runnable demonstrator.

Mirrors the production Personal-Jarvis architecture (Talker/Worker split,
in-process EventBus, Smart/Dumb tool routing, the "Oops" protocol) WITHOUT
importing from the live `jarvis.*` package, so it runs on any Python 3.11+
with zero third-party dependencies (cloud-first €5-VPS doctrine, AD-OE2).

Seed vision: docs/plans/optimistic-execution-v1/README.md (Architektur-Spezifikation v1.0).
"""
