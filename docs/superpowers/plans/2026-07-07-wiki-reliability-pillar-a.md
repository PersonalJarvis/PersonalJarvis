# Wiki Reliability (Pillar A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make wiki writes reliable and honest on every install — an explicit
"write this to the wiki" request produces a visible note file (or an honest
failure message) on any OS, with the weakest free model and any single key.

**Architecture:** Evolve the existing subsystem (`jarvis/memory/wiki/`), per
the approved spec
`docs/superpowers/specs/2026-07-07-wiki-reliability-and-premium-rag-design.md`
(Pillar A only; Pillar B Premium tier is deferred and OUT OF SCOPE). Three
waves: (1) vault path + Obsidian vault choice, (2) deterministic explicit
command path with confirm-after-write, (3) ambient tuning + health surface.

**Tech Stack:** Python 3.11+ (FastAPI, Pydantic, SQLite/FTS5, asyncio), React/
TypeScript frontend (`jarvis/ui/web/frontend/`), pytest (asyncio_mode=auto,
fakes in `tests/fakes/` — never `unittest.mock`).

## Global Constraints

- English-only artifacts (CLAUDE.md §1). German/Spanish tokens appear ONLY as
  matcher input vocabulary and test fixtures (closed-list categories 3 + 4);
  mark load-bearing lines with `# i18n-allow`.
- AP-3: tools run only via `ToolExecutor.execute()` — never `Tool.execute()`.
- AP-9 / AP-26: nothing new on the voice hot path or the boot critical path;
  background work never blocks or crashes the turn.
- AP-7: `jarvis.toml` mutations only via `jarvis/core/config_writer.py`.
- AP-16: every new config sub-table keeps `model_config = ConfigDict(extra="allow")`.
- User-facing runtime phrases carry ALL supported languages (de/en/es) and are
  resolved per turn — no two-language tables (CLAUDE.md §1 runtime rules).
- Cross-platform: `pathlib`, UTF-8, no hardcoded user paths; everything must
  boot headless on `python:3.11-slim` (Obsidian features degrade to quiet
  no-ops).
- Git: commit after each task, staging ONLY the files you touched by explicit
  path (`git add <paths>`), Conventional-Commit messages, never push.
- Working tree is shared with other sessions: never `git add -A`/`git add .`.
- New worktree? Run `pwsh scripts/preflight.ps1` first (AP-8). Live-app code
  changes take effect only after `POST /api/settings/restart-app`.
- Definition of done (spec §3 G6): the three non-maintainer paths of
  CLAUDE.md §3 hold (fresh install with one arbitrary key, headless Linux,
  cross-family fallback).

---

### Task 1: Canonical CWD-independent vault-root resolver (spec A7)

**Files:**
- Create: `jarvis/memory/wiki/vault_root.py`
- Modify: `jarvis/ui/web/server.py:2186-2190` and `:2250-2252`
- Modify: `jarvis/ui/web/wiki_routes.py:64-82` (`_resolve_vault_root`)
- Modify: `jarvis/ui/web/setup_routes.py:92-117` (`_resolve_vault_path`)
- Modify: any remaining `Path.cwd()`-based vault resolution found by grep
  (check `jarvis/brain/wiki_context.py`, `jarvis/memory/wiki/cli.py`)
- Test: `tests/unit/memory/wiki/test_vault_root.py`

**Interfaces:**
- Consumes: `jarvis.core.paths.repo_root() -> Path` (exists).
- Produces: `resolve_vault_root(raw, *, cwd=None) -> VaultRootResolution`
  with fields `path: Path`, `source: str` (`"absolute" | "repo_root" |
  "legacy_cwd"`), `legacy_conflict: bool`. Later tasks (5) read
  `vault_root.last_resolution()` for the health snapshot.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/memory/wiki/test_vault_root.py
"""Vault-root resolution must not depend on the process CWD (spec A7)."""
from pathlib import Path

from jarvis.core.paths import repo_root
from jarvis.memory.wiki.vault_root import resolve_vault_root


def test_relative_root_anchors_to_repo_root_not_cwd(tmp_path):
    res = resolve_vault_root("wiki/obsidian-vault", cwd=tmp_path)
    assert res.path == (repo_root() / "wiki" / "obsidian-vault").resolve()
    assert res.source == "repo_root"


def test_absolute_root_passes_through(tmp_path):
    res = resolve_vault_root(tmp_path / "vault", cwd=tmp_path)
    assert res.path == (tmp_path / "vault").resolve()
    assert res.source == "absolute"
    assert res.legacy_conflict is False


def test_populated_legacy_cwd_vault_wins_and_flags_conflict(tmp_path):
    legacy = tmp_path / "wiki" / "obsidian-vault"
    legacy.mkdir(parents=True)
    (legacy / "log.md").write_text("# log\n", encoding="utf-8")
    res = resolve_vault_root("wiki/obsidian-vault", cwd=tmp_path)
    # The anchored repo-root vault exists and is populated in this repo, so
    # the anchor wins; the conflict must still be flagged for the health
    # surface. If the anchored path were empty, legacy would win (see module).
    assert res.legacy_conflict is True


def test_none_falls_back_to_default_relative_root(tmp_path):
    res = resolve_vault_root(None, cwd=tmp_path)
    assert res.path.name == "obsidian-vault"
    assert res.path.is_absolute()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_vault_root.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.memory.wiki.vault_root`

- [ ] **Step 3: Implement the resolver**

```python
# jarvis/memory/wiki/vault_root.py
"""Single canonical vault-root resolution (spec A7).

Every consumer of ``[wiki_integration].vault_root`` resolves through
:func:`resolve_vault_root`. A relative root anchors to the repo root
(``jarvis/core/paths.repo_root()``), never to ``Path.cwd()`` — a desktop
launch from another directory used to read/write a different vault than
the UI displayed.

Legacy migration: installs that ran with the old CWD-based resolution may
hold a populated vault under ``<old cwd>/wiki/obsidian-vault``. When that
legacy vault is populated and the anchored one is empty/missing, the
populated one wins; the ambiguity is flagged for the health surface
instead of silently forking the vault.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.paths import repo_root

_DEFAULT_RELATIVE = Path("wiki/obsidian-vault")

_lock = threading.Lock()
_last: "VaultRootResolution | None" = None


@dataclass(frozen=True, slots=True)
class VaultRootResolution:
    path: Path
    source: str  # "absolute" | "repo_root" | "legacy_cwd"
    legacy_conflict: bool


def _non_empty_dir(p: Path) -> bool:
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return False


def resolve_vault_root(
    raw: str | Path | None, *, cwd: Path | None = None,
) -> VaultRootResolution:
    """Resolve the configured vault root to an absolute path.

    ``cwd`` is injectable for tests; production callers omit it.
    """
    raw_path = Path(raw) if raw else _DEFAULT_RELATIVE
    if raw_path.is_absolute():
        res = VaultRootResolution(raw_path.resolve(), "absolute", False)
        return _remember(res)

    anchored = (repo_root() / raw_path).resolve()
    legacy = ((cwd or Path.cwd()) / raw_path).resolve()
    if legacy == anchored:
        return _remember(VaultRootResolution(anchored, "repo_root", False))
    legacy_populated = _non_empty_dir(legacy)
    if legacy_populated and not _non_empty_dir(anchored):
        return _remember(VaultRootResolution(legacy, "legacy_cwd", True))
    return _remember(
        VaultRootResolution(anchored, "repo_root", legacy_populated)
    )


def _remember(res: VaultRootResolution) -> VaultRootResolution:
    global _last
    with _lock:
        _last = res
    return res


def last_resolution() -> VaultRootResolution | None:
    """Most recent resolution — read by the wiki health snapshot."""
    with _lock:
        return _last
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/memory/wiki/test_vault_root.py -v`
Expected: 4 passed

- [ ] **Step 5: Rewire all call sites**

In `jarvis/ui/web/server.py::_init_wiki_integration` replace

```python
        vault_root = Path(wiki_cfg.vault_root)
        if not vault_root.is_absolute():
            # Resolve relative to the repo root (same convention as the rest
            # of the app — CWD is the repo root at runtime).
            vault_root = Path.cwd() / vault_root
```

with

```python
        from jarvis.memory.wiki.vault_root import resolve_vault_root

        vault_root = resolve_vault_root(wiki_cfg.vault_root).path
```

Apply the same replacement in `_init_wiki_boot_index` (server.py:2250-2252),
in `wiki_routes._resolve_vault_root` (keep its `None`-tolerant contract:
return `resolve_vault_root(raw).path` at the end), and in
`setup_routes._resolve_vault_path` (drop the `repo_root` app-state fallback in
favor of the resolver). Then sweep for stragglers:

Run: `grep -rn "vault_root" jarvis/ --include='*.py' | grep -i "cwd"`
Expected: no remaining `Path.cwd()`-based vault resolution outside
`vault_root.py` itself. Also check `jarvis/brain/wiki_context.py` and
`jarvis/memory/wiki/cli.py` resolve through the new function
(`tests/unit/brain/test_wiki_injector_vault_root.py` guards the injector —
update it if it pinned the old convention).

- [ ] **Step 6: Run the wiki + routes test suites**

Run: `pytest tests/unit/memory/wiki/ tests/unit/ui/web/test_wiki_routes.py tests/unit/brain/test_wiki_injector_vault_root.py tests/unit/setup/ -v`
Expected: PASS (fix any test that asserted the old CWD convention)

- [ ] **Step 7: Commit**

```bash
git add jarvis/memory/wiki/vault_root.py jarvis/ui/web/server.py jarvis/ui/web/wiki_routes.py jarvis/ui/web/setup_routes.py tests/unit/memory/wiki/test_vault_root.py
# plus any straggler files updated in Step 5
git commit -m "fix(wiki): resolve vault root against repo root, not CWD"
```

---

### Task 2: Cross-platform obsidian.json discovery

**Files:**
- Modify: `jarvis/setup/obsidian.py:206-216` (`_default_obsidian_config_path`)
- Test: `tests/unit/setup/test_obsidian_detector.py` (extend)

**Interfaces:**
- Produces: `_default_obsidian_config_path(platform: str | None = None) -> Path`
  — platform-aware; `read_obsidian_vaults()` keeps its signature and now
  works on macOS/Linux. Task 3 relies on `read_obsidian_vaults()` returning
  the user's vault list on every OS.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/unit/setup/test_obsidian_detector.py
class TestDefaultConfigPathCrossPlatform:
    """obsidian.json lives in a platform-specific config dir (spec A6)."""

    def test_windows_uses_appdata(self, monkeypatch, tmp_path):
        monkeypatch.setenv("APPDATA", str(tmp_path))
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="win32")
        assert p == tmp_path / "obsidian" / "obsidian.json"

    def test_macos_uses_application_support(self, monkeypatch):
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="darwin")
        assert p == (
            Path.home() / "Library" / "Application Support"
            / "obsidian" / "obsidian.json"
        )

    def test_linux_prefers_xdg_config_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="linux")
        assert p == tmp_path / "obsidian" / "obsidian.json"

    def test_linux_falls_back_to_dot_config(self, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        from jarvis.setup.obsidian import _default_obsidian_config_path
        p = _default_obsidian_config_path(platform="linux")
        assert p == Path.home() / ".config" / "obsidian" / "obsidian.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/setup/test_obsidian_detector.py -v -k CrossPlatform`
Expected: FAIL — `TypeError: _default_obsidian_config_path() got an unexpected keyword argument 'platform'`

- [ ] **Step 3: Implement**

Replace `_default_obsidian_config_path` in `jarvis/setup/obsidian.py`:

```python
def _default_obsidian_config_path(platform: str | None = None) -> Path:
    """Return the canonical ``obsidian.json`` path for this OS.

    Obsidian stores its vault index in the per-user config dir:
    Windows ``%APPDATA%/obsidian``, macOS
    ``~/Library/Application Support/obsidian``, Linux
    ``$XDG_CONFIG_HOME/obsidian`` (fallback ``~/.config/obsidian``).
    ``platform`` is injectable for tests; production callers omit it.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "obsidian" / "obsidian.json"
        return Path.home() / "AppData" / "Roaming" / "obsidian" / "obsidian.json"
    if plat == "darwin":
        return (
            Path.home() / "Library" / "Application Support"
            / "obsidian" / "obsidian.json"
        )
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "obsidian" / "obsidian.json"
```

Add `import sys` to the module imports if absent. Update the module docstring
line "user-level ``%APPDATA%\\obsidian\\obsidian.json``" to mention all three
platforms.

- [ ] **Step 4: Run the setup test suite**

Run: `pytest tests/unit/setup/ -v`
Expected: PASS (including the pre-existing detector/register tests)

- [ ] **Step 5: Commit**

```bash
git add jarvis/setup/obsidian.py tests/unit/setup/test_obsidian_detector.py
git commit -m "feat(setup): platform-aware obsidian.json discovery (macOS/Linux)"
```

---

### Task 3: Obsidian connect with vault choice — backend (spec A6)

**Files:**
- Modify: `jarvis/ui/web/setup_routes.py` (extend register endpoint; add
  vault-list endpoint)
- Modify: `jarvis/memory/wiki/fts_index.py` (add `rebuild_index`)
- Test: `tests/unit/ui/web/test_setup_routes_vault_choice.py` (new)

**Interfaces:**
- Consumes: `read_obsidian_vaults()` (Task 2), `resolve_vault_root` (Task 1),
  `jarvis/core/config_writer.py` (existing atomic TOML writer — read its
  public API before wiring; AP-7), `fts_index.index_vault(vault_root, conn)`
  (exists).
- Produces:
  - `GET /api/setup/obsidian/vaults` → `{"ok": true, "config_exists": bool,
    "vaults": [{"path": str, "name": str}]}`
  - `POST /api/setup/obsidian/register` now accepts an optional JSON body
    `{"mode": "separate" | "existing", "existing_vault_path": str | null}`
    (no body = `"separate"` = today's behavior, backward compatible) and
    returns the existing `ObsidianRegisterResponse` plus new fields
    `active_vault_root: str` and `restart_required: bool`.
  - `fts_index.rebuild_index(vault_root: Path, conn) -> int` — clears
    `wiki_fts` and re-indexes; Task 4/9 and the vault switch use it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/ui/web/test_setup_routes_vault_choice.py
"""Obsidian connect offers a vault choice (spec A6).

Uses the same TestClient + app.state.config stubbing conventions as
tests/unit/ui/web/test_wiki_routes.py — copy its app fixture setup.
"""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import setup_routes


def _app(tmp_path: Path, obsidian_json: dict | None) -> FastAPI:
    app = FastAPI()
    app.include_router(setup_routes.router)

    class _WikiCfg:
        vault_root = tmp_path / "jarvis-vault"

    class _Cfg:
        wiki_integration = _WikiCfg()

    app.state.config = _Cfg()
    cfg_path = tmp_path / "obsidian" / "obsidian.json"
    if obsidian_json is not None:
        cfg_path.parent.mkdir(parents=True)
        cfg_path.write_text(json.dumps(obsidian_json), encoding="utf-8")
    app.state.obsidian_config_path = cfg_path  # route override hook
    return app


def test_vault_list_returns_registered_vaults(tmp_path):
    user_vault = tmp_path / "MyVault"
    user_vault.mkdir()
    app = _app(tmp_path, {"vaults": {"abc123": {"path": str(user_vault)}}})
    resp = TestClient(app).get("/api/setup/obsidian/vaults")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["vaults"][0]["path"] == str(user_vault)


def test_register_existing_mode_points_vault_root_into_jarvis_subfolder(
    tmp_path, monkeypatch,
):
    user_vault = tmp_path / "MyVault"
    user_vault.mkdir()
    app = _app(tmp_path, {"vaults": {"abc123": {"path": str(user_vault)}}})

    written: dict = {}

    def _fake_update(values):  # captures the config_writer call
        written.update(values)

    monkeypatch.setattr(setup_routes, "_write_vault_root_config", _fake_update)
    resp = TestClient(app).post(
        "/api/setup/obsidian/register",
        json={"mode": "existing", "existing_vault_path": str(user_vault)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active_vault_root"] == str(user_vault / "Jarvis")
    assert body["restart_required"] is True
    assert (user_vault / "Jarvis").is_dir()          # subfolder created
    assert written                                    # config write happened


def test_register_existing_mode_rejects_unknown_path(tmp_path):
    app = _app(tmp_path, {"vaults": {}})
    resp = TestClient(app).post(
        "/api/setup/obsidian/register",
        json={"mode": "existing", "existing_vault_path": str(tmp_path / "nope")},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "config_missing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/ui/web/test_setup_routes_vault_choice.py -v`
Expected: FAIL — 404 on `/api/setup/obsidian/vaults`, then missing fields

- [ ] **Step 3: Implement the backend**

In `jarvis/ui/web/setup_routes.py`:

1. Read `jarvis/core/config_writer.py` and identify its public atomic-update
   function; wrap it in a module-level seam so tests can stub it:

```python
def _write_vault_root_config(values: dict) -> None:
    """Persist ``[wiki_integration].vault_root`` atomically (AP-7).

    Thin seam over jarvis.core.config_writer so tests can stub the disk
    write. ``values`` is ``{"wiki_integration": {"vault_root": "<abs path>"}}``.
    """
    from jarvis.core import config_writer

    # Use config_writer's public update entry point here — check its actual
    # name/signature in jarvis/core/config_writer.py and call it with the
    # nested values dict. It must be the locked tempfile+BOM-safe path.
    config_writer.update_values(values)  # adapt name to the real API
```

2. Add the request/response models and the vaults endpoint:

```python
class ObsidianRegisterRequest(BaseModel):
    mode: Literal["separate", "existing"] = "separate"
    existing_vault_path: str | None = None


class ObsidianVaultInfo(BaseModel):
    path: str
    name: str


class ObsidianVaultListResponse(BaseModel):
    ok: bool
    config_exists: bool
    vaults: list[ObsidianVaultInfo]


@router.get("/obsidian/vaults", response_model=ObsidianVaultListResponse)
def obsidian_vaults(request: Request) -> ObsidianVaultListResponse:
    """List the user's registered Obsidian vaults for the connect picker."""
    override = getattr(request.app.state, "obsidian_config_path", None)
    try:
        state = read_obsidian_vaults(override)
    except ValueError as exc:
        log.warning("obsidian_vaults: corrupt obsidian.json: %s", exc)
        return ObsidianVaultListResponse(ok=False, config_exists=True, vaults=[])
    return ObsidianVaultListResponse(
        ok=True,
        config_exists=state.config_exists,
        vaults=[
            ObsidianVaultInfo(path=str(v.path), name=Path(str(v.path)).name)
            for v in state.vaults
        ],
    )
```

   (Adapt the `VaultEntry` attribute access to the real model in
   `jarvis/setup/obsidian.py` — it exposes at least the vault path.)

3. Extend the register endpoint: accept the optional
   `ObsidianRegisterRequest` body. `mode == "separate"` keeps the existing
   code path unchanged. `mode == "existing"`:

```python
    target_vault = Path(body.existing_vault_path or "")
    if not target_vault.is_dir():
        return ObsidianRegisterResponse(
            status="config_missing",
            error="existing vault path not found",
            active_vault_root=str(_resolve_vault_path(request)),
            restart_required=False,
        )
    jarvis_root = target_vault / "Jarvis"
    jarvis_root.mkdir(parents=True, exist_ok=True)
    _write_vault_root_config(
        {"wiki_integration": {"vault_root": str(jarvis_root)}}
    )
    return ObsidianRegisterResponse(
        status="added",
        active_vault_root=str(jarvis_root),
        restart_required=True,
    )
```

   Add `active_vault_root: str | None = None` and
   `restart_required: bool = False` to `ObsidianRegisterResponse`. Pointing
   `vault_root` INTO `<vault>/Jarvis` is the containment guarantee: every
   wiki write is physically confined to that subtree, no `AtomicWriter`
   change needed. `restart_required=True` because the running curator/FTS
   still targets the old vault until `POST /api/settings/restart-app`.

4. In `jarvis/memory/wiki/fts_index.py` add:

```python
def rebuild_index(vault_root: Path, conn) -> int:
    """Clear ``wiki_fts`` and re-index ``vault_root`` from scratch.

    Used after a vault switch so search never serves rows from the old
    vault. Returns the number of pages indexed.
    """
    ensure_schema(conn)
    conn.execute("DELETE FROM wiki_fts")
    conn.commit()
    return index_vault(vault_root, conn)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ui/web/test_setup_routes_vault_choice.py tests/unit/setup/ -v`
Expected: PASS (adapt the seam call in Step 3.1 to the real config_writer API
if the stubbed test passes but a manual smoke call fails)

- [ ] **Step 5: CLI coverage for the new route**

New REST routes must stay CLI-reachable (`scripts/ci/check_cli_coverage.py`).
Add a `vaults` subcommand to `jarvis/cli_ctl/commands/wiki.py` following that
file's existing command pattern (same HTTP client + output style), calling
`GET /api/setup/obsidian/vaults`.

Run: `python scripts/ci/check_cli_coverage.py`
Expected: exit 0

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/web/setup_routes.py jarvis/memory/wiki/fts_index.py jarvis/cli_ctl/commands/wiki.py tests/unit/ui/web/test_setup_routes_vault_choice.py
git commit -m "feat(setup): Obsidian connect vault choice — write into existing vault's Jarvis/ subfolder"
```

---

### Task 4: Obsidian vault choice — frontend

**Files:**
- Modify: `jarvis/ui/web/frontend/src/lib/obsidian.ts` (API calls)
- Modify: `jarvis/ui/web/frontend/src/components/wiki/ObsidianSetupDialog.tsx`
  (choice step) — check the actual component filename under
  `src/components/` (`ObsidianSetupDialog`, `ObsidianButton`,
  `ObsidianStatus` exist per the module map)
- Test: colocated `*.test.tsx` next to the dialog (follow
  `WikiView.test.tsx` conventions)

**Interfaces:**
- Consumes: Task 3 endpoints (`GET /api/setup/obsidian/vaults`, extended
  `POST /api/setup/obsidian/register`).
- Produces: a dialog step where the user picks "Use my existing vault"
  (with a vault dropdown) or "Create a separate Jarvis vault"; after
  registering, the dialog shows the active target path and, when
  `restart_required`, a restart hint wired to the existing restart action
  used elsewhere in Settings (`POST /api/settings/restart-app`).

- [ ] **Step 1: Add the API functions to `lib/obsidian.ts`**

```typescript
export interface ObsidianVaultInfo { path: string; name: string }

export async function fetchObsidianVaults(): Promise<ObsidianVaultInfo[]> {
  const res = await fetch("/api/setup/obsidian/vaults");
  if (!res.ok) return [];
  const body = await res.json();
  return body.ok ? body.vaults : [];
}

export type RegisterMode = "separate" | "existing";

export async function registerObsidianVault(
  mode: RegisterMode,
  existingVaultPath?: string,
): Promise<{
  status: string;
  active_vault_root?: string;
  restart_required?: boolean;
  error?: string;
}> {
  const res = await fetch("/api/setup/obsidian/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode, existing_vault_path: existingVaultPath ?? null }),
  });
  return res.json();
}
```

Match the file's existing fetch/error conventions (if it uses a shared
`apiFetch` helper, use that instead of raw `fetch`).

- [ ] **Step 2: Add the choice step to the setup dialog**

Extend the dialog's register flow: before calling register, render two
option cards (radio semantics): **Use my existing vault** (enabled only when
`fetchObsidianVaults()` returned entries; shows a `<select>` of vault names
with the path as tooltip) and **Create a separate Jarvis vault** (default).
On confirm call `registerObsidianVault(mode, selectedPath)`; on success
render the returned `active_vault_root` as "Jarvis writes to: <path>" and,
when `restart_required`, the restart hint/button. All new UI strings are
English source text through the app's existing i18n mechanism (follow how
the dialog's current strings are defined).

- [ ] **Step 3: Frontend tests + build**

Write a component test covering: vault list renders both options; choosing
"existing" + a vault calls the API with `mode: "existing"` and shows the
returned target path. Then:

Run: `cd jarvis/ui/web/frontend && npm run test`
Expected: PASS
Run: `cd jarvis/ui/web/frontend && npm run build`
Expected: build succeeds

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/frontend/src/lib/obsidian.ts "jarvis/ui/web/frontend/src/components/wiki/ObsidianSetupDialog.tsx" jarvis/ui/web/frontend/src/components/wiki/*.test.tsx
git commit -m "feat(ui): vault choice step in Obsidian setup dialog"
```

---

### Task 5: Wiki health recorder + endpoint + CLI (spec A5, backend)

**Files:**
- Create: `jarvis/memory/wiki/health.py`
- Modify: `jarvis/plugins/tool/wiki_ingest.py` (record outcomes)
- Modify: `jarvis/memory/wiki/provider_chain.py:113-123` (record exhaustion)
- Modify: `jarvis/ui/web/server.py` (record bootstrap outcome around
  `_init_wiki_integration`, see `:1726-1731` catch site and `:2224`)
- Modify: `jarvis/memory/wiki/integration.py` (record journal backlog after
  ingest/drain — reuse the sites that already call
  `journal.backlog_count()` or add one call after each consolidation)
- Modify: `jarvis/ui/web/wiki_routes.py` (add `GET /api/wiki/health`)
- Modify: `jarvis/cli_ctl/commands/wiki.py` (add `health` subcommand)
- Test: `tests/unit/memory/wiki/test_health.py`,
  extend `tests/unit/ui/web/test_wiki_routes.py`

**Interfaces:**
- Consumes: `vault_root.last_resolution()` (Task 1),
  `CandidateJournal.backlog_count()` (exists, `journal.py:222`).
- Produces: module-level singleton `health` in
  `jarvis.memory.wiki.health` with:
  - `record_bootstrap(ok: bool, error: str | None = None) -> None`
  - `record_write(ok: bool, *, pages: list[str], error: str | None, source: str) -> None`
  - `record_chain_failure(detail: str) -> None`
  - `record_backlog(count: int) -> None`
  - `snapshot() -> dict` — JSON-safe; keys: `bootstrap_ok`, `bootstrap_error`,
    `vault_root`, `vault_root_source`, `vault_legacy_conflict`, `last_write`
    (`{ts, ok, pages, error, source}` or `null`), `last_chain_failure`
    (`{ts, detail}` or `null`), `journal_backlog`.
  Tasks 8 and 10 read this contract.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/memory/wiki/test_health.py
"""WikiHealth: silent failures must become visible state (spec A5)."""
from jarvis.memory.wiki.health import WikiHealth


def test_snapshot_starts_unknown_but_valid():
    h = WikiHealth()
    snap = h.snapshot()
    assert snap["bootstrap_ok"] is None
    assert snap["last_write"] is None
    assert snap["journal_backlog"] == 0


def test_record_write_success_and_failure_round_trip():
    h = WikiHealth()
    h.record_write(True, pages=["entities/joy.md"], error=None, source="tool")
    assert h.snapshot()["last_write"]["ok"] is True
    h.record_write(False, pages=[], error="all providers failed", source="tool")
    last = h.snapshot()["last_write"]
    assert last["ok"] is False
    assert "providers" in last["error"]


def test_chain_failure_and_backlog_recorded():
    h = WikiHealth()
    h.record_chain_failure("openai 401; gemini 429")
    h.record_backlog(5)
    snap = h.snapshot()
    assert snap["last_chain_failure"]["detail"].startswith("openai")
    assert snap["journal_backlog"] == 5


def test_snapshot_is_json_safe():
    import json

    h = WikiHealth()
    h.record_bootstrap(True)
    h.record_write(True, pages=["log.md"], error=None, source="bridge")
    json.dumps(h.snapshot())  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.memory.wiki.health`

- [ ] **Step 3: Implement `health.py`**

```python
# jarvis/memory/wiki/health.py
"""Process-wide wiki health state (spec A5): honest, not silent.

The wiki subsystem is fire-and-forget by design (AP-9) — failures must
never interrupt a voice turn. This module is the other half of that
contract: every swallowed failure is recorded HERE so the Wiki tab and
``GET /api/wiki/health`` can show it. Pure in-memory state guarded by a
lock; recording must never raise (a health write failing a write path
would invert the design).
"""
from __future__ import annotations

import threading
import time
from typing import Any


class WikiHealth:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bootstrap_ok: bool | None = None
        self._bootstrap_error: str | None = None
        self._last_write: dict[str, Any] | None = None
        self._last_chain_failure: dict[str, Any] | None = None
        self._journal_backlog: int = 0

    def record_bootstrap(self, ok: bool, error: str | None = None) -> None:
        with self._lock:
            self._bootstrap_ok = ok
            self._bootstrap_error = error

    def record_write(
        self, ok: bool, *, pages: list[str], error: str | None, source: str,
    ) -> None:
        with self._lock:
            self._last_write = {
                "ts": time.time(),
                "ok": ok,
                "pages": list(pages),
                "error": error,
                "source": source,
            }

    def record_chain_failure(self, detail: str) -> None:
        with self._lock:
            self._last_chain_failure = {"ts": time.time(), "detail": detail}

    def record_backlog(self, count: int) -> None:
        with self._lock:
            self._journal_backlog = max(0, int(count))

    def snapshot(self) -> dict[str, Any]:
        from jarvis.memory.wiki.vault_root import last_resolution

        res = last_resolution()
        with self._lock:
            return {
                "bootstrap_ok": self._bootstrap_ok,
                "bootstrap_error": self._bootstrap_error,
                "vault_root": str(res.path) if res else None,
                "vault_root_source": res.source if res else None,
                "vault_legacy_conflict": bool(res.legacy_conflict) if res else False,
                "last_write": dict(self._last_write) if self._last_write else None,
                "last_chain_failure": (
                    dict(self._last_chain_failure)
                    if self._last_chain_failure else None
                ),
                "journal_backlog": self._journal_backlog,
            }


#: Process-wide singleton — import as ``from jarvis.memory.wiki.health import health``.
health = WikiHealth()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/memory/wiki/test_health.py -v`
Expected: 4 passed

- [ ] **Step 5: Wire the recorders**

All wiring is append-only and wrapped so a health failure never propagates:

1. `wiki_ingest.py::execute` — after computing `applied/skipped/failed` (and
   in every early failure return involving the curator), add:

```python
        from jarvis.memory.wiki.health import health

        health.record_write(
            bool(applied),
            pages=[str(p) for p in applied],
            error=None if applied else "no pages written",
            source="tool:wiki-ingest",
        )
```

   (In the `curator is None` branch record
   `record_write(False, pages=[], error="wiki integration not bootstrapped", source="tool:wiki-ingest")`.)

2. `provider_chain.py` — in the all-providers-exhausted branch
   (`:113-123`), next to the existing telemetry call:

```python
        from jarvis.memory.wiki.health import health

        health.record_chain_failure("; ".join(failure_summaries))
```

   (Use whatever per-provider failure list that branch already formats for
   its log/telemetry message.)

3. `server.py` — wrap the `_init_wiki_integration` call site: on success
   `health.record_bootstrap(True)`, in the existing `except` catch
   `health.record_bootstrap(False, error=str(exc))`. Also record when
   `wiki_cfg.enabled` is False: `record_bootstrap(False, error="disabled in config")`.

4. `integration.py` — wherever the journal-pressure check reads
   `journal.backlog_count()` (and after each consolidation drain), add
   `health.record_backlog(journal.backlog_count())`.

- [ ] **Step 6: Add the route + CLI**

In `wiki_routes.py` (house envelope style):

```python
@router.get("/health")
async def wiki_health(request: Request) -> dict[str, Any]:
    """Wiki subsystem health for the Wiki tab status panel (spec A5)."""
    from jarvis.memory.wiki.health import health as _health

    return {"ok": True, "health": _health.snapshot()}
```

Extend `tests/unit/ui/web/test_wiki_routes.py` with a test asserting 200 +
`body["health"]["journal_backlog"] == 0` on a fresh app. Add a `health`
subcommand to `jarvis/cli_ctl/commands/wiki.py` calling `GET /api/wiki/health`
(same client pattern as the file's existing commands), then:

Run: `pytest tests/unit/memory/wiki/test_health.py tests/unit/ui/web/test_wiki_routes.py -v && python scripts/ci/check_cli_coverage.py`
Expected: PASS / exit 0

- [ ] **Step 7: Commit**

```bash
git add jarvis/memory/wiki/health.py jarvis/plugins/tool/wiki_ingest.py jarvis/memory/wiki/provider_chain.py jarvis/ui/web/server.py jarvis/memory/wiki/integration.py jarvis/ui/web/wiki_routes.py jarvis/cli_ctl/commands/wiki.py tests/unit/memory/wiki/test_health.py tests/unit/ui/web/test_wiki_routes.py
git commit -m "feat(wiki): health recorder + GET /api/wiki/health + CLI"
```

---

### Task 6: Ambient journal tuning (spec A4)

**Files:**
- Modify: `jarvis/core/config.py:896` (`consolidate_after_candidates` 8 → 3;
  add `flush_pending_max_age_minutes`)
- Modify: `jarvis/memory/wiki/journal.py` (add `oldest_pending_ms()`)
- Modify: `jarvis/memory/wiki/integration.py` (age-based flush loop)
- Test: `tests/unit/memory/wiki/test_journal_age_flush.py` (new); adjust any
  test pinning the old threshold (grep `consolidate_after_candidates` in
  `tests/`)

**Interfaces:**
- Consumes: `CuratorScheduler.trigger(...)` with the JOURNAL trigger source —
  reuse EXACTLY the call the existing journal-pressure site in
  `integration.py` makes (find it by grepping `consolidate_after_candidates`
  / `TriggerSource.JOURNAL` there).
- Produces: `CandidateJournal.oldest_pending_ms() -> int | None`;
  `SchedulerConfig.flush_pending_max_age_minutes: int = 10` (0 disables).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/memory/wiki/test_journal_age_flush.py
"""Pending ambient candidates must become visible by age, not only count
(spec A4) — a fresh machine with 2 candidates used to sit at zero .md
files forever (threshold was 8)."""
from pathlib import Path

from jarvis.memory.wiki.journal import CandidateJournal


def _mk_journal(tmp_path: Path, now_ms: list[int]) -> CandidateJournal:
    return CandidateJournal(tmp_path / "journal.db", clock=lambda: now_ms[0] / 1000)


def test_oldest_pending_ms_none_when_empty(tmp_path):
    j = _mk_journal(tmp_path, [1_000_000])
    assert j.oldest_pending_ms() is None


def test_oldest_pending_ms_returns_first_pending(tmp_path):
    now = [1_000_000]
    j = _mk_journal(tmp_path, now)
    j.append(source_label="test", turn_hash="h1", fact="Fact one about Joy.",
             kind="fact", subjects=["joy"])
    now[0] += 60_000
    j.append(source_label="test", turn_hash="h2", fact="Fact two about Rome.",
             kind="fact", subjects=["rome"])
    oldest = j.oldest_pending_ms()
    assert oldest is not None
    assert oldest <= 1_000_000


def test_default_consolidation_threshold_is_three():
    from jarvis.core.config import SchedulerConfig

    cfg = SchedulerConfig()
    assert cfg.consolidate_after_candidates == 3
    assert cfg.flush_pending_max_age_minutes == 10
```

(Adapt the `append(...)` keyword names to the real signature in
`journal.py:115` — check it first; the test must construct real rows.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_journal_age_flush.py -v`
Expected: FAIL — `AttributeError: oldest_pending_ms` / threshold assert 8 != 3

- [ ] **Step 3: Implement**

1. `journal.py` — next to `backlog_count()` (`:222`):

```python
    def oldest_pending_ms(self) -> int | None:
        """created_ms of the oldest pending row, or None when none pending."""
        with self._lock:
            conn = self._connection()
            if conn is None:
                return None
            row = conn.execute(
                "SELECT MIN(created_ms) FROM wiki_candidate_journal "
                "WHERE status = 'pending'"
            ).fetchone()
            return int(row[0]) if row and row[0] is not None else None
```

2. `config.py` `SchedulerConfig`: change the default to
   `consolidate_after_candidates: int = 3` and add below it:

```python
    # Age-based flush (spec A4): even below the count threshold, pending
    # candidates older than this become a JOURNAL trigger so a quiet fresh
    # install still produces visible pages. 0 disables the age flush.
    flush_pending_max_age_minutes: int = 10
```

3. `integration.py` — add a background age-check task started where the
   hourly telemetry loop is started (`:555-563` region), same
   fire-and-forget conventions (never raises, cancelled in
   `WikiIntegrationHandle.shutdown`):

```python
    async def _journal_age_flush_loop() -> None:
        """Fire a JOURNAL trigger when the oldest pending candidate exceeds
        the configured age (spec A4). Runs off the voice path (AP-9)."""
        max_age_min = int(
            getattr(sched_cfg, "flush_pending_max_age_minutes", 10)
        )
        if max_age_min <= 0:
            return
        while True:
            await asyncio.sleep(120)
            try:
                oldest = journal.oldest_pending_ms()
                if oldest is None:
                    continue
                age_min = (time.time() * 1000 - oldest) / 60_000
                if age_min >= max_age_min:
                    # Reuse the exact trigger invocation of the existing
                    # journal-pressure site in this module.
                    await _fire_journal_trigger()
            except Exception:  # noqa: BLE001 — never kill the loop
                log.debug("journal age flush check failed", exc_info=True)
```

   Where `_fire_journal_trigger()` is a small helper you extract from the
   existing count-based journal-pressure site so BOTH sites share one code
   path (count trigger and age trigger must not drift). Store the task on
   the handle and cancel it in `shutdown()` exactly like the telemetry task.

4. Grep `tests/` for `consolidate_after_candidates` and update any test
   pinning 8.

- [ ] **Step 4: Run the wiki test suite**

Run: `pytest tests/unit/memory/wiki/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py jarvis/memory/wiki/journal.py jarvis/memory/wiki/integration.py tests/unit/memory/wiki/test_journal_age_flush.py
git commit -m "feat(wiki): consolidate ambient candidates at 3 + age-based flush"
```

---

### Task 7: Deterministic wiki-intent matcher (spec A1)

**Files:**
- Create: `jarvis/memory/wiki/intent.py`
- Test: `tests/unit/memory/wiki/test_wiki_intent.py`

**Interfaces:**
- Consumes: nothing (pure regex module — no imports from `jarvis.brain`,
  keeping the layer direction clean; it gets IMPORTED by the brain layer).
- Produces: `match_wiki_intent(user_text: str) -> WikiIntentMatch | None`;
  `WikiIntentMatch(content: str | None, matched: str)` — `content is None`
  means anaphoric ("write THAT to the wiki" — caller supplies the last
  exchange). Task 8 consumes this.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/memory/wiki/test_wiki_intent.py
"""Deterministic wiki-write intent (spec A1).

The de/en/es utterances below are speech-recognition input vocabulary /
fixtures under test (closed-list categories 3+4).
"""
import pytest

from jarvis.memory.wiki.intent import match_wiki_intent


@pytest.mark.parametrize("text", [
    "Schreib das ins Wiki",                                  # i18n-allow
    "Jarvis, schreib das bitte ins Wiki.",                   # i18n-allow
    "Merk dir das im Wiki",                                  # i18n-allow
    "Notier das im Wiki",                                    # i18n-allow
    "write that to the wiki",
    "save this in my wiki please",
    "guarda eso en la wiki",                                 # i18n-allow
])
def test_anaphoric_commands_match_with_no_inline_content(text):
    m = match_wiki_intent(text)
    assert m is not None
    assert m.content is None


@pytest.mark.parametrize(("text", "expected_fragment"), [
    ("Schreib ins Wiki, dass Joys Geburtstag am 14. August ist",  # i18n-allow
     "geburtstag"),
    ("Merk dir im Wiki: die VPS-IP ist jetzt statisch",           # i18n-allow
     "vps-ip"),
    ("save to the wiki that the deploy key rotated today",
     "deploy key"),
    ("anota en la wiki que el vuelo sale el viernes",             # i18n-allow
     "vuelo"),
])
def test_inline_content_is_extracted(text, expected_fragment):
    m = match_wiki_intent(text)
    assert m is not None
    assert m.content is not None
    assert expected_fragment in m.content.lower()


@pytest.mark.parametrize("text", [
    "Was steht im Wiki über Joy?",           # recall, not write  # i18n-allow
    "Wie funktioniert ein Wiki?",            # general question   # i18n-allow
    "what's in the wiki about the server?",
    "Merk dir das",                          # no wiki object     # i18n-allow
    "remember that for later",
    "Ich habe gestern einen Wiki-Artikel gelesen",  # mention     # i18n-allow
    "open the wiki tab",
])
def test_non_write_utterances_do_not_match(text):
    assert match_wiki_intent(text) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/memory/wiki/test_wiki_intent.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.memory.wiki.intent`

- [ ] **Step 3: Implement the matcher**

```python
# jarvis/memory/wiki/intent.py
"""Deterministic wiki-write intent matcher (spec A1).

Explicit "write this to the wiki" commands must never depend on the
router LLM choosing the ``wiki-ingest`` tool — the weak free default
model on fresh installs almost never does (forensics Bug 12/18). This
matcher runs on the final user transcript in the brain's fast-path
pre-pass (same philosophy as ``jarvis/brain/local_action_gate.py``) and
fires the ingest pipeline model-independently.

Pure regex — no LLM, no IO (AP-9/AP-11). The de/en/es tokens are
speech-recognition input vocabulary (closed-list category 3).
Precision over recall: a false positive writes noise to the vault, a
false negative falls back to the (possibly capable) LLM tool path — so
every pattern REQUIRES an explicit wiki object.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})  # i18n-allow: transliteration table

# Write verbs (normalized, de/en/es).  # i18n-allow: input vocabulary
_VERBS = (
    r"(?:schreib(?:e|st)?|notier(?:e)?|speicher(?:e)?|merk(?:e)?\s+dir|"
    r"trag(?:e)?\s+.{0,20}?ein|halte?\s+.{0,30}?fest|"
    r"write|save|note|add|store|put|record|"
    r"escribe|guarda|anota|apunta|agrega)"
)

# Explicit wiki object (normalized, de/en/es).  # i18n-allow: input vocabulary
_WIKI_OBJ = (
    r"(?:(?:ins|in\s+das|im|in\s+mein(?:em)?|zum)\s+wiki"  # i18n-allow: input vocabulary
    r"|(?:to|in|into)\s+(?:the\s+|my\s+)?wiki"
    r"|(?:en|al)\s+(?:la\s+|el\s+|mi\s+)?wiki)"
)

_PREFIX = r"^(?:hey\s+)?(?:jarvis[,\s]+)?"

# Question openers that mean recall/general questions, never a write command.
_QUESTION_RE = re.compile(
    r"^(?:was|wer|wie|wo|wann|warum|what|who|how|where|when|why|que|qu|quien|"
    r"como|donde|cuando)\b",
)

# Anaphoric objects: the command refers to prior conversation content.
_ANAPHORA = frozenset({
    "das", "es", "dies", "diese", "dieses", "den", "die",   # i18n-allow
    "that", "this", "it", "them",
    "eso", "esto", "lo", "la",                              # i18n-allow
})

_COMMAND_RE = re.compile(
    _PREFIX
    + _VERBS
    + r"\s+(?P<pre>.*?)\s*"
    + _WIKI_OBJ
    + r"\s*[:,]?\s*(?P<post>.*?)\s*[?.!]*$",
    re.IGNORECASE,
)

_FILLER_RE = re.compile(
    r"^(?:bitte|mal|doch|kurz|please|por\s+favor|que|dass|,)+\s*"  # i18n-allow
)


@dataclass(frozen=True, slots=True)
class WikiIntentMatch:
    #: Inline content to ingest; ``None`` = anaphoric — the caller supplies
    #: the last conversation exchange as the source.
    content: str | None
    #: The full matched utterance (normalized) for logging.
    matched: str


def _normalize(text: str) -> str:
    return text.strip().lower().translate(_UMLAUTS)


def _strip_filler(fragment: str) -> str:
    prev = None
    frag = fragment.strip()
    while prev != frag:
        prev = frag
        frag = _FILLER_RE.sub("", frag).strip()
    return frag


def match_wiki_intent(user_text: str) -> WikiIntentMatch | None:
    """Return a match for an explicit wiki-WRITE command, else ``None``."""
    norm = _normalize(user_text)
    if not norm or len(norm) > 600:
        return None
    if _QUESTION_RE.match(norm) or norm.endswith("?"):
        return None
    m = _COMMAND_RE.match(norm)
    if m is None:
        return None
    pre = _strip_filler(m.group("pre") or "")
    post = _strip_filler(m.group("post") or "")
    content = " ".join(part for part in (pre, post) if part).strip()
    words = [w for w in re.split(r"\s+", content) if w]
    if not words or all(w in _ANAPHORA for w in words):
        return WikiIntentMatch(content=None, matched=norm)
    return WikiIntentMatch(content=content, matched=norm)
```

- [ ] **Step 4: Run tests; iterate on the regex until green**

Run: `pytest tests/unit/memory/wiki/test_wiki_intent.py -v`
Expected: PASS (the negative cases are the contract — do NOT loosen them to
make positives pass; extend the pattern instead)

- [ ] **Step 5: Commit**

```bash
git add jarvis/memory/wiki/intent.py tests/unit/memory/wiki/test_wiki_intent.py
git commit -m "feat(wiki): deterministic multilingual wiki-write intent matcher"
```

---

### Task 8: Wiki fast path in BrainManager — confirm after write (spec A1-A3)

**Files:**
- Modify: `jarvis/voice/action_phrases.py` (new phrase keys)
- Modify: `jarvis/brain/manager.py` (fast path + background task + hook)
- Test: `tests/unit/brain/test_wiki_fast_path.py`

**Interfaces:**
- Consumes: `match_wiki_intent` (Task 7); `self._tools.get("wiki-ingest")`
  (router tool, wired in `factory.py:423-431`);
  `self._tool_executor.execute(tool, args, user_utterance=..., trace_id=...)`
  (AP-3); `self._direct_ack_language(user_text)`;
  `render_readback(...)` + `action_phrase(key, lang, **fields)`;
  `AnnouncementRequested(text=..., priority="normal", language=...,
  kind="completion", detail=...)` (pattern:
  `manager.py:5255-5440` `_run_computer_use_background`);
  `self._append_cu_outcome_to_history(user_request=..., outcome_text=...,
  diagnostic=...)` (`manager.py:5442`).
- Produces: `_run_wiki_ingest_fast_path(user_text, *, trace_id) -> str | None`
  hooked into the turn pipeline directly after the local-action fast path
  (`manager.py:6438-6449`), before the navigation fast path.

- [ ] **Step 1: Add the phrase keys (all three languages, CLAUDE.md §1)**

Append to `_PHRASES` in `jarvis/voice/action_phrases.py`:

```python
    # Deterministic wiki-write fast path (spec A1-A3). The saving line is a
    # PROGRESS ack — it must never claim the write already happened; the
    # saved/failed lines are the post-write truth (confirm-after-write).
    "wiki_saving": {
        "de": "Ich schreibe das jetzt ins Wiki.",  # i18n-allow
        "en": "Writing that to the wiki now.",
        "es": "Lo estoy escribiendo en la wiki.",
    },
    "wiki_saved": {
        "de": "Im Wiki gespeichert.",  # i18n-allow
        "en": "Saved to the wiki.",
        "es": "Guardado en la wiki.",
    },
    "wiki_saved_detail": {
        "de": "Im Wiki gespeichert: {detail}.",  # i18n-allow
        "en": "Saved to the wiki: {detail}.",
        "es": "Guardado en la wiki: {detail}.",
    },
    "wiki_save_failed": {
        "de": "Das Speichern im Wiki hat nicht geklappt.",  # i18n-allow
        "en": "Saving to the wiki did not work.",
        "es": "No se pudo guardar en la wiki.",
    },
    "wiki_save_failed_reason": {
        "de": "Das Speichern im Wiki hat nicht geklappt: {reason}",  # i18n-allow
        "en": "Saving to the wiki did not work: {reason}",
        "es": "No se pudo guardar en la wiki: {reason}",
    },
    "wiki_nothing_to_save": {
        "de": "Mir ist nicht klar, was ich ins Wiki schreiben soll — sag es mir bitte noch einmal mit Inhalt.",  # i18n-allow
        "en": "I am not sure what to write to the wiki — please say it again with the content.",
        "es": "No tengo claro qué escribir en la wiki; dímelo otra vez con el contenido.",
    },
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/brain/test_wiki_fast_path.py
"""Explicit wiki commands run model-independently and confirm only after
the write (spec A1-A3). Uses constructor-injected fakes per house style —
build a minimal BrainManager the same way test_routing.py does (copy its
manager fixture) and register a recording fake executor + a fake
wiki-ingest tool."""
import asyncio

import pytest

from jarvis.core.protocols import ToolResult


class FakeWikiIngestTool:
    name = "wiki-ingest"
    risk_tier = "monitor"

    def __init__(self, result: ToolResult, delay_s: float = 0.0) -> None:
        self.result = result
        self.delay_s = delay_s
        self.calls: list[dict] = []


class RecordingExecutor:
    def __init__(self) -> None:
        self.order: list[str] = []

    async def execute(self, tool, args, **kwargs):
        self.order.append("execute:start")
        if getattr(tool, "delay_s", 0):
            await asyncio.sleep(tool.delay_s)
        tool.calls.append(dict(args))
        self.order.append("execute:done")
        return tool.result


async def test_explicit_command_ingests_and_announces_after_write(manager_factory):
    """'Schreib ins Wiki, dass X' → tool called with X; the completion
    announcement fires only AFTER the executor returned success."""  # i18n-allow
    tool = FakeWikiIngestTool(
        ToolResult(success=True, output="Wiki ingest done:\n- applied: 1\nPages touched:\n  - joy.md")
    )
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    reply = await mgr._run_wiki_ingest_fast_path(
        "Schreib ins Wiki, dass Joys Geburtstag am 14. August ist"  # i18n-allow
    )
    assert reply is not None                     # immediate progress ack
    await asyncio.sleep(0.05)                    # let the background task run
    assert tool.calls, "wiki-ingest must be invoked through the executor"
    completed = [e for e in bus.published if type(e).__name__ == "AnnouncementRequested"]
    assert completed, "outcome must be announced (zero silent drops)"
    assert executor.order.index("execute:done") < len(executor.order)


async def test_failure_is_announced_honestly_never_as_success(manager_factory):
    tool = FakeWikiIngestTool(
        ToolResult(success=False, output="", error="wiki integration not bootstrapped")
    )
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    reply = await mgr._run_wiki_ingest_fast_path("write that to the wiki")
    assert reply is not None
    await asyncio.sleep(0.05)
    completed = [e for e in bus.published if type(e).__name__ == "AnnouncementRequested"]
    assert completed
    text = completed[-1].text.lower()
    assert "saved to the wiki." != text          # no bare success phrase
    assert any(k in text for k in ("not work", "nicht geklappt", "no se pudo"))  # i18n-allow


async def test_non_wiki_turn_returns_none(manager_factory):
    tool = FakeWikiIngestTool(ToolResult(success=True, output=""))
    mgr, executor, bus = manager_factory(tools={"wiki-ingest": tool})
    assert await mgr._run_wiki_ingest_fast_path("wie wird das wetter morgen?") is None  # i18n-allow
    assert not tool.calls
```

Build `manager_factory` as a fixture in this file following the minimal
manager construction used by `tests/unit/brain/test_routing.py` (fake bus
that records `published` events, fake executor above, no real providers).

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/brain/test_wiki_fast_path.py -v`
Expected: FAIL — `AttributeError: _run_wiki_ingest_fast_path`

- [ ] **Step 4: Implement in `manager.py`**

Add (near `_run_local_action_fast_path`):

```python
    async def _run_wiki_ingest_fast_path(
        self,
        user_text: str,
        *,
        trace_id: UUID | None = None,
    ) -> str | None:
        """Deterministic explicit wiki-write path (spec A1-A3).

        Explicit "write this to the wiki" commands must not depend on the
        router LLM picking ``wiki-ingest`` (fresh-machine forensics Bug
        12/18). Mirrors the Computer-Use offload: immediate localized
        progress ack, background ingest, completion announcement AFTER the
        write — the success phrase is generated from the tool result, so
        it can never precede (or contradict) the file on disk.
        """
        from jarvis.memory.wiki.intent import match_wiki_intent

        if self._tool_executor is None:
            return None
        match = match_wiki_intent(user_text)
        if match is None:
            return None
        tool = self._tools.get("wiki-ingest")
        if tool is None:
            return None
        lang = self._direct_ack_language(user_text)

        content = match.content
        if content is None:
            content = self._last_exchange_text()
        if not content or len(content.strip()) < 12:
            return await render_readback(
                getattr(self, "_readback_composer", None),
                instruction=(
                    "The user asked to write something to the wiki but no "
                    "content could be determined; ask them to repeat it "
                    "with the content."
                ),
                language=lang,
                canned=lambda: action_phrase("wiki_nothing_to_save", lang),
            )

        tid = trace_id or uuid4()
        task = asyncio.create_task(
            self._run_wiki_ingest_background(
                tool=tool,
                text=content,
                user_text=user_text,
                trace_id=tid,
                lang=lang,
            )
        )
        self._retain_background_task(task)
        return await render_readback(
            getattr(self, "_readback_composer", None),
            instruction=(
                "Briefly acknowledge that you are writing this to the wiki "
                "right now. Do NOT claim it is already saved."
            ),
            language=lang,
            canned=lambda: action_phrase("wiki_saving", lang),
        )
```

For `_retain_background_task`, reuse whatever retention the CU offload uses
at its `asyncio.create_task` site in `_run_local_action_fast_path` (find it;
if it keeps a strong reference set, share it — do not invent a second
mechanism). For `_last_exchange_text`, add a small helper next to
`_append_cu_outcome_to_history` that renders the last user+assistant turn
from the same history structure that method writes to, defensively:

```python
    def _last_exchange_text(self) -> str | None:
        """Last user+assistant exchange as an ingest source ('write THAT')."""
        try:
            items = list(self._history)[-4:]
        except Exception:  # noqa: BLE001 — history shape is provider-owned
            return None
        parts: list[str] = []
        for item in items:
            role = getattr(item, "role", None) or (
                item.get("role") if isinstance(item, dict) else None
            )
            text = getattr(item, "content", None) or (
                item.get("content") if isinstance(item, dict) else None
            )
            if role in ("user", "assistant") and isinstance(text, str) and text.strip():
                parts.append(f"{role}: {text.strip()}")
        return "\n".join(parts[-2:]) or None
```

(Verify the real `_history` item shape while implementing and simplify to
the actual one — the defensive double-read is the fallback, not the goal.)

The background worker mirrors `_run_computer_use_background`
(`manager.py:5255-5440`) exactly in structure — `render_readback` with
`honesty_bound=True` on success, history grounding, then
`AnnouncementRequested`:

```python
    async def _run_wiki_ingest_background(
        self,
        *,
        tool: Any,
        text: str,
        user_text: str,
        trace_id: UUID,
        lang: str,
    ) -> None:
        """Run wiki-ingest off the voice turn and announce the outcome.

        Never raises; ALWAYS announces (zero silent drops), except nothing
        here is user-cancellable so there is no cancel branch.
        """
        out: str
        diag: str | None = None
        try:
            result = await asyncio.wait_for(
                self._tool_executor.execute(
                    tool,
                    {"text": text, "source": "voice:wiki-command"},
                    user_utterance=user_text,
                    trace_id=trace_id,
                ),
                timeout=90.0,
            )
            if result.success:
                pages = ", ".join(
                    line.strip(" -")
                    for line in str(result.output or "").splitlines()
                    if line.strip().startswith("- ") and line.strip().endswith(".md")
                )
                canned_ok = (
                    action_phrase("wiki_saved_detail", lang, detail=pages)
                    if pages else action_phrase("wiki_saved", lang)
                )
                out = await render_readback(
                    getattr(self, "_readback_composer", None),
                    instruction=(
                        "The user's note was just written to their wiki; "
                        "confirm naturally, keeping the page name if given."
                    ),
                    language=lang,
                    canned=lambda: canned_ok,
                    facts={"user_request": user_text, "result": canned_ok},
                    honesty_bound=True,
                    latency_budget_ms=2500,
                )
            else:
                diag = str(getattr(result, "error", "") or "unknown")
                canned_fail = action_phrase("wiki_save_failed", lang)
                out = await render_readback(
                    getattr(self, "_readback_composer", None),
                    instruction=(
                        "Writing the user's note to the wiki failed; tell "
                        "them plainly, keep the reason simple, and mention "
                        "they can check the wiki settings."
                    ),
                    language=lang,
                    canned=lambda: canned_fail,
                    facts={"user_request": user_text, "what_happened": diag},
                    honesty_bound=False,
                    latency_budget_ms=2500,
                )
        except TimeoutError:
            diag = "wiki-ingest timeout after 90s"
            out = await render_readback(
                getattr(self, "_readback_composer", None),
                instruction="Writing the note to the wiki took too long and was stopped.",
                language=lang,
                canned=lambda: action_phrase("wiki_save_failed", lang),
                latency_budget_ms=2000,
            )
        except Exception as exc:  # noqa: BLE001 — background crash must not leak
            log.error("wiki-ingest background task failed: %r", exc, exc_info=True)
            diag = repr(exc)
            out = await render_readback(
                getattr(self, "_readback_composer", None),
                instruction="Something went wrong while writing the note to the wiki.",
                language=lang,
                canned=lambda: action_phrase("wiki_save_failed", lang),
                latency_budget_ms=2000,
            )
        self._append_cu_outcome_to_history(
            user_request=user_text, outcome_text=out, diagnostic=diag,
        )
        try:
            await self._bus.publish(AnnouncementRequested(
                text=out,
                priority="normal",
                language=lang,
                kind="completion",
                detail=diag,
            ))
        except Exception:  # noqa: BLE001
            log.debug("wiki-ingest completion announce failed", exc_info=True)
```

Hook it into the turn pipeline directly after the local-action fast path
block (`manager.py:6438-6449`), same guard:

```python
        if self._skill_turn_match is None:
            wiki_reply = await self._run_wiki_ingest_fast_path(
                user_text, trace_id=turn_trace_id,
            )
            if wiki_reply is not None:
                await self._record_response_side_effects(
                    user_text=user_text,
                    response_text=wiki_reply,
                    use_history=use_history,
                    trace_id=turn_trace_id,
                )
                return wiki_reply
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/brain/test_wiki_fast_path.py tests/unit/brain/test_routing.py tests/unit/speech/test_phrase_language.py -v`
Expected: PASS (`test_phrase_language.py` guards that every new phrase key
carries all supported languages)

- [ ] **Step 6: Commit**

```bash
git add jarvis/voice/action_phrases.py jarvis/brain/manager.py tests/unit/brain/test_wiki_fast_path.py
git commit -m "feat(brain): deterministic wiki-write fast path with confirm-after-write"
```

---

### Task 9: Tool-loop guidance — explicit store requests call wiki-ingest

**Files:**
- Modify: `jarvis/brain/tool_use_loop.py:106-108, 225-228, 644-656` (the
  storing-bias passages)
- Test: extend the module's existing prompt-content test if one exists
  (grep `tests/unit/brain/` for `tool_use_loop`); otherwise add a minimal
  assertion test on the built system-prompt string

**Interfaces:**
- Consumes/Produces: prompt text only — no signature changes.

- [ ] **Step 1: Adjust the guidance**

The passages currently bias the model AGAINST manual storing (they exist so
the ambient bridge is preferred). Keep the ambient preference for
UNPROMPTED storing, but add one explicit carve-out sentence wherever that
bias appears, e.g.:

```
Exception: when the user EXPLICITLY asks to store or write something to
the wiki, call the `wiki-ingest` tool with their content — never claim it
was stored without a successful tool result.
```

This covers typed-chat phrasings the Task 7 matcher deliberately does not
catch (precision-first). The deterministic fast path remains the primary
guarantee for spoken commands.

- [ ] **Step 2: Test + verify no regression**

Add/extend a test asserting the built router system prompt contains the
carve-out sentence (exact substring `"EXPLICITLY asks to store"`), then:

Run: `pytest tests/unit/brain/ -v -k "tool_use_loop or prompt"`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add jarvis/brain/tool_use_loop.py tests/unit/brain/
git commit -m "fix(brain): explicit store requests direct the model to wiki-ingest"
```

---

### Task 10: Wiki health panel — frontend (spec A5, UI)

**Files:**
- Modify: `jarvis/ui/web/frontend/src/lib/wikiApi.ts` (add `fetchWikiHealth`)
- Modify: `jarvis/ui/web/frontend/src/views/WikiView.tsx` (status strip)
- Test: extend `WikiView.test.tsx` (same directory conventions)

**Interfaces:**
- Consumes: `GET /api/wiki/health` (Task 5 contract).
- Produces: a compact status strip at the top of the Wiki tab.

- [ ] **Step 1: API function**

```typescript
export interface WikiHealthSnapshot {
  bootstrap_ok: boolean | null;
  bootstrap_error: string | null;
  vault_root: string | null;
  vault_root_source: string | null;
  vault_legacy_conflict: boolean;
  last_write: {
    ts: number; ok: boolean; pages: string[];
    error: string | null; source: string;
  } | null;
  last_chain_failure: { ts: number; detail: string } | null;
  journal_backlog: number;
}

export async function fetchWikiHealth(): Promise<WikiHealthSnapshot | null> {
  const res = await fetch("/api/wiki/health");
  if (!res.ok) return null;
  const body = await res.json();
  return body.ok ? body.health : null;
}
```

- [ ] **Step 2: Status strip in WikiView**

Poll `fetchWikiHealth` on mount + every 30 s. Render one row: a green/amber/
red dot (green: `bootstrap_ok && last_write?.ok !== false`; amber: pending
backlog > 0 or `vault_legacy_conflict`; red: `bootstrap_ok === false` or
`last_write?.ok === false` or `last_chain_failure`), the vault path, the
last-write summary ("Last write: <page> · ok" / the error text), and the
backlog count when > 0. English source strings via the app's i18n mechanism.
Follow WikiView's existing styling primitives.

- [ ] **Step 3: Tests + build**

Extend `WikiView.test.tsx`: mocked healthy snapshot renders the vault path;
mocked failure snapshot renders the error text (assert on the error string,
not the color).

Run: `cd jarvis/ui/web/frontend && npm run test && npm run build`
Expected: PASS + build succeeds

- [ ] **Step 4: Commit**

```bash
git add jarvis/ui/web/frontend/src/lib/wikiApi.ts jarvis/ui/web/frontend/src/views/WikiView.tsx jarvis/ui/web/frontend/src/views/WikiView.test.tsx
git commit -m "feat(ui): wiki health status strip"
```

---

### Task 11: Fresh-machine anchor test — end to end (spec §7, G1/G6)

**Files:**
- Create: `tests/integration/memory/wiki/test_fresh_machine_explicit_write.py`
- Consult: `tests/integration/memory/wiki/test_curator_concurrent_edit.py`
  (fixture conventions for constructing a REAL curator on a tmp vault),
  `tests/fakes/` (existing fake LLM/provider fakes — reuse before writing new)

**Interfaces:**
- Consumes: everything from Tasks 1-8. This test is the spec's anchor: it
  reproduces the maintainer's fresh-machine scenario and must stay green
  forever.

- [ ] **Step 1: Write the test (it should pass already if Tasks 1-8 landed)**

```python
# tests/integration/memory/wiki/test_fresh_machine_explicit_write.py
"""Fresh-machine anchor (spec §7): empty vault, weakest model (i.e. the
LLM never calls a tool — the deterministic path must not need it), one
fake provider → an explicit wiki command produces a real .md file, and
the confirmation exists only after the write. Failure twin: dead provider
chain → honest failure, no file, no success phrase.

Build the pipeline the way test_curator_concurrent_edit.py does: a REAL
WikiCurator + AtomicWriter on a tmp vault, with the curator LLM backed by
a fake from tests/fakes/ that deterministically proposes one page update
for the given fact (success case) or raises/exhausts (failure twin).
"""
import pytest

from jarvis.memory.wiki.intent import match_wiki_intent
from jarvis.plugins.tool.wiki_ingest import WikiIngestTool


async def test_explicit_command_produces_a_real_file(tmp_vault_curator):
    """tmp_vault_curator: fixture returning (curator, vault_root) with a
    fake proposing LLM — copy the construction from
    test_curator_concurrent_edit.py and swap in the deterministic fake."""
    curator, vault_root = tmp_vault_curator
    utterance = "Schreib ins Wiki, dass Joys Geburtstag am 14. August ist"  # i18n-allow
    m = match_wiki_intent(utterance)
    assert m is not None and m.content is not None

    tool = WikiIngestTool(curator_resolver=lambda: curator)
    result = await tool.execute({"text": m.content, "source": "test"}, ctx=None)

    assert result.success, result.error
    written = list(vault_root.rglob("*.md"))
    assert written, "an explicit wiki command MUST produce a visible page"


async def test_dead_provider_chain_fails_honestly(tmp_vault_dead_curator):
    """tmp_vault_dead_curator: same construction, but the fake provider
    chain is exhausted (every provider raises)."""
    curator, vault_root = tmp_vault_dead_curator
    m = match_wiki_intent("write that to the wiki")
    assert m is not None

    tool = WikiIngestTool(curator_resolver=lambda: curator)
    result = await tool.execute(
        {"text": "The deploy key rotated today.", "source": "test"}, ctx=None,
    )

    assert result.success is False           # honest failure, never a lie
    assert result.error                       # with a stated reason
    assert not list(vault_root.rglob("*.md")), "no file on failure"


async def test_curator_none_reports_not_bootstrapped():
    tool = WikiIngestTool(curator_resolver=lambda: None)
    result = await tool.execute(
        {"text": "Something long enough to ingest."}, ctx=None,
    )
    assert result.success is False
    assert "not bootstrapped" in (result.error or "")
```

Implement the two fixtures in the same file (or a local `conftest.py`)
by copying the real-component construction from
`test_curator_concurrent_edit.py`; reuse an existing fake from
`tests/fakes/` for the proposing LLM if one fits, else add one there
(house rule: fakes, not `unittest.mock`).

- [ ] **Step 2: Run**

Run: `pytest tests/integration/memory/wiki/test_fresh_machine_explicit_write.py -v`
Expected: PASS

- [ ] **Step 3: Full regression + gates**

Run: `pytest tests/unit/ -m "not slow" -q && ruff check jarvis/ && mypy jarvis/`
Expected: PASS / clean (fix anything the sweep surfaces in the files this
plan touched)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/memory/wiki/test_fresh_machine_explicit_write.py tests/fakes/
git commit -m "test(wiki): fresh-machine anchor — explicit write produces a file or fails honestly"
```

---

## Final verification (Definition of Done, spec §3/G6)

- [ ] Fresh-install path: with ONLY the anchor test's fake single provider,
  chat + the explicit wiki command work (Tasks 8 + 11 prove this).
- [ ] Headless Linux: `pytest tests/unit/memory/wiki/ tests/unit/setup/` has
  no Windows-only import at module scope (Obsidian win32 imports stay lazy —
  Task 2 must not hoist them); boot-budget gate stays green (`python
  scripts/ci/check_boot_budget.py` if runnable locally).
- [ ] Honest manual trace on the maintainer machine: say
  "Schreib das ins Wiki" → progress ack → completion announcement → file in
  the vault → health panel shows the write. <!-- i18n-allow: quoted trigger utterance -->
- [ ] Restart the live app via `POST /api/settings/restart-app` before the
  manual trace (editable-install changes need it).
