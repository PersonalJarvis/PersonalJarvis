"""Bounded CLI model discovery, scoped to the executable and login that run it."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)
Rows = list[dict[str, Any]]


def catalog_key(argv: list[str], env: Mapping[str, str], cwd: Path) -> str:
    """Fingerprint settings without storing credentials in the cache or logs.

    Include file metadata: replacing a login or updating the CLI in place must
    invalidate discovery even when its path and environment did not change.
    """
    homes = [
        Path.home() / name
        for name in (".codex", ".gemini", ".config/opencode", ".local/share/opencode")
    ]
    homes += [
        Path(value)
        for key, value in env.items()
        if value and key.endswith(("_HOME", "_CONFIG_DIR", "_DATA_HOME"))
    ]
    paths = [Path(part) for part in argv if Path(part).is_file()]
    paths += [path.parent.parent / "package.json" for path in paths]
    paths += [Path(env[key]) for key in ("OPENCODE_CONFIG",) if env.get(key)]
    for home in homes:
        paths.extend(
            home / name
            for name in (
                "auth.json",
                "oauth_creds.json",
                "opencode/auth.json",
                "opencode/opencode.json",
                "opencode/opencode.jsonc",
                "config.toml",
                "settings.json",
                "opencode.json",
                "opencode.jsonc",
            )
        )
    paths += [cwd / name for name in ("opencode.json", "opencode.jsonc", ".codex/config.toml")]
    stamps: list[tuple[str, int | None, int | None]] = []
    for path in paths:
        try:
            stat = path.stat()
            stamps.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            stamps.append((str(path), None, None))
    payload = json.dumps([argv, sorted(env.items()), str(cwd), stamps], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class CatalogCache:
    """Small single-flight cache; unknown models refresh, failed probes back off."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Rows | None]] = {}

    def read(
        self, key: str, load: Callable[[], Rows | None], *, required_model: str = ""
    ) -> Rows | None:
        with self._lock:
            now = time.monotonic()
            entry = self._entries.get(key)
            if entry is not None:
                at, rows = entry
                ttl = 300.0 if rows is not None else 10.0
                missing = bool(required_model) and not any(
                    row["id"] == required_model for row in rows or []
                )
                # A missing model gets a new discovery after a short cooldown,
                # including when it appears before the ordinary cache expires.
                if now - at < ttl and (not missing or now - at < 10.0):
                    return copy.deepcopy(rows)
            rows = load()
            if key not in self._entries and len(self._entries) >= 32:
                self._entries.pop(next(iter(self._entries)))
            self._entries[key] = (time.monotonic(), copy.deepcopy(rows))
            return rows


async def _codex_models(argv: list[str], env: dict[str, str], cwd: Path) -> Rows:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        "app-server",
        env=env,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        limit=4 * 1024 * 1024,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )
    assert proc.stdin is not None and proc.stdout is not None

    async def send(payload: dict[str, Any]) -> None:
        assert proc.stdin is not None
        proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def call(request_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        await send({"id": request_id, "method": method, "params": params})
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise ValueError("Codex model discovery ended before replying")
            reply = json.loads(line)
            if not isinstance(reply, dict) or reply.get("id") != request_id:
                continue
            result = reply.get("result")
            if not isinstance(result, dict):
                # Provider error bodies may contain private data.
                raise ValueError("Codex model discovery rejected the request")
            return result

    try:
        await call(1, "initialize", {"clientInfo": {"name": "jarvis", "version": "1.0.0"}})
        await send({"method": "initialized"})
        rows: Rows = []
        cursor: str | None = None
        seen: set[str] = set()
        for page in range(100):
            result = await call(
                page + 2, "model/list", {"limit": 100, "includeHidden": False, "cursor": cursor}
            )
            models = result.get("data")
            if not isinstance(models, list):
                raise ValueError("Codex returned an invalid model catalog")
            rows.extend(parse_codex_models(models))
            cursor = result.get("nextCursor")
            if cursor is None:
                return rows
            if not isinstance(cursor, str) or cursor in seen:
                raise ValueError("Codex returned an invalid model cursor")
            seen.add(cursor)
        raise ValueError("Codex model catalog exceeded the page limit")
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass  # The child exited between returncode and kill.
        await proc.wait()


def parse_codex_models(models: list[Any]) -> Rows:
    """Translate the CLI's model/list response without a hard-coded model ladder."""
    rows: Rows = []
    for model in models:
        if not isinstance(model, dict) or model.get("hidden") is True:
            continue
        slug = model.get("model") or model.get("id")
        if not isinstance(slug, str) or not slug.strip():
            continue
        note = ""
        upgrade = model.get("upgradeInfo")
        retired = upgrade.get("retirementAt") if isinstance(upgrade, dict) else None
        if isinstance(retired, (float, int)):
            if retired <= time.time():
                continue
            note = f"retires {datetime.fromtimestamp(retired, UTC).date()}"
        levels = model.get("supportedReasoningEfforts")
        if not isinstance(levels, list):
            raise ValueError("Codex model has no reasoning metadata")
        efforts = [
            entry["reasoningEffort"]
            for entry in levels
            if isinstance(entry, dict) and isinstance(entry.get("reasoningEffort"), str)
        ]
        rows.append(
            {
                "id": slug,
                "label": model.get("displayName") or slug,
                "efforts": efforts,
                "note": note,
            }
        )
    return rows


def discover_codex_models(
    argv: list[str], env: dict[str, str], cwd: Path, timeout_s: float = 8.0
) -> Rows | None:
    """Run off the event loop. Discovery never starts a model turn or runs tools."""

    async def bounded() -> Rows:
        return await asyncio.wait_for(_codex_models(argv, env, cwd), timeout_s)

    try:
        return asyncio.run(bounded())
    except (OSError, ValueError, TimeoutError) as exc:
        log.debug("Codex model discovery unavailable (%s)", type(exc).__name__)
        return None
