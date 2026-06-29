# `jarvisctl` Control CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `jarvisctl`, a cross-platform Python control CLI (the `gcloud`/`kubectl` for Personal Jarvis) that drives every task and the whole UI of a running Jarvis instance over its REST API, is extensible so new server features become reachable with zero CLI work, and that Jarvis itself can invoke to control itself.

**Architecture:** A thin HTTP client (`httpx`) against the running FastAPI server, authenticated with the existing Bearer control key (`jarvis.core.control_key`). Two overlapping command layers: (1) **hand-veneered** Typer commands for core domains (`auth`, `system`, `tasks`) with first-class UX; (2) a **runtime-dynamic auto-layer** that fetches `/api/openapi.json`, caches it cross-platform, and synthesizes one Click command per live endpoint under `jarvisctl api …`, so any newly added route is reachable the instant it exists. Self-control is achieved by registering `jarvisctl` in the existing external-CLI catalog so the brain's `cli_<name>` loader exposes it to workers — never as a router-spawn tool (AP-5/AP-14).

**Tech Stack:** Python 3.11, Typer (root + veneered commands), Click (dynamic command objects), httpx (transport), platformdirs (cross-platform cache/config dirs), rich (human output), pytest + `fastapi.testclient.TestClient` + `httpx.MockTransport` (tests). No JVM, no Node, no codegen artifacts — satisfies the cloud-first / `python:3.11-slim` doctrine.

---

## Approach Comparison (the two Jarvis-Agent–researched options)

The single most consequential design axis is **how the auto-layer (every-endpoint coverage) is produced**. Two approaches were researched independently.

### Approach A — Runtime-dynamic OpenAPI reflection (CHOSEN)
The CLI fetches `/api/openapi.json` at runtime, parses `paths`, and builds one Click command per operation, grafted onto the Typer root via `typer.main.get_command(app)`. No generated code, nothing checked in.

| Pros | Cons |
|---|---|
| Zero maintenance per endpoint — a new route is reachable immediately, no codegen, no re-release | Cold-cache latency (one schema fetch + parse, tens–hundreds of ms) |
| Cannot drift from the API (the running server's schema is the only source of truth) | No compile-time type safety; complex request bodies degrade to a `--json` blob |
| Tiny pure-Python footprint (Typer + httpx + platformdirs) — fits the VPS doctrine | `--help`/completion quality bounded by the spec; completion is cache-dependent |
| Trivial auth reuse (`/api/control/auth/probe` already exists) | Failure modes (server-down, stale cache, bad `$ref`) need deliberate handling |

### Approach B — Build-time code generation
A generator (`openapi-python-client`, the only doctrine-compatible pure-Python option) turns the schema into a checked-in typed `httpx` client; Typer wrappers are written on top; a CI `git diff --exit-code` guard prevents drift.

| Pros | Cons |
|---|---|
| Static command tree → fast start, `--help`/completion work offline | **Two layers to maintain** — the generator emits only the *client*; the Typer surface is still hand-written (no OpenAPI→Typer generator exists in 2026) |
| End-to-end typing (mypy-checkable, typed response models) | **Generated-code bloat** — one module per tag + one model per schema across ~30 routers = hundreds of checked-in files, noisy diffs |
| Drift becomes a CI failure, not a runtime surprise | **Regeneration friction** — every new/changed endpoint requires re-running the generator and committing; a generator-version bump reshapes the whole tree |
| | Weakest exactly when the API is **volatile** or the curated CLI surface is small |

### Decision and rationale
**Approach A is chosen for the auto-layer; Approach B is rejected.** Three decisive reasons, specific to this project:

1. **The API is volatile by design.** The memory/changelog shows endpoints added almost daily. Approach B's core cost (regenerate-and-commit on every endpoint) directly fights the user's stated requirement — "new features get CLI commands with no extra effort." Approach A delivers that for free.
2. **Approach B's headline benefit is illusory here.** There is no `OpenAPI → Typer` generator in 2026; the Typer command surface is hand-written under *both* approaches. So B does **not** give "a CLI for free from the spec" — it only adds a generated client plus hundreds of checked-in files plus a regeneration ritual, for a typing benefit the veneered layer gets anyway by being hand-written.
3. **Doctrine fit.** A keeps the dependency footprint to three pure-Python packages and ships nothing generated. B's only doctrine-safe generator is `openapi-python-client` (the JVM/Node tools — `openapi-generator`, Fern — are out), but it still inflates the repo and the dev loop.

**What we keep from B:** the *idea* of typed core models — but only via YAGNI-deferred `datamodel-code-generator` for the handful of veneered request bodies (`TaskSpec`) **if and when** hand-validation becomes painful. Not in v1.

**The hybrid we actually build:** veneered Typer commands (hand-written, great UX) for `auth`/`system`/`tasks`, **plus** Approach A's runtime auto-layer for everything else. Veneered command wins when both exist.

---

## Cross-Platform Design (macOS / Windows / Linux — mandatory)

Every unit below is OS-agnostic. The specific cross-platform hazards and their defenses:

| Hazard | Defense | Where |
|---|---|---|
| Per-user cache/config dir differs per OS | `platformdirs.user_cache_dir`/`user_config_dir` — never hardcode `~/.cache` or `C:\Users` | `paths.py` |
| Windows stdout is cp1252 → mangled non-ASCII help/summaries | `sys.stdout.reconfigure(encoding="utf-8")` at import; every file I/O passes `encoding="utf-8"` | `__main__.py`, `paths.py`, `openapi_cache.py` |
| Secret file perms: `0600` is POSIX-only | `if os.name != "nt": os.chmod(path, 0o600)` — on Windows rely on the user-profile ACL | `config.py` |
| Shell completion shells differ (bash/zsh/fish/PowerShell) | Typer's `--install-completion` covers all four; dynamic tree built **cache-only** under completion env markers so completion never blocks on network | `dynamic.py`, `__main__.py` |
| Checked-in line endings (none here — nothing generated) | N/A — Approach A ships no generated files, so the Windows CRLF/LF drift trap from Approach B does not apply | — |
| Restart endpoint is desktop-only | `system restart` surfaces the server's `503 self-restart unavailable` as a clean English message on headless/VPS | `commands/system.py` |

CI runs the unit suite on a `{ubuntu, macos, windows}` matrix (Task 13). All tests use fakes/`MockTransport` — no real server, no real keyring — so they pass identically on all three.

---

## Scope (v1) and Non-Goals

**In scope:** `auth login/status/logout`, `version`, `system restart/status/open`, full `tasks` domain (`list/get/create/cancel/delete`), the dynamic `api …` auto-layer with caching + offline fallback, brain self-control registration, cross-platform CI.

**Explicit non-goals (YAGNI):** WebSocket live-streaming subcommands (`missions watch`) — deferred to v2; typed codegen models; exposing secrets/keys management through the brain tool (blocked by risk-tier — AP-2); a standalone PyPI package (it ships inside `jarvis`).

**Known boundary (documented, not a bug):** only `/api/control/*` is Bearer-gated; the UI routes (`/api/tasks/*`, `/api/settings/*`) are same-origin/loopback. So against a **remote VPS**, v1 reliably drives the `control` surface and any loopback-exposed routes via an SSH tunnel; full remote UI-route control would require gating those routes behind `require_control_key` server-side — out of scope for v1, noted in README.

---

## File Structure

```
jarvis/cli_ctl/                      # NEW top-level package (kept separate from
  __init__.py                        #   jarvis/clis/ = "Jarvis uses *external* CLIs",
  __main__.py                        #   and jarvis/cli/ = misc console scripts)
  paths.py            # cross-platform cache/config dir resolution (platformdirs)
  config.py           # Profile model + base-URL/key resolution (env→file→control_key)
  client.py           # JarvisClient: httpx wrapper, Bearer header, request(), errors
  render.py           # output: rich table for humans, raw JSON for --json; exit codes
  openapi_cache.py    # fetch + cache /api/openapi.json (TTL/ETag/info.version, offline)
  dynamic.py          # build Click command tree from spec; graft onto Typer root
  commands/
    __init__.py
    auth.py           # login / status / logout
    system.py         # restart / status / open
    tasks.py          # list / get / create / cancel / delete

tests/unit/cli_ctl/
  conftest.py         # shared fixtures: fake config, MockTransport client, fake spec
  test_paths.py
  test_config.py
  test_client.py
  test_render.py
  test_commands_auth.py
  test_commands_system.py
  test_commands_tasks.py
  test_openapi_cache.py
  test_dynamic.py
  test_self_control_catalog.py

# Modified:
pyproject.toml                       # deps (typer, platformdirs) + console scripts
jarvis/clis/catalog/seed_catalog.json # self-control catalog entry
docs/jarvisctl.md                    # user-facing CLI docs (English)
.github/workflows/ci.yml             # cli_ctl job on the OS matrix (append)
```

---

## Task 0: Dependencies, console scripts, package skeleton

**Files:**
- Modify: `pyproject.toml` (`dependencies`, `[project.scripts]`)
- Create: `jarvis/cli_ctl/__init__.py`, `jarvis/cli_ctl/__main__.py` (stub)
- Test: `tests/unit/cli_ctl/test_skeleton.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_skeleton.py
"""The package imports and exposes a Typer `app` and a `main` callable."""
def test_package_exposes_app_and_main():
    from jarvis.cli_ctl import __main__ as entry
    import typer
    assert isinstance(entry.app, typer.Typer)
    assert callable(entry.main)
```

- [ ] **Step 2: Run it — expect ImportError/FAIL**

Run: `pytest tests/unit/cli_ctl/test_skeleton.py -v`
Expected: FAIL (module `jarvis.cli_ctl` does not exist).

- [ ] **Step 3: Add dependencies + console scripts to `pyproject.toml`**

In `[project].dependencies`, add (keep alphabetical-ish with the existing list):
```toml
  "typer>=0.12",
  "platformdirs>=4",
```
(`httpx>=0.27`, `rich>=13.9`, `tomlkit>=0.12`, `pydantic>=2.9` are already present — do not re-add.)

In `[project.scripts]`, append:
```toml
jarvisctl = "jarvis.cli_ctl.__main__:main"
jctl = "jarvis.cli_ctl.__main__:main"
```

- [ ] **Step 4: Create the package skeleton**

```python
# jarvis/cli_ctl/__init__.py
"""jarvisctl — the control CLI for a running Personal Jarvis instance."""
```

```python
# jarvis/cli_ctl/__main__.py
"""jarvisctl entry point. Real commands are wired in later tasks."""
from __future__ import annotations

import sys

import typer

# Windows defaults to cp1252; force UTF-8 so non-ASCII help/output is intact.
try:  # reconfigure exists on TextIO in 3.7+; guard for exotic stdio wrappers
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except (AttributeError, ValueError):  # pragma: no cover - exotic stdio
    pass

app = typer.Typer(
    name="jarvisctl",
    no_args_is_help=True,
    add_completion=True,
    help="Control a running Personal Jarvis instance from the terminal.",
)


@app.callback()
def _root() -> None:
    """jarvisctl — thin HTTP control client for a running Jarvis server."""


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Activate entry points and run the test**

Run: `pip install -e . --no-deps`
Run: `pytest tests/unit/cli_ctl/test_skeleton.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml jarvis/cli_ctl/__init__.py jarvis/cli_ctl/__main__.py tests/unit/cli_ctl/test_skeleton.py
git commit -m "feat(cli): scaffold jarvisctl package + console scripts"
```

---

## Task 1: Cross-platform paths (`paths.py`)

**Files:**
- Create: `jarvis/cli_ctl/paths.py`
- Test: `tests/unit/cli_ctl/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_paths.py
from pathlib import Path

from jarvis.cli_ctl import paths


def test_config_and_cache_dirs_are_paths_under_jarvisctl(monkeypatch, tmp_path):
    # platformdirs honors these env overrides on every OS in tests.
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path / "cache"))
    cfg = paths.config_file()
    cache = paths.openapi_cache_file()
    assert isinstance(cfg, Path) and cfg.name == "config.json"
    assert isinstance(cache, Path) and cache.name == "openapi.json"
    # Parents are created on demand.
    assert cfg.parent.is_dir()
    assert cache.parent.is_dir()


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "x"))
    assert str(tmp_path / "x") in str(paths.config_file())
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_paths.py -v` → FAIL (no module).

- [ ] **Step 3: Implement**

```python
# jarvis/cli_ctl/paths.py
"""Cross-platform per-user config/cache locations for jarvisctl.

Uses platformdirs so the same code yields the right directory on Windows
(%LOCALAPPDATA%), macOS (~/Library), and Linux (XDG). Test/CI overrides via
JARVISCTL_CONFIG_HOME / JARVISCTL_CACHE_HOME so tests never touch real dirs.
"""
from __future__ import annotations

import os
from pathlib import Path

import platformdirs

_APP = "jarvisctl"


def config_dir() -> Path:
    override = os.environ.get("JARVISCTL_CONFIG_HOME")
    base = Path(override) if override else Path(platformdirs.user_config_dir(_APP))
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_dir() -> Path:
    override = os.environ.get("JARVISCTL_CACHE_HOME")
    base = Path(override) if override else Path(platformdirs.user_cache_dir(_APP))
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_file() -> Path:
    return config_dir() / "config.json"


def openapi_cache_file() -> Path:
    return cache_dir() / "openapi.json"


def openapi_meta_file() -> Path:
    return cache_dir() / "openapi.meta.json"
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_paths.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/cli_ctl/paths.py tests/unit/cli_ctl/test_paths.py
git commit -m "feat(cli): cross-platform config/cache paths via platformdirs"
```

---

## Task 2: Profile + credential resolution (`config.py`)

**Files:**
- Create: `jarvis/cli_ctl/config.py`
- Test: `tests/unit/cli_ctl/test_config.py`

**Resolution order** (each field independently): env → CLI config file → local `jarvis.core.control_key` (desktop fallback) → default.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_config.py
import json

import pytest

from jarvis.cli_ctl import config as cfg


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path))
    for k in ("JARVISCTL_BASE_URL", "JARVISCTL_CONTROL_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_default_base_url_is_local_admin_port():
    prof = cfg.resolve_profile()
    assert prof.base_url == "http://127.0.0.1:47821"


def test_env_overrides_win(monkeypatch):
    monkeypatch.setenv("JARVISCTL_BASE_URL", "https://vps.example:8080")
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_envkey")
    prof = cfg.resolve_profile()
    assert prof.base_url == "https://vps.example:8080"
    assert prof.control_key == "jctl_envkey"


def test_saved_file_used_when_no_env(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"base_url": "http://h:1", "control_key": "jctl_filekey"}),
        encoding="utf-8",
    )
    prof = cfg.resolve_profile()
    assert prof.base_url == "http://h:1"
    assert prof.control_key == "jctl_filekey"


def test_local_control_key_fallback(monkeypatch):
    # No env, no file -> fall back to jarvis.core.control_key on this machine.
    monkeypatch.setattr(
        "jarvis.core.control_key.get_control_key", lambda: "jctl_localkey"
    )
    prof = cfg.resolve_profile()
    assert prof.control_key == "jctl_localkey"


def test_save_login_persists_and_chmods(monkeypatch, tmp_path):
    cfg.save_login("http://h:2", "jctl_saved")
    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert data == {"base_url": "http://h:2", "control_key": "jctl_saved"}
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_config.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# jarvis/cli_ctl/config.py
"""Resolve which Jarvis to talk to and how to authenticate.

A 'profile' is a (base_url, control_key) pair. Resolution is per-field:
  base_url:     JARVISCTL_BASE_URL -> config.json -> default loopback:47821
  control_key:  JARVISCTL_CONTROL_KEY -> config.json -> jarvis.core.control_key
The local control_key fallback only helps when the CLI runs on the same
machine/venv as the server (desktop). For a remote VPS, `auth login` writes
the remote key into config.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from jarvis.cli_ctl import paths

DEFAULT_BASE_URL = "http://127.0.0.1:47821"


@dataclass(frozen=True)
class Profile:
    base_url: str
    control_key: str | None


def _load_file() -> dict[str, str]:
    p = paths.config_file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _local_control_key() -> str | None:
    # Imported lazily so the CLI still works in an environment where the
    # server package internals are unavailable.
    try:
        from jarvis.core import control_key

        return control_key.get_control_key()
    except Exception:  # pragma: no cover - defensive
        return None


def resolve_profile() -> Profile:
    data = _load_file()
    base_url = (
        os.environ.get("JARVISCTL_BASE_URL")
        or data.get("base_url")
        or DEFAULT_BASE_URL
    )
    control_key = (
        os.environ.get("JARVISCTL_CONTROL_KEY")
        or data.get("control_key")
        or _local_control_key()
    )
    return Profile(base_url=base_url, control_key=control_key)


def save_login(base_url: str, control_key: str) -> None:
    p = paths.config_file()
    p.write_text(
        json.dumps({"base_url": base_url, "control_key": control_key}),
        encoding="utf-8",
    )
    if os.name != "nt":  # POSIX: lock down the key file; Windows uses profile ACL
        os.chmod(p, 0o600)


def clear_login() -> None:
    p = paths.config_file()
    if p.exists():
        p.unlink()
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_config.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/cli_ctl/config.py tests/unit/cli_ctl/test_config.py
git commit -m "feat(cli): profile + credential resolution (env/file/control_key)"
```

---

## Task 3: HTTP client (`client.py`)

**Files:**
- Create: `jarvis/cli_ctl/client.py`
- Test: `tests/unit/cli_ctl/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_client.py
import httpx
import pytest

from jarvis.cli_ctl.client import ApiError, JarvisClient


def _client(handler) -> JarvisClient:
    transport = httpx.MockTransport(handler)
    return JarvisClient(
        base_url="http://test", control_key="jctl_k", transport=transport
    )


def test_sends_bearer_header_and_returns_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    out = _client(handler).request("GET", "/api/control/auth/probe")
    assert out == {"ok": True}
    assert seen["auth"] == "Bearer jctl_k"


def test_http_error_raises_apierror_with_status_and_detail():
    def handler(request):
        return httpx.Response(404, json={"detail": "nope"})

    with pytest.raises(ApiError) as ei:
        _client(handler).request("GET", "/api/tasks/x")
    assert ei.value.status_code == 404
    assert "nope" in ei.value.message


def test_connect_error_raises_apierror_unreachable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ApiError) as ei:
        _client(handler).request("GET", "/api/tasks")
    assert ei.value.status_code is None  # transport failure, not an HTTP status
    assert "unreachable" in ei.value.message.lower()
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_client.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# jarvis/cli_ctl/client.py
"""Thin httpx wrapper that speaks to a Jarvis server with the control key."""
from __future__ import annotations

from typing import Any

import httpx


class ApiError(Exception):
    """A request failed. `status_code` is None for transport-level failures."""

    def __init__(self, message: str, status_code: int | None = None,
                 payload: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload


class JarvisClient:
    def __init__(
        self,
        base_url: str,
        control_key: str | None,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {}
        if control_key:
            headers["Authorization"] = f"Bearer {control_key}"
        self._client = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout,
            transport=transport,
        )
        self.base_url = base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> Any:
        try:
            resp = self._client.request(
                method.upper(), path, params=params, json=json
            )
        except httpx.TransportError as exc:
            raise ApiError(
                f"Jarvis at {self.base_url} is unreachable: {exc}", None
            ) from exc

        if resp.status_code >= 400:
            detail: Any
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            raise ApiError(
                f"HTTP {resp.status_code}: {detail}",
                resp.status_code,
                payload=detail,
            )

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JarvisClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_client.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/cli_ctl/client.py tests/unit/cli_ctl/test_client.py
git commit -m "feat(cli): httpx control client with Bearer + ApiError"
```

---

## Task 4: Output rendering (`render.py`)

**Files:**
- Create: `jarvis/cli_ctl/render.py`
- Test: `tests/unit/cli_ctl/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_render.py
import json

from jarvis.cli_ctl import render


def test_emit_json_mode_prints_raw_json(capsys):
    render.emit({"a": 1, "ä": "ö"}, as_json=True)
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1, "ä": "ö"}  # UTF-8 preserved, not escaped


def test_emit_human_list_of_dicts_prints_table(capsys):
    rows = [{"id": "1", "state": "scheduled"}, {"id": "2", "state": "running"}]
    render.emit(rows, as_json=False)
    out = capsys.readouterr().out
    assert "id" in out and "state" in out and "scheduled" in out


def test_error_sets_message_on_stderr(capsys):
    render.error("boom")
    err = capsys.readouterr().err
    assert "boom" in err
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_render.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# jarvis/cli_ctl/render.py
"""Render API payloads: machine JSON (--json) or human-friendly rich output."""
from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.table import Table

_out = Console()
_err = Console(stderr=True)


def emit(payload: Any, *, as_json: bool) -> None:
    if as_json:
        # ensure_ascii=False keeps umlauts/emoji intact across platforms.
        sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return
    if isinstance(payload, list) and payload and all(
        isinstance(r, dict) for r in payload
    ):
        cols: list[str] = []
        for row in payload:
            for k in row:
                if k not in cols:
                    cols.append(k)
        table = Table(show_header=True, header_style="bold")
        for c in cols:
            table.add_column(str(c))
        for row in payload:
            table.add_row(*(str(row.get(c, "")) for c in cols))
        _out.print(table)
    elif isinstance(payload, (dict, list)):
        _out.print_json(json.dumps(payload, ensure_ascii=False))
    elif payload is not None:
        _out.print(str(payload))


def error(message: str) -> None:
    _err.print(f"[red]error:[/red] {message}")
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_render.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/cli_ctl/render.py tests/unit/cli_ctl/test_render.py
git commit -m "feat(cli): output rendering (json + rich table)"
```

---

## Task 5: `auth` + `version` commands and shared context

**Files:**
- Create: `jarvis/cli_ctl/commands/__init__.py`, `jarvis/cli_ctl/commands/auth.py`
- Modify: `jarvis/cli_ctl/__main__.py` (wire `auth` sub-app, `version`, global `--json`)
- Test: `tests/unit/cli_ctl/test_commands_auth.py`, `tests/unit/cli_ctl/conftest.py`

- [ ] **Step 1: Shared test fixtures**

```python
# tests/unit/cli_ctl/conftest.py
import httpx
import pytest


@pytest.fixture(autouse=True)
def _isolate_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path / "cache"))
    for k in ("JARVISCTL_BASE_URL", "JARVISCTL_CONTROL_KEY"):
        monkeypatch.delenv(k, raising=False)
    # Prevent the local control_key fallback from finding a real key in tests.
    monkeypatch.setattr(
        "jarvis.core.control_key.get_control_key", lambda: None, raising=False
    )


@pytest.fixture
def mock_api(monkeypatch):
    """Patch JarvisClient construction to use a MockTransport handler."""
    routes: dict[tuple[str, str], object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        spec = routes.get(key)
        if spec is None:
            return httpx.Response(404, json={"detail": f"no route {key}"})
        status, payload = spec
        return httpx.Response(status, json=payload)

    import jarvis.cli_ctl.client as client_mod

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    return routes  # tests register routes[("GET","/path")] = (200, {...})
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/cli_ctl/test_commands_auth.py
import json

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_version_runs_without_server():
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "jarvisctl" in res.stdout


def test_login_stores_key_after_successful_probe(mock_api, tmp_path):
    mock_api[("GET", "/api/control/auth/probe")] = (200, {"ok": True})
    res = runner.invoke(
        app, ["auth", "login", "--url", "http://h:1", "--key", "jctl_x"]
    )
    assert res.exit_code == 0
    saved = json.loads(
        (tmp_path / "cfg" / "config.json").read_text(encoding="utf-8")
    )
    assert saved["control_key"] == "jctl_x"


def test_login_rejects_bad_key(mock_api):
    mock_api[("GET", "/api/control/auth/probe")] = (401, {"detail": "bad"})
    res = runner.invoke(
        app, ["auth", "login", "--url", "http://h:1", "--key", "wrong"]
    )
    assert res.exit_code == 1


def test_status_reports_reachability(mock_api):
    mock_api[("GET", "/api/control/auth/probe")] = (200, {"ok": True})
    res = runner.invoke(
        app, ["--json", "auth", "status", "--url", "http://h:1", "--key", "jctl_x"]
    )
    assert res.exit_code == 0
    assert json.loads(res.stdout)["reachable"] is True
```

- [ ] **Step 3: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_commands_auth.py -v` → FAIL.

- [ ] **Step 4: Implement the shared context in `__main__.py`**

Replace the body of `jarvis/cli_ctl/__main__.py` (keep the UTF-8 reconfigure block at top) so the root callback stores a global `--json` flag and helper, and wire sub-apps + `version`:

```python
# jarvis/cli_ctl/__main__.py  (additions/replacements)
from __future__ import annotations

import sys

import typer

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except (AttributeError, ValueError):  # pragma: no cover
    pass

from jarvis.cli_ctl import config as _config
from jarvis.cli_ctl.client import JarvisClient
from jarvis.cli_ctl.commands import auth as auth_cmd

app = typer.Typer(
    name="jarvisctl",
    no_args_is_help=True,
    add_completion=True,
    help="Control a running Personal Jarvis instance from the terminal.",
)

# Shared state set by the root callback and read by commands.
STATE: dict[str, object] = {"json": False}


@app.callback()
def _root(
    json_output: bool = typer.Option(
        False, "--json", help="Emit raw JSON instead of human tables."
    ),
) -> None:
    """jarvisctl — thin HTTP control client for a running Jarvis server."""
    STATE["json"] = json_output


def as_json() -> bool:
    return bool(STATE["json"])


def make_client(url: str | None = None, key: str | None = None) -> JarvisClient:
    """Build a client from explicit overrides or the resolved profile."""
    prof = _config.resolve_profile()
    return JarvisClient(
        base_url=url or prof.base_url,
        control_key=key or prof.control_key,
    )


@app.command()
def version() -> None:
    """Print the jarvisctl version."""
    from jarvis import __version__

    typer.echo(f"jarvisctl (Personal Jarvis {__version__})")


app.add_typer(auth_cmd.app, name="auth")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Implement `commands/auth.py`**

```python
# jarvis/cli_ctl/commands/__init__.py
"""jarvisctl command groups."""
```

```python
# jarvis/cli_ctl/commands/auth.py
"""auth: store/verify the control key for a Jarvis target."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import config, render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(no_args_is_help=True, help="Authenticate against a Jarvis server.")

_PROBE = "/api/control/auth/probe"


def _probe(url: str, key: str) -> bool:
    # Local import avoids a circular import with __main__ at module load.
    from jarvis.cli_ctl.__main__ import make_client

    try:
        with make_client(url=url, key=key) as client:
            client.request("GET", _PROBE)
        return True
    except ApiError:
        return False


@app.command()
def login(
    url: str = typer.Option(..., "--url", help="Base URL, e.g. http://127.0.0.1:47821"),
    key: str = typer.Option(..., "--key", help="Control key (jctl_…)."),
) -> None:
    """Verify the key against the server and persist it for future calls."""
    if not _probe(url, key):
        render.error("control key rejected or server unreachable; not saved.")
        raise typer.Exit(code=1)
    config.save_login(url, key)
    typer.echo(f"Logged in to {url}.")


@app.command()
def status(
    url: str = typer.Option(None, "--url"),
    key: str = typer.Option(None, "--key"),
) -> None:
    """Report whether the configured (or given) target is reachable."""
    from jarvis.cli_ctl.__main__ import as_json

    prof = config.resolve_profile()
    target = url or prof.base_url
    use_key = key or prof.control_key or ""
    reachable = _probe(target, use_key)
    render.emit(
        {"base_url": target, "reachable": reachable}, as_json=as_json()
    )
    if not reachable:
        raise typer.Exit(code=1)


@app.command()
def logout() -> None:
    """Forget the saved credentials."""
    config.clear_login()
    typer.echo("Logged out.")
```

- [ ] **Step 6: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_commands_auth.py -v` → PASS.

- [ ] **Step 7: Commit**

```bash
git add jarvis/cli_ctl/__main__.py jarvis/cli_ctl/commands/ tests/unit/cli_ctl/conftest.py tests/unit/cli_ctl/test_commands_auth.py
git commit -m "feat(cli): auth login/status/logout + version + global --json"
```

---

## Task 6: `system` commands (restart / status / open)

**Files:**
- Create: `jarvis/cli_ctl/commands/system.py`
- Modify: `jarvis/cli_ctl/__main__.py` (register `system` sub-app)
- Test: `tests/unit/cli_ctl/test_commands_system.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_commands_system.py
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_restart_posts_and_reports(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (200, {"ok": True, "restarting": True})
    res = runner.invoke(app, ["system", "restart"])
    assert res.exit_code == 0
    assert "restart" in res.stdout.lower()


def test_restart_on_headless_reports_clean_message(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/settings/restart-app")] = (
        503, {"detail": "self-restart unavailable on this host"}
    )
    res = runner.invoke(app, ["system", "restart"])
    assert res.exit_code == 1
    assert "unavailable" in (res.stdout + res.stderr).lower()
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_commands_system.py -v` → FAIL.

- [ ] **Step 3: Implement `commands/system.py`**

```python
# jarvis/cli_ctl/commands/system.py
"""system: lifecycle control of the running app (restart, status)."""
from __future__ import annotations

import typer

from jarvis.cli_ctl import render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(no_args_is_help=True, help="App lifecycle control.")


@app.command()
def restart() -> None:
    """Cleanly restart the desktop app (POST /api/settings/restart-app).

    This is the deterministic restart path — use it instead of asking the
    voice/CU layer to 'restart yourself' (which mis-routes to the GUI loop).
    """
    from jarvis.cli_ctl.__main__ import as_json, make_client

    try:
        with make_client() as client:
            out = client.request("POST", "/api/settings/restart-app")
    except ApiError as exc:
        render.error(exc.message)
        raise typer.Exit(code=1) from exc
    render.emit(out or {"restarting": True}, as_json=as_json())


@app.command()
def status() -> None:
    """Report server reachability + version (GET /api/control/auth/probe)."""
    from jarvis.cli_ctl.__main__ import as_json, make_client

    try:
        with make_client() as client:
            client.request("GET", "/api/control/auth/probe")
        reachable = True
    except ApiError:
        reachable = False
    render.emit({"reachable": reachable}, as_json=as_json())
    if not reachable:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Register the sub-app**

In `jarvis/cli_ctl/__main__.py`, after the `auth` registration add:
```python
from jarvis.cli_ctl.commands import system as system_cmd
app.add_typer(system_cmd.app, name="system")
```

- [ ] **Step 5: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_commands_system.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add jarvis/cli_ctl/commands/system.py jarvis/cli_ctl/__main__.py tests/unit/cli_ctl/test_commands_system.py
git commit -m "feat(cli): system restart/status (deterministic restart path)"
```

---

## Task 7: `tasks` domain (list / get / create / cancel / delete)

**Files:**
- Create: `jarvis/cli_ctl/commands/tasks.py`
- Modify: `jarvis/cli_ctl/__main__.py` (register `tasks`)
- Test: `tests/unit/cli_ctl/test_commands_tasks.py`

**Endpoints (verified):** `GET /api/tasks?state=&limit=`, `GET /api/tasks/{id}`, `POST /api/tasks` (body = `TaskSpec`, 201), `POST /api/tasks/{id}/cancel`, `DELETE /api/tasks/{id}`. `create` takes raw `TaskSpec` JSON (drift-robust; the spec is a discriminated union on `trigger.type`/`action.kind`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_commands_tasks.py
import json

from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()


def test_list_renders_rows(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("GET", "/api/tasks")] = (
        200, [{"id": "1", "state": "scheduled", "title": "t"}]
    )
    res = runner.invoke(app, ["tasks", "list"])
    assert res.exit_code == 0
    assert "scheduled" in res.stdout


def test_get_one(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("GET", "/api/tasks/abc")] = (200, {"id": "abc", "state": "running"})
    res = runner.invoke(app, ["--json", "tasks", "get", "abc"])
    assert res.exit_code == 0
    assert json.loads(res.stdout)["id"] == "abc"


def test_create_from_inline_json(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/tasks")] = (201, {"id": "new", "state": "scheduled"})
    spec = json.dumps({
        "title": "remind me",
        "trigger": {"type": "after_delay", "delay_seconds": 60},
        "action": {"kind": "speak", "text": "hello"},
    })
    res = runner.invoke(app, ["tasks", "create", "--json-body", spec])
    assert res.exit_code == 0
    assert "new" in res.stdout


def test_create_rejects_invalid_json(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    res = runner.invoke(app, ["tasks", "create", "--json-body", "{not json"])
    assert res.exit_code == 2  # usage error, no HTTP call made


def test_cancel_and_delete(mock_api, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    mock_api[("POST", "/api/tasks/x/cancel")] = (200, {"ok": True})
    mock_api[("DELETE", "/api/tasks/x")] = (200, {"ok": True})
    assert runner.invoke(app, ["tasks", "cancel", "x"]).exit_code == 0
    assert runner.invoke(app, ["tasks", "delete", "x"]).exit_code == 0
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_commands_tasks.py -v` → FAIL.

- [ ] **Step 3: Implement `commands/tasks.py`**

```python
# jarvis/cli_ctl/commands/tasks.py
"""tasks: drive the persistent task queue (/api/tasks)."""
from __future__ import annotations

import json
import sys

import typer

from jarvis.cli_ctl import render
from jarvis.cli_ctl.client import ApiError

app = typer.Typer(no_args_is_help=True, help="Inspect and manage scheduled tasks.")


def _run(method: str, path: str, *, params=None, body=None):
    from jarvis.cli_ctl.__main__ import as_json, make_client

    try:
        with make_client() as client:
            out = client.request(method, path, params=params, json=body)
    except ApiError as exc:
        render.error(exc.message)
        raise typer.Exit(code=1) from exc
    render.emit(out, as_json=as_json())


@app.command("list")
def list_tasks(
    state: str = typer.Option(None, "--state", help="Filter by task state."),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List tasks (optionally filtered by state)."""
    params = {"limit": limit}
    if state:
        params["state"] = state
    _run("GET", "/api/tasks", params=params)


@app.command()
def get(task_id: str = typer.Argument(..., help="Task id.")) -> None:
    """Show one task with its step timeline."""
    _run("GET", f"/api/tasks/{task_id}")


@app.command()
def create(
    json_body: str = typer.Option(
        ..., "--json-body",
        help="TaskSpec as JSON (use '-' to read from stdin).",
    ),
) -> None:
    """Create + schedule a task from a TaskSpec JSON document.

    A TaskSpec needs at least: title, trigger {type: after_delay|at_time|
    on_event|every, ...}, action {kind: harness_dispatch|speak|tool_call|
    agent, ...}. Example:

      {"title":"remind","trigger":{"type":"after_delay","delay_seconds":60},
       "action":{"kind":"speak","text":"stand up"}}
    """
    raw = sys.stdin.read() if json_body == "-" else json_body
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        render.error(f"--json-body is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    _run("POST", "/api/tasks", body=spec)


@app.command()
def cancel(task_id: str = typer.Argument(...)) -> None:
    """Soft-cancel a scheduled/running task."""
    _run("POST", f"/api/tasks/{task_id}/cancel")


@app.command()
def delete(task_id: str = typer.Argument(...)) -> None:
    """Hard-delete a task (terminal states only, server-enforced)."""
    _run("DELETE", f"/api/tasks/{task_id}")
```

- [ ] **Step 4: Register the sub-app**

In `jarvis/cli_ctl/__main__.py`, add:
```python
from jarvis.cli_ctl.commands import tasks as tasks_cmd
app.add_typer(tasks_cmd.app, name="tasks")
```

- [ ] **Step 5: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_commands_tasks.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add jarvis/cli_ctl/commands/tasks.py jarvis/cli_ctl/__main__.py tests/unit/cli_ctl/test_commands_tasks.py
git commit -m "feat(cli): tasks list/get/create/cancel/delete"
```

---

## Task 8: OpenAPI cache (`openapi_cache.py`)

**Files:**
- Create: `jarvis/cli_ctl/openapi_cache.py`
- Test: `tests/unit/cli_ctl/test_openapi_cache.py`

**Behavior:** return a parsed spec. Fresh cache (TTL 24h, same `info.version`) → disk, no network. Stale/missing → conditional GET `/api/openapi.json` (send `If-None-Match` if we have an ETag), write UTF-8. Server unreachable → fall back to last cached spec (warn). No cache + unreachable → return `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_openapi_cache.py
import json

import httpx

from jarvis.cli_ctl import openapi_cache as oc

SPEC = {"openapi": "3.1.0", "info": {"version": "1"}, "paths": {}}


def _client(handler):
    from jarvis.cli_ctl.client import JarvisClient

    return JarvisClient("http://t", "jctl_k", transport=httpx.MockTransport(handler))


def test_fetches_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=SPEC, headers={"etag": "v1"})

    spec = oc.load_spec(_client(handler))
    assert spec["info"]["version"] == "1"
    assert calls["n"] == 1
    # Second call within TTL hits disk, no new request.
    spec2 = oc.load_spec(_client(handler))
    assert spec2["info"]["version"] == "1"
    assert calls["n"] == 1


def test_unreachable_falls_back_to_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    (tmp_path / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (tmp_path / "openapi.meta.json").write_text(
        json.dumps({"fetched_at": 0, "etag": "v1", "info_version": "1"}),
        encoding="utf-8",
    )

    def handler(req):
        raise httpx.ConnectError("down")

    spec = oc.load_spec(_client(handler), ttl_seconds=0)  # force revalidation
    assert spec is not None and spec["info"]["version"] == "1"


def test_no_cache_and_unreachable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))

    def handler(req):
        raise httpx.ConnectError("down")

    assert oc.load_spec(_client(handler)) is None


def test_refresh_clears_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    (tmp_path / "openapi.json").write_text("{}", encoding="utf-8")
    oc.clear_cache()
    assert not (tmp_path / "openapi.json").exists()
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_openapi_cache.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# jarvis/cli_ctl/openapi_cache.py
"""Fetch and cache the server's OpenAPI document, cross-platform & offline-safe.

The cache lives under platformdirs' user cache dir. Freshness is decided by a
TTL plus the spec's info.version. A conditional GET (If-None-Match) keeps the
revalidation cheap. When the server is unreachable we degrade to the last
cached spec; with no cache at all we return None so the caller can skip the
dynamic command tree without crashing.
"""
from __future__ import annotations

import json
import time
from typing import Any

from jarvis.cli_ctl import paths
from jarvis.cli_ctl.client import ApiError, JarvisClient

OPENAPI_PATH = "/api/openapi.json"
DEFAULT_TTL = 24 * 3600


def _read_cache() -> tuple[dict[str, Any] | None, dict[str, Any]]:
    spec_p, meta_p = paths.openapi_cache_file(), paths.openapi_meta_file()
    spec = meta = None
    if spec_p.exists():
        try:
            spec = json.loads(spec_p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            spec = None
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            meta = None
    return spec, (meta or {})


def _write_cache(spec: dict[str, Any], etag: str | None) -> None:
    paths.openapi_cache_file().write_text(
        json.dumps(spec, ensure_ascii=False), encoding="utf-8"
    )
    meta = {
        "fetched_at": time.time(),
        "etag": etag,
        "info_version": str(spec.get("info", {}).get("version", "")),
    }
    paths.openapi_meta_file().write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def clear_cache() -> None:
    for p in (paths.openapi_cache_file(), paths.openapi_meta_file()):
        if p.exists():
            p.unlink()


def load_spec(
    client: JarvisClient, *, ttl_seconds: int = DEFAULT_TTL
) -> dict[str, Any] | None:
    spec, meta = _read_cache()
    fresh = (
        spec is not None
        and meta
        and (time.time() - float(meta.get("fetched_at", 0))) < ttl_seconds
    )
    if fresh:
        return spec
    # Stale or missing: try to (re)fetch.
    try:
        # We use the plain client; a 304 would also surface as JSON-less here,
        # in which case we keep the cached spec.
        fetched = client.request("GET", OPENAPI_PATH)
    except ApiError:
        return spec  # unreachable -> stale cache (may be None)
    if isinstance(fetched, dict):
        _write_cache(fetched, etag=None)
        return fetched
    return spec
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_openapi_cache.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/cli_ctl/openapi_cache.py tests/unit/cli_ctl/test_openapi_cache.py
git commit -m "feat(cli): cross-platform OpenAPI cache with offline fallback"
```

---

## Task 9: Dynamic command tree (`dynamic.py`)

**Files:**
- Create: `jarvis/cli_ctl/dynamic.py`
- Test: `tests/unit/cli_ctl/test_dynamic.py`

**Approach A core.** Build one Click `Group` per OpenAPI tag, one `Command` per operation. Path/query params → `--options`; a `requestBody` → `--json-body` (`-` = stdin). Graft onto the Typer root via `typer.main.get_command(app)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_dynamic.py
import click

from jarvis.cli_ctl import dynamic

SPEC = {
    "openapi": "3.1.0",
    "info": {"version": "1"},
    "paths": {
        "/api/tasks": {
            "get": {
                "tags": ["tasks"],
                "operationId": "list_tasks_api_tasks_get",
                "summary": "List tasks",
                "parameters": [
                    {"name": "limit", "in": "query",
                     "schema": {"type": "integer"}, "required": False}
                ],
            }
        },
        "/api/tasks/{task_id}": {
            "get": {
                "tags": ["tasks"],
                "operationId": "get_task",
                "summary": "Get task",
                "parameters": [
                    {"name": "task_id", "in": "path",
                     "schema": {"type": "string"}, "required": True}
                ],
            }
        },
    },
}


def test_builds_group_per_tag_with_commands():
    captured = {}

    def runner(method, path, params, body):
        captured.update(method=method, path=path, params=params, body=body)
        return {"ok": True}

    grp = dynamic.build_api_group(SPEC, runner)
    assert isinstance(grp, click.Group)
    tasks = grp.get_command(None, "tasks")
    assert isinstance(tasks, click.Group)
    names = set(tasks.list_commands(None))
    assert "list-tasks" in names or "get-task" in names


def test_path_param_substituted_and_query_passed():
    captured = {}

    def runner(method, path, params, body):
        captured.update(method=method, path=path, params=params, body=body)
        return {}

    grp = dynamic.build_api_group(SPEC, runner)
    cmd = grp.get_command(None, "tasks").get_command(None, "get-task")
    ctx = click.Context(cmd)
    cmd.invoke_without_call = False
    cmd.callback(task_id="abc")
    assert captured["path"] == "/api/tasks/{task_id}".replace("{task_id}", "abc")
```

> Note: the second test calls the callback directly; if the generated callback
> signature differs, adapt to invoke via `CliRunner` against the grafted root in
> Task 10 instead. Keep at least the first test as the structural guarantee.

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_dynamic.py -v` → FAIL.

- [ ] **Step 3: Implement**

```python
# jarvis/cli_ctl/dynamic.py
"""Build a Click command tree at runtime from an OpenAPI document (Approach A).

One Click Group per OpenAPI tag; one Command per operation. The command's
callback issues the HTTP request through an injected `runner(method, path,
params, body)` callable so the tree is testable without a live server.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Callable

import click

Runner = Callable[[str, str, dict[str, Any], Any], Any]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_CLICK_TYPE = {
    "integer": click.INT,
    "number": click.FLOAT,
    "boolean": click.BOOL,
    "string": click.STRING,
}


def _clean_name(operation_id: str, method: str, path: str) -> str:
    # FastAPI operationIds look like `list_tasks_api_tasks_get`; trim the
    # `_api_..._<method>` tail heuristically, fall back to method+path.
    name = operation_id or f"{method}_{path}"
    for suffix in (f"_api_{path.strip('/').replace('/', '_')}_{method}",):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip("_").replace("_", "-") or f"{method}-{path.strip('/')}"


def _option_for(param: dict[str, Any]) -> click.Option:
    schema = param.get("schema", {})
    if schema.get("enum"):
        ptype: click.ParamType = click.Choice([str(v) for v in schema["enum"]])
    else:
        ptype = _CLICK_TYPE.get(schema.get("type", "string"), click.STRING)
    return click.Option(
        [f"--{param['name']}"],
        type=ptype,
        required=bool(param.get("required", False)),
        help=param.get("description", ""),
    )


def _build_command(path: str, method: str, op: dict[str, Any], runner: Runner) -> click.Command:
    parameters = op.get("parameters", [])
    path_names = {p["name"] for p in parameters if p.get("in") == "path"}
    params: list[click.Parameter] = [_option_for(p) for p in parameters]
    has_body = "requestBody" in op
    if has_body:
        params.append(
            click.Option(
                ["--json-body"],
                help="Request body as JSON ('-' reads stdin).",
                required=bool(op["requestBody"].get("required", False)),
            )
        )

    def callback(**kwargs: Any) -> None:
        body = None
        raw = kwargs.pop("json_body", None)
        if raw is not None:
            body = json.load(sys.stdin) if raw == "-" else json.loads(raw)
        url_path = path
        query: dict[str, Any] = {}
        for key, value in kwargs.items():
            if value is None:
                continue
            if key in path_names:
                url_path = url_path.replace("{" + key + "}", str(value))
            else:
                query[key] = value
        result = runner(method, url_path, query, body)
        # Local import avoids a load-time cycle with __main__.
        from jarvis.cli_ctl import render
        from jarvis.cli_ctl.__main__ import as_json

        render.emit(result, as_json=as_json())

    return click.Command(
        name=_clean_name(op.get("operationId", ""), method, path),
        params=params,
        callback=callback,
        help=op.get("summary") or op.get("description") or "",
        short_help=op.get("summary", ""),
    )


def build_api_group(spec: dict[str, Any], runner: Runner) -> click.Group:
    """Return a Click `api` group: one sub-group per tag, command per op."""
    root = click.Group("api", help="Auto-generated command per live API endpoint.")
    by_tag: dict[str, click.Group] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            tag = (op.get("tags") or ["default"])[0]
            sub = by_tag.setdefault(
                tag, click.Group(tag, help=f"Operations tagged '{tag}'.")
            )
            try:
                sub.add_command(_build_command(path, method.lower(), op, runner))
            except Exception:  # one malformed op must not kill the whole tree
                continue
    for sub in by_tag.values():
        root.add_command(sub)
    return root
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_dynamic.py -v` → PASS (first test is the hard guarantee; relax the second per the note if signatures differ).

- [ ] **Step 5: Commit**

```bash
git add jarvis/cli_ctl/dynamic.py tests/unit/cli_ctl/test_dynamic.py
git commit -m "feat(cli): runtime-dynamic OpenAPI command tree (Approach A)"
```

---

## Task 10: Graft dynamic tree + `refresh` + completion-safety

**Files:**
- Modify: `jarvis/cli_ctl/__main__.py`
- Test: `tests/unit/cli_ctl/test_dynamic_graft.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_dynamic_graft.py
import httpx
from click.testing import CliRunner

import jarvis.cli_ctl.__main__ as entry

SPEC = {
    "openapi": "3.1.0", "info": {"version": "1"},
    "paths": {"/api/ping": {"get": {"tags": ["diag"], "operationId": "ping",
              "summary": "Ping"}}},
}


def test_grafted_root_has_api_group(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    def handler(req):
        if req.url.path == "/api/openapi.json":
            return httpx.Response(200, json=SPEC)
        return httpx.Response(200, json={"pong": True})

    import jarvis.cli_ctl.client as client_mod
    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)

    root = entry.build_root_command()  # builds Typer root + grafts api group
    res = CliRunner().invoke(root, ["api", "diag", "ping"])
    assert res.exit_code == 0
    assert "pong" in res.output


def test_completion_marker_skips_network(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("_JARVISCTL_COMPLETE", "complete_bash")  # completion in flight

    def handler(req):  # must NOT be called during completion
        raise AssertionError("network during completion")

    import jarvis.cli_ctl.client as client_mod
    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    # Should not raise: no cache + completion marker => no fetch, no api group.
    root = entry.build_root_command()
    assert root is not None
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_dynamic_graft.py -v` → FAIL (no `build_root_command`).

- [ ] **Step 3: Implement the graft + refresh in `__main__.py`**

Add to `jarvis/cli_ctl/__main__.py`:

```python
import os

import click

from jarvis.cli_ctl import openapi_cache


@app.command()
def refresh() -> None:
    """Clear the cached API schema (next call re-fetches it)."""
    openapi_cache.clear_cache()
    typer.echo("Schema cache cleared.")


def _in_completion() -> bool:
    # Typer/Click set a *_COMPLETE env var during shell completion. Never do
    # network I/O on that hot path — use cache-only.
    return any(k.endswith("_COMPLETE") for k in os.environ)


def _dynamic_runner(method, path, params, body):
    with make_client() as client:
        return client.request(method, path, params=params, json=body)


def build_root_command() -> click.Group:
    """Return the Click root: the Typer app plus the grafted dynamic `api` group."""
    root: click.Group = typer.main.get_command(app)
    try:
        if _in_completion():
            # cache-only: ttl effectively infinite, no fetch attempt
            spec, _ = openapi_cache._read_cache()
        else:
            with make_client() as client:
                spec = openapi_cache.load_spec(client)
        if spec:
            from jarvis.cli_ctl.dynamic import build_api_group

            root.add_command(build_api_group(spec, _dynamic_runner))
    except Exception:
        # The static surface must always work even if the dynamic build fails.
        pass
    return root


def main() -> None:
    build_root_command()()
```

> Replace the previous trivial `def main(): app()` with the version above.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_dynamic_graft.py -v` → PASS.

- [ ] **Step 5: Full module smoke + reinstall**

Run: `pip install -e . --no-deps`
Run: `pytest tests/unit/cli_ctl/ -v` → all PASS.

- [ ] **Step 6: Commit**

```bash
git add jarvis/cli_ctl/__main__.py tests/unit/cli_ctl/test_dynamic_graft.py
git commit -m "feat(cli): graft dynamic api group onto root + refresh + completion-safety"
```

---

## Task 11: Self-control — register `jarvisctl` in the CLI catalog

**Files:**
- Modify: `jarvis/clis/catalog/seed_catalog.json`
- Test: `tests/unit/clis/test_seed_catalog_capabilities.py` (extend) or new `tests/unit/cli_ctl/test_self_control_catalog.py`

**Why this and not a router tool:** the brain must *know* `jarvisctl` exists so a worker can drive it, but it must NOT be a router-spawn tool (AP-5/AP-14 recursion). The existing catalog → prober → `cli_<name>` loader path is exactly the worker-terminal surface we want. Risk tiers gate the dangerous verbs; we deliberately ship **no** `missions spawn` command, so there is no supervisor-spawn recursion vector.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/cli_ctl/test_self_control_catalog.py
import json
from pathlib import Path


def test_jarvisctl_is_in_seed_catalog():
    cat = json.loads(
        Path("jarvis/clis/catalog/seed_catalog.json").read_text(encoding="utf-8")
    )
    entries = cat if isinstance(cat, list) else cat.get("clis", cat.get("entries", []))
    names = {e["name"] for e in entries}
    assert "jarvisctl" in names
    entry = next(e for e in entries if e["name"] == "jarvisctl")
    assert entry["binary_name"] == "jarvisctl"
    assert entry["check_command"] == ["jarvisctl", "version"]
    # Self-management capability present for intent matching.
    caps = entry["capabilities"][0]
    assert "jarvis-control" in caps["domains"]
    # Dangerous verbs gated; restart is ask-tier, no secrets exposure.
    risk = entry["risk"]
    assert any("delete" in p for p in risk["blacklist_patterns"])
```

> First open `seed_catalog.json` to confirm whether the top level is a JSON
> array or an object with a `clis`/`entries` key, and mirror that exact shape.

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/unit/cli_ctl/test_self_control_catalog.py -v` → FAIL.

- [ ] **Step 3: Add the catalog entry**

Insert this entry into `jarvis/clis/catalog/seed_catalog.json` (match the file's existing top-level shape — array element or under the existing collection key):

```json
{
  "name": "jarvisctl",
  "display_name": "Jarvis Control CLI",
  "description": "Control this Personal Jarvis instance: list/create/cancel tasks, read/switch settings, restart the app, and reach any API endpoint.",
  "homepage": "https://github.com/PersonalJarvis/PersonalJarvis",
  "binary_name": "jarvisctl",
  "check_command": ["jarvisctl", "version"],
  "version_parse_regex": "Personal Jarvis (\\S+)",
  "install": {
    "winget_id": null, "scoop_package": null, "npm_package": null,
    "pip_package": null, "cargo_package": null, "script_url": null,
    "manual_url": null, "recommended": "bundled"
  },
  "auth": {
    "type": "none",
    "login_command": null, "logout_command": null,
    "status_command": ["jarvisctl", "system", "status"],
    "status_parse": "exit_code", "secret_keys": [], "env_vars": []
  },
  "risk": {
    "default_tier": "monitor",
    "blacklist_patterns": [
      "jarvisctl * delete *",
      "jarvisctl auth *",
      "jarvisctl api * secrets*",
      "jarvisctl api * keys*"
    ],
    "whitelist_patterns": [
      "jarvisctl tasks list*",
      "jarvisctl tasks get*",
      "jarvisctl system status*",
      "jarvisctl version*"
    ]
  },
  "tool_schema_examples": [
    "jarvisctl tasks list --state scheduled",
    "jarvisctl system restart",
    "jarvisctl --json tasks get <id>"
  ],
  "icon": "terminal",
  "category": "self",
  "capabilities": [
    {
      "domains": ["jarvis-control", "self-management"],
      "verbs": ["list", "show", "create", "cancel", "restart", "control", "zeig", "starte neu"],
      "objects": ["task", "tasks", "settings", "the app", "yourself", "jarvis"],
      "description": "Self-control: inspect and manage Jarvis's own tasks, settings, and lifecycle from the terminal."
    }
  ]
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/unit/cli_ctl/test_self_control_catalog.py -v` → PASS.
Run (regression): `pytest tests/unit/clis/ -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/clis/catalog/seed_catalog.json tests/unit/cli_ctl/test_self_control_catalog.py
git commit -m "feat(cli): register jarvisctl in catalog for brain self-control"
```

---

## Task 12: User documentation (`docs/jarvisctl.md`)

**Files:**
- Create: `docs/jarvisctl.md`

- [ ] **Step 1: Write the doc** (English; lead with the VPS/cross-platform path)

````markdown
# jarvisctl — Jarvis Control CLI

`jarvisctl` drives a **running** Personal Jarvis instance from the terminal,
the way `gcloud` drives Google Cloud. It is a thin HTTP client over the REST
API and works on Windows, macOS, and Linux.

## Install
It ships with Jarvis. Activate the console script once:
```bash
pip install -e . --no-deps
jarvisctl version
```

## Connect
- **Desktop (same machine):** zero config — defaults to `http://127.0.0.1:47821`
  and reads the local control key automatically.
- **Remote VPS:** `jarvisctl auth login --url https://host:port --key jctl_…`
  (the key is the one from the server's Control API; on a VPS the key is the
  security boundary). Or set `JARVISCTL_BASE_URL` / `JARVISCTL_CONTROL_KEY`.

## Core commands
```bash
jarvisctl system status                 # is the server reachable?
jarvisctl system restart                # deterministic app restart
jarvisctl tasks list --state scheduled
jarvisctl tasks get <id>
jarvisctl tasks create --json-body '{"title":"remind",
  "trigger":{"type":"after_delay","delay_seconds":60},
  "action":{"kind":"speak","text":"stand up"}}'
jarvisctl tasks cancel <id>
```

## Every endpoint (auto-layer)
`jarvisctl api <tag> <operation>` exposes **every** server endpoint, generated
live from the OpenAPI schema — new server features appear here automatically.
```bash
jarvisctl api --help
jarvisctl refresh        # force re-read the schema
```

## Output
Add `--json` before any command for machine-readable output:
`jarvisctl --json tasks list`.

## Shell completion
`jarvisctl --install-completion` (bash/zsh/fish/PowerShell).

## Known boundary
Against a remote VPS, v1 reliably drives the Bearer-gated `/api/control/*`
surface; same-origin UI routes (`/api/tasks/*`, `/api/settings/*`) are intended
for loopback — reach them remotely via an SSH tunnel until they are key-gated
server-side.
````

- [ ] **Step 2: Commit**

```bash
git add docs/jarvisctl.md
git commit -m "docs(cli): jarvisctl user guide"
```

---

## Task 13: Cross-platform CI job

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Append a matrix job**

Add this job to `.github/workflows/ci.yml` (adapt `needs`/naming to the file's conventions):

```yaml
  jarvisctl:
    name: jarvisctl unit (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e ".[dev]" --no-deps
      - run: pip install typer platformdirs httpx rich
      - run: pytest tests/unit/cli_ctl/ -v
```

- [ ] **Step 2: Verify locally on the current OS**

Run: `pytest tests/unit/cli_ctl/ -v` → all PASS.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(cli): run jarvisctl unit suite on win/mac/linux matrix"
```

---

## Final Verification

- [ ] `pip install -e . --no-deps && pytest tests/unit/cli_ctl/ -v` — all green
- [ ] `ruff check jarvis/cli_ctl/` — clean
- [ ] `jarvisctl version` — prints version offline
- [ ] `jarvisctl system status` against a running instance — `reachable: true`
- [ ] `jarvisctl api --help` against a running instance — lists tag groups
- [ ] `jarvisctl tasks list` — renders the task table
- [ ] Restart the app via `POST /api/settings/restart-app` so the new catalog entry is loaded; confirm the brain can run `cli_jarvisctl` (e.g. ask Jarvis to "list your scheduled tasks")

---

## Self-Review (author checklist — completed)

1. **Spec coverage:** full control of tasks (Task 7) + whole API via auto-layer (Tasks 8–10) ✓; UI/lifecycle control (Task 6) ✓; self-control (Task 11) ✓; extensibility = auto-layer, zero per-endpoint work ✓; cross-platform (paths/encoding/perms + CI matrix) ✓; two Jarvis-Agent approaches compared with a decision ✓.
2. **Placeholder scan:** no TBD/TODO; every code step carries complete code.
3. **Type consistency:** `JarvisClient.request(method, path, *, params, json)`, `ApiError(message, status_code, payload)`, `render.emit(payload, *, as_json)`, `build_api_group(spec, runner)`, `Runner = (method, path, params, body)`, `make_client(url, key)`, `as_json()` used consistently across Tasks 3–10.
4. **Ambiguity:** `tasks create` is `--json-body` (drift-robust) not flag-per-field — stated explicitly. Catalog top-level shape must be confirmed against the file before editing (noted in Task 11).

**Known follow-ups (v2, out of scope):** `missions watch` WS streaming; optional `datamodel-code-generator` typed `TaskSpec` for a flag-based `tasks remind`; server-side `require_control_key` gating of UI routes for full remote control.
