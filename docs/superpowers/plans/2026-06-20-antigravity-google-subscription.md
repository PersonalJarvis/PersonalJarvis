# Antigravity / Google-subscription Brain + Jarvis-Agent — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Jarvis run its Brain and Jarvis-Agents against the user's Google subscription by driving the official Antigravity/Gemini CLI as a subprocess over the existing OAuth login — selectable from the API-Keys section, no API key.

**Architecture:** A new `antigravity` brain provider mirrors the existing Codex provider (one provider, subscription-OAuth backend, drives an external CLI). A binary-agnostic resolver prefers `agy` and falls back to the installed Gemini CLI. An auth service reports login status from `~/.gemini/`. The provider is OAuth-only (no API key slot) and excluded from the live model catalog. A heavy-worker variant reuses the resolver for Jarvis-Agents.

**Tech Stack:** Python 3.11 (stdlib `asyncio`/`subprocess`/`pathlib`), FastAPI routes, React/TS frontend, pytest + vitest.

## Global Constraints

- Output language of all artifacts is **English** (code, comments, docstrings, route descriptions, tests).
- Cross-platform (CLOUD.md Rule #1): pure stdlib, `pathlib`, capability probes; degrade to a clean "not installed" snapshot when no CLI is present; must boot on `python:3.11-slim` with the provider simply unavailable. No new hard dependency.
- Subprocess hygiene (AP-1): every `subprocess.*` / `asyncio.create_subprocess_exec` passes `creationflags=NO_WINDOW_CREATIONFLAGS` from `jarvis.core.process_utils` (visible-console flag only for the deliberate interactive login).
- **ToS (hard):** only ever shell out to the official binary. Never read the OAuth token value to make our own HTTP request. Never log a token value.
- Risk-tier / router discipline unchanged. No new spawn-tool in any worker set (AP-5/AP-14).
- After editing `pyproject.toml` entry points: `pip install -e . --no-deps`.

---

## File structure

| File | Responsibility | New/Modify |
|---|---|---|
| `jarvis/google_cli/__init__.py` | package marker | New |
| `jarvis/google_cli/resolver.py` | resolve which official CLI to drive + argv prefix | New |
| `jarvis/google_cli/auth_service.py` | report login status, start/stop login | New |
| `jarvis/plugins/brain/antigravity.py` | OAuth-only brain via CLI subprocess | New |
| `pyproject.toml` | brain entry point | Modify |
| `jarvis/ui/web/provider_spec.py` | `AuthMode` literal + provider entry | Modify |
| `jarvis/brain/model_catalog.py` | curated model list (exclude from live fetch) | Modify |
| `jarvis/ui/web/provider_routes.py` | `/api/antigravity/{status,login,logout}` | Modify |
| `jarvis/ui/web/frontend/src/components/AntigravityAuthWidget.tsx` | connect UI | New |
| `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx` | render widget for `auth_mode="antigravity"` | Modify |
| `jarvis/ui/web/frontend/src/hooks/useProviders.ts` | login/logout/status fns | Modify |
| `jarvis/missions/workers/google_cli_worker.py` | heavy worker via CLI | New (Phase 3) |

---

## Phase 1 — Backend brain path

### Task 1: Binary resolver

**Files:**
- Create: `jarvis/google_cli/__init__.py` (empty)
- Create: `jarvis/google_cli/resolver.py`
- Test: `tests/unit/google_cli/test_resolver.py`

**Interfaces:**
- Produces: `GoogleCli` frozen dataclass `{kind: str, argv_prefix: list[str], version: str | None}`; `resolve_google_cli(*, which=shutil.which, npm_bundle=_default_npm_bundle) -> GoogleCli | None`.

- [ ] **Step 1: Write the failing test** (`tests/unit/google_cli/test_resolver.py`)

```python
from jarvis.google_cli.resolver import resolve_google_cli, GoogleCli


def test_prefers_agy_when_on_path():
    def which(name):
        return f"/usr/bin/{name}" if name in ("agy", "agy.exe") else None
    cli = resolve_google_cli(which=which, npm_bundle=lambda: None)
    assert isinstance(cli, GoogleCli)
    assert cli.kind == "agy"
    assert cli.argv_prefix[0].endswith("agy") or cli.argv_prefix == ["/usr/bin/agy"]


def test_falls_back_to_gemini_on_path():
    def which(name):
        return f"/usr/bin/{name}" if name in ("gemini", "gemini.cmd") else None
    cli = resolve_google_cli(which=which, npm_bundle=lambda: None)
    assert cli.kind == "gemini"


def test_falls_back_to_npm_bundle(tmp_path):
    bundle = tmp_path / "gemini.js"
    bundle.write_text("// stub")
    cli = resolve_google_cli(which=lambda name: None, npm_bundle=lambda: str(bundle))
    assert cli.kind == "gemini"
    assert cli.argv_prefix[0] == "node"
    assert cli.argv_prefix[1] == str(bundle)


def test_none_when_nothing_available():
    assert resolve_google_cli(which=lambda name: None, npm_bundle=lambda: None) is None
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/unit/google_cli/test_resolver.py -v` → FAIL (module missing).

- [ ] **Step 3: Write minimal implementation** (`jarvis/google_cli/resolver.py`)

```python
"""Resolve which official Google agent CLI to drive, and how to invoke it.

Order: Antigravity ``agy`` (official successor) > Gemini CLI on PATH >
the npm-global Gemini bundle via ``node`` (covers a broken PATH shim).
Returns ``None`` when no official binary is present — the caller then reports
the provider as not installed (CLOUD.md Rule #1: clean no-op, never raises).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

_AGY = ("agy", "agy.exe")
_GEMINI = ("gemini", "gemini.cmd", "gemini.exe")


@dataclass(frozen=True)
class GoogleCli:
    kind: str               # "agy" | "gemini"
    argv_prefix: list[str] = field(default_factory=list)
    version: str | None = None


def _default_npm_bundle() -> str | None:
    """Path to the npm-global gemini bundle, or None. Best-effort, never raises."""
    try:
        root = subprocess.run(
            ["npm", "root", "-g"], capture_output=True, text=True, timeout=5.0,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not root:
        return None
    bundle = os.path.join(root, "@google", "gemini-cli", "bundle", "gemini.js")
    return bundle if os.path.isfile(bundle) else None


def resolve_google_cli(
    *,
    which: Callable[[str], str | None] = shutil.which,
    npm_bundle: Callable[[], str | None] = _default_npm_bundle,
) -> GoogleCli | None:
    for name in _AGY:
        path = which(name)
        if path:
            return GoogleCli(kind="agy", argv_prefix=[path])
    for name in _GEMINI:
        path = which(name)
        if path:
            return GoogleCli(kind="gemini", argv_prefix=[path])
    bundle = npm_bundle()
    if bundle:
        node = which("node") or "node"
        return GoogleCli(kind="gemini", argv_prefix=[node, bundle])
    return None
```

- [ ] **Step 4: Run test to verify it passes** — `pytest tests/unit/google_cli/test_resolver.py -v` → PASS (4 tests).

- [ ] **Step 5: Commit** — `git add jarvis/google_cli/ tests/unit/google_cli/test_resolver.py && git commit -m "feat(antigravity): binary resolver (agy > gemini > npm bundle)"`

---

### Task 2: Auth service

**Files:**
- Create: `jarvis/google_cli/auth_service.py`
- Test: `tests/unit/google_cli/test_auth_service.py`

**Interfaces:**
- Consumes: `resolve_google_cli` (Task 1).
- Produces: `GoogleCliAuthStatus` frozen dataclass with `to_dict()`; `GoogleCliAuthService` with `status() -> GoogleCliAuthStatus`, `start_login() -> subprocess.Popen`, `logout_blocking() -> tuple[bool, str | None]`. Status detection reads `~/.gemini/oauth_creds.json` + `~/.gemini/settings.json` + `~/.gemini/google_accounts.json` (override dir via `$GEMINI_HOME`, default `~/.gemini`).

- [ ] **Step 1: Write the failing test** (`tests/unit/google_cli/test_auth_service.py`)

```python
import json
from jarvis.google_cli.auth_service import GoogleCliAuthService, _derive_google_auth


def test_derive_oauth_personal():
    settings = {"security": {"auth": {"selectedType": "oauth-personal"}}}
    assert _derive_google_auth(creds_present=True, settings=settings) == (True, "oauth-personal")


def test_derive_unknown_without_creds():
    assert _derive_google_auth(creds_present=False, settings={}) == (False, "unknown")


def test_status_connected(tmp_path, monkeypatch):
    gem = tmp_path / ".gemini"
    gem.mkdir()
    (gem / "oauth_creds.json").write_text(json.dumps({"access_token": "x", "refresh_token": "y"}))
    (gem / "settings.json").write_text(json.dumps(
        {"security": {"auth": {"selectedType": "oauth-personal"}}, "model": {"name": "gemini-3.1-pro-preview"}}))
    (gem / "google_accounts.json").write_text(json.dumps({"active": "user@example.com"}))
    monkeypatch.setenv("GEMINI_HOME", str(gem))
    svc = GoogleCliAuthService()
    # Force "installed" by stubbing the resolver seam:
    svc._resolve = lambda: type("C", (), {"kind": "gemini", "argv_prefix": ["gemini"], "version": "0.47.0"})()
    st = svc.status()
    assert st.installed and st.connected
    assert st.mode == "oauth-personal"
    assert st.user_email == "user@example.com"


def test_status_not_installed(monkeypatch):
    svc = GoogleCliAuthService()
    svc._resolve = lambda: None
    st = svc.status()
    assert not st.installed and not st.connected
    assert st.to_dict()["mode"] == "unknown"
```

- [ ] **Step 2: Run** — `pytest tests/unit/google_cli/test_auth_service.py -v` → FAIL.

- [ ] **Step 3: Write implementation** (`jarvis/google_cli/auth_service.py`) — mirror `jarvis/codex_auth.py`, adapted:

```python
"""Google agent CLI auth service — status / login / logout.

Reports an honest snapshot of the official Google CLI login (Antigravity ``agy``
or the Gemini CLI) used to bill the Brain/Subagents against the user's Google
subscription. Pure stdlib, cross-platform; degrades to "not installed" when no
binary resolves. No token value is ever read into business logic or logged.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.google_cli.resolver import GoogleCli, resolve_google_cli

log = logging.getLogger(__name__)

if sys.platform == "win32":
    _NEW_CONSOLE_FLAGS: int = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
else:
    _NEW_CONSOLE_FLAGS = 0


def _gemini_home() -> Path:
    override = os.environ.get("GEMINI_HOME")
    return Path(override) if override else (Path.home() / ".gemini")


def _derive_google_auth(*, creds_present: bool, settings: dict[str, Any]) -> tuple[bool, str]:
    """``(connected, mode)`` from the on-disk gemini login state."""
    sel = (
        settings.get("security", {}).get("auth", {}).get("selectedType")
        if isinstance(settings, dict) else None
    )
    if creds_present and sel == "oauth-personal":
        return True, "oauth-personal"
    if creds_present:
        return True, "oauth-personal"  # creds without an explicit type still mean a login
    if sel in ("gemini-api-key", "vertex-ai"):
        return True, "api_key"
    return False, "unknown"


def _email_from_accounts(home: Path) -> str | None:
    try:
        data = json.loads((home / "google_accounts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    active = data.get("active") if isinstance(data, dict) else None
    return active if isinstance(active, str) and active else None


@dataclass(frozen=True)
class GoogleCliAuthStatus:
    installed: bool = False
    connected: bool = False
    mode: str = "unknown"            # "oauth-personal" | "api_key" | "unknown"
    cli_kind: str | None = None      # "agy" | "gemini"
    message: str = ""
    version: str | None = None
    user_email: str | None = None
    binary_path: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "installed": self.installed, "connected": self.connected,
            "mode": self.mode, "cli_kind": self.cli_kind, "message": self.message,
            "version": self.version, "user_email": self.user_email,
            "binary_path": self.binary_path, "error": self.error,
        }


class GoogleCliAuthService:
    def _resolve(self) -> GoogleCli | None:   # seam for tests
        return resolve_google_cli()

    def _read_json(self, name: str) -> dict[str, Any]:
        try:
            data = json.loads((_gemini_home() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def status(self) -> GoogleCliAuthStatus:
        cli = self._resolve()
        if cli is None:
            return GoogleCliAuthStatus(
                message="No Google CLI found — install Antigravity (agy) or the Gemini CLI.",
                error="no google cli binary",
            )
        creds_present = (_gemini_home() / "oauth_creds.json").is_file()
        connected, mode = _derive_google_auth(
            creds_present=creds_present, settings=self._read_json("settings.json"))
        email = _email_from_accounts(_gemini_home()) if connected else None
        if not connected:
            msg = "Installed but not logged in — run the Google login."
        elif mode == "oauth-personal":
            msg = f"Connected via Google subscription ({email})." if email else "Connected via Google subscription."
        else:
            msg = "Connected via a Google API key."
        return GoogleCliAuthStatus(
            installed=True, connected=connected, mode=mode, cli_kind=cli.kind,
            message=msg, version=cli.version, user_email=email,
            binary_path=(cli.argv_prefix[0] if cli.argv_prefix else ""),
        )

    def start_login(self) -> subprocess.Popen[bytes]:
        cli = self._resolve()
        if cli is None:
            raise FileNotFoundError("No Google CLI found (install agy or the Gemini CLI).")
        # agy: `agy login`; gemini: bare run drops into interactive auth picker.
        argv = [*cli.argv_prefix, "login"] if cli.kind == "agy" else list(cli.argv_prefix)
        if sys.platform == "win32":
            kwargs: dict[str, Any] = {"creationflags": _NEW_CONSOLE_FLAGS}
        else:
            kwargs = {
                "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL, "start_new_session": True,
            }
        return subprocess.Popen(argv, **kwargs)  # noqa: S603 — fixed argv, shell=False

    def logout_blocking(self) -> tuple[bool, str | None]:
        # Gemini CLI: remove the on-disk creds. agy: best-effort `agy logout`.
        cli = self._resolve()
        if cli is not None and cli.kind == "agy":
            try:
                proc = subprocess.run(
                    [*cli.argv_prefix, "logout"], capture_output=True, text=True,
                    timeout=15.0, creationflags=__import__("jarvis.core.process_utils",
                    fromlist=["NO_WINDOW_CREATIONFLAGS"]).NO_WINDOW_CREATIONFLAGS)
                if proc.returncode == 0:
                    return True, None
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            (_gemini_home() / "oauth_creds.json").unlink(missing_ok=True)
            return True, None
        except OSError as exc:
            return False, str(exc)
```

> Note for the implementer: the `__import__(...)` inline in `logout_blocking` is ugly — replace it with a module-level `from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS` import and use it directly. (Kept inline here only to keep the diff one block; fix it in the real edit.)

- [ ] **Step 4: Run** — `pytest tests/unit/google_cli/test_auth_service.py -v` → PASS.

- [ ] **Step 5: Commit** — `git add jarvis/google_cli/auth_service.py tests/unit/google_cli/test_auth_service.py && git commit -m "feat(antigravity): Google CLI auth service (status/login/logout)"`

---

### Task 3: Brain plugin + entry point

**Files:**
- Create: `jarvis/plugins/brain/antigravity.py`
- Modify: `pyproject.toml` (`[project.entry-points."jarvis.brain"]` → add `antigravity`)
- Test: `tests/unit/plugins/brain/test_antigravity_brain.py`

**Interfaces:**
- Consumes: `resolve_google_cli` (Task 1), `BrainDelta`/`BrainRequest` (`jarvis.core.protocols`).
- Produces: `AntigravityBrain` (`name="antigravity"`, `complete(req) -> AsyncIterator[BrainDelta]`, OAuth-only).

This mirrors `jarvis/plugins/brain/codex.py::CodexBrain._complete_via_cli`, with these deltas: resolve via `resolve_google_cli`; argv = `[*prefix, "-p", prompt, "-m", model, "--approval-mode", "plan", "-o", "json"]`; drop `GEMINI_API_KEY`/`GOOGLE_API_KEY`/`GOOGLE_APPLICATION_CREDENTIALS` from env; parse the gemini `-o json` result (a single JSON object with a `response` field) instead of codex's NDJSON `agent_message` frames.

- [ ] **Step 1: Write the failing test** — fake the subprocess (patch `asyncio.create_subprocess_exec` to return a stub proc whose `communicate()` yields `(b'{"response":"OK"}', b"")`), assert the brain yields a `BrainDelta(content="OK")`; a second test with `resolve_google_cli` returning `None` asserts `RuntimeError`. (Mirror `tests/unit/plugins/brain/test_codex_brain.py` if present.)

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `AntigravityBrain` — copy `CodexBrain`'s CLI structure (temp dir, `communicate()` task, 3s progress ticks, `_CLI_TIMEOUT_S=120`, kill-on-timeout), swapping the argv/env/parse per the deltas above. Default model `gemini-3.1-pro-preview`. Then add the entry point:

```toml
# pyproject.toml, under [project.entry-points."jarvis.brain"]
antigravity = "jarvis.plugins.brain.antigravity:AntigravityBrain"
```

- [ ] **Step 4: Run** → PASS; then `pip install -e . --no-deps` and `python -m jarvis --plugins` shows `antigravity`.

- [ ] **Step 5: Commit** — `git add jarvis/plugins/brain/antigravity.py pyproject.toml tests/unit/plugins/brain/test_antigravity_brain.py && git commit -m "feat(antigravity): OAuth-only brain via official Google CLI"`

---

### Task 4: Provider spec + catalog wiring

**Files:**
- Modify: `jarvis/ui/web/provider_spec.py` (AuthMode literal + ProviderSpec entry)
- Modify: `jarvis/brain/model_catalog.py` (curated list, keep out of live fetch)
- Test: `tests/unit/ui/test_provider_spec_antigravity.py`

- [ ] **Step 1: Test** — assert `get_spec("antigravity").auth_mode == "antigravity"`, `secret_keys == ()`, `tier == "brain"`; assert `"antigravity" not in model_catalog.CATALOG_PROVIDERS`; assert the curated list for `antigravity` is non-empty and contains `gemini-3.1-pro-preview`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement**
  - `provider_spec.py`: `AuthMode = Literal["api_key", "codex", "antigravity", "none"]`; add the `ProviderSpec(id="antigravity", label="Antigravity (Google subscription)", tier="brain", auth_mode="antigravity", secret_keys=(), dashboard_url="https://antigravity.google", login_cli=("agy","login"), install_hint="Install Antigravity (agy) or sign in with the Gemini CLI", credential_path_hint="~/.gemini/oauth_creds.json")`.
  - `model_catalog.py`: ensure `antigravity` is **not** in `CATALOG_PROVIDERS`; add a curated fallback list (mirror the Codex curated block, ~lines 192-197): `("gemini-3.1-pro-preview", "gemini-3-flash", "gemini-3.5-flash")`, `source="curated"`.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(antigravity): provider spec + curated model catalog"`

---

### Task 5: Backend routes

**Files:**
- Modify: `jarvis/ui/web/provider_routes.py` (add `/api/antigravity/{status,login,logout}`)
- Test: `tests/integration/test_antigravity_routes.py`

- [ ] **Step 1: Test** — FastAPI `TestClient`: `GET /api/antigravity/status` returns the `GoogleCliAuthStatus.to_dict()` shape; `POST /api/antigravity/login` returns 409 with an install hint when no CLI is present (stub the service).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** — mirror the codex routes (`provider_routes.py:770-829`) for `antigravity`, backed by `GoogleCliAuthService`. Also make sure the `set_brain_model` probe-skip (line 763) treats `auth_mode == "antigravity"` like `"codex"` (skip the live probe — the CLI ignores/slow-paths the model id): change the guard to `getattr(spec, "auth_mode", None) not in ("codex", "antigravity")`.

- [ ] **Step 4: Run** → PASS; full `pytest tests/integration/test_antigravity_routes.py -v`.

- [ ] **Step 5: Commit** — `git commit -am "feat(antigravity): status/login/logout routes"`

---

## Phase 2 — Frontend

### Task 6: Connect widget + API-Keys wiring

**Files:**
- Create: `jarvis/ui/web/frontend/src/components/AntigravityAuthWidget.tsx`
- Modify: `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx` (render the widget for `auth_mode="antigravity"`, mirror the `CodexAuthWidget` branch)
- Modify: `jarvis/ui/web/frontend/src/hooks/useProviders.ts` (`startAntigravityLogin`, `antigravityLogout`, `getAntigravityStatus`)
- Test: `AntigravityAuthWidget.test.tsx`

- [ ] **Step 1: Test** — render connected/disconnected states; clicking "Connect with Google" calls `POST /api/antigravity/login`.
- [ ] **Step 2: Run** (`npm run test`) → FAIL.
- [ ] **Step 3: Implement** — copy `CodexAuthWidget` (`ApiKeysView.tsx:539-669`) into `AntigravityAuthWidget.tsx`, swap endpoints to `/api/antigravity/*`, label "Antigravity (Google subscription)", reuse `CliConnectCoach`/`CliConnectPoller` for the connect-then-detect UX. Add the three hook functions to `useProviders.ts`. Wire the `AuthWidget` switch in `ApiKeysView.tsx` to render `AntigravityAuthWidget` when `auth_mode === "antigravity"`.
- [ ] **Step 4: Run** → PASS; `npx tsc -b` clean; `npm run build` succeeds.
- [ ] **Step 5: Commit** — `git commit -am "feat(antigravity): connect-with-Google widget in API-Keys"`

---

## Phase 3 — Jarvis-Agent worker

### Task 7: Heavy worker backend

**Files:**
- Create: `jarvis/missions/workers/google_cli_worker.py`
- Modify: Jarvis-Agent mapping source (the `/api/jarvis-agent/status` mapping rows) to add an `antigravity` row whose `key_set` = `GoogleCliAuthService().status().connected`
- Modify: `jarvis/ui/web/frontend/src/components/SubagentSection.tsx` (`PROVIDER_LABELS`: `"antigravity" → "Antigravity (Google subscription)"`)
- Test: `tests/missions/test_google_cli_worker.py`

- [ ] **Step 1: Test** — fake subprocess emitting `stream-json` worker frames; assert the worker streams `WorkerProgress` and a final result. (Mirror `tests/missions/test_codex_worker.py`.)
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — mirror `CodexWorker`, driving `[*prefix, "-p", task, "--approval-mode", "yolo", "-o", "stream-json"]` inside the existing worktree + Job-Object isolation (no isolation change). Wire the mapping row + label.
- [ ] **Step 4: Run** → PASS; `pytest tests/missions/test_google_cli_worker.py -v`.
- [ ] **Step 5: Commit** — `git commit -am "feat(antigravity): Google-CLI subagent worker"`

---

## Verification (whole feature)

- `pytest tests/unit/google_cli/ tests/unit/plugins/brain/test_antigravity_brain.py tests/integration/test_antigravity_routes.py tests/missions/test_google_cli_worker.py -v`
- `pytest tests/unit/ -q` (no regressions) + `npm run test` + `ruff check jarvis/` + `npx tsc -b` + `npm run build`
- `python -m jarvis --plugins` lists `antigravity`.
- Manual: API-Keys → "Antigravity (Google subscription)" card → Connect → status flips to connected → set active → "Test" button (user-triggered, D2).

## Self-review notes

- Spec coverage: resolver (§5.1)→T1, auth (§5.2)→T2, brain (§5.3)→T3, spec/config/catalog (§5.5/5.6)→T4, routes (§5.7)→T5, UI (§5.8)→T6, Jarvis-Agent (§5.4)→T7. All covered.
- Open points carried from spec §10: live billed verification + browser tier-check deferred; `agy` Windows install unconfirmed (resolver falls back to Gemini CLI).
