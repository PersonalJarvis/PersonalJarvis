# Wire the 7 Coming-Soon Marketplace Plugins — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the seven "Coming Soon" teasers (Stripe, Cloudflare, Discord, Google Drive, Gmail, Telegram, Asana) into real, persistent, connectable marketplace plugins.

**Architecture:** Reuse the five existing catalog auth modes. Most providers are catalog-data only; the frontend auto-promotes them out of "Coming Soon". Minimal code: one additive `auth_scheme` field on `pat_paste`, a `resource` pass-through on PKCE-loopback, a Telegram channel-reuse hook, and a Gmail REST tool. Persistence is hardened so a revoked token is marked `needs_reauth` rather than deleted — a plugin never silently disappears.

**Tech Stack:** Python 3.11, Pydantic v2, httpx, FastAPI, keyring (Windows Credential Manager), pytest (asyncio_mode=auto); React/TS frontend (auto-handled).

**Design spec:** `docs/superpowers/specs/2026-06-01-marketplace-coming-soon-plugins-design.md`

**Wave order (each wave ships independently):**
- **Wave 1** — Persistence hardening + Stripe + Cloudflare (zero maintainer setup).
- **Wave 2** — PAT `auth_scheme` + Telegram (channel reuse) + Discord (bot token).
- **Wave 3** — PKCE `resource` param + Asana + Drive + Gmail (maintainer registers OAuth apps).

**Conventions:** new providers use `logo_color: "F4F4F5"` (monochrome, matching GitHub/Vercel/Notion). Tests live in `tests/unit/marketplace/`. After any catalog edit, the lru-cache must be cleared in tests via `load_catalog.cache_clear()` (or `from jarvis.marketplace.catalog_data import clear_cache`). Commit after each task.

---

## WAVE 1 — Persistence hardening + Stripe + Cloudflare

### Task 1: Add `needs_reauth` to the `Tokens` model

**Files:**
- Modify: `jarvis/marketplace/token_store.py` (the `Tokens` dataclass + `to_json`/`from_json`)
- Test: `tests/unit/marketplace/test_token_store.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_token_store.py
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def test_needs_reauth_round_trips_through_json():
    t = Tokens(access="a", refresh="r", needs_reauth=True)
    restored = Tokens.from_json(t.to_json())
    assert restored.needs_reauth is True


def test_needs_reauth_defaults_false_for_legacy_blob():
    # A blob written before the field existed must load as needs_reauth=False.
    legacy = '{"access":"a","refresh":null,"expires_at":null,"extra":{}}'
    assert Tokens.from_json(legacy).needs_reauth is False


def test_store_persists_needs_reauth():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="a", needs_reauth=True))
    loaded = store.load("p")
    assert loaded is not None and loaded.needs_reauth is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_token_store.py -v`
Expected: FAIL — `TypeError: Tokens.__init__() got an unexpected keyword argument 'needs_reauth'`

- [ ] **Step 3: Add the field + serialize it**

In `jarvis/marketplace/token_store.py`, add the field to the dataclass (after `extra`):

```python
@dataclass(frozen=True, slots=True)
class Tokens:
    """A plugin's auth state at a point in time. Immutable by design."""

    access: str
    refresh: str | None = None
    expires_at: datetime | None = None
    extra: dict[str, str] = field(default_factory=dict)
    # Set when the refresh scheduler hit an unrecoverable refresh (revoked /
    # un-healable). The entry is KEPT (never deleted) so the plugin stays
    # visible with a "Reconnect" affordance instead of silently disappearing.
    needs_reauth: bool = False
```

In `to_json`, add `"needs_reauth": self.needs_reauth` to the payload dict. In `from_json`, add `needs_reauth=bool(data.get("needs_reauth", False))` to the `cls(...)` call.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/marketplace/test_token_store.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/token_store.py tests/unit/marketplace/test_token_store.py
git commit -m "feat(marketplace): add needs_reauth flag to Tokens (never-delete groundwork)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Scheduler marks `needs_reauth` instead of deleting

**Files:**
- Modify: `jarvis/marketplace/refresh_scheduler.py:75-83` (the `RuntimeError`/"revoked" branch)
- Test: `tests/unit/marketplace/test_refresh_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_refresh_scheduler.py
import dataclasses
from datetime import UTC, datetime, timedelta

from jarvis.marketplace.refresh_scheduler import REVOKED, refresh_due_tokens
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


class _RevokingHandler:
    plugin_id = "stripe"

    async def refresh(self, current):
        raise RuntimeError("revoked")


async def test_revoked_refresh_marks_needs_reauth_and_keeps_token():
    store = TokenStore(InMemoryBackend())
    near = datetime.now(UTC) + timedelta(seconds=60)  # near expiry -> eligible
    store.save("stripe", Tokens(access="dead", refresh="r", expires_at=near))

    outcomes = await refresh_due_tokens(
        ["stripe"], store, lambda pid: _RevokingHandler()
    )

    assert outcomes["stripe"] == REVOKED
    kept = store.load("stripe")
    assert kept is not None, "revoked token must NOT be deleted"
    assert kept.needs_reauth is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_refresh_scheduler.py::test_revoked_refresh_marks_needs_reauth_and_keeps_token -v`
Expected: FAIL — `store.load("stripe")` returns `None` (current code deletes).

- [ ] **Step 3: Replace `store.delete` with a `needs_reauth` save**

In `refresh_scheduler.py`, change the revoked branch:

```python
        try:
            new_tokens = await handler.refresh(tokens)
        except RuntimeError as exc:
            if "revoked" in str(exc):
                # Do NOT delete — keep the entry and flag it so the UI shows a
                # "Reconnect" prompt. A plugin must never silently disappear
                # (the only user-visible delete path is an explicit DELETE).
                store.save(pid, dataclasses.replace(tokens, needs_reauth=True))
                outcomes[pid] = REVOKED
                log.info("plugin %s refresh revoked — marked needs_reauth", pid)
            else:
                outcomes[pid] = FAILED
                log.warning("plugin %s refresh failed: %s", pid, exc)
            continue
```

Add `import dataclasses` at the top of the module.

On a successful refresh, clear any stale flag — change the success path:

```python
        store.save(pid, dataclasses.replace(new_tokens, needs_reauth=False))
        outcomes[pid] = REFRESHED
```

- [ ] **Step 4: Run the full scheduler suite**

Run: `pytest tests/unit/marketplace/test_refresh_scheduler.py -v`
Expected: PASS (new test passes; pre-existing tests still pass — the SKIPPED/REFRESHED/FAILED labels are unchanged).

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/refresh_scheduler.py tests/unit/marketplace/test_refresh_scheduler.py
git commit -m "fix(marketplace): revoked refresh marks needs_reauth, never deletes the token

A plugin must stay visible until the user disconnects it. The scheduler was
the only non-user delete path; it now keeps the entry and flags it for a
Reconnect prompt instead.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `_plugin_status` surfaces `needs_reauth`

**Files:**
- Modify: `jarvis/ui/web/marketplace_routes.py:51-56` (`_plugin_status`)
- Test: `tests/unit/marketplace/test_plugin_status.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_plugin_status.py
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore
from jarvis.ui.web.marketplace_routes import _plugin_status


def test_status_connected_for_healthy_token():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="a"))
    assert _plugin_status("p", store) == "connected"


def test_status_needs_reauth_when_flagged():
    store = TokenStore(InMemoryBackend())
    store.save("p", Tokens(access="dead", needs_reauth=True))
    assert _plugin_status("p", store) == "needs_reauth"


def test_status_not_connected_when_absent():
    store = TokenStore(InMemoryBackend())
    assert _plugin_status("p", store) == "not_connected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_plugin_status.py -v`
Expected: FAIL on `test_status_needs_reauth_when_flagged` (returns "connected").

- [ ] **Step 3: Branch on the flag**

```python
def _plugin_status(plugin_id: str, store: TokenStore) -> str:
    try:
        tokens = store.load(plugin_id)
    except RuntimeError:
        return "error"
    if tokens is None:
        return "not_connected"
    if tokens.needs_reauth:
        return "needs_reauth"
    return "connected"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/marketplace/test_plugin_status.py -v`
Expected: PASS (3 passed). The frontend already renders a `needs_reauth` status type (`PluginStatus` in `PluginsView.tsx`).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/marketplace_routes.py tests/unit/marketplace/test_plugin_status.py
git commit -m "feat(marketplace): surface needs_reauth status from the plugin list endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Restart-survival regression test

**Files:**
- Test: `tests/unit/marketplace/test_persistence_restart.py` (create)

- [ ] **Step 1: Write the test (this is the user's hard requirement, encoded)**

```python
# tests/unit/marketplace/test_persistence_restart.py
"""The user's invariant: connected plugins survive app close / PC restart and
are NEVER auto-removed — only an explicit user DELETE removes one."""
from datetime import UTC, datetime, timedelta

from jarvis.marketplace.refresh_scheduler import refresh_due_tokens
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


class _InvalidGrantHandler:
    plugin_id = "stripe"

    async def refresh(self, current):
        raise RuntimeError("revoked")  # auth server returned invalid_grant


async def test_dcr_and_pat_plugins_survive_restart_and_a_revoked_refresh():
    backend = InMemoryBackend()  # stands in for Credential Manager

    # --- session 1: connect a DCR plugin (with refresh) + a PAT plugin (none)
    s1 = TokenStore(backend)
    near = datetime.now(UTC) + timedelta(seconds=60)
    s1.save("stripe", Tokens(access="x", refresh="r", expires_at=near,
                             extra={"client_id": "c"}))
    s1.save("github", Tokens(access="ghp_static"))  # PAT, no refresh

    # --- "restart": a brand-new store over the same backend (keyring survives)
    s2 = TokenStore(backend)
    assert s2.load("stripe") is not None
    assert s2.load("github") is not None

    # --- a refresh cycle where the DCR refresh is rejected
    outcomes = await refresh_due_tokens(
        ["stripe", "github"], s2, lambda pid: _InvalidGrantHandler()
    )

    # PAT skipped (no refresh token); DCR revoked-but-kept.
    assert outcomes["github"] == "skipped"
    assert s2.load("github") is not None, "PAT plugin must never be touched"
    assert s2.load("stripe") is not None, "revoked DCR plugin must NOT vanish"
    assert s2.load("stripe").needs_reauth is True
```

- [ ] **Step 2: Run it**

Run: `pytest tests/unit/marketplace/test_persistence_restart.py -v`
Expected: PASS (depends on Tasks 1–2). If it fails, the persistence hardening is incomplete — fix before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/marketplace/test_persistence_restart.py
git commit -m "test(marketplace): regression guard — plugins survive restart + revoked refresh

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Stripe catalog entry (hosted-MCP DCR)

**Files:**
- Modify: `jarvis/marketplace/seed_catalog.json` (append to `plugins`)
- Test: `tests/unit/marketplace/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_catalog_seed.py
from jarvis.marketplace.catalog import HostedMcpOAuthDcrAuth
from jarvis.marketplace.catalog_data import load_catalog


def test_stripe_is_dcr_one_click_with_http_mcp():
    load_catalog.cache_clear()
    spec = load_catalog().by_id("stripe")
    assert spec is not None
    assert spec.display_name == "Stripe"  # must match the COMING_SOON label
    assert isinstance(spec.auth, HostedMcpOAuthDcrAuth)
    assert spec.auth.mcp_url == "https://mcp.stripe.com"
    assert spec.mcp_server["transport"] == "http"
    # DCR: NO static client_id anywhere in the entry.
    assert "client_id" not in spec.auth.model_dump()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py::test_stripe_is_dcr_one_click_with_http_mcp -v`
Expected: FAIL — `spec is None`.

- [ ] **Step 3: Append the Stripe entry to `plugins` in `seed_catalog.json`**

```json
    {
      "id": "stripe",
      "display_name": "Stripe",
      "description": "Payments, customers, invoices and balance — read-first",
      "category": "Developer",
      "logo_slug": "stripe",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "hosted_mcp_oauth_dcr",
        "discovery_url": "https://mcp.stripe.com/.well-known/oauth-protected-resource",
        "mcp_url": "https://mcp.stripe.com",
        "refresh_supported": true,
        "capabilities": ["tools"]
      },
      "mcp_server": {
        "transport": "http",
        "url": "https://mcp.stripe.com",
        "auth_header_template": "Authorization: Bearer ${plugin_stripe_access_token}"
      },
      "post_install_hint_md": "Stripe's hosted MCP server is read-first; grant only the tool access you need on Stripe's consent screen."
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py -v`
Expected: PASS. Also run `pytest tests/unit/marketplace/test_catalog_load.py -v` to confirm the seed still validates.

- [ ] **Step 5: Commit**

```bash
git add jarvis/marketplace/seed_catalog.json tests/unit/marketplace/test_catalog_seed.py
git commit -m "feat(marketplace): Stripe plugin via hosted-MCP DCR (one-click, zero setup)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Cloudflare catalog entry (hosted-MCP DCR)

**Files:**
- Modify: `jarvis/marketplace/seed_catalog.json`
- Test: `tests/unit/marketplace/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_catalog_seed.py
def test_cloudflare_is_dcr_one_click_with_http_mcp():
    load_catalog.cache_clear()
    spec = load_catalog().by_id("cloudflare")
    assert spec is not None
    assert spec.display_name == "Cloudflare"
    assert spec.auth.mode == "hosted_mcp_oauth_dcr"
    assert spec.auth.mcp_url == "https://observability.mcp.cloudflare.com/mcp"
    assert spec.mcp_server["url"] == "https://observability.mcp.cloudflare.com/mcp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py::test_cloudflare_is_dcr_one_click_with_http_mcp -v`
Expected: FAIL — `spec is None`.

- [ ] **Step 3: Append the Cloudflare entry to `plugins`**

```json
    {
      "id": "cloudflare",
      "display_name": "Cloudflare",
      "description": "Workers, observability logs, analytics and Radar insights",
      "category": "Developer",
      "logo_slug": "cloudflare",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "hosted_mcp_oauth_dcr",
        "discovery_url": "https://observability.mcp.cloudflare.com/.well-known/oauth-authorization-server",
        "mcp_url": "https://observability.mcp.cloudflare.com/mcp",
        "refresh_supported": true,
        "capabilities": [
          "https://bindings.mcp.cloudflare.com/mcp",
          "https://radar.mcp.cloudflare.com/mcp",
          "https://graphql.mcp.cloudflare.com/mcp"
        ]
      },
      "mcp_server": {
        "transport": "http",
        "url": "https://observability.mcp.cloudflare.com/mcp",
        "auth_header_template": "Authorization: Bearer ${plugin_cloudflare_access_token}"
      },
      "post_install_hint_md": "Defaults to the read-only observability/analytics server. Pick read scopes on Cloudflare's consent screen."
    }
```

> NOTE: Cloudflare's `discovery_url` points straight at the `oauth-authorization-server` doc (it does not nest behind a protected-resource doc the way Stripe/Notion do). `HostedMcpDcrHandler._discover` (Step 1) expects a protected-resource doc with `authorization_servers`. Verify during execution: fetch both `.well-known` URLs; if the protected-resource doc exists and lists `authorization_servers`, use it as `discovery_url` instead. The research confirmed `https://observability.mcp.cloudflare.com/.well-known/oauth-protected-resource` exists and returns `{"resource":...,"authorization_servers":["https://observability.mcp.cloudflare.com"]}` — prefer that URL as `discovery_url` so the handler's two-step discovery works unchanged.

- [ ] **Step 4: Correct `discovery_url` to the protected-resource doc**

Set `"discovery_url": "https://observability.mcp.cloudflare.com/.well-known/oauth-protected-resource"` (matches the handler's two-step discovery; the auth-server doc is then fetched from `authorization_servers[0]`).

- [ ] **Step 5: Run test to verify it passes + commit**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py tests/unit/marketplace/test_catalog_load.py -v`
Expected: PASS.

```bash
git add jarvis/marketplace/seed_catalog.json tests/unit/marketplace/test_catalog_seed.py
git commit -m "feat(marketplace): Cloudflare plugin via hosted-MCP DCR (observability default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: MCP-bridge wiring assertion for the DCR pair

**Files:**
- Test: `tests/unit/marketplace/test_mcp_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_mcp_bridge.py
from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.mcp_bridge import assemble_claude_mcp_servers
from jarvis.marketplace.token_store import InMemoryBackend, Tokens, TokenStore


def test_connected_stripe_becomes_http_mcp_with_bearer():
    load_catalog.cache_clear()
    store = TokenStore(InMemoryBackend())
    store.save("stripe", Tokens(access="sk_live_abc"))
    servers = assemble_claude_mcp_servers(load_catalog(), store)
    assert servers["stripe"]["type"] == "http"
    assert servers["stripe"]["url"] == "https://mcp.stripe.com"
    assert servers["stripe"]["headers"] == {"Authorization": "Bearer sk_live_abc"}


def test_unconnected_cloudflare_is_absent():
    load_catalog.cache_clear()
    store = TokenStore(InMemoryBackend())
    servers = assemble_claude_mcp_servers(load_catalog(), store)
    assert "cloudflare" not in servers
```

- [ ] **Step 2: Run it**

Run: `pytest tests/unit/marketplace/test_mcp_bridge.py -v`
Expected: PASS (no code change — this verifies the bridge already wires DCR plugins; the `${plugin_<id>_access_token}` placeholder in the catalog is resolved by `mcp_bridge._token_replacements`).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/marketplace/test_mcp_bridge.py
git commit -m "test(marketplace): assert connected DCR plugins wire to http MCP with bearer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend — drop stale "Linear" teaser, rebuild

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/PluginsView.tsx:121-130` (`COMING_SOON`)

- [ ] **Step 1: Trim the array**

The seven new entries auto-drop from "Coming Soon" once their catalog `display_name` matches (`ComingSoonStrip` filters `taken`). Remove the already-shipped `"Linear"`:

```tsx
const COMING_SOON = [
  "Stripe",
  "Cloudflare",
  "Discord",
  "Google Drive",
  "Gmail",
  "Telegram",
  "Asana",
];
```

(Stripe + Cloudflare now have catalog entries and will disappear from the strip; the rest stay listed until their waves land. Linear is gone because it already shipped.)

- [ ] **Step 2: Build the frontend**

Run: `cd jarvis/ui/web/frontend && npm run build`
Expected: build succeeds (output to `jarvis/ui/web/dist`). Per `feedback_verify_ui_visually`, a frontend change needs `npm run build` + app restart to take effect (pywebview holds the RAM bundle).

- [ ] **Step 3: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/PluginsView.tsx jarvis/ui/web/dist
git commit -m "feat(ui): drop shipped Linear from the coming-soon strip

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Wave 1 verification gate

- [ ] Run the marketplace suite: `pytest tests/unit/marketplace/ -v` — all green.
- [ ] Run `pip install -e . --no-deps` if entry-points changed (they did not — no pyproject edit this wave).
- [ ] **Live check (maintainer):** restart the app, open Plugins → Stripe + Cloudflare appear under Developer with a "One-Click" badge; click → browser opens Stripe/Cloudflare consent → authorize → row flips to "Connected". Restart the PC; the row stays "Connected".
- [ ] Stripe + Cloudflare are gone from the "Coming Soon" strip.

---

## WAVE 2 — PAT auth_scheme + Telegram + Discord

### Task 9: Add `auth_scheme` to `PatPasteAuth`

**Files:**
- Modify: `jarvis/marketplace/catalog.py` (`PatPasteAuth`)
- Test: `tests/unit/marketplace/test_catalog_load.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_catalog_load.py
from jarvis.marketplace.catalog import PatPasteAuth


def test_pat_auth_scheme_defaults_to_bearer():
    a = PatPasteAuth(
        mode="pat_paste", token_creation_url="https://x", token_prefix="",
        validation_endpoint="https://x", instruction_md="md",
    )
    assert a.auth_scheme == "bearer"


def test_pat_auth_scheme_accepts_bot_and_telegram_path():
    for scheme in ("bot", "telegram_path"):
        a = PatPasteAuth(
            mode="pat_paste", token_creation_url="https://x", token_prefix="",
            validation_endpoint="https://x", instruction_md="md",
            auth_scheme=scheme,
        )
        assert a.auth_scheme == scheme
```

- [ ] **Step 2: Run it (fails)**

Run: `pytest tests/unit/marketplace/test_catalog_load.py -k auth_scheme -v`
Expected: FAIL — `auth_scheme` is rejected by `extra="forbid"`.

- [ ] **Step 3: Add the field**

In `catalog.py`, add to `PatPasteAuth`:

```python
class PatPasteAuth(_BaseAuth):
    mode: Literal["pat_paste"]
    token_creation_url: str
    token_prefix: str
    validation_endpoint: str
    instruction_md: str
    # How to present the pasted token when validating + wiring downstream.
    #   bearer        -> Authorization: Bearer <token>  (GitHub/Vercel/Supabase)
    #   bot           -> Authorization: Bot <token>      (Discord)
    #   telegram_path -> token spliced into the URL path, no header; body ok==true
    auth_scheme: Literal["bearer", "bot", "telegram_path"] = "bearer"
```

- [ ] **Step 4: Run it (passes) + commit**

Run: `pytest tests/unit/marketplace/test_catalog_load.py -k auth_scheme -v`
Expected: PASS.

```bash
git add jarvis/marketplace/catalog.py tests/unit/marketplace/test_catalog_load.py
git commit -m "feat(marketplace): add auth_scheme to pat_paste (bearer|bot|telegram_path)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: `connect_pat` branches on `auth_scheme`

**Files:**
- Modify: `jarvis/ui/web/marketplace_routes.py:104-146` (`connect_pat`)
- Test: `tests/unit/marketplace/test_connect_pat_schemes.py` (create)

- [ ] **Step 1: Write the failing test (httpx mocked via a transport)**

```python
# tests/unit/marketplace/test_connect_pat_schemes.py
import httpx
import pytest

from jarvis.ui.web import marketplace_routes as mr


def _patch_catalog(monkeypatch, spec):
    class _Cat:
        def by_id(self, _):
            return spec
    monkeypatch.setattr(mr, "load_catalog", lambda: _Cat())


def _capture_transport(captured):
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_bot_scheme_uses_bot_header(monkeypatch):
    from jarvis.marketplace.catalog import PatPasteAuth, PluginSpec
    spec = PluginSpec(
        id="discord", display_name="Discord", description="d",
        category="Communication", logo_slug="discord",
        auth=PatPasteAuth(
            mode="pat_paste", token_creation_url="https://x", token_prefix="",
            validation_endpoint="https://discord.com/api/v10/users/@me",
            instruction_md="md", auth_scheme="bot",
        ),
    )
    _patch_catalog(monkeypatch, spec)
    captured = {}
    monkeypatch.setattr(
        mr, "_validate_token",
        mr._make_validator(_capture_transport(captured)),
    )
    monkeypatch.setattr(mr, "TokenStore",
                        lambda: type("S", (), {"save": lambda *_: None})())
    await mr.connect_pat("discord", mr.PatConnectBody(token="abc.def.ghi"))
    assert captured["auth"] == "Bot abc.def.ghi"
```

> The test calls a small extracted seam `_validate_token` / `_make_validator` so the HTTP layer is injectable. Introduce that seam in Step 3.

- [ ] **Step 2: Run it (fails)**

Run: `pytest tests/unit/marketplace/test_connect_pat_schemes.py -v`
Expected: FAIL — `_make_validator`/`_validate_token` do not exist.

- [ ] **Step 3: Refactor validation into an injectable seam + branch on scheme**

Replace the inline `httpx` block in `connect_pat` with a helper that branches:

```python
def _make_validator(transport: httpx.AsyncBaseTransport | None = None):
    async def _validate(auth, token: str) -> tuple[bool, int]:
        """Returns (ok, status). Branches on auth.auth_scheme."""
        scheme = getattr(auth, "auth_scheme", "bearer")
        headers = {"User-Agent": "Personal-Jarvis/1.0"}
        if scheme == "telegram_path":
            url = auth.validation_endpoint.replace("{token}", token)
        elif scheme == "bot":
            url = auth.validation_endpoint
            headers["Authorization"] = f"Bot {token}"
        else:  # bearer
            url = auth.validation_endpoint
            headers["Authorization"] = f"Bearer {token}"
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as c:
            resp = await c.get(url, headers=headers)
        if resp.status_code != 200:
            return False, resp.status_code
        if scheme == "telegram_path":
            # Telegram returns 200 with {"ok": false} for soft errors.
            try:
                return bool(resp.json().get("ok")), 200
            except ValueError:
                return False, 200
        return True, 200
    return _validate


_validate_token = _make_validator()
```

In `connect_pat`, after the prefix check, replace the httpx block:

```python
    ok, status = await _validate_token(spec.auth, token)
    if not ok:
        raise HTTPException(
            status_code=401,
            detail=f"{spec.display_name} rejected the token (HTTP {status})",
        )
    store = TokenStore()
    store.save(plugin_id, Tokens(access=token))
    return {"ok": True, "plugin_id": plugin_id, "status": "connected"}
```

Keep the existing `token_prefix` guard but make it tolerant of an empty prefix (already true: `if spec.auth.token_prefix and not token.startswith(...)`).

- [ ] **Step 4: Run it (passes) + add bearer + telegram_path cases**

Add two more tests mirroring the bot case: a `bearer` plugin asserts `captured["auth"] == "Bearer <token>"`; a `telegram_path` plugin (validation_endpoint `https://api.telegram.org/bot{token}/getMe`, transport returns `{"ok": true}`) asserts `captured["url"]` contains the token and `captured["auth"] is None`.

Run: `pytest tests/unit/marketplace/test_connect_pat_schemes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/marketplace_routes.py tests/unit/marketplace/test_connect_pat_schemes.py
git commit -m "feat(marketplace): connect_pat honours auth_scheme (bot header, telegram path)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Discord catalog entry (bot token + community stdio MCP)

**Files:**
- Modify: `jarvis/marketplace/seed_catalog.json`
- Test: `tests/unit/marketplace/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_catalog_seed.py
def test_discord_is_bot_pat_with_stdio_mcp():
    load_catalog.cache_clear()
    spec = load_catalog().by_id("discord")
    assert spec is not None
    assert spec.display_name == "Discord"
    assert spec.auth.mode == "pat_paste"
    assert spec.auth.auth_scheme == "bot"
    assert spec.mcp_server["transport"] == "stdio"
    assert "mcp-discord" in spec.mcp_server["install"]
```

- [ ] **Step 2: Run it (fails), then append the entry**

```json
    {
      "id": "discord",
      "display_name": "Discord",
      "description": "Read and send messages in your servers (bot)",
      "category": "Communication",
      "logo_slug": "discord",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "pat_paste",
        "auth_scheme": "bot",
        "token_creation_url": "https://discord.com/developers/applications",
        "token_prefix": "",
        "validation_endpoint": "https://discord.com/api/v10/users/@me",
        "instruction_md": "1. Open https://discord.com/developers/applications and click 'New Application'.\n2. Go to the 'Bot' page, click 'Reset Token', and copy it (shown once).\n3. Under 'Privileged Gateway Intents', enable 'Message Content Intent' so the bot can read message text.\n4. Invite the bot to your server: OAuth2 -> URL Generator -> scope 'bot' + the permissions you want (View Channels, Read Message History, Send Messages), open the link, pick your server, Authorize.\n5. Paste the bot token below."
      },
      "mcp_server": {
        "transport": "stdio",
        "install": ["npx", "-y", "mcp-discord", "--config", "$plugin_discord_access_token"],
        "env_template": {"DISCORD_TOKEN": "$plugin_discord_access_token"}
      },
      "post_install_hint_md": "The bot only sees servers it has been invited to. This MCP server (community: barryyip0625/mcp-discord) needs Node/npx on the host."
    }
```

- [ ] **Step 3: Run + commit**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py -k discord -v && pytest tests/unit/marketplace/test_catalog_load.py -v`
Expected: PASS.

```bash
git add jarvis/marketplace/seed_catalog.json tests/unit/marketplace/test_catalog_seed.py
git commit -m "feat(marketplace): Discord plugin via bot token + community stdio MCP

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Telegram catalog entry + channel-reuse post-connect hook

**Files:**
- Create: `jarvis/marketplace/telegram_connect.py` (the hook)
- Modify: `jarvis/marketplace/seed_catalog.json`, `jarvis/ui/web/marketplace_routes.py` (call hook on telegram connect/disconnect)
- Test: `tests/unit/marketplace/test_telegram_connect.py` (create), `tests/unit/marketplace/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test for the hook**

```python
# tests/unit/marketplace/test_telegram_connect.py
from jarvis.marketplace import telegram_connect as tc


def test_enable_writes_secret_and_flips_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(tc, "set_secret",
                        lambda k, v: calls.__setitem__("secret", (k, v)) or True)
    monkeypatch.setattr(tc, "_set_telegram_enabled",
                        lambda on: calls.__setitem__("enabled", on))
    tc.on_telegram_connected("123:ABC")
    assert calls["secret"] == ("telegram_bot_token", "123:ABC")
    assert calls["enabled"] is True


def test_disable_clears_secret_and_flag(monkeypatch):
    calls = {}
    monkeypatch.setattr(tc, "delete_secret",
                        lambda k: calls.__setitem__("deleted", k))
    monkeypatch.setattr(tc, "_set_telegram_enabled",
                        lambda on: calls.__setitem__("enabled", on))
    tc.on_telegram_disconnected()
    assert calls["deleted"] == "telegram_bot_token"
    assert calls["enabled"] is False
```

- [ ] **Step 2: Run it (fails), then create the hook**

```python
# jarvis/marketplace/telegram_connect.py
"""Bridge a Telegram marketplace connect into the existing TelegramChannel.

"Connecting Telegram" is not an MCP tool — it enables the in-repo bidirectional
channel (jarvis/channels/telegram.py). We mirror the validated bot token into
the canonical `telegram_bot_token` secret and flip [integrations.telegram].enabled
so the channel boots. Disconnecting reverses both.
"""
from __future__ import annotations

import logging

from jarvis.core.config import delete_secret, set_secret

log = logging.getLogger(__name__)

_SECRET_KEY = "telegram_bot_token"


def _set_telegram_enabled(on: bool) -> None:
    from jarvis.core.config_writer import set_value
    set_value("integrations.telegram.enabled", on)


def on_telegram_connected(token: str) -> None:
    if not set_secret(_SECRET_KEY, token):
        raise RuntimeError("could not store telegram_bot_token")
    _set_telegram_enabled(True)
    log.info("telegram connected via marketplace — channel enabled")


def on_telegram_disconnected() -> None:
    delete_secret(_SECRET_KEY)
    _set_telegram_enabled(False)
    log.info("telegram disconnected via marketplace — channel disabled")
```

> EXECUTION NOTE: confirm the real config-writer API. The codebase mutates `jarvis.toml` via `jarvis/core/config_writer.py` (lock + tempfile + BOM-safe). Find the exact setter (e.g. `set_value(dotted_key, value)` or a `ConfigWriter` class) by reading that module, and adapt `_set_telegram_enabled` to it. Do NOT hand-write TOML.

- [ ] **Step 3: Run the hook test (passes), then append the Telegram catalog entry**

```json
    {
      "id": "telegram",
      "display_name": "Telegram",
      "description": "Chat with Jarvis from Telegram (your bot)",
      "category": "Communication",
      "logo_slug": "telegram",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "pat_paste",
        "auth_scheme": "telegram_path",
        "token_creation_url": "https://t.me/BotFather",
        "token_prefix": "",
        "validation_endpoint": "https://api.telegram.org/bot{token}/getMe",
        "instruction_md": "1. Open @BotFather in Telegram (https://t.me/BotFather).\n2. Send /newbot, pick a name and a username ending in 'bot'.\n3. Copy the token BotFather gives you (looks like 123456789:AAH...).\n4. Paste it below, then send /start to your new bot so it can reach you."
      },
      "post_install_hint_md": "This enables the Telegram channel — message your bot to talk to Jarvis. Outbound replies pass through the voice scrubber and your allowlist."
    }
```

(No `mcp_server` — the capability is the channel.)

- [ ] **Step 4: Wire the hook into the route**

In `marketplace_routes.connect_pat`, after `store.save(...)` succeeds, if `plugin_id == "telegram"` call `on_telegram_connected(token)`. In `disconnect`, if `plugin_id == "telegram"` call `on_telegram_disconnected()` after `TokenStore().delete(...)`. Import at top:

```python
from jarvis.marketplace.telegram_connect import (
    on_telegram_connected,
    on_telegram_disconnected,
)
```

Add a route test in `test_telegram_connect.py` asserting `connect_pat("telegram", ...)` calls the hook (monkeypatch `on_telegram_connected`).

- [ ] **Step 5: Run + commit**

Run: `pytest tests/unit/marketplace/test_telegram_connect.py tests/unit/marketplace/test_catalog_seed.py -v`
Expected: PASS.

```bash
git add jarvis/marketplace/telegram_connect.py jarvis/marketplace/seed_catalog.json jarvis/ui/web/marketplace_routes.py tests/unit/marketplace/test_telegram_connect.py tests/unit/marketplace/test_catalog_seed.py
git commit -m "feat(marketplace): Telegram plugin reuses the existing channel (token mirror + enable)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Wave 2 verification gate

- [ ] `pytest tests/unit/marketplace/ -v` — all green.
- [ ] **Live (maintainer):** create a Telegram bot via @BotFather and a Discord bot; paste each token in the Plugins UI; Telegram → message the bot, Jarvis replies; Discord → the worker can read/send in an invited server. Both survive a restart.

---

## WAVE 3 — PKCE `resource` param + Asana + Drive + Gmail

### Task 13: Add `resource` pass-through to PKCE-loopback

**Files:**
- Modify: `jarvis/marketplace/auth/oauth_pkce_loopback.py` (`PkceLoopbackConfig` + `start`/`_exchange`/`refresh`), `jarvis/marketplace/catalog.py` (`OAuthPkceLoopbackAuth`), `jarvis/marketplace/connect_helpers.py` + `jarvis/ui/web/marketplace_routes.py` (thread the field)
- Test: `tests/unit/marketplace/test_pkce_resource.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/marketplace/test_pkce_resource.py
from jarvis.marketplace.auth.oauth_pkce_loopback import (
    PkceLoopbackConfig, PkceLoopbackHandler,
)


def test_resource_param_is_added_to_authorize_url(monkeypatch):
    cfg = PkceLoopbackConfig(
        plugin_id="asana", authorization_url="https://app.asana.com/-/oauth_authorize",
        token_url="https://app.asana.com/-/oauth_token", client_id="cid",
        callback_port=0, scopes=["default"],
        resource="https://mcp.asana.com/v2",
    )
    h = PkceLoopbackHandler(cfg)
    # Build the authorize params the way start() does, without binding a socket:
    params = h._authorize_params(redirect_uri="http://127.0.0.1:5/cb",
                                 state="s", challenge="c")
    assert params["resource"] == "https://mcp.asana.com/v2"
```

- [ ] **Step 2: Run it (fails — no `resource` field, no `_authorize_params`)**

Run: `pytest tests/unit/marketplace/test_pkce_resource.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the field + extract `_authorize_params` + thread `resource`**

In `PkceLoopbackConfig` add `resource: str | None = None`. Extract the param-building in `start()` into a method and add `resource`:

```python
    def _authorize_params(self, *, redirect_uri, state, challenge) -> dict:
        params = {
            "response_type": "code",
            "client_id": self._config.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if self._config.scopes:
            params[self._config.scope_param_name] = ",".join(self._config.scopes)
        if self._config.resource:
            params["resource"] = self._config.resource
        return params
```

Use it in `start()`. In `_exchange` and `refresh`, add `body["resource"] = pending.config.resource` / `self._config.resource` when set.

In `catalog.py`, add `resource: str | None = None` to `OAuthPkceLoopbackAuth`. In `connect_helpers.build_handler_from_catalog` and `marketplace_routes.connect_start`, pass `resource=auth.resource` into `PkceLoopbackConfig(...)`.

- [ ] **Step 4: Run it (passes) + commit**

Run: `pytest tests/unit/marketplace/test_pkce_resource.py tests/unit/marketplace/test_connect_helpers.py -v`
Expected: PASS.

```bash
git add jarvis/marketplace/auth/oauth_pkce_loopback.py jarvis/marketplace/catalog.py jarvis/marketplace/connect_helpers.py jarvis/ui/web/marketplace_routes.py tests/unit/marketplace/test_pkce_resource.py
git commit -m "feat(marketplace): PKCE-loopback passes an OAuth resource param (Asana V2 MCP)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Asana catalog entry (PKCE loopback, placeholder client_id)

**Files:**
- Modify: `jarvis/marketplace/seed_catalog.json`
- Test: `tests/unit/marketplace/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_catalog_seed.py
def test_asana_is_pkce_loopback_with_resource_and_http_mcp():
    load_catalog.cache_clear()
    spec = load_catalog().by_id("asana")
    assert spec is not None
    assert spec.display_name == "Asana"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert spec.auth.resource == "https://mcp.asana.com/v2"
    assert spec.mcp_server["url"] == "https://mcp.asana.com/v2/mcp"
```

- [ ] **Step 2: Run (fails), append the entry**

```json
    {
      "id": "asana",
      "display_name": "Asana",
      "description": "Tasks, projects and workspaces",
      "category": "Productivity",
      "logo_slug": "asana",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "oauth_pkce_loopback",
        "authorization_url": "https://app.asana.com/-/oauth_authorize",
        "token_url": "https://app.asana.com/-/oauth_token",
        "revocation_url": "https://app.asana.com/-/oauth_revoke",
        "client_id": "REPLACE_WITH_JARVIS_ASANA_CLIENT_ID",
        "callback_port": 3119,
        "scopes": ["default"],
        "refresh_supported": true,
        "resource": "https://mcp.asana.com/v2"
      },
      "mcp_server": {
        "transport": "http",
        "url": "https://mcp.asana.com/v2/mcp",
        "auth_header_template": "Authorization: Bearer ${plugin_asana_access_token}"
      },
      "post_install_hint_md": "Setup: register an OAuth app at https://app.asana.com/0/my-apps, add a redirect URI of http://127.0.0.1:3119/oauth/callback, and put its Client ID into data/plugin_catalog.json. If Asana rejects the 127.0.0.1 redirect, switch this plugin to pat_paste (token at app.asana.com/0/my-apps, validate https://app.asana.com/api/1.0/users/me)."
    }
```

- [ ] **Step 3: Run + commit**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py -k asana -v && pytest tests/unit/marketplace/test_catalog_load.py -v`

```bash
git add jarvis/marketplace/seed_catalog.json tests/unit/marketplace/test_catalog_seed.py
git commit -m "feat(marketplace): Asana plugin via PKCE loopback + V2 hosted MCP (resource param)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> EXECUTION NOTE — Asana loopback risk: Asana docs only list `https`/`oob` redirects. Before the live test, register the app and confirm `http://127.0.0.1:3119/oauth/callback` is accepted. If not, flip `auth` to `pat_paste` (`token_prefix: ""`, `validation_endpoint: https://app.asana.com/api/1.0/users/me`, default `bearer` scheme) and wire `mcp_server` to a community Asana stdio server (the hosted V2 server rejects PATs).

---

### Task 15: Google Cloud client setup doc (maintainer click-path)

**Files:**
- Create: `docs/marketplace/google-oauth-setup.md`

- [ ] **Step 1: Write the maintainer setup doc**

Document, end to end: create a GCP project; enable the Gmail API + Google Drive API; configure the OAuth consent screen (External; add the maintainer as a Test User); create an OAuth client of type **Desktop app**; copy the `client_id`; that ONE client covers both Gmail and Drive. Include the publish-to-Production + restricted-scope-verification (CASA) path for permanent Gmail read access, and note that `drive.file` needs no verification. Cite the spec's §9.

- [ ] **Step 2: Commit**

```bash
git add docs/marketplace/google-oauth-setup.md
git commit -m "docs(marketplace): Google Cloud OAuth client setup for Gmail + Drive plugins

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Google Drive catalog entry (PKCE loopback, drive.file)

**Files:**
- Modify: `jarvis/marketplace/seed_catalog.json`
- Test: `tests/unit/marketplace/test_catalog_seed.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/unit/marketplace/test_catalog_seed.py
def test_google_drive_uses_drive_file_scope():
    load_catalog.cache_clear()
    spec = load_catalog().by_id("google_drive")
    assert spec is not None
    assert spec.display_name == "Google Drive"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert "https://www.googleapis.com/auth/drive.file" in spec.auth.scopes
```

- [ ] **Step 2: Run (fails), append the entry**

```json
    {
      "id": "google_drive",
      "display_name": "Google Drive",
      "description": "Files Jarvis creates or you share with it (drive.file)",
      "category": "Productivity",
      "logo_slug": "googledrive",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "oauth_pkce_loopback",
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revocation_url": "https://oauth2.googleapis.com/revoke",
        "client_id": "REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID",
        "callback_port": 3120,
        "scopes": ["https://www.googleapis.com/auth/drive.file"],
        "user_scopes_only": false,
        "refresh_supported": true
      },
      "mcp_server": {
        "transport": "http",
        "url": "https://drivemcp.googleapis.com/mcp/v1",
        "auth_header_template": "Authorization: Bearer ${plugin_google_drive_access_token}"
      },
      "post_install_hint_md": "Setup: see docs/marketplace/google-oauth-setup.md. Uses the non-sensitive drive.file scope (no Google verification, stays connected permanently). The Drive MCP server is Google's official endpoint (Developer Preview)."
    }
```

> NOTE: Google's authorize endpoint needs `access_type=offline` + `prompt=consent` to return a refresh token. The DCR handler already sends `prompt=consent`; the PKCE-loopback handler does not. During execution, extend `_authorize_params` to also emit `access_type=offline` + `prompt=consent` when `refresh_supported`/a new `offline_access` catalog flag is set (add an `offline_access: bool = False` field to `OAuthPkceLoopbackAuth`, set true for the Google entries). Add a unit test asserting both params appear. This keeps Slack/Asana unaffected.

- [ ] **Step 3: Implement the `offline_access` flag (TDD)**

Add `offline_access: bool = False` to `OAuthPkceLoopbackAuth` and `PkceLoopbackConfig`; in `_authorize_params`, when set, add `"access_type": "offline"` and `"prompt": "consent"`. Test in `test_pkce_resource.py`. Set `"offline_access": true` on the Drive entry.

- [ ] **Step 4: Run + commit**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py -k drive tests/unit/marketplace/test_pkce_resource.py -v`

```bash
git add jarvis/marketplace/seed_catalog.json jarvis/marketplace/catalog.py jarvis/marketplace/auth/oauth_pkce_loopback.py tests/unit/marketplace/test_catalog_seed.py tests/unit/marketplace/test_pkce_resource.py
git commit -m "feat(marketplace): Google Drive plugin via PKCE loopback (drive.file, offline access)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Gmail catalog entry + in-repo Gmail REST tool

**Files:**
- Create: `jarvis/plugins/tool/gmail_rest.py` (the tool), entry in `pyproject.toml` `[project.entry-points."jarvis.tool"]`
- Modify: `jarvis/marketplace/seed_catalog.json`
- Test: `tests/unit/marketplace/test_catalog_seed.py`, `tests/unit/plugins/tool/test_gmail_rest.py` (create)

- [ ] **Step 1: Write the failing catalog test**

```python
# add to tests/unit/marketplace/test_catalog_seed.py
def test_gmail_pkce_loopback_read_and_send_scopes():
    load_catalog.cache_clear()
    spec = load_catalog().by_id("gmail")
    assert spec is not None
    assert spec.display_name == "Gmail"
    assert spec.auth.mode == "oauth_pkce_loopback"
    assert "https://www.googleapis.com/auth/gmail.readonly" in spec.auth.scopes
    assert "https://www.googleapis.com/auth/gmail.send" in spec.auth.scopes
```

- [ ] **Step 2: Append the Gmail entry**

```json
    {
      "id": "gmail",
      "display_name": "Gmail",
      "description": "Read and send mail from your inbox",
      "category": "Communication",
      "logo_slug": "gmail",
      "logo_color": "F4F4F5",
      "featured": false,
      "auth": {
        "mode": "oauth_pkce_loopback",
        "authorization_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revocation_url": "https://oauth2.googleapis.com/revoke",
        "client_id": "REPLACE_WITH_JARVIS_GOOGLE_CLIENT_ID",
        "callback_port": 3121,
        "scopes": [
          "https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.send"
        ],
        "user_scopes_only": false,
        "refresh_supported": true,
        "offline_access": true
      },
      "post_install_hint_md": "Setup: see docs/marketplace/google-oauth-setup.md (shares the Google client with Drive). In Google 'Testing' status the connection expires every 7 days; publish the app + complete Google's restricted-scope verification to make it permanent."
    }
```

(No `mcp_server` — Gmail is wired by the in-repo REST tool, which reads the keyring access token. This keeps it Node-free and under the marketplace token model.)

- [ ] **Step 3: Write the failing tool test**

```python
# tests/unit/plugins/tool/test_gmail_rest.py
import httpx
import pytest

from jarvis.plugins.tool.gmail_rest import GmailRestTool


@pytest.mark.asyncio
async def test_list_messages_uses_bearer_from_store():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer at_123"
        return httpx.Response(200, json={"messages": [{"id": "m1"}]})

    tool = GmailRestTool(
        access_token_provider=lambda: "at_123",
        transport=httpx.MockTransport(handler),
    )
    out = await tool.list_messages(max_results=1)
    assert out["messages"][0]["id"] == "m1"
```

- [ ] **Step 4: Implement the tool (minimal: list + get + send)**

Create `jarvis/plugins/tool/gmail_rest.py` with a `GmailRestTool` that calls `https://gmail.googleapis.com/gmail/v1/users/me/...` with `Authorization: Bearer <token>` from an injected `access_token_provider` (in production: `lambda: TokenStore().load("gmail").access`). Structural-compatible with the tool protocol (see a sibling in `jarvis/plugins/tool/`). Register the entry-point in `pyproject.toml` and run `pip install -e . --no-deps`.

> EXECUTION NOTE: read an existing `jarvis/plugins/tool/*.py` first to match the exact tool protocol (name, description, risk tier, `execute`/streaming signature) and the `ROUTER_TOOLS` policy. The Gmail tool is a worker-tier action tool, NOT a router tool — do not add it to `ROUTER_TOOLS` (AP-5).

- [ ] **Step 5: Run + commit**

Run: `pytest tests/unit/marketplace/test_catalog_seed.py -k gmail tests/unit/plugins/tool/test_gmail_rest.py -v`

```bash
git add jarvis/plugins/tool/gmail_rest.py jarvis/marketplace/seed_catalog.json pyproject.toml tests/unit/marketplace/test_catalog_seed.py tests/unit/plugins/tool/test_gmail_rest.py
git commit -m "feat(marketplace): Gmail plugin via PKCE loopback + in-repo REST tool (Node-free)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Wave 3 verification gate

- [ ] `pytest tests/unit/marketplace/ tests/unit/plugins/tool/test_gmail_rest.py -v` — all green.
- [ ] **Maintainer setup:** register the Google Cloud Desktop client + Asana app per the docs; put the client_ids into `data/plugin_catalog.json` (overrides the seed). Restart.
- [ ] **Live:** connect Asana, Drive, Gmail via browser login; all three flip to "Connected". Drive + Asana survive restart permanently. Gmail survives until the 7-day testing window (permanent once Google verification completes).

---

## Final self-review checklist (run before declaring done)

- [ ] `pytest tests/unit/marketplace/ -v` — full marketplace suite green.
- [ ] `ruff check jarvis/marketplace/ jarvis/ui/web/marketplace_routes.py jarvis/plugins/tool/gmail_rest.py`
- [ ] All seven providers appear in the Plugins UI; none remain in "Coming Soon".
- [ ] The restart-survival test (Task 4) passes — the user's core invariant.
- [ ] No `spawn-*` tool entered `ROUTER_TOOLS`; no secret in code/`jarvis.toml`; subprocess (if any new) uses `NO_WINDOW_CREATIONFLAGS`.
- [ ] Update `docs/BUGS.md` only if a new bug class surfaced; otherwise no change.
