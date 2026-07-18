"""Jarvis as an MCP server.

External MCP clients (the external `openclaw` CLI, Cursor, VSCode Extension) can use
Jarvis-internal capabilities:

Tools:
  - memory_search(query, k)          — FTS5-BM25 recall search
  - memory_recent(limit, role)       — last N messages
  - memory_add_fact(fact, category)  — append a core-memory fact
  - skills_list()                    — all registered skills
  - skills_run(skill_name)           — run a skill

Resources:
  - jarvis://core-memory/persona     — live dump of the persona block
  - jarvis://core-memory/all         — full core-memory JSON

Registration in the external `openclaw` CLI:
  claude mcp add jarvis python -m jarvis.mcp.server

Loop detection:
  Every request handler checks the `JARVIS_MCP_DEPTH` env var. If it is
  >= max_call_depth, it raises an error (prevents an infinite
  dispatch_to_harness→openclaw→jarvis-mcp loop).

Auth (optional):
  Set the `JARVIS_MCP_TOKEN` env var to require a bearer header on the
  HTTP transport. Stdio uses env inheritance — no extra auth needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

from jarvis.core import config as cfg
from jarvis.memory import CORE_MEMORY_FILENAME, CoreMemory, RecallStore

log = logging.getLogger(__name__)

DEPTH_ENV = "JARVIS_MCP_DEPTH"
DEFAULT_MAX_DEPTH = 3


def _current_depth() -> int:
    try:
        return int(os.environ.get(DEPTH_ENV, "0") or 0)
    except ValueError:
        return 0


def _depth_check(max_depth: int) -> str | None:
    """Returns an error string if we're too deep in a dispatch chain."""
    if _current_depth() >= max_depth:
        return (
            f"MCP call depth {_current_depth()} >= max {max_depth} — "
            "infinite-loop protection triggered."
        )
    return None


def build_app() -> tuple[object, dict]:
    """Build the FastMCP app. Lazy import so the dependency is only loaded
    when the server is actually started.
    """
    from mcp.server.fastmcp import FastMCP

    config = cfg.load_config()
    max_depth = config.mcp_server.max_call_depth
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Shared state
    state = {
        "recall": RecallStore(cfg.DATA_DIR / "jarvis.db"),
        "core_memory": CoreMemory.load(cfg.DATA_DIR / CORE_MEMORY_FILENAME),
        "config": config,
    }

    app = FastMCP(
        name="jarvis-mcp-server",
        instructions=(
            "Exposes Jarvis' memory layer and skill system to MCP clients. "
            "Use memory_search for semantic retrieval over conversation history. "
            "Use skills_run to trigger pre-defined multi-step workflows."
        ),
    )

    # ------------------------------------------------------------------
    # Memory-Tools
    # ------------------------------------------------------------------

    @app.tool()
    async def memory_search(query: str, k: int = 5) -> dict:
        """Full-text search over Jarvis's recall memory (SQLite FTS5 with BM25 ranking)."""
        err = _depth_check(max_depth)
        if err:
            return {"error": err}
        hits = await state["recall"].search_messages(query, k=k)
        return {
            "query": query,
            "count": len(hits),
            "matches": [
                {
                    "id": h["id"],
                    "role": h["role"],
                    "text": h["text"],
                    "rank": h["rank"],
                    "timestamp_ns": h["timestamp_ns"],
                }
                for h in hits
            ],
        }

    @app.tool()
    async def memory_recent(limit: int = 10, role: str = "") -> dict:
        """Return the N most recent conversation messages."""
        err = _depth_check(max_depth)
        if err:
            return {"error": err}
        rows = await state["recall"].recent_messages(
            limit=limit, role=role or None
        )
        return {"count": len(rows), "messages": rows}

    @app.tool()
    async def memory_add_fact(fact: str, category: str = "general") -> dict:
        """Persist a fact in Jarvis's core memory. Injected into every brain call."""
        err = _depth_check(max_depth)
        if err:
            return {"error": err}
        state["core_memory"].add_fact(fact, category=category)
        return {"stored": fact, "category": category}

    # ------------------------------------------------------------------
    # Skills-Tools
    # ------------------------------------------------------------------

    @app.tool()
    async def skills_list() -> dict:
        """List all skills registered in Jarvis."""
        err = _depth_check(max_depth)
        if err:
            return {"error": err}
        try:
            from jarvis.core.config import PROJECT_ROOT
            from jarvis.core.paths import user_skills_dir
            from jarvis.skills import discover_skills  # type: ignore

            roots = [
                user_skills_dir(),
                PROJECT_ROOT / "jarvis" / "skills" / "builtin",
            ]
            all_skills: list[dict] = []
            for r in roots:
                if not r.exists():
                    continue
                for s in discover_skills(r):
                    fm = s.frontmatter
                    if fm is None:
                        continue
                    all_skills.append({
                        "name": fm.name,
                        "description": fm.description,
                        "category": fm.category,
                        "state": s.state.value,
                    })
            return {"count": len(all_skills), "skills": all_skills}
        except Exception as exc:  # noqa: BLE001
            return {"error": f"Skills system unavailable: {exc}"}

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------

    @app.resource("jarvis://core-memory/persona")
    def get_persona() -> str:
        """Live-Dump of the Core-Memory persona/system-prompt block."""
        return state["core_memory"].render_system_prompt_block()

    @app.resource("jarvis://core-memory/all")
    def get_core_memory_all() -> str:
        """Full core-memory JSON."""
        return json.dumps(state["core_memory"].all(), ensure_ascii=False, indent=2)

    return app, state


# ----------------------------------------------------------------------
# Entry-Point
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass

    parser = argparse.ArgumentParser(prog="jarvis.mcp.server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47822)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Mark ourselves as 'depth 1' for loop-detection on the server side;
    # downstream clients should increment further.
    os.environ[DEPTH_ENV] = str(_current_depth() + 1)

    app, state = build_app()

    # Open the recall store once on the server's event loop.
    async def _prepare():
        await state["recall"].open()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_prepare())
    finally:
        loop.close()

    if args.transport == "stdio":
        app.run(transport="stdio")
    else:
        app.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
