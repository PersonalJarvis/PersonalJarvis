"""An agent's own browser, driven by browser-use — out of process.

browser-use pins its own ``openai`` / ``anthropic`` / ``pydantic`` versions,
which collide with the app's, so it never enters the app environment. It
lives in a managed virtual environment under the data directory
(``install.py``), and every run is a subprocess of that environment's Python
executing ``runner.py`` — a dependency-free script that speaks JSON lines
over stdin/stdout (``session.py`` drives it). Nothing here imports
``browser_use``; nothing here runs at boot (AP-26).

* ``install.py``   one-click, poll-shaped, resumable install of the venv,
                   browser-use and its Chromium
* ``llm.py``       roster provider → browser-use LLM class + explicit key
* ``session.py``   per-agent persistent profile, run/login jobs, caps
* ``runner.py``    the in-venv script (probe / login / run)
* ``tool.py``      ``society_browser`` — the agent's hand
"""

from __future__ import annotations

__all__: list[str] = []
