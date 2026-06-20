"""GeminiWorker binary resolution survives broken npm PATH shims.

On this machine the npm-installed gemini.cmd/gemini shims are stale temp files,
so shutil.which misses them. The worker must still find the bundle via the
robust npm-global probe (jarvis.google_cli.resolver._default_npm_bundle) and
drive `node <bundle>` instead of failing on a bare `gemini`.
"""
from __future__ import annotations

import shutil

from jarvis.missions.workers.gemini_worker import _resolve_gemini_argv_prefix


def test_uses_npm_bundle_when_shims_broken(monkeypatch):
    # Broken shims: which() finds node but neither gemini.cmd nor gemini.
    monkeypatch.setattr(
        shutil, "which",
        lambda n: "/usr/bin/node" if n in ("node", "node.exe") else None,
    )
    argv = _resolve_gemini_argv_prefix(bundle_finder=lambda: "/x/bundle/gemini.js")
    assert argv == ["/usr/bin/node", "/x/bundle/gemini.js"]


def test_falls_back_to_bare_cli_when_no_bundle(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda n: None)
    argv = _resolve_gemini_argv_prefix(bundle_finder=lambda: None)
    assert argv == ["gemini"]
