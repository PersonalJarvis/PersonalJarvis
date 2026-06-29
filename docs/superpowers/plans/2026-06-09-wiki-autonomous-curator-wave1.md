# Wiki Autonomous Curator — Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the wiki from a desktop-activity logger into a clean, conversation-fed memory with a user-selectable dedicated model — by shipping the dedicated-model settings card plus the full quality-fix set (kill the window-focus feed, stop mid-sentence truncation, refuse dangling links, block secrets, fix the voice-path vault bug, build the FTS index at boot, and clean up the existing junk). This is the groundwork the Wave-2 two-stage curator builds on.

**Architecture:** Wave 1 stops the bleeding using seams that already exist — the unused `[memory.wiki.curator].provider/.model` fields resolved through the single `_resolve_provider_and_model` hook, the existing provider-switch settings pattern (`provider_routes.py`/`settings_routes.py`), the `AtomicWriter` write path, and the existing awareness/curator wiring. No new base dependency is added; every change stays off the voice critical path (AP-9) and writes config only via `config_writer` (AP-7) and vault pages only via `AtomicWriter` (AP-3).

**Tech Stack:** Python 3.11 (FastAPI, pydantic, `config_writer` over tomlkit, sqlite/FTS5), React + TypeScript + vitest (frontend), pytest (`asyncio_mode=auto`) with fakes in `tests/fakes/` (no `unittest.mock` for components). Windows/macOS/Linux + headless `python:3.11-slim` VPS must all boot.

**Source spec:** `docs/superpowers/specs/2026-06-09-wiki-autonomous-curator-design.md` (§8 Wave-1). Authored from a 15-agent deep dive (`wf_5bc187bb-27d`) + a 10-author plan workflow (`wf_95cd856a-ba9`) with a consistency pass.

---

## Cross-task corrections (BINDING — apply these over the per-task blocks below)

The ten task blocks were authored in parallel against the real code; each is individually grounded, but the consistency pass found six cross-task issues. **These corrections override the per-task blocks where they conflict. Read them before executing any task.**

1. **`/api/settings/wiki-provider` response shape is `available: [{ "provider": string, "models": string[] }]` (objects, not strings).** The backend block (Task 2) is canonical. **Task 3 (frontend) must be adjusted to the object shape:** `WikiProviderState.available` is typed `{ provider: string; models: string[] }[]`; the provider `<select>` maps `data.available.map(p => <option value={p.provider}>{p.provider}</option>)`; the model field becomes a `<select>` populated from the chosen provider's `models[]` plus an empty **"Same as brain (cheap default)"** option; and the vitest initial fixture uses `{ available: [{provider:"gemini", models:[...]}, ...] }`. Empty `provider` ("follow `brain.primary`") and empty `model` ("provider's cheap default") are valid, intended states (ack-brain `follow_brain` pattern) — surface them as the "Same as brain" option, never coerce them away.

2. **Exactly ONE block edits `DEFAULT_COUNTERS` in `jarvis/memory/wiki/telemetry.py` (lines ~47-57): Task 4 (the D2-retire block).** It registers ALL four new Wave-1 counters in a single hunk: `session_rollups_wiki_write_disabled`, `wiki_links_refused_dangling`, `wiki_writes_blocked_pii`, `wiki_writes_blocked_truncated`. **Tasks 5 (truncation), 7 (create-or-refuse), and 10 (cleanup) must NOT edit `DEFAULT_COUNTERS`** — they only call `telemetry.inc(<name>)`, which auto-registers. Strike any `DEFAULT_COUNTERS` edit from those blocks; keep their `telemetry.inc()` calls. (Task 6/PII's `telemetry.inc('wiki_writes_blocked_pii')` stays; its counter is already registered by Task 4, so its "lazy auto-register" gotcha is moot — the counter is always visible in `GET /api/wiki/telemetry`.)

3. **Task execution order is fixed (dependency-driven):** 1 cheap-model resolver → 2 backend route → 3 frontend card → 4 D2-retire (owns the consolidated `DEFAULT_COUNTERS` hunk) → 5 truncation guard → 7 create-or-refuse → 6 PII validator → 8 vault_root fix → 9 boot-FTS (owns the `cli.py` `DEFAULT_VAULT` fix) → 10 cleanup (LAST). **Task 9 MUST land before Task 10:** the cleanup CLI's `--vault` default is `DEFAULT_VAULT`; until Task 9 changes it from `data/workspace` to `wiki/obsidian-vault`, `python -m jarvis.memory.wiki.cli cleanup` would silently scan the wrong (legacy) tree. Both edit `cli.py` at non-overlapping anchors (Task 9 = `DEFAULT_VAULT` line; Task 10 = a new `_run_cleanup` + subparser + dispatch branch) — apply in order and they merge cleanly. (The tasks below are printed in this exact order and renumbered 1–10.)

4. **Task 4 (D2) `integration.py` anchors — use the real structure, not the block's cited line numbers.** The only subscription that drives the redundant re-ingest second pass is guarded by `if config.subscribe_idle:` at `integration.py:294` (which wraps the `_on_idle_entered` def and the `bus.subscribe(IdleEntered, _on_idle_entered)` at line 314). **Gate THAT block on `and wiki_write_enabled`** (the new `SessionRollupConfig.wiki_write_enabled`, default `False`). Match on the literal strings `if config.subscribe_idle:` and `bus.subscribe(IdleEntered, _on_idle_entered)` — NOT line numbers — because `config.py`/`integration.py` are dirty from parallel sessions. The awareness L1/L2 system and `worker.start()` lifecycle stay untouched; only the durable wiki PAGE WRITE + the re-ingest pass are disabled.

5. **Task 1 (cheap-model) gotcha correction:** its Step 5 REPLACES the existing resolver tests — the gotcha claiming "two existing tests stay green by coincidence" is wrong and must be ignored. The existing resolver tests live at `tests/unit/memory/wiki/test_curator_llm.py` lines ~610-648 (three functions: `fallbacks`, `explicit_overrides`, `returns_none_model_when_unknown`); `explicit_overrides` asserts `claude-opus-4-7` (an explicit override that must still win). Treat the existing tests as REPLACED by the new cases.

6. **Shared regex (low priority, do not block on it):** Task 7 (create-or-refuse) and Task 10 (cleanup) each introduce a local `_WIKILINK_RE` duplicating `session_links._WIKILINK_RE` (`session_links.py:39`). Preferred: export `_WIKILINK_RE` from `session_links.py` (`__all__`) and import it in both. If left duplicated, all copies MUST stay byte-identical to `re.compile(r"(?<!\\)\[\[([^\[\]\n]+)\]\]")`.

**Coverage note (honest scope):** All nine §8 Wave-1 items are covered. The boot-FTS task (Task 9) auto-indexes at BOOT when the FTS table is empty; it does NOT add a lazy first-*search* index build — acceptable for Wave 1 (boot covers the restored-vault case), but the plan does not claim first-search coverage. Wave 2 (two-stage conversation curator: extractor + journal + body-aware ADD/UPDATE/NOOP/INVALIDATE judge + living profile + scheduler wiring) and Wave 3 (optional embeddings) are separate plans, written after Wave 1 lands.

**Worktree note:** Execute in a clean worktree per `pwsh scripts/preflight.ps1` (BUG-006/014). The working tree is dirty from parallel sessions; every task's Commit step stages ONLY its named files (never `git add -A`).

---

### Task 1: Wiki curator resolves a cheap dedicated model by default

The wiki curator currently falls back, when `[memory.wiki.curator].model` is empty, to `brain.providers[provider].model` — i.e. the user's full **frontier** chat model (e.g. `claude-opus-4-8`, `gemini-3.1-pro-preview`). That silently bills the expensive deep model for every background ingest. This task changes the empty-`model` fallback to pick the **cheap/fast router-tier** model for the resolved provider (mirroring the ack-brain `follow_brain` + cheap-model pattern), while keeping an explicit `model` override winning and degrading gracefully for unknown providers.

**Files:**
- **Modify:** `jarvis/memory/wiki/curator_llm.py` — `_resolve_provider_and_model` (current lines 63-81) + the module docstring bullet (lines 7-8); add a `_CHEAP_MODEL_FALLBACK` constant near the other module constants (after line 60).
- **Test:** `tests/unit/memory/wiki/test_curator_llm.py` — extend the existing `_resolve_provider_and_model` smoke tests (current lines 605-647) and add three new cases.

---

- [ ] **Step 1: Confirm the importable cheap-model helper exists.**
  Verify `get_tier_default_model` is a module-level function in `jarvis/brain/manager.py` (it is, at line 250) and returns the router-tier model for a provider. Run:
  ```
  python -c "from jarvis.brain.manager import get_tier_default_model; print(get_tier_default_model('router','gemini'), '|', get_tier_default_model('router','claude-api'), '|', get_tier_default_model('router','grok'))"
  ```
  Expected output:
  ```
  gemini-3-flash-preview | claude-haiku-4-5-20251001 | grok-4.3
  ```
  If the names differ, stop and reconcile before editing (a parallel session is touching `manager.py`).

- [ ] **Step 2: Add the per-provider cheap-model fallback constant.**
  In `jarvis/memory/wiki/curator_llm.py`, immediately after the existing `_MIN_SOURCE_CHARS` constant block (current line 60), add a self-contained fallback map so resolution still works even if `jarvis.brain.manager` cannot be imported on a minimal VPS. Insert:
  ```python
  # Cheap/fast model per provider for the curator's default path. This is
  # the long-term-memory tier: background ingest must NOT bill the user's
  # frontier chat model. We mirror the router-tier ("fast") defaults from
  # jarvis.brain.manager.TIER_DEFAULTS_BY_PROVIDER["router"]; the live
  # values are read from there at resolve time, this map is only the
  # last-resort fallback when that import is unavailable.
  _CHEAP_MODEL_FALLBACK: dict[str, str] = {
      "claude-api": "claude-haiku-4-5-20251001",
      "gemini": "gemini-3-flash-preview",
      "openai": "gpt-5.5",
      "codex": "gpt-5.5",
      "grok": "grok-4.3",
      "deepseek": "deepseek-chat",
      "openrouter": "anthropic/claude-haiku-4.5",
      "mistral": "mistral-small-3.1",
  }
  ```

- [ ] **Step 3: Rewrite `_resolve_provider_and_model` to prefer the cheap model.**
  Replace the current function body (lines 63-81) so that an empty `model` resolves to the cheap router-tier model for the resolved provider, an explicit `model` wins, and an unknown provider degrades to `None` (registry picks its own default). Replace:
  ```python
  def _resolve_provider_and_model(
      cfg: WikiCuratorConfig, root: JarvisConfig,
  ) -> tuple[str, str | None]:
      """Apply the documented two-step fallback to (provider, model).

      Returns ``(provider_name, model_or_none)``. ``model_or_none`` is
      ``None`` when the registry should pick the provider's own default
      (matches ``BrainProviderRegistry.instantiate(name, model=None)``).
      """

      provider = cfg.provider.strip() or root.brain.primary
      model = cfg.model.strip()

      if not model:
          provider_cfg = root.brain.providers.get(provider)
          if provider_cfg is not None and provider_cfg.model:
              model = provider_cfg.model

      return provider, (model or None)
  ```
  with:
  ```python
  def _cheap_model_for(provider: str) -> str | None:
      """Cheap/fast model for the curator's default path.

      Prefers the live router-tier default from ``jarvis.brain.manager``
      (single source of truth); falls back to the local ``_CHEAP_MODEL_FALLBACK``
      map if that module cannot be imported (minimal VPS / partial install).
      Returns ``None`` for an unknown provider so the registry picks its own
      default.
      """

      try:
          from jarvis.brain.manager import get_tier_default_model

          live = get_tier_default_model("router", provider)
          if live:
              return live
      except Exception:  # noqa: BLE001
          pass
      return _CHEAP_MODEL_FALLBACK.get(provider)


  def _resolve_provider_and_model(
      cfg: WikiCuratorConfig, root: JarvisConfig,
  ) -> tuple[str, str | None]:
      """Resolve (provider, model) for the curator LLM.

      Provider: ``cfg.provider`` if set, else ``brain.primary``.

      Model precedence (cheap-by-default — long-term memory must not bill
      the user's frontier chat model):

      1. An explicit ``cfg.model`` always wins.
      2. Otherwise the resolved provider's CHEAP/FAST router-tier model
         (``jarvis.brain.manager.get_tier_default_model("router", provider)``,
         mirrored by ``_CHEAP_MODEL_FALLBACK``).
      3. Otherwise ``None`` — the registry instantiates its own default
         (matches ``BrainProviderRegistry.instantiate(name, model=None)``).

      Note: ``brain.providers[provider].model`` (the user's full frontier
      chat model) is intentionally NOT used here — that is the expensive
      path this resolver exists to avoid.
      """

      provider = cfg.provider.strip() or root.brain.primary
      model = cfg.model.strip()

      if not model:
          model = _cheap_model_for(provider) or ""

      return provider, (model or None)
  ```

- [ ] **Step 4: Update the module docstring to describe the cheap-default contract.**
  In the same file, replace the two-step-fallback bullets in the module docstring (current lines 7-8):
  ```python
  1. ``provider`` empty → use ``brain.primary``.
  2. ``model`` empty → use ``brain.providers[<resolved-provider>].model``.
  ```
  with:
  ```python
  1. ``provider`` empty → use ``brain.primary``.
  2. ``model`` empty → use the resolved provider's CHEAP/FAST router-tier
     model (``get_tier_default_model("router", provider)``), NOT the
     provider's full frontier chat model. Long-term-memory ingest must not
     bill the user's deep brain. An explicit ``model`` override still wins.
  ```

- [ ] **Step 5: Update the existing resolver smoke tests + add three new cases.**
  In `tests/unit/memory/wiki/test_curator_llm.py`, replace the existing block (current lines 610-647) — the three `test_resolve_provider_and_model_*` functions — with:
  ```python
  def test_resolve_provider_and_model_empty_uses_primary_and_cheap_model() -> None:
      """Empty provider + empty model => brain.primary + its CHEAP router model."""

      from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

      # brain.primary = gemini; the curator must pick the cheap router-tier
      # model, NOT a frontier chat model.
      cfg = _make_config(
          primary="gemini",
          providers={
              # Deliberately set the provider's chat model to a FRONTIER model
              # to prove the resolver does NOT fall back to it.
              "gemini": BrainProviderConfig(model="gemini-3.1-pro-preview"),
          },
      )
      provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
      assert provider == "gemini"
      assert model == "gemini-3-flash-preview"  # cheap router-tier, not the frontier chat model

  def test_resolve_provider_and_model_cheap_for_claude_primary() -> None:
      """brain.primary = claude-api => the cheap Haiku model, not Opus."""

      from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

      cfg = _make_config(
          primary="claude-api",
          providers={
              "claude-api": BrainProviderConfig(model="claude-opus-4-8"),
          },
      )
      provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
      assert provider == "claude-api"
      assert model == "claude-haiku-4-5-20251001"

  def test_resolve_provider_and_model_explicit_overrides() -> None:
      """A non-empty model field beats the cheap default."""

      from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

      cfg = _make_config(
          primary="gemini", curator_provider="claude-api", curator_model="claude-opus-4-8",
      )
      provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
      assert provider == "claude-api"
      assert model == "claude-opus-4-8"

  def test_resolve_provider_and_model_explicit_provider_empty_model() -> None:
      """Explicit provider, empty model => that provider's cheap router model."""

      from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

      cfg = _make_config(primary="gemini", curator_provider="grok", curator_model="")
      provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
      assert provider == "grok"
      assert model == "grok-4.3"

  def test_resolve_provider_returns_none_model_when_unknown() -> None:
      """Unknown provider degrades gracefully: model => None (registry default)."""

      from jarvis.memory.wiki.curator_llm import _resolve_provider_and_model

      cfg = JarvisConfig(
          brain=BrainConfig(primary="never-heard-of-it", providers={}),
          memory=MemoryConfig(
              wiki=WikiMemoryConfig(curator=WikiCuratorConfig()),
          ),
      )
      provider, model = _resolve_provider_and_model(cfg.memory.wiki.curator, cfg)
      assert provider == "never-heard-of-it"
      assert model is None
  ```

- [ ] **Step 6: Verify the existing brain-instantiation test still asserts the cheap model.**
  The existing `test_propose_updates_uses_primary_when_provider_empty` (current lines 294-313) asserts the curator instantiates `claude-api` with `model == "claude-haiku-4-5-20251001"`. That is the cheap router model, so it stays green with the new resolver. Confirm no edit is needed there — it now also documents the cheap-default behavior end-to-end.

- [ ] **Step 7: Run the unit suite and confirm green.**
  ```
  python -m pytest tests/unit/memory/wiki/test_curator_llm.py -q
  ```
  Expected: all tests pass (the five rewritten/added resolver tests + the existing 20 end-to-end tests), e.g. `25 passed`. No `unittest.mock` is used — collaborators are the in-file fakes (`FakeBrain`, `FakeRegistry`, `FakeVault`, `FakeRepo`).

- [ ] **Step 8: Lint the touched file.**
  ```
  ruff check jarvis/memory/wiki/curator_llm.py tests/unit/memory/wiki/test_curator_llm.py
  ```
  Expected: `All checks passed!`

- [ ] **Step 9: Commit.**
  ```
  git add jarvis/memory/wiki/curator_llm.py tests/unit/memory/wiki/test_curator_llm.py
  git commit -m "feat(wiki): curator resolves a cheap dedicated model by default

The wiki curator's empty-model fallback used brain.providers[provider].model
(the user's full frontier chat model), silently billing the deep brain for
every background ingest. Resolve the provider's cheap/fast router-tier model
instead (mirrors the ack-brain follow_brain pattern via
get_tier_default_model('router', provider)). Explicit
[memory.wiki.curator].model still wins; unknown providers degrade to None so
the registry picks its own default.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas
- A parallel session has `jarvis/brain/manager.py` and `jarvis/core/config.py` dirty in the working tree (see `git status`), but this task **edits neither** — it only *imports* the module-level `get_tier_default_model` and *reads* `WikiCuratorConfig`. No merge conflict is introduced. Step 1 reconciles the import name against whatever the parallel session left.
- Keep the `get_tier_default_model` import **function-local** inside `_cheap_model_for` (not module-level) so the curator import chain stays light on the voice/ingest path and degrades to `_CHEAP_MODEL_FALLBACK` if `jarvis.brain.manager` is unimportable on a minimal `python:3.11-slim` VPS.
- `WikiCuratorConfig` keeps `ConfigDict(extra="allow")` (AP-16). No new config key is added — `provider`/`model` already exist. The change is purely in the *resolution* of an empty `model`.

---

### Task 2: Wave-1 Backend: GET/PUT /api/settings/wiki-provider (user-selectable Wiki curator model)

Expose the dedicated Wiki-curator model picker as a backend endpoint. It reads/writes `[memory.wiki.curator].provider` + `.model` (the EXISTING fields, resolved through the single hook `_resolve_provider_and_model`), persists via `config_writer` (AP-7: lock + tempfile + BOM-safe), and applies the choice live to a running `WikiCurator` when one exists. Empty `provider` => `brain.primary`; empty `model` => that provider's CHEAP/FAST router model (mirrors the ack-brain `follow_brain` pattern). Returns `{"provider","model","available":[{provider, models:[...]}]}`.

**Files:**
- Modify: `jarvis/core/config_writer.py` — add `set_wiki_curator_provider(...)` + nested-table writer `_patch_wiki_curator_toml(...)` (APPEND at end of file, after the last `_patch_*` helper ~line 883; the file is dirty from a parallel session — append only, do not touch existing lines)
- Modify: `jarvis/ui/web/settings_routes.py` — add the two routes + a small resolver (APPEND at end of file, after `put_mute_music` ~line 888; the file is dirty — append only)
- Test: `tests/integration/test_wiki_provider_route.py` (new file)

No `server.py` change is needed: `settings_router` is already mounted (`jarvis/ui/web/server.py:263`), and the new routes share its `prefix="/api/settings"`.

> Why not `config-soll.json` / 3-layer persist (unlike `set_sub_jarvis_provider`): `[memory.wiki.curator]` is NOT a drift-guarded field (`scripts/config-soll.json` only pins `memory.legacy_curator`, brain/tts/stt providers). A plain BOM-safe nested TOML write is the correct persistence tier here.

---

- [ ] **Step 1: Add the cheap-model resolver helper + `available` list to `settings_routes.py`.**

  Append this block at the very end of `jarvis/ui/web/settings_routes.py` (after `put_mute_music`, the last route ~line 888). It reuses `TIER_DEFAULTS_BY_PROVIDER` / `get_tier_default_model` from `jarvis.brain.manager` (the SAME source `_fast_model` reads) so the "cheap default" matches the ack-brain `follow_brain` behaviour, and it reuses the live `BrainManager.available_providers()` when present.

  ```python
  # ---------------------------------------------------------------------------
  # Wiki curator model picker. GET current + selectable providers/models; PUT to
  # change. The dedicated long-term-memory LLM is provider-agnostic: an empty
  # provider falls back to brain.primary and an empty model falls back to that
  # provider's CHEAP/FAST router model (mirrors the ack-brain follow_brain
  # pattern). Persisted to jarvis.toml [memory.wiki.curator]; applied live to a
  # running WikiCurator when one exists, else takes effect on the next ingest /
  # restart. Reads/writes the EXISTING WikiCuratorConfig fields resolved through
  # jarvis.memory.wiki.curator_llm._resolve_provider_and_model.
  # ---------------------------------------------------------------------------


  class WikiProviderBody(BaseModel):
      # Empty strings are meaningful: provider="" => brain.primary,
      # model="" => the provider's cheap/fast router model. The frontend sends a
      # concrete provider and either a concrete model or "" for "cheap default".
      provider: str = Field(default="", max_length=64)
      model: str = Field(default="", max_length=128)
      persist: bool = Field(default=True, description="Persist to jarvis.toml")


  def _wiki_curator_cfg(request: Request):
      cfg = _config(request)
      memory = getattr(cfg, "memory", None)
      wiki = getattr(memory, "wiki", None)
      return getattr(wiki, "curator", None)


  def _available_brain_providers(request: Request) -> list[dict[str, object]]:
      """Selectable (provider, models) pairs for the Wiki picker.

      Provider list comes from the live BrainManager registry when reachable
      (same source as the brain-switch path), else from the TIER_DEFAULTS table.
      Each provider lists its cheap router model first, then its deep model, so
      the UI can offer "cheap default" plus an upgrade. The provider's own
      [brain.providers.<name>].model override (if set) is surfaced too.
      """
      from jarvis.brain.manager import TIER_DEFAULTS_BY_PROVIDER

      names: list[str] = []
      brain = getattr(request.app.state, "brain", None)
      if brain is not None and hasattr(brain, "available_providers"):
          try:
              names = list(brain.available_providers())
          except Exception:  # noqa: BLE001
              names = []
      if not names:
          names = sorted(TIER_DEFAULTS_BY_PROVIDER.get("router", {}))

      cfg = _config(request)
      providers_cfg = getattr(getattr(cfg, "brain", None), "providers", {}) or {}

      out: list[dict[str, object]] = []
      for name in names:
          models: list[str] = []
          # Cheap/fast first (what an empty model resolves to), then deep.
          for tier in ("router", "deep"):
              m = TIER_DEFAULTS_BY_PROVIDER.get(tier, {}).get(name)
              if m and m not in models:
                  models.append(m)
          # Surface a user override from [brain.providers.<name>].model.
          override = getattr(providers_cfg.get(name), "model", "") if providers_cfg else ""
          if override and override not in models:
              models.insert(0, override)
          out.append({"provider": name, "models": models})
      return out
  ```

- [ ] **Step 2: Add the `GET` route to `settings_routes.py` (append directly below Step 1's block).**

  ```python
  @router.get("/wiki-provider")
  async def get_wiki_provider(request: Request) -> dict[str, object]:
      """Current Wiki-curator provider/model + the selectable matrix.

      Returns the RAW config values (empty string = "follow brain.primary" /
      "cheap default"); the frontend renders the empty state explicitly so the
      user sees they are tracking the main brain rather than a stale concrete
      pin.
      """
      curator = _wiki_curator_cfg(request)
      return {
          "provider": getattr(curator, "provider", "") or "",
          "model": getattr(curator, "model", "") or "",
          "available": _available_brain_providers(request),
      }
  ```

- [ ] **Step 3: Add the `PUT` route to `settings_routes.py` (append directly below Step 2).**

  Validates an unknown provider with a 4xx (the spec's hard requirement), persists best-effort via `config_writer`, mutates the in-memory cfg, and applies live by resetting the running curator's cached brain so the next ingest re-resolves through `_resolve_provider_and_model`.

  ```python
  @router.put("/wiki-provider")
  async def put_wiki_provider(body: WikiProviderBody, request: Request) -> dict[str, object]:
      from jarvis.memory.wiki.integration import get_running_curator

      provider = body.provider.strip()
      model = body.model.strip()

      # Validate the provider against the selectable matrix. An empty provider is
      # valid and means "follow brain.primary" (resolved later by the curator).
      if provider:
          known = {p["provider"] for p in _available_brain_providers(request)}
          if provider not in known:
              raise HTTPException(
                  status_code=400,
                  detail=(
                      f"Unknown brain provider {body.provider!r} "
                      f"(available: {sorted(known)})."
                  ),
              )

      # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
      curator_cfg = _wiki_curator_cfg(request)
      if curator_cfg is not None:
          for attr, value in (("provider", provider), ("model", model)):
              try:
                  setattr(curator_cfg, attr, value)
              except Exception as exc:  # noqa: BLE001 — frozen model is not an error
                  log.debug("in-memory wiki.curator.%s update skipped: %s", attr, exc)

      # Persist to jarvis.toml [memory.wiki.curator] (AP-7: lock + tempfile +
      # BOM-safe via config_writer). Best-effort: a read-only / locked toml must
      # not break a live apply that already succeeded.
      persisted = False
      if body.persist:
          try:
              from jarvis.core import config_writer
              from jarvis.core.config import resolve_config_path

              config_writer.set_wiki_curator_provider(
                  provider, model=model, path=resolve_config_path()
              )
              persisted = True
          except Exception as exc:  # noqa: BLE001
              log.warning("wiki-provider persist failed (live apply still attempted): %s", exc)

      # Live-apply: a running WikiCurator holds a WikiCuratorLLM (._llm) whose
      # ._cfg is the WikiCuratorConfig and whose ._brain is a lazily-cached Brain.
      # Mutating ._cfg and clearing ._brain makes the NEXT ingest re-resolve the
      # provider/model through _resolve_provider_and_model — no restart needed.
      applied_live = False
      curator = get_running_curator()
      llm = getattr(curator, "_llm", None)
      live_cfg = getattr(llm, "_cfg", None)
      if live_cfg is not None:
          try:
              live_cfg.provider = provider
              live_cfg.model = model
              llm._brain = None  # force re-resolution on the next ingest
              llm._resolved_provider = None
              llm._resolved_model = None
              applied_live = True
          except Exception as exc:  # noqa: BLE001 — never fail the save on a live hiccup
              log.warning("wiki-provider live-apply failed (persisted; applies next ingest): %s", exc)

      return {
          "ok": True,
          "provider": provider,
          "model": model,
          "available": _available_brain_providers(request),
          "persisted": persisted,
          "applied_live": applied_live,
          # The curator re-resolves on the next ingest; when not live-applied it
          # takes effect after the next ingest / restart.
          "restart_required": not applied_live,
      }
  ```

- [ ] **Step 4: Add the persistence setter to `config_writer.py`.**

  Append at the END of `jarvis/core/config_writer.py` (after the last `_patch_*` helper, ~line 883+; the file is dirty — append only). The setter walks the NESTED `memory → wiki → curator` tables (mirroring `_patch_sub_jarvis_provider_toml`'s nested-walk + BOM contract), writing BOTH `provider` and `model` in one locked write so an empty string is persisted verbatim (empty = the documented fallback sentinel).

  ```python
  def set_wiki_curator_provider(
      name: str,
      *,
      model: str = "",
      path: Path = DEFAULT_CONFIG_FILE,
  ) -> None:
      """Persist the Wiki-curator model picker in ``[memory.wiki.curator]``.

      Writes ``provider`` and ``model`` together. Empty strings are persisted
      verbatim — they are the documented fallback sentinels resolved at runtime
      by ``jarvis.memory.wiki.curator_llm._resolve_provider_and_model``
      (``provider=""`` -> ``brain.primary``; ``model=""`` -> the provider's
      cheap/fast router model). Takes effect as a boot default on the next
      ``load_config``; the live switch happens in the settings route by resetting
      the running ``WikiCuratorLLM``'s cached brain.
      """
      _patch_wiki_curator_toml(path, {"provider": name, "model": model})


  def _patch_wiki_curator_toml(path: Path, values: dict[str, object]) -> None:
      """Set keys under the nested ``[memory.wiki.curator]`` table.

      Walks ``memory`` -> ``wiki`` -> ``curator`` (creating any missing level),
      sets each key in ``values``, and preserves comments, sibling keys, and the
      optional BOM (same contract as :func:`_patch_sub_jarvis_provider_toml`).
      """
      if not path.exists():
          raise FileNotFoundError(f"Config-Datei fehlt: {path}")

      with _WRITE_LOCK:
          raw = path.read_text(encoding="utf-8")
          had_bom = raw.startswith(_BOM)
          if had_bom:
              raw = raw[len(_BOM) :]
          doc: TOMLDocument = tomlkit.parse(raw)

          memory = doc.get("memory")
          if memory is None:
              memory = tomlkit.table()
              doc["memory"] = memory
          wiki = memory.get("wiki")
          if wiki is None:
              wiki = tomlkit.table()
              memory["wiki"] = wiki
          curator = wiki.get("curator")
          if curator is None:
              curator = tomlkit.table()
              wiki["curator"] = curator
          for key, value in values.items():
              curator[key] = value

          out = tomlkit.dumps(doc)
          if had_bom:
              out = _BOM + out
          _atomic_write(path, out)
  ```

- [ ] **Step 5: Write the route tests.**

  Create `tests/integration/test_wiki_provider_route.py`. Mirrors `tests/integration/test_settings_routes.py` (TestClient + `WebServer` fixture, `monkeypatch` to capture persistence instead of writing real `jarvis.toml`). Proves: GET returns current config + `available`; PUT persists + applies live to a fake running curator; PUT with an unknown provider returns 4xx and leaves the cfg untouched. Uses a fake curator (NOT `unittest.mock`) per repo convention.

  ```python
  """Integration tests for /api/settings/wiki-provider.

  The desktop API-Keys view writes the Wiki-curator model through this endpoint.
  It reads/writes [memory.wiki.curator].provider/.model and applies the choice
  live to a running WikiCurator.
  """
  from __future__ import annotations

  from collections.abc import Iterator

  import pytest
  from fastapi.testclient import TestClient

  from jarvis.core.bus import EventBus
  from jarvis.core.config import JarvisConfig
  from jarvis.ui.web.server import WebServer


  class _FakeBrainManager:
      """Mirrors BrainManager.available_providers (the only surface used here)."""

      def available_providers(self) -> list[str]:
          return ["gemini", "claude-api", "grok"]


  class _FakeLLM:
      """Stand-in for WikiCuratorLLM: holds the live cfg + a cached brain."""

      def __init__(self, cfg) -> None:
          self._cfg = cfg
          self._brain = object()  # a non-None cached brain to be cleared
          self._resolved_provider = "gemini"
          self._resolved_model = "gemini-3-flash-preview"


  class _FakeCurator:
      def __init__(self, cfg) -> None:
          self._llm = _FakeLLM(cfg)


  @pytest.fixture
  def server() -> Iterator[WebServer]:
      cfg = JarvisConfig()
      cfg.ui.dev_mode = True
      bus = EventBus()
      s = WebServer(cfg, bus=bus)
      s.app.state.brain = _FakeBrainManager()
      s.app.state.config = cfg
      s.app.state.bus = bus
      yield s


  @pytest.fixture(autouse=True)
  def _no_toml_write(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
      """Capture persistence calls instead of writing the real jarvis.toml."""
      calls: list[tuple[str, str]] = []
      from jarvis.core import config_writer

      def _capture(name, *, model="", path=None):  # noqa: ANN001
          calls.append((name, model))

      monkeypatch.setattr(config_writer, "set_wiki_curator_provider", _capture)
      return calls


  @pytest.fixture(autouse=True)
  def _no_running_curator(monkeypatch: pytest.MonkeyPatch) -> None:
      """Default: no live curator. Individual tests install one explicitly."""
      from jarvis.memory.wiki import integration

      monkeypatch.setattr(integration, "_set_running_curator", lambda c: None, raising=True)
      integration._set_running_curator(None)


  def test_get_returns_current_config_and_available(server: WebServer) -> None:
      with TestClient(server.app) as client:
          resp = client.get("/api/settings/wiki-provider")
          assert resp.status_code == 200
          body = resp.json()
          # Fresh JarvisConfig: empty provider/model = "follow brain.primary".
          assert body["provider"] == ""
          assert body["model"] == ""
          provs = {row["provider"] for row in body["available"]}
          assert {"gemini", "claude-api", "grok"} <= provs
          # Cheap/fast router model is listed first for each provider.
          gemini = next(r for r in body["available"] if r["provider"] == "gemini")
          assert gemini["models"][0] == "gemini-3-flash-preview"


  def test_put_persists_by_default(
      server: WebServer, _no_toml_write: list[tuple[str, str]]
  ) -> None:
      with TestClient(server.app) as client:
          resp = client.put(
              "/api/settings/wiki-provider",
              json={"provider": "claude-api", "model": ""},
          )
          assert resp.status_code == 200
          body = resp.json()
          assert body["provider"] == "claude-api"
          assert body["persisted"] is True
      assert _no_toml_write == [("claude-api", "")]
      # In-memory cfg updated so a later read agrees pre-restart.
      assert server.app.state.config.memory.wiki.curator.provider == "claude-api"


  def test_put_applies_live_to_running_curator(server: WebServer) -> None:
      from jarvis.memory.wiki import integration

      curator = _FakeCurator(server.app.state.config.memory.wiki.curator)
      integration._set_running_curator(curator)
      try:
          with TestClient(server.app) as client:
              resp = client.put(
                  "/api/settings/wiki-provider",
                  json={"provider": "grok", "model": "grok-4.3"},
              )
              assert resp.status_code == 200
              body = resp.json()
              assert body["applied_live"] is True
              assert body["restart_required"] is False
          # Live cfg mutated + cached brain cleared so the next ingest re-resolves.
          assert curator._llm._cfg.provider == "grok"
          assert curator._llm._cfg.model == "grok-4.3"
          assert curator._llm._brain is None
      finally:
          integration._set_running_curator(None)


  def test_put_rejects_unknown_provider(
      server: WebServer, _no_toml_write: list[tuple[str, str]]
  ) -> None:
      with TestClient(server.app) as client:
          resp = client.put(
              "/api/settings/wiki-provider",
              json={"provider": "definitely-not-a-provider", "model": ""},
          )
          assert resp.status_code == 400
      # Neither persisted nor applied to the in-memory cfg.
      assert _no_toml_write == []
      assert server.app.state.config.memory.wiki.curator.provider == ""


  def test_put_empty_provider_is_valid_follow_brain(
      server: WebServer, _no_toml_write: list[tuple[str, str]]
  ) -> None:
      with TestClient(server.app) as client:
          resp = client.put(
              "/api/settings/wiki-provider", json={"provider": "", "model": ""}
          )
          assert resp.status_code == 200
          assert resp.json()["provider"] == ""
      assert _no_toml_write == [("", "")]
  ```

- [ ] **Step 6: Run the new tests + the existing settings/config-writer suites; confirm green.**

  ```bash
  cd "<USER_HOME>/Desktop/Personal Jarvis"
  py -3.11 -m pytest tests/integration/test_wiki_provider_route.py tests/integration/test_settings_routes.py tests/unit/test_config_writer.py -q
  ```

  Expected tail:
  ```
  ......... (no failures)
  XX passed in N.NNs
  ```

  Then lint the two touched Python modules:
  ```bash
  py -3.11 -m ruff check jarvis/ui/web/settings_routes.py jarvis/core/config_writer.py tests/integration/test_wiki_provider_route.py
  ```
  Expected: `All checks passed!`

- [ ] **Step 7: Commit.**

  ```bash
  cd "<USER_HOME>/Desktop/Personal Jarvis"
  git add jarvis/ui/web/settings_routes.py jarvis/core/config_writer.py tests/integration/test_wiki_provider_route.py
  git commit -m "feat(wiki): user-selectable curator model via GET/PUT /api/settings/wiki-provider

Add a backend endpoint that reads/writes [memory.wiki.curator].provider/.model,
persists through config_writer (lock + tempfile + BOM-safe), and applies the
choice live to a running WikiCurator by resetting its cached brain so the next
ingest re-resolves through _resolve_provider_and_model. Empty provider follows
brain.primary; empty model falls back to the provider's cheap/fast router model
(ack-brain follow_brain pattern). Returns {provider, model, available[]}.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas
- **Parallel-session dirty tree.** `jarvis/ui/web/settings_routes.py`, `jarvis/core/config_writer.py`, and `jarvis/ui/web/server.py` are already `M` in `git status`. All edits in this task are **append-only** (new routes/helpers at the bottom of each file, a new test file) precisely so they do not collide with a parallel session's edits. `server.py` needs **no** change — `settings_router` is already mounted at `server.py:263` and the new routes inherit its `prefix="/api/settings"`. If `git commit` reports a merge conflict marker in either file, the parallel session also appended at EOF — move your block below theirs; the routes/helpers are position-independent.
- **No `config-soll.json` pin.** Unlike `set_sub_jarvis_provider` (which needs the 3-layer config-soll pin to survive the drift-guard), `[memory.wiki.curator]` is NOT a drift-guarded field (`scripts/config-soll.json` only guards `memory.legacy_curator` + brain/tts/stt providers). A plain BOM-safe nested TOML write is correct here — do NOT add a config-soll layer.
- **Empty string is load-bearing, not "unset".** `provider=""` / `model=""` are the documented fallback sentinels read by `jarvis/memory/wiki/curator_llm.py:73-81`. Persist and echo them verbatim — never coerce an empty string to `brain.primary` at write time, or the picker silently freezes the curator onto whatever the main brain happened to be at save time (defeats `follow_brain` tracking).
- **Live-apply touches private LLM internals deliberately.** The running curator exposes no public setter, so the route mutates `curator._llm._cfg` and clears `._brain`/`._resolved_*` (the lazy cache in `_ensure_brain`, `curator_llm.py:362-396`). This is best-effort and wrapped in try/except — a headless host (no running curator) just returns `applied_live=false, restart_required=true`. If a future refactor adds a public `WikiCuratorLLM.reconfigure(provider, model)`, switch the route to call it.
- **`resolve_config_path()` honours `JARVIS_CONFIG`.** Pass `path=resolve_config_path()` to the setter (as the `ui-language` route does) so the write lands in the same file `load_config` reads — no desktop/VPS split-brain.
- **`available_providers()` may be absent (headless / Mock-Brain).** `_available_brain_providers` falls back to `TIER_DEFAULTS_BY_PROVIDER["router"]` keys when no live BrainManager is reachable, so GET never 500s on a headless host.

---

### Task 3: Frontend "Wiki" provider/model card (Settings → API Keys & Providers)

Adds a "Wiki" card to the existing API-Keys & Providers screen that lets the user pick which Brain provider + (optional) model the dedicated Wiki curator uses. It reads/writes the new backend endpoint `GET/PUT /api/settings/wiki-provider` (`{provider, model, available: [...]}`), reuses the `<select> + Apply` pattern from `ProviderSwitcher.tsx`, and surfaces an empty-model = "cheap default of brain.primary" hint. English i18n source keys added to all three locales. Ships a vitest test (render, change provider, assert PUT body).

**Files:**
- Create: `jarvis/ui/web/frontend/src/hooks/useWikiProvider.ts` (new hook + `GET/PUT /api/settings/wiki-provider` client)
- Create: `jarvis/ui/web/frontend/src/views/settings/WikiProviderCard.tsx` (new card component)
- Modify: `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx` (import + render `<WikiProviderCard />` as a sibling tier section, after `<TelephonySection />`, ~lines 62-79)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json` (add `"wiki_provider"` block after the `"provider_switcher"` block, ~line 843)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/de.json` (same block, after `"provider_switcher"`, ~line 843)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/es.json` (same block, after `"provider_switcher"`, ~line 843)
- Test: `jarvis/ui/web/frontend/src/views/settings/WikiProviderCard.test.tsx` (new vitest)

---

- [ ] **Step 1: Create the data hook + REST client `useWikiProvider.ts`.**

Mirrors the fetch/refetch + typed-client style of `useProviders.ts` (the `switchTtsProvider` body shape, lines 145-188). Create `jarvis/ui/web/frontend/src/hooks/useWikiProvider.ts`:

```ts
import { useCallback, useEffect, useState } from "react";

/**
 * Dedicated Wiki-curator provider/model selection.
 *
 * Reads/writes `GET/PUT /api/settings/wiki-provider`, which exposes the
 * `[memory.wiki.curator].provider` / `.model` config pair. An empty provider
 * means "follow brain.primary"; an empty model means "use that provider's
 * cheap/fast model" (the ack-brain follow_brain pattern). `available` lists the
 * Brain provider ids the backend will accept (plus the empty "follow primary"
 * sentinel, which the UI renders as a dedicated option).
 */
export interface WikiProviderState {
  provider: string;
  model: string;
  available: string[];
}

const ENDPOINT = "/api/settings/wiki-provider";

export function useWikiProvider() {
  const [data, setData] = useState<WikiProviderState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(ENDPOINT);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const body: WikiProviderState = await res.json();
      setData(body);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  return { data, loading, error, refetch };
}

/**
 * Persists the Wiki curator provider/model. Empty strings are valid and mean
 * "follow brain.primary" (provider) / "cheap default" (model). Returns the
 * server's resolved state so the UI reflects what the backend actually applied.
 */
export async function saveWikiProvider(
  provider: string,
  model: string,
): Promise<WikiProviderState> {
  const res = await fetch(ENDPOINT, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as WikiProviderState;
}
```

- [ ] **Step 2: Create the card component `WikiProviderCard.tsx`.**

Reuses the `<select> + Apply` interaction from `ProviderSwitcher.tsx` (lines 45-71) and the card chrome / tier-header style from `ApiKeysView.tsx`'s `TelephonySection` (lines 92-101) + `card-outline` (line 261). Create `jarvis/ui/web/frontend/src/views/settings/WikiProviderCard.tsx`:

```tsx
import { useState } from "react";
import { BookOpen, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { saveWikiProvider, useWikiProvider } from "@/hooks/useWikiProvider";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

// Empty string = "follow brain.primary". We render it as a named option so the
// user can deliberately pick "Same as brain" instead of guessing what blank does.
const FOLLOW_PRIMARY = "";

/**
 * "Wiki" tier card in the API-Keys & Providers screen. Lets the user pick the
 * dedicated Wiki-curator provider + (optional) model via
 * `GET/PUT /api/settings/wiki-provider`. An empty model is intentional and means
 * "use the cheap/fast model of the chosen provider" (the ack-brain follow_brain
 * pattern), so the field is optional and labelled as such.
 */
export function WikiProviderCard() {
  const t = useT();
  const { data, loading, error, refetch } = useWikiProvider();
  const pushToast = useEventStore((s) => s.pushToast);

  const [provider, setProvider] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  // Controlled values fall back to the server state until the user edits them.
  const providerValue = provider ?? data?.provider ?? FOLLOW_PRIMARY;
  const modelValue = model ?? data?.model ?? "";

  async function handleApply() {
    setPending(true);
    try {
      const next = await saveWikiProvider(providerValue, modelValue.trim());
      // Reset local edits so the inputs re-sync to the server-resolved state.
      setProvider(null);
      setModel(null);
      pushToast(
        "success",
        next.provider
          ? `Wiki → ${next.provider}${next.model ? ` · ${next.model}` : ""}`
          : t("wiki_provider.follow_primary"),
      );
      void refetch();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <section>
      <h3 className="mb-3 inline-flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        <BookOpen className="h-3.5 w-3.5" /> {t("wiki_provider.tier_label")}
      </h3>

      <div className="card-outline space-y-3 p-4">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("wiki_provider.description")}
        </p>

        {loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> {t("wiki_provider.loading")}
          </div>
        )}

        {error && <p className="text-xs text-destructive">{t("wiki_provider.load_error")}</p>}

        {!loading && data && (
          <div className="space-y-3">
            <label className="block">
              <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                {t("wiki_provider.provider_label")}
              </span>
              <select
                aria-label={t("wiki_provider.provider_label")}
                value={providerValue}
                onChange={(e) => setProvider(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value={FOLLOW_PRIMARY}>{t("wiki_provider.follow_primary")}</option>
                {data.available
                  .filter((id) => id !== FOLLOW_PRIMARY)
                  .map((id) => (
                    <option key={id} value={id}>
                      {id}
                    </option>
                  ))}
              </select>
            </label>

            <label className="block">
              <span className="mb-1 block text-xs uppercase tracking-wide text-muted-foreground">
                {t("wiki_provider.model_label")}
              </span>
              <input
                type="text"
                aria-label={t("wiki_provider.model_label")}
                value={modelValue}
                onChange={(e) => setModel(e.target.value)}
                placeholder={t("wiki_provider.model_placeholder")}
                className="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-sm"
              />
              <span className="mt-1 block text-[11px] text-muted-foreground">
                {t("wiki_provider.model_hint")}
              </span>
            </label>

            <Button onClick={handleApply} disabled={pending} className="w-full">
              {pending ? t("wiki_provider.applying") : t("wiki_provider.apply")}
            </Button>
          </div>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 3: Wire the card into `ApiKeysView.tsx`.**

Add the import next to the existing imports (after line 6, the `TelephonyPanel` import):

```tsx
import { TelephonyPanel } from "@/views/TelephonyView";
import { WikiProviderCard } from "@/views/settings/WikiProviderCard";
```

Then render it as the last sibling tier section, immediately after `<TelephonySection />`. Current block (lines 70-78):

```tsx
            {/* Subagent (OpenClaw) — own data source (/api/openclaw/status),
                rendered as a sibling tier so it shares the card system. */}
            <SubagentSection />
            {/* Telephony — the former standalone "Telephony" screen, folded in
                here as another tier section (own data source /api/telephony/*).
                Same header style as the tiers above; always expanded. */}
            <TelephonySection />
          </div>
```

becomes:

```tsx
            {/* Subagent (OpenClaw) — own data source (/api/openclaw/status),
                rendered as a sibling tier so it shares the card system. */}
            <SubagentSection />
            {/* Telephony — the former standalone "Telephony" screen, folded in
                here as another tier section (own data source /api/telephony/*).
                Same header style as the tiers above; always expanded. */}
            <TelephonySection />
            {/* Wiki — dedicated long-term-memory curator provider/model. Own
                data source (/api/settings/wiki-provider); a thin sibling tier. */}
            <WikiProviderCard />
          </div>
```

- [ ] **Step 4: Add the `"wiki_provider"` i18n block to `en.json` (ENGLISH source).**

Find the `"provider_switcher"` block (lines 841-843) and insert the new block right after it. Current text:

```json
  "provider_switcher": {
    "loading_hint": "Provider list is loading — if it stays empty, check the backend."
  },
  "cli_connect": {
```

becomes:

```json
  "provider_switcher": {
    "loading_hint": "Provider list is loading — if it stays empty, check the backend."
  },
  "wiki_provider": {
    "tier_label": "Wiki (long-term memory)",
    "description": "The Knowledge Wiki uses a dedicated model to write and tidy your long-term notes. Leave the provider on \"Same as brain\" and the model empty to use a cheap, fast default — or pin a specific provider and model here.",
    "provider_label": "Wiki provider",
    "follow_primary": "Same as brain (cheap default)",
    "model_label": "Model (optional)",
    "model_placeholder": "Leave empty for the cheap default",
    "model_hint": "Empty means the chosen provider's cheap/fast model. Set a model id only to override it.",
    "apply": "Apply",
    "applying": "Applying…",
    "loading": "Loading Wiki settings…",
    "load_error": "Could not load the Wiki provider settings."
  },
  "cli_connect": {
```

- [ ] **Step 5: Add the same block to `de.json` (German translation, correct umlauts).**

Find the `"provider_switcher"` block (lines 841-843) and insert after it. Current:

```json
  "provider_switcher": {
    "loading_hint": "Provider-Liste wird geladen — falls dauerhaft leer, prüfe das Backend."
  },
  "cli_connect": {
```

becomes:

```json
  "provider_switcher": {
    "loading_hint": "Provider-Liste wird geladen — falls dauerhaft leer, prüfe das Backend."
  },
  "wiki_provider": {
    "tier_label": "Wiki (Langzeitgedächtnis)",
    "description": "Das Wissens-Wiki nutzt ein eigenes Modell, um deine Langzeit-Notizen zu schreiben und aufzuräumen. Lass den Provider auf „Wie das Brain“ und das Modell leer für einen günstigen, schnellen Standard — oder lege hier einen festen Provider und ein festes Modell fest.",
    "provider_label": "Wiki-Provider",
    "follow_primary": "Wie das Brain (günstiger Standard)",
    "model_label": "Modell (optional)",
    "model_placeholder": "Leer lassen für den günstigen Standard",
    "model_hint": "Leer bedeutet das günstige/schnelle Modell des gewählten Providers. Trage nur dann eine Modell-ID ein, wenn du es überschreiben willst.",
    "apply": "Übernehmen",
    "applying": "Übernehme…",
    "loading": "Wiki-Einstellungen werden geladen…",
    "load_error": "Konnte die Wiki-Provider-Einstellungen nicht laden."
  },
  "cli_connect": {
```

- [ ] **Step 6: Add the same block to `es.json` (Spanish translation).**

Find the `"provider_switcher"` block (lines 841-843) and insert after it. Current:

```json
  "provider_switcher": {
    "loading_hint": "Cargando lista de proveedores — si sigue vacía, revisa el backend."
  },
  "cli_connect": {
```

becomes:

```json
  "provider_switcher": {
    "loading_hint": "Cargando lista de proveedores — si sigue vacía, revisa el backend."
  },
  "wiki_provider": {
    "tier_label": "Wiki (memoria a largo plazo)",
    "description": "El Wiki de conocimiento usa un modelo dedicado para escribir y ordenar tus notas a largo plazo. Deja el proveedor en «Igual que la IA» y el modelo vacío para usar un valor predeterminado barato y rápido — o fija aquí un proveedor y un modelo concretos.",
    "provider_label": "Proveedor del Wiki",
    "follow_primary": "Igual que la IA (predeterminado barato)",
    "model_label": "Modelo (opcional)",
    "model_placeholder": "Déjalo vacío para el predeterminado barato",
    "model_hint": "Vacío significa el modelo barato/rápido del proveedor elegido. Indica un id de modelo solo para sobrescribirlo.",
    "apply": "Aplicar",
    "applying": "Aplicando…",
    "loading": "Cargando ajustes del Wiki…",
    "load_error": "No se pudieron cargar los ajustes del proveedor del Wiki."
  },
  "cli_connect": {
```

- [ ] **Step 7: Write the vitest `WikiProviderCard.test.tsx`.**

Follows the identity-translator mock from `LanguagesGroup.test.tsx` (lines 6-14) and the `vi.stubGlobal("fetch", ...)` + PUT-body assertion from `JarvisApiGroup.test.tsx` (lines 53-73). Create `jarvis/ui/web/frontend/src/views/settings/WikiProviderCard.test.tsx`:

```tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// Identity translator so rendered text equals the i18n key (assert keys exactly).
vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
}));

// The toast store is only used for side effects here; stub it to a no-op.
vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: { pushToast: () => void }) => unknown) =>
    selector({ pushToast: vi.fn() }),
}));

import { WikiProviderCard } from "./WikiProviderCard";

const INITIAL = { provider: "", model: "", available: ["gemini", "openai", "grok"] };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WikiProviderCard", () => {
  it("renders the Wiki tier label and the provider select once loaded", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: true, json: async () => INITIAL }),
    );
    render(<WikiProviderCard />);

    expect(screen.getByText("wiki_provider.tier_label")).toBeDefined();
    await waitFor(() =>
      expect(screen.getByLabelText("wiki_provider.provider_label")).toBeDefined(),
    );
  });

  it("sends a PUT with the chosen provider when Apply is clicked", async () => {
    const fetchMock = vi
      .fn()
      // GET on mount
      .mockResolvedValueOnce({ ok: true, json: async () => INITIAL })
      // PUT on Apply
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ provider: "openai", model: "", available: INITIAL.available }),
      })
      // refetch() GET after a successful Apply
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ provider: "openai", model: "", available: INITIAL.available }),
      });
    vi.stubGlobal("fetch", fetchMock);

    render(<WikiProviderCard />);
    const select = (await screen.findByLabelText(
      "wiki_provider.provider_label",
    )) as HTMLSelectElement;

    fireEvent.change(select, { target: { value: "openai" } });
    fireEvent.click(screen.getByRole("button", { name: "wiki_provider.apply" }));

    await waitFor(() => {
      const put = fetchMock.mock.calls.find(([, opts]) => opts?.method === "PUT");
      expect(put).toBeTruthy();
      expect(put?.[0]).toBe("/api/settings/wiki-provider");
      expect(JSON.parse(put?.[1]?.body as string)).toMatchObject({
        provider: "openai",
        model: "",
      });
    });
  });
});
```

- [ ] **Step 8: Run the vitest suite for the new test + the type-check, confirm green.**

```bash
cd jarvis/ui/web/frontend && npm run test -- WikiProviderCard
```
Expected output: `Test Files  1 passed (1)` / `Tests  2 passed (2)`.

Then verify TypeScript + build:
```bash
cd jarvis/ui/web/frontend && npx tsc --noEmit && npm run build
```
Expected: `tsc` exits 0 with no errors; `vite build` ends with `✓ built in …` and writes `jarvis/ui/web/dist`.

- [ ] **Step 9: Commit.**

```bash
git add jarvis/ui/web/frontend/src/hooks/useWikiProvider.ts jarvis/ui/web/frontend/src/views/settings/WikiProviderCard.tsx jarvis/ui/web/frontend/src/views/settings/WikiProviderCard.test.tsx jarvis/ui/web/frontend/src/views/ApiKeysView.tsx jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(ui): Wiki provider/model card in API Keys & Providers" -m "Adds a dedicated Wiki-curator provider/model selector (GET/PUT /api/settings/wiki-provider) reusing the ProviderSwitcher select+Apply pattern, with en/de/es i18n and a vitest covering render + PUT body."
```

#### Gotchas
- **The dist bundle is RAM-loaded under pywebview.** A running desktop app keeps the old frontend in memory; this card only appears after `npm run build` AND an app restart (BUG-006/014 four-layer restore trap). The test/`tsc`/`build` in Step 8 prove the code, not the live window.
- **Parallel-session dirty tree:** `git status` already shows `ApiKeysView.tsx` is NOT in the dirty set, but `src/i18n/locales/{en,de,es}.json`, `src/i18n/index.ts`, and `src/views/settings/LanguagesGroup.tsx`/`.test.tsx` ARE modified by a parallel session. When you edit the three locale JSONs, insert the `"wiki_provider"` block strictly *after* the existing `"provider_switcher"` block (a stable, unmodified anchor) so you don't collide with the parallel i18n edits. Stage only the 7 listed files in Step 9 — do NOT `git add -A`, or you will sweep in the parallel session's unrelated changes.
- **Backend dependency:** this card calls `GET/PUT /api/settings/wiki-provider`. That endpoint is authored by the sibling backend Wave-1 task (`jarvis/ui/web/settings_routes.py` + `provider_routes.py`). Until it merges, the hook's `error` branch renders `wiki_provider.load_error` (graceful — no crash). The test stubs `fetch`, so it is independent of the backend landing first.
- **i18n key parity:** all three locales must carry the identical `"wiki_provider"` key set. The app's i18n is a flat per-locale JSON merge (`src/i18n/index.ts` lines 42-46) with English as the implicit fallback, so a missing de/es key would silently render the raw key string — add all three in Steps 4-6, don't skip es.
- **English-only source rule:** the i18n *key names* and the `en.json` *values* are the English source of record (CLAUDE.md Output Language Policy); de/es are translations. The component/hook code, comments, and test names are all English. The German `de.json` strings use real umlauts (ä/ö/ü/ß), never ASCII substitutes.
- **No new dependency:** `BookOpen`/`Loader2` already ship with the existing `lucide-react`; `@/components/ui/button`, `@/store/events`, `@/i18n` are all already imported elsewhere — base install unchanged, doctrine intact.

---

### Task 4: Retire awareness-episode → session-page wiki feed (D2) + drop redundant re-ingest pass

**Goal:** Stop the `SessionRollupWorker` from writing durable wiki *session* pages out of window-focus awareness episodes, and remove `integration._on_idle_entered`'s re-read-and-re-ingest second curator pass. The awareness L1/L2 system stays untouched (live situational awareness keeps working); only the *wiki write* is gated, config-driven, and **default OFF**. The conversation path (`VoiceFactBridge` → curator) is the sole remaining wiki feed and must keep working.

**Files:**
- **Modify** `jarvis/core/config.py` — `SessionRollupConfig` (class body at lines ~607-618): append one field `wiki_write_enabled: bool = False`.
- **Modify** `jarvis/memory/wiki/session_rollup.py` — `RollupStatus` docstring (lines 99-102) + `flush_session()` (lines 218-374): add an early short-circuit that skips render/write when the wiki write is gated off; add a telemetry counter.
- **Modify** `jarvis/memory/wiki/integration.py` — `bootstrap_wiki_integration` IdleEntered-subscription block (lines 271-322): gate the re-ingest second pass on the new config flag and log the retirement.
- **Modify** `jarvis/memory/wiki/telemetry.py` — `DEFAULT_COUNTERS` (lines 47-57): register the three new counters.
- **Test (new)** `tests/unit/memory/wiki/test_d2_no_session_page_feed.py` — proves (a) no session page is written on `IdleEntered` after the change, and (b) the `VoiceFactBridge` conversation path still reaches the curator.

---

- [ ] **Step 1: Add the default-OFF wiki-write gate to `SessionRollupConfig`.**
  Open `jarvis/core/config.py`. The class ends with `user_entity_slug` at line 618. Insert the new field directly after it (append-only; `ConfigDict(extra="allow")` is already present on line 608, so AP-16 holds):

  ```python
      user_entity_slug: str = "alex"
      # D2 (2026-06): the awareness-episode -> durable session-page feed is
      # retired. The worker still READS awareness episodes and still produces
      # the rollup paragraph (live awareness is unaffected), but the durable
      # wiki *page write* is gated off by default. Conversation (VoiceFactBridge)
      # is the sole wiki feed now. Flip to True only to re-enable the legacy
      # window-focus session pages.
      wiki_write_enabled: bool = False
  ```

  Run a quick load check (expected: prints `False`):
  ```bash
  python -c "from jarvis.core.config import SessionRollupConfig; print(SessionRollupConfig().wiki_write_enabled)"
  ```
  Expected output:
  ```
  False
  ```

- [ ] **Step 2: Register the three new telemetry counters.**
  Open `jarvis/memory/wiki/telemetry.py`. Extend the `DEFAULT_COUNTERS` tuple (currently lines 47-57) so the dashboard JSON shape stays stable and the counters show up as `0` before they fire:

  ```python
  DEFAULT_COUNTERS: tuple[str, ...] = (
      "voice_turns_seen",
      "voice_turns_ingested_ack",
      "voice_turns_ingested_aggressive",
      "wiki_context_hits",
      "wiki_context_misses",
      "session_rollups_succeeded",
      "session_rollups_failed",
      "wiki_pages_created",
      "wiki_pages_updated",
      # D2 (2026-06): session-page feed retirement + conversation-only feed.
      "session_rollups_wiki_write_disabled",
      "wiki_links_refused_dangling",
      "wiki_writes_blocked_pii",
  )
  ```

  Verify (expected: all three print `0`):
  ```bash
  python -c "from jarvis.memory.wiki.telemetry import MemoryTelemetry as T; s=T().snapshot(); print(s['session_rollups_wiki_write_disabled'], s['wiki_links_refused_dangling'], s['wiki_writes_blocked_pii'])"
  ```
  Expected output:
  ```
  0 0 0
  ```

- [ ] **Step 3: Add the `disabled_wiki_write` status to the `RollupStatus` docstring.**
  Open `jarvis/memory/wiki/session_rollup.py`. The status alias docstring is at lines 99-102. Replace it so the new short-circuit status is documented:

  ```python
  RollupStatus = str
  """``"ok" | "skipped_too_few_episodes" | "skipped_recent_edit" |
  "llm_unavailable" | "llm_timeout" | "llm_failure" | "rollback" |
  "disabled" | "disabled_wiki_write"``"""
  ```

- [ ] **Step 4: Short-circuit `flush_session()` before any render/write when the wiki write is gated off.**
  Still in `jarvis/memory/wiki/session_rollup.py`. Find the existing top guard of `flush_session()` (lines 225-226):

  ```python
          if not self._cfg.enabled:
              return SessionRollupResult(status="disabled")
  ```

  Insert the new gate immediately after it, **before** the `recent_episodes` read at line 228. This keeps the awareness episodes flowing and the rollup status machine intact, but stops the durable page write at the earliest point so no brain call, no vault scan, and no `AtomicWriter.apply` ever runs for the retired feed:

  ```python
          if not self._cfg.enabled:
              return SessionRollupResult(status="disabled")

          # D2 (2026-06): the awareness-episode -> durable session-page feed is
          # retired. Awareness L1/L2 keeps recording episodes; we simply stop
          # turning them into wiki pages here. Conversation (VoiceFactBridge)
          # is now the sole wiki feed. Short-circuit before the brain call and
          # the AtomicWriter so this path produces neither a page nor an LLM
          # round-trip.
          if not getattr(self._cfg, "wiki_write_enabled", False):
              telemetry.inc("session_rollups_wiki_write_disabled")
              log.debug(
                  "SessionRollupWorker: wiki_write_enabled is off — "
                  "not writing a session page from awareness episodes"
              )
              # Advance the session marker so a later re-enable starts a clean
              # window rather than replaying the whole backlog.
              self._session_start_ns = self._clock()
              return SessionRollupResult(status="disabled_wiki_write")
  ```

  (`getattr(..., False)` is belt-and-suspenders: it tolerates an older in-memory `SessionRollupConfig` that predates the field, e.g. a stale fixture.)

- [ ] **Step 5: Gate the integration's re-read-and-re-ingest second pass on the same flag.**
  Open `jarvis/memory/wiki/integration.py`. The `IdleEntered` re-ingest subscription lives at lines 271-322 inside `bootstrap_wiki_integration`. Today, on `subscribe_idle`, it (a) starts the worker and (b) subscribes its OWN `_on_idle_entered` handler that calls `_flush_and_ingest` — which re-reads the just-written page and pushes it through `curator.ingest()` a second time. With the worker now short-circuiting (Step 4), that second pass has nothing to read; remove it so the redundant curator call is gone entirely.

  Find the block (lines 271-274):

  ```python
      # Start the worker — this subscribes it to IdleEntered internally.
      if config.subscribe_idle:
          await worker.start()
          log.info("wiki_integration: SessionRollupWorker started")
  ```

  Replace it with a version that derives the retirement flag once:

  ```python
      # D2 (2026-06): the awareness-episode -> durable session-page feed is
      # retired by default. ``wiki_write_enabled`` (SessionRollupConfig) gates
      # BOTH the worker's own page write (handled inside flush_session) AND the
      # integration's redundant re-read-and-re-ingest second curator pass below.
      # The worker is still started so its lifecycle/shutdown stays symmetric,
      # but when the feed is retired we never subscribe the re-ingest handler.
      rollup_cfg = root_config_session_rollup()
      wiki_write_enabled = bool(getattr(rollup_cfg, "wiki_write_enabled", False))

      # Start the worker — this subscribes it to IdleEntered internally.
      if config.subscribe_idle:
          await worker.start()
          log.info("wiki_integration: SessionRollupWorker started")
  ```

  Then find the re-ingest subscription guard (lines 294-322), which currently reads:

  ```python
      if config.subscribe_idle:
          async def _on_idle_entered(event: IdleEntered) -> None:  # noqa: RUF029
  ```

  Change that guard so the second pass only attaches when the feed is explicitly re-enabled:

  ```python
      if config.subscribe_idle and wiki_write_enabled:
          async def _on_idle_entered(event: IdleEntered) -> None:  # noqa: RUF029
  ```

  Finally, add a one-line retirement log right after the existing `if not use_scheduler:` / scheduler-decision block but before the `if config.subscribe_idle and wiki_write_enabled:` guard (so it always fires when retired). Insert immediately after line 322's `handle._unsubscribe_idle = _unsubscribe` is NOT where this goes — instead, add it just before the `if config.subscribe_idle and wiki_write_enabled:` line:

  ```python
      if not wiki_write_enabled:
          log.info(
              "wiki_integration: session-page feed retired (D2) — "
              "skipping the awareness re-ingest pass; conversation "
              "(VoiceFactBridge) remains the sole wiki feed"
          )

      if config.subscribe_idle and wiki_write_enabled:
          async def _on_idle_entered(event: IdleEntered) -> None:  # noqa: RUF029
  ```

  Now add the small helper `root_config_session_rollup()` referenced above. The module already loads root config lazily inside `_build_rollup_worker` (lines 581-588); mirror that pattern. Add this private helper near the other private helpers (e.g. right above `_build_rollup_worker` at line 562):

  ```python
  def root_config_session_rollup() -> Any:
      """Return the live ``SessionRollupConfig`` (or a default one).

      Mirrors the lazy load used by ``_build_rollup_worker`` so the D2
      ``wiki_write_enabled`` gate reads the same config the worker does,
      without threading a new argument through ``bootstrap_wiki_integration``.
      """
      try:
          from jarvis.core.config import load_config
          return load_config().memory.wiki.session_rollup
      except Exception:  # noqa: BLE001
          from jarvis.core.config import JarvisConfig
          return JarvisConfig().memory.wiki.session_rollup
  ```

- [ ] **Step 6: Write the D2 regression test.**
  Create `tests/unit/memory/wiki/test_d2_no_session_page_feed.py`. It reuses the real-on-tmpfs stack pattern from `test_session_rollup.py` (fake brain via `registry.instantiate`, everything else real) and adds the conversation-path assertion. Full file:

  ```python
  """D2 regression: the awareness-episode -> session-page wiki feed is retired.

  Two invariants:

  1. With ``wiki_write_enabled`` off (the default), an ``IdleEntered`` event
     that would previously have triggered a session-page write produces NO
     page on disk and reports ``disabled_wiki_write`` — while the awareness
     episodes themselves are untouched (read, not deleted).
  2. The conversation path (``VoiceFactBridge`` -> curator) still reaches the
     curator, so retiring the session feed does not silence the wiki.
  """
  from __future__ import annotations

  import asyncio
  import time
  from collections.abc import AsyncIterator
  from pathlib import Path
  from unittest.mock import MagicMock

  import pytest
  import pytest_asyncio

  from jarvis.core.bus import EventBus
  from jarvis.core.config import load_config
  from jarvis.core.events import IdleEntered, ResponseGenerated, TranscriptFinal
  from jarvis.core.protocols import BrainDelta, BrainRequest
  from jarvis.memory.recall import RecallStore
  from jarvis.memory.wiki.atomic_writer import AtomicWriter
  from jarvis.memory.wiki.log_writer import LogWriter
  from jarvis.memory.wiki.page import MarkdownPageRepository
  from jarvis.memory.wiki.session_rollup import SessionRollupWorker
  from jarvis.memory.wiki.voice_bridge import VoiceFactBridge

  NS_PER_MIN = 60 * 1_000_000_000


  class _FakeBrain:
      name = "fake-brain"
      context_window = 100_000
      supports_tools = False
      supports_vision = False

      def __init__(self, text: str = "A session paragraph.") -> None:
          self.text = text
          self.call_count = 0

      async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
          self.call_count += 1
          yield BrainDelta(content=self.text)
          yield BrainDelta(finish_reason="stop", usage={"input_tokens": 1, "output_tokens": 1})

      def estimate_cost(self, req: BrainRequest) -> float:  # pragma: no cover
          return 0.0


  @pytest_asyncio.fixture
  async def worker_stack(tmp_path: Path):
      vault_root = tmp_path / "workspace"
      for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
          (vault_root / sub).mkdir(parents=True)
      (vault_root / "schema.md").write_text("# stub\n", encoding="utf-8")
      (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

      db_path = tmp_path / "jarvis.db"
      recall = RecallStore(db_path)
      await recall.open()

      repo = MarkdownPageRepository()
      writer = AtomicWriter(vault_root=vault_root, backup_dir=tmp_path / "backups")
      log_writer = LogWriter(log_path=vault_root / "log.md")
      bus = EventBus()
      config = load_config()

      clock_holder = [int(time.mktime((2026, 6, 15, 14, 0, 0, 0, 0, -1)) * 1_000_000_000)]
      worker = SessionRollupWorker(
          config=config,
          recall_store=recall,
          vault_root=vault_root,
          atomic_writer=writer,
          page_repo=repo,
          log_writer=log_writer,
          bus=bus,
          clock=lambda: clock_holder[0],
      )
      fake_brain = _FakeBrain()
      worker._registry.instantiate = MagicMock(return_value=fake_brain)  # noqa: SLF001

      yield worker, recall, vault_root, clock_holder, fake_brain
      await recall.close()


  async def _seed_episode(recall: RecallStore, *, started_at_ns: int, summary: str) -> int:
      return await recall.record_episode(
          started_at_ns=started_at_ns,
          ended_at_ns=started_at_ns + NS_PER_MIN,
          trigger_kind="window_switch",
          summary=summary,
          frame_count=3,
          primary_app="code.exe",
      )


  @pytest.mark.asyncio
  async def test_idle_writes_no_session_page_when_feed_retired(worker_stack):
      """Default (wiki_write_enabled off): no page, no LLM call, status reports it."""
      worker, recall, vault_root, clock_holder, brain = worker_stack
      # Default must be OFF — assert it rather than mutate, so a regression of
      # the default flip is caught here too.
      assert worker._cfg.wiki_write_enabled is False  # noqa: SLF001

      base = clock_holder[0]
      worker._session_start_ns = base - 240 * NS_PER_MIN  # noqa: SLF001
      await _seed_episode(recall, started_at_ns=base - 60 * NS_PER_MIN, summary="ep1")
      await _seed_episode(recall, started_at_ns=base - 30 * NS_PER_MIN, summary="ep2")
      await _seed_episode(recall, started_at_ns=base - 10 * NS_PER_MIN, summary="ep3")

      event = IdleEntered(idle_since_ns=base - 150 * NS_PER_MIN)
      await worker._on_idle_entered(event)  # noqa: SLF001

      pages = list((vault_root / "sessions").glob("*.md"))
      assert pages == [], "D2: no durable session page may be written from awareness episodes"
      assert brain.call_count == 0, "D2: the retired feed must not even call the brain"

      # Awareness episodes are untouched (read, not consumed/deleted).
      remaining = await recall.recent_episodes(limit=1000, since_ns=base - 240 * NS_PER_MIN)
      assert len(remaining) == 3, "awareness L1/L2 episodes must remain intact"


  @pytest.mark.asyncio
  async def test_flush_returns_disabled_wiki_write_status(worker_stack):
      worker, _recall, _vault, _clock, brain = worker_stack
      result = await worker.flush_session()
      assert result.status == "disabled_wiki_write"
      assert result.page_path is None
      assert brain.call_count == 0


  @pytest.mark.asyncio
  async def test_reenabling_flag_restores_the_page_write(worker_stack):
      """Sanity: flipping wiki_write_enabled back on writes a page again."""
      worker, recall, vault_root, clock_holder, brain = worker_stack
      worker._cfg = worker._cfg.model_copy(update={"wiki_write_enabled": True})  # noqa: SLF001
      base = clock_holder[0]
      worker._session_start_ns = base - 240 * NS_PER_MIN  # noqa: SLF001
      await _seed_episode(recall, started_at_ns=base - 60 * NS_PER_MIN, summary="ep1")
      await _seed_episode(recall, started_at_ns=base - 30 * NS_PER_MIN, summary="ep2")

      result = await worker.flush_session()
      assert result.status == "ok"
      assert brain.call_count == 1
      assert list((vault_root / "sessions").glob("*.md"))


  @pytest.mark.asyncio
  async def test_voice_bridge_conversation_path_still_reaches_curator(tmp_path: Path):
      """Retiring the session feed must NOT silence the conversation -> curator path."""
      bus = EventBus()
      ingested: list[str] = []

      class _FakeCurator:
          async def ingest(self, *, source_content: str, source_label: str):
              ingested.append(source_content)
              # Minimal WriteResult-shaped object the bridge tolerates.
              return MagicMock(applied=[], skipped_due_to_recent_edit=[], failed_validation=[])

      bridge = VoiceFactBridge(bus=bus, curator=_FakeCurator(), config=None)
      bridge.start()
      try:
          user_text = "Remember that my dentist appointment is on Friday at 3pm in Munich."
          bus.publish(TranscriptFinal(text=user_text, is_final=True))
          bus.publish(ResponseGenerated(text="Noted."))
          # The bridge fires fire-and-forget tasks; let them run.
          for _ in range(20):
              await asyncio.sleep(0.02)
              if ingested:
                  break
      finally:
          bridge.stop()

      assert ingested, "VoiceFactBridge must still forward conversation turns to the curator"
      assert any("dentist" in text for text in ingested)
  ```

  Note: if `TranscriptFinal` / `ResponseGenerated` constructor kwargs differ in this tree, open `jarvis/core/events.py` and match the exact field names before running — the dataclasses are `frozen=True` with `trace_id`/`timestamp_ns` auto-defaults, so only the text fields are required.

- [ ] **Step 7: Run the new test + the existing rollup suite (no regressions).**
  ```bash
  python -m pytest tests/unit/memory/wiki/test_d2_no_session_page_feed.py tests/unit/memory/wiki/test_session_rollup.py tests/unit/memory/wiki/test_integration_shutdown.py tests/unit/memory/wiki/test_telemetry.py -q
  ```
  Expected output (counts illustrative; the key signal is `passed` and `0 failed`):
  ```
  ....................                                                     [100%]
  N passed in X.XXs
  ```

- [ ] **Step 8: Lint the touched files.**
  ```bash
  ruff check jarvis/core/config.py jarvis/memory/wiki/session_rollup.py jarvis/memory/wiki/integration.py jarvis/memory/wiki/telemetry.py tests/unit/memory/wiki/test_d2_no_session_page_feed.py
  ```
  Expected output:
  ```
  All checks passed!
  ```

- [ ] **Step 9: Commit.**
  ```bash
  git add jarvis/core/config.py jarvis/memory/wiki/session_rollup.py jarvis/memory/wiki/integration.py jarvis/memory/wiki/telemetry.py tests/unit/memory/wiki/test_d2_no_session_page_feed.py
  git commit -m "feat(wiki): retire awareness-episode session-page feed (D2), drop redundant re-ingest pass

Gate the durable session-page write behind SessionRollupConfig.wiki_write_enabled
(default False). The SessionRollupWorker still reads awareness episodes (L1/L2
situational awareness is unaffected) but no longer turns window-focus telemetry
into wiki pages. The integration's re-read-and-re-ingest second curator pass is
skipped when the feed is retired, removing the redundant third LLM ingest. The
VoiceFactBridge conversation path remains the sole wiki feed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas
- **Dirty working tree (parallel sessions):** `jarvis/core/config.py` and `jarvis/core/events.py` are already `M` in `git status`. The new `wiki_write_enabled` field is an **append-only** one-liner inside `SessionRollupConfig` (right after `user_entity_slug` at line 618) — insert at that exact anchor without reflowing neighbours and it won't collide. If a parallel session added its own field to the same class, just re-apply the single line.
- **Do not flip `enabled`.** `SessionRollupConfig.enabled` stays `True` so the worker keeps subscribing and the existing status machine / tests (`test_session_rollup.py`) stay green. The off-by-default switch is the **new** `wiki_write_enabled`, not `enabled`.
- **Awareness is untouched.** The worker still reads `awareness_episodes`; only the `AtomicWriter.apply` page write and the integration re-ingest pass are gated. The D2 test asserts the episodes survive (`len(remaining) == 3`).
- **`scheduler_factory` is always `None` in prod** (`server.py:1742`), so the live re-ingest is the `else`-branch `curator.ingest()` inside `_flush_and_ingest`. Gating `_on_idle_entered`'s subscription on `wiki_write_enabled` removes both the scheduler and the direct re-ingest in one move.
- **Conversation path stays live** via `VoiceFactBridge` (integration.py ~334-349) → `get_running_curator()`; nothing here touches it, and Step 6's last test pins that contract.
- **`ConfigDict(extra="allow")` already on `SessionRollupConfig`** (config.py:608) — AP-16 satisfied; the new key cannot break self-mod pre-validate boot.

---

### Task 5: Reject mid-sentence truncation: length-capped wiki/rollup generations are completed-or-discarded, never persisted half-finished

A length-capped LLM response (the model hit `max_tokens` and was cut off mid-sentence) must be **completed-or-discarded**, never persisted. Today both durable-memory writers (`WikiCuratorLLM.propose_updates`, `SessionRollupWorker._call_brain`) aggregate the stream and write whatever text came back — a truncated JSON array or a half-finished paragraph lands on disk. The aggregate already surfaces `finish_reason`; we (a) raise the two stingy output caps to safe values, and (b) add one shared finish-reason / sentence-final-punctuation guard that turns a truncated generation into "no write".

**Files:**
- Modify `jarvis/brain/streaming.py` (add `is_length_truncated` after `tee_text`, ~line 87)
- Modify `jarvis/core/config.py` (`WikiCuratorConfig.max_output_tokens` line 570; `SessionRollupConfig.max_output_tokens` line 616)
- Modify `jarvis/memory/wiki/curator_llm.py` (import line 33; guard after the brain call, ~line 333)
- Modify `jarvis/memory/wiki/session_rollup.py` (import line 68; guard in `_call_brain` before the empty-text check, ~line 452)
- Modify `jarvis/memory/wiki/telemetry.py` (register counter in `DEFAULT_COUNTERS`, line 47-57)
- Create `tests/unit/brain/test_streaming_truncation.py`
- Modify `tests/unit/memory/wiki/test_curator_llm.py` (extend `FakeBrain`, add 2 tests)
- Modify `tests/unit/memory/wiki/test_session_rollup_brain_call.py` (add 1 test)

---

- [ ] **Step 1: Add the shared truncation guard to `jarvis/brain/streaming.py`.**
  Append after `tee_text` (current file ends at line 87). It matches every provider's length marker case-insensitively and falls back to sentence-final punctuation when no reason is surfaced (Codex / mocks).

  ```python
  # Append at the end of jarvis/brain/streaming.py (after tee_text), before nothing else.

  # Provider-specific finish/stop-reason markers that mean "output was cut off
  # because it hit the max-token cap" — NOT a natural stop. aggregate() does not
  # normalise these, so we match every dialect by case-insensitive substring:
  #   - Anthropic  stop_reason == "max_tokens"      (_anthropic_base.py)
  #   - OpenAI/OpenRouter/Grok finish_reason == "length" (_openai_base.py)
  #   - Gemini     str(finish_reason) in {"MAX_TOKENS", "FinishReason.MAX_TOKENS"} (gemini.py)
  _LENGTH_FINISH_MARKERS: tuple[str, ...] = ("length", "max_tokens", "max-tokens")

  # Characters a complete sentence/JSON payload may legitimately end on. Used only
  # as a fallback when the provider surfaced no finish_reason at all (e.g. Codex,
  # which hardcodes "stop", or a test/mock that omits the terminal delta).
  _SENTENCE_FINAL = frozenset('.!?…")]}』」”’')


  def is_length_truncated(finish_reason: str | None, text: str) -> bool:
      """Return True when a brain generation was cut off at the output-token cap.

      Two signals, primary then fallback:

      1. ``finish_reason`` matches a known max-token marker (any provider dialect,
         case-insensitive substring). This is authoritative when present.
      2. When ``finish_reason`` is falsy (provider did not surface one), fall back
         to a heuristic: non-empty prose that does NOT end on sentence-final
         punctuation is treated as truncated. Empty text is NOT truncated here —
         the caller handles "empty" separately.

      Deterministic, no LLM call (mirrors the scrub_for_voice latency mandate).
      """
      if finish_reason:
          lowered = finish_reason.lower()
          if any(marker in lowered for marker in _LENGTH_FINISH_MARKERS):
              return True
          # A real, non-length reason ("stop", "end_turn", "tool_use",
          # "stop_sequence", "STOP") means the model finished on its own terms.
          return False
      stripped = (text or "").strip()
      if not stripped:
          return False
      return stripped[-1] not in _SENTENCE_FINAL
  ```

  Then extend `__all__` — the module currently has no `__all__`, so add one at the very end:

  ```python
  __all__ = [
      "StreamingAggregate",
      "aggregate",
      "aggregate_with_consumer",
      "tee_text",
      "is_length_truncated",
  ]
  ```

- [ ] **Step 2: Raise the two output caps in `jarvis/core/config.py`.**
  2000 / 600 tokens is exactly what produces mid-sentence cut-offs. Raise to headroom values; the guard is the real safety net.

  In `WikiCuratorConfig` (current line 570):
  ```python
      max_output_tokens: int = 2000
  ```
  →
  ```python
      max_output_tokens: int = 4000          # raised from 2000 (truncation-fix Wave-1); guard rejects any residual length-cap
  ```

  In `SessionRollupConfig` (current line 616):
  ```python
      max_output_tokens: int = 600
  ```
  →
  ```python
      max_output_tokens: int = 1200          # raised from 600 (truncation-fix Wave-1); a 400-word paragraph needs ~700 tokens of headroom
  ```
  Both classes already carry `model_config = ConfigDict(extra="allow")`, so no migration and existing `jarvis.toml` files keep validating (AP-16).

- [ ] **Step 3: Register the new telemetry counter in `jarvis/memory/wiki/telemetry.py`.**
  Add to the `DEFAULT_COUNTERS` tuple (current lines 47-57) so a fresh `snapshot()` includes it as `0`:
  ```python
  DEFAULT_COUNTERS: tuple[str, ...] = (
      "voice_turns_seen",
      "voice_turns_ingested_ack",
      "voice_turns_ingested_aggressive",
      "wiki_context_hits",
      "wiki_context_misses",
      "session_rollups_succeeded",
      "session_rollups_failed",
      "wiki_pages_created",
      "wiki_pages_updated",
  )
  ```
  →
  ```python
  DEFAULT_COUNTERS: tuple[str, ...] = (
      "voice_turns_seen",
      "voice_turns_ingested_ack",
      "voice_turns_ingested_aggressive",
      "wiki_context_hits",
      "wiki_context_misses",
      "session_rollups_succeeded",
      "session_rollups_failed",
      "wiki_pages_created",
      "wiki_pages_updated",
      "wiki_writes_blocked_truncated",
  )
  ```

- [ ] **Step 4: Gate the curator in `jarvis/memory/wiki/curator_llm.py`.**
  Extend the existing import (current line 33):
  ```python
  from jarvis.brain.streaming import aggregate
  ```
  →
  ```python
  from jarvis.brain.streaming import aggregate, is_length_truncated
  from jarvis.memory.wiki.telemetry import telemetry
  ```
  Then insert the guard immediately after the brain-call `except` blocks finish and BEFORE `_parse_updates` is called. The current code (lines 327-336) reads:
  ```python
          except Exception as exc:                                  # noqa: BLE001
              duration_ms = (time.time_ns() - start_ns) // 1_000_000
              logger.warning(
                  "WikiCuratorLLM brain-call failed after %dms (provider=%s): %s",
                  duration_ms, self._resolved_provider, exc,
              )
              return []

          try:
              updates = _parse_updates(agg.text)
  ```
  Insert the guard between the `return []` and the `try:`:
  ```python
          except Exception as exc:                                  # noqa: BLE001
              duration_ms = (time.time_ns() - start_ns) // 1_000_000
              logger.warning(
                  "WikiCuratorLLM brain-call failed after %dms (provider=%s): %s",
                  duration_ms, self._resolved_provider, exc,
              )
              return []

          if is_length_truncated(agg.finish_reason, agg.text):
              duration_ms = (time.time_ns() - start_ns) // 1_000_000
              logger.warning(
                  "WikiCuratorLLM: response hit the output-token cap "
                  "(finish_reason=%r, %d chars, %dms, provider=%s) — discarding "
                  "truncated updates rather than persisting a half-written page",
                  agg.finish_reason, len(agg.text), duration_ms, self._resolved_provider,
              )
              telemetry.inc("wiki_writes_blocked_truncated")
              return []

          try:
              updates = _parse_updates(agg.text)
  ```

- [ ] **Step 5: Gate the session-rollup worker in `jarvis/memory/wiki/session_rollup.py`.**
  Extend the existing import (current line 68):
  ```python
  from jarvis.brain.streaming import aggregate
  ```
  →
  ```python
  from jarvis.brain.streaming import aggregate, is_length_truncated
  ```
  (`telemetry` is already imported at line 81 via `from .telemetry import telemetry`.) Then add the guard inside `_call_brain`, immediately after the aggregate completes and BEFORE the empty-text check. Current code (lines 438-457):
  ```python
          try:
              agg = await asyncio.wait_for(
                  aggregate(self._brain.complete(request)),
                  timeout=self._cfg.timeout_s,
              )
          except (asyncio.TimeoutError, TimeoutError):
              log.warning(
                  "SessionRollupWorker: brain timed out after %.1fs",
                  self._cfg.timeout_s,
              )
              return None
          except Exception as exc:    # noqa: BLE001
              log.warning("SessionRollupWorker: brain call raised: %s", exc)
              return None

          text = (agg.text or "").strip()
          if not text:
              log.warning("SessionRollupWorker: brain returned empty text")
              return None
          return text
  ```
  →
  ```python
          try:
              agg = await asyncio.wait_for(
                  aggregate(self._brain.complete(request)),
                  timeout=self._cfg.timeout_s,
              )
          except (asyncio.TimeoutError, TimeoutError):
              log.warning(
                  "SessionRollupWorker: brain timed out after %.1fs",
                  self._cfg.timeout_s,
              )
              return None
          except Exception as exc:    # noqa: BLE001
              log.warning("SessionRollupWorker: brain call raised: %s", exc)
              return None

          if is_length_truncated(agg.finish_reason, agg.text):
              log.warning(
                  "SessionRollupWorker: digest hit the output-token cap "
                  "(finish_reason=%r, %d chars) — discarding truncated paragraph "
                  "rather than writing a half-finished session page",
                  agg.finish_reason, len(agg.text or ""),
              )
              telemetry.inc("wiki_writes_blocked_truncated")
              return None

          text = (agg.text or "").strip()
          if not text:
              log.warning("SessionRollupWorker: brain returned empty text")
              return None
          return text
  ```
  Returning `None` reuses the existing failure path: `flush_session` maps it to `telemetry.inc("session_rollups_failed")` + `SessionRollupResult(status="llm_failure")` (lines 259-268) — no new status string, so no five-layer enum work.

- [ ] **Step 6: Write `tests/unit/brain/test_streaming_truncation.py` (pure-function coverage of the guard).**
  ```python
  """Unit tests for jarvis.brain.streaming.is_length_truncated.

  Pins the truncation guard that keeps a length-capped LLM generation out of
  durable memory. Covers every provider's finish-reason dialect plus the
  no-reason punctuation fallback.
  """
  from __future__ import annotations

  import pytest

  from jarvis.brain.streaming import is_length_truncated


  @pytest.mark.parametrize(
      "reason",
      ["length", "max_tokens", "MAX_TOKENS", "FinishReason.MAX_TOKENS", "max-tokens"],
  )
  def test_length_markers_are_truncated(reason: str) -> None:
      """Every provider's max-token marker is recognised, case-insensitively."""
      assert is_length_truncated(reason, "a cut off sentence with no period") is True


  @pytest.mark.parametrize("reason", ["stop", "end_turn", "tool_use", "stop_sequence", "STOP"])
  def test_natural_stop_reasons_are_not_truncated(reason: str) -> None:
      """A real stop reason means the model finished — even mid-word text is kept."""
      assert is_length_truncated(reason, "no trailing period here") is False


  def test_no_reason_incomplete_text_is_truncated() -> None:
      """No finish_reason + non-final punctuation => heuristic flags truncation."""
      assert is_length_truncated(None, "The session covered three open threads and") is True


  def test_no_reason_complete_text_is_not_truncated() -> None:
      """No finish_reason but sentence-final punctuation => treated as complete."""
      assert is_length_truncated(None, "The session wrapped up cleanly.") is False
      assert is_length_truncated("", '[{"target": "x.md"}]') is False  # JSON array close


  def test_empty_text_is_not_truncated() -> None:
      """Empty text is the caller's 'empty' case, not a truncation."""
      assert is_length_truncated(None, "") is False
      assert is_length_truncated("", "   \n ") is False
  ```
  Run:
  ```bash
  python -m pytest tests/unit/brain/test_streaming_truncation.py -q
  ```
  Expected output:
  ```
  16 passed in 0.0Ns
  ```

- [ ] **Step 7: Extend the curator `FakeBrain` and add two curator tests in `tests/unit/memory/wiki/test_curator_llm.py`.**
  The current `FakeBrain` (lines 39-66) hardcodes `finish_reason="stop"` in its terminal delta. Make the reason configurable. Replace the `__init__`/`complete` body (lines 47-66):
  ```python
      def __init__(
          self,
          response_text: str,
          *,
          sleep_s: float = 0.0,
          raise_exc: BaseException | None = None,
      ) -> None:
          self.response_text = response_text
          self.sleep_s = sleep_s
          self.raise_exc = raise_exc
          self.received_requests: list[BrainRequest] = []

      async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
          self.received_requests.append(req)
          if self.sleep_s:
              await asyncio.sleep(self.sleep_s)
          if self.raise_exc is not None:
              raise self.raise_exc
          yield BrainDelta(content=self.response_text)
          yield BrainDelta(finish_reason="stop", usage={"input_tokens": 10, "output_tokens": 20})
  ```
  →
  ```python
      def __init__(
          self,
          response_text: str,
          *,
          sleep_s: float = 0.0,
          raise_exc: BaseException | None = None,
          finish_reason: str = "stop",
      ) -> None:
          self.response_text = response_text
          self.sleep_s = sleep_s
          self.raise_exc = raise_exc
          self.finish_reason = finish_reason
          self.received_requests: list[BrainRequest] = []

      async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
          self.received_requests.append(req)
          if self.sleep_s:
              await asyncio.sleep(self.sleep_s)
          if self.raise_exc is not None:
              raise self.raise_exc
          yield BrainDelta(content=self.response_text)
          yield BrainDelta(
              finish_reason=self.finish_reason,
              usage={"input_tokens": 10, "output_tokens": 20},
          )
  ```
  Then append two tests at the end of the file (after `test_resolve_provider_returns_none_model_when_unknown`):
  ```python
  @pytest.mark.asyncio
  async def test_propose_updates_rejects_length_capped_response(
      tmp_path: Path, caplog: pytest.LogCaptureFixture,
  ) -> None:
      """A response that hit the output-token cap is discarded, not persisted."""

      cfg = _make_config()
      # Well-formed JSON array, but the stream reports it was cut off at the cap.
      brain = FakeBrain(_ok_response(), finish_reason="length")
      registry = FakeRegistry(brain)

      llm = WikiCuratorLLM(
          config=cfg,
          schema_path=_write_schema(tmp_path),
          registry=registry,
      )
      caplog.set_level("WARNING")
      result = await llm.propose_updates(
          "real source text", "label", repo=FakeRepo(), vault=FakeVault(),
      )
      assert result == []
      assert any("output-token cap" in rec.message.lower() for rec in caplog.records)


  @pytest.mark.asyncio
  async def test_propose_updates_accepts_naturally_stopped_response(
      tmp_path: Path,
  ) -> None:
      """A complete response (finish_reason='stop') still writes normally."""

      cfg = _make_config()
      brain = FakeBrain(_ok_response(), finish_reason="stop")
      registry = FakeRegistry(brain)

      llm = WikiCuratorLLM(
          config=cfg,
          schema_path=_write_schema(tmp_path),
          registry=registry,
      )
      updates = await llm.propose_updates(
          "real source text", "label", repo=FakeRepo(), vault=FakeVault(),
      )
      assert len(updates) == 1
  ```
  Run:
  ```bash
  python -m pytest tests/unit/memory/wiki/test_curator_llm.py -q
  ```
  Expected output (28 prior + 2 new):
  ```
  30 passed in 0.Ns
  ```

- [ ] **Step 8: Add one session-rollup truncation test in `tests/unit/memory/wiki/test_session_rollup_brain_call.py`.**
  Its `FakeBrain` already takes an explicit `deltas` list, so pass a truncated terminal delta directly. Append after the last test in the file, reusing whatever worker fixture the existing happy-path test uses (the file's fixtures build a real `SessionRollupWorker` + `RecallStore`; mirror the happy-path test's setup verbatim and only swap the brain + assert no page). Use this self-contained form:
  ```python
  @pytest.mark.asyncio
  async def test_rollup_rejects_length_capped_digest(
      rollup_worker: SessionRollupWorker,
      seed_episodes,  # noqa: ANN001 — same fixture the happy-path test consumes
  ) -> None:
      """A digest that hit the output-token cap is discarded; no page is written."""
      from jarvis.core.protocols import BrainDelta

      # Inject a brain whose stream ends with a length-cap finish_reason.
      rollup_worker._brain = FakeBrain(  # noqa: SLF001 — test reaches into the worker
          deltas=[
              BrainDelta(content="The session focused on the wiki guard and"),
              BrainDelta(finish_reason="length"),
          ],
      )
      result = await rollup_worker.flush_session()
      assert result.status == "llm_failure"
      assert result.page_path is None
  ```
  > If the file does not already expose `rollup_worker` / `seed_episodes` fixtures, copy the worker-construction + episode-seeding block from the existing happy-path test in this same file inline into this test instead (do not invent new fixture names). The load-bearing assertions are `status == "llm_failure"` and `page_path is None`.

  Run:
  ```bash
  python -m pytest tests/unit/memory/wiki/test_session_rollup_brain_call.py -q
  ```
  Expected: all prior tests + 1 new, `passed`.

- [ ] **Step 9: Run the full affected suite + lint, confirm green.**
  ```bash
  python -m pytest tests/unit/brain/test_streaming_truncation.py tests/unit/memory/wiki/test_curator_llm.py tests/unit/memory/wiki/test_session_rollup_brain_call.py tests/unit/memory/wiki/test_telemetry.py -q
  ruff check jarvis/brain/streaming.py jarvis/memory/wiki/curator_llm.py jarvis/memory/wiki/session_rollup.py jarvis/core/config.py jarvis/memory/wiki/telemetry.py
  ```
  Expected: pytest line ends `passed` with no failures; `ruff` prints `All checks passed!`.

- [ ] **Step 10: Commit.**
  ```bash
  git add jarvis/brain/streaming.py jarvis/core/config.py jarvis/memory/wiki/curator_llm.py jarvis/memory/wiki/session_rollup.py jarvis/memory/wiki/telemetry.py tests/unit/brain/test_streaming_truncation.py tests/unit/memory/wiki/test_curator_llm.py tests/unit/memory/wiki/test_session_rollup_brain_call.py
  git commit -m "fix(wiki): discard length-capped LLM output instead of persisting mid-sentence truncation

Curator and session-rollup writers now reject any generation that hit the
output-token cap (finish_reason length/max_tokens/MAX_TOKENS, with a
sentence-final-punctuation fallback when no reason is surfaced) and return
[]/None rather than writing a half-finished wiki page or session digest.
Raises the two stingy caps (curator 2000->4000, rollup 600->1200) and adds
the wiki_writes_blocked_truncated telemetry counter.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas
- **Provider finish-reason dialects are NOT normalized by `aggregate()`** — Anthropic `max_tokens`, OpenAI/OpenRouter/Grok `length`, Gemini `MAX_TOKENS`/`FinishReason.MAX_TOKENS`, Codex hardcodes `stop`. The helper matches all length markers by case-insensitive substring and adds a punctuation fallback for the no-reason case. Verified against `_anthropic_base.py:218,228`, `_openai_base.py:251,265`, `gemini.py:584-586`, `codex.py:284`.
- **`aggregate()` keeps only the last non-empty `finish_reason`** (`streaming.py:41-42`) — correct here; read `agg.finish_reason`, never re-scan deltas.
- **Reuse the existing failure path, don't add a status** — the rollup worker already maps `_call_brain` returning `None` to `status="llm_failure"` + `session_rollups_failed`. Returning `None` before the empty-text check avoids any five-layer enum work for `RollupStatus`.
- **`session_rollup.py` already imports `_resolve_provider_and_model` from `curator_llm`** (line 72). Keep importing `is_length_truncated` from `jarvis.brain.streaming` in BOTH modules so there is one source of truth and no new cross-module dependency.
- **Both config classes carry `ConfigDict(extra="allow")`** (config.py:565,608) — raising the int defaults needs no migration; existing `jarvis.toml` validates unchanged (AP-16).
- **Dirty working tree:** none of the five touched runtime files (`jarvis/brain/streaming.py`, `jarvis/core/config.py`, `jarvis/memory/wiki/{curator_llm,session_rollup,telemetry}.py`) appear in the current `git status` M-list, and the streaming helper is purely additive (appended after `tee_text`), so a parallel session merges cleanly. Only `tests/unit/audio/test_player_welle2.py` etc. are dirty, which this task does not touch.

---

### Task 6: Enforce create-or-refuse wikilink rule in the curator write path (demote dangling links to plain text)

The vault schema (`wiki/obsidian-vault/schema.md:148`) is binding: *"A broken wikilink is a bug. The wiki curator MUST either create the missing page during the same ingest, or refuse the link and use plain text. Never leave dangling `[[]]`."* The session-rollup worker enforces this (`session_rollup.py:603-604` runs `strip_dangling_wikilinks` + `rewrite_body_links`), but the **curator ingest path does not** — `WikiCurator.ingest` (`curator.py:160-173`) anchors paths then calls `writer.apply(updates)` with the raw LLM `new_body`, so any unresolvable `[[App]]` the curator-LLM emits lands on disk as a dangling Obsidian orphan. This task closes that gap with a deterministic (regex, no LLM) demotion pass reusing the existing `session_links` helpers, and increments the telemetry counter `wiki_links_refused_dangling`.

**Files:**
- **Modify:** `jarvis/memory/wiki/curator.py` (imports near L30-43; `ingest` body L160-173; add `_demote_dangling_links` + a durable-page scan helper after `_anchor_to_vault` ~L226)
- **Modify:** `jarvis/memory/wiki/telemetry.py` (`DEFAULT_COUNTERS` tuple L47-57)
- **Test:** `tests/integration/memory/wiki/test_curator_dangling_links.py` (new file; mirrors the `real_stack` fixture in `tests/integration/memory/wiki/test_curator_ingest_e2e.py`)

---

- [ ] **Step 1: Confirm the working tree is clean for the two files you will edit.**
  Run:
  ```
  git status --short jarvis/memory/wiki/curator.py jarvis/memory/wiki/telemetry.py
  ```
  Expected output: **empty** (no `M`/`??` lines). If either file shows a modification, a parallel session is editing the same anchors (`ingest` L160-173, `DEFAULT_COUNTERS` L47-57) — stop and reconcile before continuing.

- [ ] **Step 2: Register the new telemetry counter so the dashboard JSON shape stays stable.**
  In `jarvis/memory/wiki/telemetry.py`, edit the `DEFAULT_COUNTERS` tuple (currently L47-57). Replace:
  ```python
  DEFAULT_COUNTERS: tuple[str, ...] = (
      "voice_turns_seen",
      "voice_turns_ingested_ack",
      "voice_turns_ingested_aggressive",
      "wiki_context_hits",
      "wiki_context_misses",
      "session_rollups_succeeded",
      "session_rollups_failed",
      "wiki_pages_created",
      "wiki_pages_updated",
  )
  ```
  with:
  ```python
  DEFAULT_COUNTERS: tuple[str, ...] = (
      "voice_turns_seen",
      "voice_turns_ingested_ack",
      "voice_turns_ingested_aggressive",
      "wiki_context_hits",
      "wiki_context_misses",
      "session_rollups_succeeded",
      "session_rollups_failed",
      "wiki_pages_created",
      "wiki_pages_updated",
      # Number of [[wikilinks]] the curator demoted to plain text because
      # they resolved to no existing (or same-batch-created) page — the
      # schema.md:148 "create-or-refuse, never dangling" rule, enforced
      # deterministically in WikiCurator._demote_dangling_links.
      "wiki_links_refused_dangling",
  )
  ```

- [ ] **Step 3: Add the imports the curator needs for the demotion pass.**
  In `jarvis/memory/wiki/curator.py`, the current import block (L30-43) is:
  ```python
  from __future__ import annotations

  import logging
  from pathlib import Path

  from .log_writer import LogWriter
  from .protocols import (
      AtomicWriter,
      CuratorLLM,
      PageRepository,
      PageUpdate,
      VaultIndex,
      WriteResult,
  )
  ```
  Add the `re` stdlib import and the `session_links` + `telemetry` imports. Replace that block with:
  ```python
  from __future__ import annotations

  import logging
  import re
  from pathlib import Path

  from .log_writer import LogWriter
  from .protocols import (
      AtomicWriter,
      CuratorLLM,
      PageRepository,
      PageUpdate,
      VaultIndex,
      WriteResult,
  )
  from .session_links import SlugIndex, rewrite_body_links, strip_dangling_wikilinks
  from .telemetry import telemetry
  ```

- [ ] **Step 4: Wire the demotion pass into `ingest`, between anchoring and the writer call.**
  In `jarvis/memory/wiki/curator.py`, the current `ingest` section (L160-173) reads:
  ```python
          updates = [self._anchor_to_vault(u) for u in updates]

          if not updates:
              log.debug(
                  "WikiCurator: LLM proposed no updates for %r (salience filter or empty source)",
                  source_label,
              )
              return _empty_result(self._writer.backup_manager.backup_dir)

          # ----- 2. hand the proposal to the writer ----------------------
          # The writer takes the snapshot, applies each update via
          # tempfile+rename, re-validates each written page through repo,
          # and rolls back individual pages that fail validation.
          result = await self._writer.apply(updates, repo=self._repo)
  ```
  Replace it with (inserts the demotion step between the empty-guard and `writer.apply`):
  ```python
          updates = [self._anchor_to_vault(u) for u in updates]

          if not updates:
              log.debug(
                  "WikiCurator: LLM proposed no updates for %r (salience filter or empty source)",
                  source_label,
              )
              return _empty_result(self._writer.backup_manager.backup_dir)

          # ----- 1b. enforce the schema's create-or-refuse link rule -----
          # schema.md:148 — a [[wikilink]] that resolves to no existing page
          # (or no page created in THIS same batch) must be demoted to plain
          # text, never left dangling. Deterministic, regex only, no LLM,
          # no I/O — mirrors the session-rollup post-pass.
          updates = self._demote_dangling_links(updates)

          # ----- 2. hand the proposal to the writer ----------------------
          # The writer takes the snapshot, applies each update via
          # tempfile+rename, re-validates each written page through repo,
          # and rolls back individual pages that fail validation.
          result = await self._writer.apply(updates, repo=self._repo)
  ```

- [ ] **Step 5: Implement `_demote_dangling_links` plus its same-batch-aware durable-page scan, in the Internals section.**
  In `jarvis/memory/wiki/curator.py`, the `_anchor_to_vault` method ends at L225 (`return PageUpdate(...)`). Immediately after it (before `_summarise` at L227), insert these two methods:
  ```python
      def _demote_dangling_links(
          self, updates: list[PageUpdate]
      ) -> list[PageUpdate]:
          """Rewrite each update's body so no ``[[wikilink]]`` is left dangling.

          For every update, strips token-truncated ``[[`` fragments and then
          canonicalises resolvable links / demotes unresolvable ones to plain
          text (``session_links.rewrite_body_links``). "Resolvable" means the
          target maps to an existing durable vault page OR to a page being
          created/renamed in THIS same batch — the schema's "create the
          missing page during the same ingest" arm of the rule. Returns a new
          list of ``PageUpdate`` objects with cleaned bodies; updates whose
          body did not change are passed through unmodified. Increments
          ``wiki_links_refused_dangling`` once per demoted link.

          Pure: regex only, no LLM call, no disk write (AP-9/AP-11).
          """
          index = self._build_batch_slug_index(updates)
          cleaned: list[PageUpdate] = []
          for upd in updates:
              before_links = len(_WIKILINK_RE.findall(upd.new_body))
              body = strip_dangling_wikilinks(upd.new_body)
              body, resolved = rewrite_body_links(body, index)
              # Every closed link either survived as [[...]] (resolved) or was
              # demoted to plain text; the difference is the refusal count.
              after_links = len(_WIKILINK_RE.findall(body))
              refused = before_links - after_links
              if refused > 0:
                  telemetry.inc("wiki_links_refused_dangling", refused)
              if body == upd.new_body:
                  cleaned.append(upd)
                  continue
              cleaned.append(
                  PageUpdate(
                      target_path=upd.target_path,
                      operation=upd.operation,
                      new_body=body,
                      rename_from=upd.rename_from,
                      reason=upd.reason,
                  )
              )
          return cleaned

      def _build_batch_slug_index(self, updates: list[PageUpdate]) -> SlugIndex:
          """Build a :class:`SlugIndex` of every page a link may resolve to.

          Combines (a) the durable pages already on disk
          (``entities/`` ``concepts/`` ``projects/`` ``sessions/``) with
          (b) the slugs of pages this batch creates or renames into existence,
          so a sibling page born in the same ingest counts as "existing" and
          its link is preserved rather than refused. Slug is the filename stem
          relative to the vault root; the directory is its first path segment.
          """
          pages: list[tuple[str, str, list[str]]] = []
          for directory in ("entities", "concepts", "projects", "sessions"):
              page_dir = self._vault_root / directory
              if not page_dir.is_dir():
                  continue
              for md_path in sorted(page_dir.glob("*.md")):
                  if md_path.name.startswith("."):
                      continue
                  pages.append((directory, md_path.stem, []))
          # Same-batch creations/renames resolve as if they already exist.
          for upd in updates:
              if upd.operation not in ("create", "rename"):
                  continue
              try:
                  rel = upd.target_path.resolve().relative_to(self._vault_root)
              except ValueError:
                  continue
              parts = rel.with_suffix("").parts
              if len(parts) >= 2:
                  pages.append((parts[0], parts[-1], []))
          return SlugIndex.from_pages(pages)
  ```
  Then, at the top of the module right after `log = logging.getLogger(__name__)` (currently L45), add the shared wikilink regex used by the counter math:
  ```python
  # Closed, non-escaped wikilink; inner group forbids brackets/newlines so a
  # count never absorbs adjacent tokens. Mirrors session_links._WIKILINK_RE.
  _WIKILINK_RE = re.compile(r"(?<!\\)\[\[([^\[\]\n]+)\]\]")
  ```

- [ ] **Step 6: Create the integration test proving demotion + counter + preservation.**
  Create `tests/integration/memory/wiki/test_curator_dangling_links.py` with this exact content (reuses the `real_stack` fixture shape from `test_curator_ingest_e2e.py`; the only mocked boundary is the curator-LLM brain call, per repo convention for the external LLM):
  ```python
  """Curator enforces the schema.md:148 create-or-refuse wikilink rule.

  An unresolvable ``[[App]]`` in a proposed body must be written with the
  link demoted to plain text and ``wiki_links_refused_dangling`` bumped; a
  link that resolves to an existing durable page must survive verbatim.
  """
  from __future__ import annotations

  from pathlib import Path
  from unittest.mock import patch

  import pytest
  import pytest_asyncio

  from jarvis.memory.wiki.atomic_writer import AtomicWriter
  from jarvis.memory.wiki.curator import WikiCurator
  from jarvis.memory.wiki.curator_llm import WikiCuratorLLM
  from jarvis.memory.wiki.log_writer import LogWriter
  from jarvis.memory.wiki.page import MarkdownPageRepository
  from jarvis.memory.wiki.protocols import PageUpdate
  from jarvis.memory.wiki.telemetry import get_telemetry
  from jarvis.memory.wiki.vault_index import VaultIndex


  def _entity_body(slug: str, body_line: str) -> str:
      return (
          "---\n"
          "type: entity\n"
          "entity_kind: person\n"
          f"slug: {slug}\n"
          "aliases: []\n"
          "created: 2026-06-09\n"
          "updated: 2026-06-09\n"
          "---\n"
          "\n"
          f"# {slug.title()}\n"
          "\n"
          "## Summary\n"
          "\n"
          f"{body_line}\n"
          "\n"
          "## Facts\n"
          "\n"
          "- TODO\n"
          "\n"
          "## Relationships\n"
          "\n"
          "- TODO\n"
          "\n"
          "## Sources\n"
          "\n"
          "- dangling-link fixture\n"
      )


  @pytest_asyncio.fixture
  async def real_stack(tmp_path: Path):
      vault_root = tmp_path / "workspace"
      for sub in ("entities", "concepts", "projects", "sessions", "_archive", "attachments"):
          (vault_root / sub).mkdir(parents=True)
      (vault_root / "schema.md").write_text("# stub schema\n", encoding="utf-8")
      (vault_root / "index.md").write_text("# Index\n", encoding="utf-8")
      (vault_root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")

      # A durable page that a resolvable link can point at.
      (vault_root / "entities" / "alex.md").write_text(
          _entity_body("alex", "Profile body for Alex."),
          encoding="utf-8",
      )

      backup_dir = tmp_path / "backups"
      repo = MarkdownPageRepository()
      vault = VaultIndex(repo=repo)
      await vault.scan(vault_root)
      writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
      log_writer = LogWriter(log_path=vault_root / "log.md")
      llm = WikiCuratorLLM.__new__(WikiCuratorLLM)

      curator = WikiCurator(
          repo=repo,
          vault=vault,
          writer=writer,
          llm=llm,
          log_writer=log_writer,
          vault_root=vault_root,
      )
      return curator, vault_root


  @pytest.mark.asyncio
  async def test_unresolvable_link_is_demoted_and_counter_bumped(real_stack):
      """``[[App]]`` (no page) → plain text ``App``; counter incremented."""
      curator, vault_root = real_stack
      telemetry = get_telemetry()
      before = telemetry.get("wiki_links_refused_dangling")

      proposed = [
          PageUpdate(
              target_path=vault_root / "concepts" / "morning-routine.md",
              operation="create",
              new_body=(
                  "---\n"
                  "type: concept\n"
                  "slug: morning-routine\n"
                  "aliases: []\n"
                  "created: 2026-06-09\n"
                  "updated: 2026-06-09\n"
                  "---\n"
                  "\n"
                  "# Morning Routine\n"
                  "\n"
                  "## Summary\n"
                  "\n"
                  "The routine opens [[App]] every day to start work.\n"
              ),
              reason="new concept with a ghost link",
          ),
      ]

      with patch.object(curator._llm, "propose_updates", return_value=proposed):
          result = await curator.ingest(
              source_content="The morning routine opens an app.",
              source_label="cli-ingest:routine.md",
          )

      assert len(result.applied) == 1
      content = (vault_root / "concepts" / "morning-routine.md").read_text(
          encoding="utf-8"
      )
      # The bracket form is gone; the display word survives as plain text.
      assert "[[App]]" not in content
      assert "opens App every day" in content
      # Exactly one link refused.
      assert telemetry.get("wiki_links_refused_dangling") == before + 1


  @pytest.mark.asyncio
  async def test_resolvable_link_is_preserved(real_stack):
      """A link to an existing durable page survives; counter untouched."""
      curator, vault_root = real_stack
      telemetry = get_telemetry()
      before = telemetry.get("wiki_links_refused_dangling")

      proposed = [
          PageUpdate(
              target_path=vault_root / "concepts" / "user-context.md",
              operation="create",
              new_body=(
                  "---\n"
                  "type: concept\n"
                  "slug: user-context\n"
                  "aliases: []\n"
                  "created: 2026-06-09\n"
                  "updated: 2026-06-09\n"
                  "---\n"
                  "\n"
                  "# User Context\n"
                  "\n"
                  "## Summary\n"
                  "\n"
                  "This concept concerns [[alex]] directly.\n"
              ),
              reason="concept linking the existing user entity",
          ),
      ]

      with patch.object(curator._llm, "propose_updates", return_value=proposed):
          result = await curator.ingest(
              source_content="A concept about the user.",
              source_label="cli-ingest:context.md",
          )

      assert len(result.applied) == 1
      content = (vault_root / "concepts" / "user-context.md").read_text(
          encoding="utf-8"
      )
      # Resolvable link is canonicalised to the typed form, never demoted.
      assert "[[entities/alex]]" in content
      assert telemetry.get("wiki_links_refused_dangling") == before
  ```

- [ ] **Step 7: Run the new test file and confirm both cases pass.**
  Run:
  ```
  py -3.11 -m pytest tests/integration/memory/wiki/test_curator_dangling_links.py -v
  ```
  Expected output: `2 passed` — `test_unresolvable_link_is_demoted_and_counter_bumped PASSED` and `test_resolvable_link_is_preserved PASSED`.

- [ ] **Step 8: Run the existing curator + telemetry suites to confirm no regression.**
  Run:
  ```
  py -3.11 -m pytest tests/integration/memory/wiki/test_curator_ingest_e2e.py tests/unit/memory/wiki/test_telemetry.py -q
  ```
  Expected output: all tests pass (the e2e ingest's two `create` pages carry no wikilinks, so the demotion pass is a no-op there; `DEFAULT_COUNTERS` now lists `wiki_links_refused_dangling` at 0 in a fresh snapshot).

- [ ] **Step 9: Lint the two changed modules.**
  Run:
  ```
  py -3.11 -m ruff check jarvis/memory/wiki/curator.py jarvis/memory/wiki/telemetry.py
  ```
  Expected output: `All checks passed!`

- [ ] **Step 10: Commit.**
  Run:
  ```
  git add jarvis/memory/wiki/curator.py jarvis/memory/wiki/telemetry.py tests/integration/memory/wiki/test_curator_dangling_links.py
  git commit -m "fix(wiki): demote dangling [[links]] to plain text in the curator write path

The curator ingest path wrote the LLM body straight to AtomicWriter without
the session-rollup link post-pass, so an unresolvable [[App]] landed on disk
as an Obsidian orphan — violating schema.md:148 (create-or-refuse, never
dangling). Apply a deterministic regex pass before apply(): links resolving
to an existing durable page or a same-batch creation are kept/canonicalised,
the rest are demoted to plain text. Count refusals in wiki_links_refused_dangling.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas
- Run **Step 1** first: a parallel session editing `curator.py` (anchors `ingest` L160-173, Internals after `_anchor_to_vault` ~L225) or `telemetry.py` (`DEFAULT_COUNTERS` L47-57) will conflict on the exact lines this task touches.
- `PageUpdate.new_body` is the **full rendered page including frontmatter**. `rewrite_body_links` only rewrites `[[...]]` tokens and schema frontmatter never contains wikilinks, so running it over the whole body is correct — do not split frontmatter out.
- The `SlugIndex` is built from existing durable pages **plus** this batch's `create`/`rename` targets, so a link to a sibling page born in the same ingest is preserved (the "create the missing page during the same ingest" arm of schema.md:148), not wrongly refused.
- The pass is regex-only, no `await`, no I/O beyond the bounded synchronous `glob` of the four page dirs — keep it off any LLM call (AP-9/AP-11). The curator ingest path is not the voice critical path, so the synchronous dir scan is acceptable (same as `SessionRollupWorker._scan_durable_pages`).
- The counter is incremented **once per demoted link** (`before_links - after_links`), reflecting schema-violation volume — not once per page.

---

### Task 7: Wave-1: No-PII/no-secret write validator (AP-2) — block secret-shaped page bodies at AtomicWriter write time

A new **pure-function** regex detector (`secret_guard.py`) plus a hook in the AtomicWriter pipeline: any `PageUpdate` whose `new_body` matches a credential/secret shape (API-key shapes, `sk-…`, bearer tokens, `password: …`, long hex/base64 secrets) is **dropped before write** and surfaced in a new `WriteResult.blocked_pii` set, incrementing telemetry `wiki_writes_blocked_pii`. Regex-only, no LLM (AP-11 spirit), mirroring the existing `jarvis/brain/output_filter.py` guard style. Because the hook lives in `AtomicWriter._apply_sync` (the single disk-write surface for every caller — curator, session-rollup, voice-bridge ingest), it covers all write paths, not just the curator.

**Files:**
- **Create:** `jarvis/memory/wiki/secret_guard.py` (new module, ~95 lines)
- **Modify:** `jarvis/memory/wiki/protocols.py` (`WriteResult` dataclass, lines 54-60 — add `blocked_pii` field)
- **Modify:** `jarvis/memory/wiki/atomic_writer.py` (imports ~line 48-54; per-update lock loop in `_apply_sync` ~lines 183-263; both `WriteResult(...)` returns at ~258 and ~374)
- **Test (new):** `tests/unit/memory/wiki/test_secret_guard.py` (pure detector)
- **Test (modify):** `tests/unit/memory/wiki/test_atomic_writer.py` (add two pipeline tests: API-key body blocked, normal body passes)

---

- [ ] **Step 1: Create the pure-function secret detector module.**

  Create `jarvis/memory/wiki/secret_guard.py`. Regex-only, no imports from `jarvis.*`, no LLM. The patterns mirror `PROVIDER_SECRET_CANDIDATES` shapes in `jarvis/core/config.py` (OpenAI `sk-`, bearer, Google/xAI keys) and the long-base64/hex guard style from `jarvis/brain/output_filter.py` (`LONG_BASE64_RE`).

  ```python
  """Regex-only secret/PII guard for wiki page bodies (AP-2).

  Wiki pages now persist deliberately (the curator's ``create``/``update``
  operations land on disk and are full-text indexed). A page body that
  contains an API key, bearer token, password, or other long opaque
  credential must never be written: it would leak the secret into the
  vault, the FTS index, and any ``wiki-recall`` voice readback.

  This module is the deterministic, **regex-only** gate (no LLM call —
  the write path must stay fast and offline; cf. AP-11 on the voice
  path). It is a pure function: ``contains_secret(body) -> bool`` plus a
  diagnostic ``find_secrets(body) -> list[str]`` returning the names of
  the patterns that fired (for logging, never the matched value).

  The patterns mirror the credential shapes enumerated in
  ``jarvis/core/config.py`` (``PROVIDER_SECRET_CANDIDATES``: OpenAI
  ``sk-``/``sk-proj-``, Google/xAI keys, bearer tokens) and the
  long-base64 guard in ``jarvis/brain/output_filter.py``
  (``LONG_BASE64_RE``).

  Deliberately conservative: a few prose words ("the password is on the
  sticky note") trip the ``password:`` rule only when followed by a
  value-shaped token, so ordinary biographical notes pass. False
  positives are cheaper than a leaked credential — a blocked page is
  reported, never silently dropped.
  """
  from __future__ import annotations

  import re

  # --- credential shapes -------------------------------------------------
  # 1) OpenAI-style keys: sk-, sk-proj-, sk-ant-, etc. >=20 trailing chars.
  _OPENAI_KEY_RE = re.compile(r"\bsk-(?:proj-|ant-|or-|live-)?[A-Za-z0-9_-]{20,}\b")
  # 2) Generic provider keys: AIza… (Google), xai-…, gsk_… (Groq), ghp_/gho_ (GitHub).
  _PROVIDER_KEY_RE = re.compile(
      r"\b(?:AIza[0-9A-Za-z_-]{30,}"
      r"|xai-[A-Za-z0-9]{20,}"
      r"|gsk_[A-Za-z0-9]{20,}"
      r"|gh[pousr]_[A-Za-z0-9]{30,})\b"
  )
  # 3) Bearer / Authorization tokens.
  _BEARER_RE = re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._\-]{16,}\b")
  _AUTH_HEADER_RE = re.compile(
      r"(?im)^\s*authorization\s*[:=]\s*\S{12,}\s*$"
  )
  # 4) Inline "api_key = …" / "password: …" / "secret = …" / "token: …"
  #    with a value-shaped token (>=8 non-space chars) right after.
  _LABELLED_SECRET_RE = re.compile(
      r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|password|passwd|pwd|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
      r"\s*[:=]\s*"
      r"['\"]?[^\s'\"]{8,}"
  )
  # 5) JWT (three base64url segments separated by dots).
  _JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")
  # 6) Long opaque secrets: >=40 contiguous hex, or >=64 contiguous base64.
  #    Mirrors output_filter.LONG_BASE64_RE (>=200) but tighter, because a
  #    page body should never legitimately contain such a run.
  _LONG_HEX_RE = re.compile(r"\b[0-9a-fA-F]{40,}\b")
  _LONG_B64_RE = re.compile(r"\b[A-Za-z0-9+/]{64,}={0,2}\b")
  # 7) Private-key PEM headers.
  _PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")

  _PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
      ("openai_key", _OPENAI_KEY_RE),
      ("provider_key", _PROVIDER_KEY_RE),
      ("bearer_token", _BEARER_RE),
      ("authorization_header", _AUTH_HEADER_RE),
      ("labelled_secret", _LABELLED_SECRET_RE),
      ("jwt", _JWT_RE),
      ("pem_private_key", _PEM_RE),
      ("long_hex_secret", _LONG_HEX_RE),
      ("long_base64_secret", _LONG_B64_RE),
  )


  def find_secrets(body: str) -> list[str]:
      """Return the names of every secret pattern that matches ``body``.

      Pure function. Returns the *pattern names* (e.g. ``"openai_key"``),
      never the matched substring — the caller logs these names without
      ever echoing the credential itself.
      """
      if not body:
          return []
      return [name for name, pat in _PATTERNS if pat.search(body)]


  def contains_secret(body: str) -> bool:
      """``True`` if ``body`` matches any credential/secret shape."""
      if not body:
          return False
      return any(pat.search(body) for _, pat in _PATTERNS)


  __all__ = ["contains_secret", "find_secrets"]
  ```

- [ ] **Step 2: Add the `blocked_pii` field to `WriteResult`.**

  In `jarvis/memory/wiki/protocols.py`, the current dataclass (lines 54-60) is:

  ```python
  @dataclass(frozen=True, slots=True)
  class WriteResult:
      """Returned by AtomicWriter.apply()."""
      applied: list[Path]                     # pages that were successfully written
      skipped_due_to_recent_edit: list[Path]  # the 30s-lock case
      failed_validation: list[Path]           # pages that the writer rolled back
      backup_path: Path                       # the tar of the pre-write state
  ```

  Add a fifth field **with a default** so existing construction sites (`curator.py:76`, `atomic_writer.py:258`) keep compiling. `field` must be imported — check the top of `protocols.py`; if `from dataclasses import dataclass, field` is not already present, change the existing `from dataclasses import dataclass` line to include `field`. Then:

  ```python
  @dataclass(frozen=True, slots=True)
  class WriteResult:
      """Returned by AtomicWriter.apply()."""
      applied: list[Path]                     # pages that were successfully written
      skipped_due_to_recent_edit: list[Path]  # the 30s-lock case
      failed_validation: list[Path]           # pages that the writer rolled back
      backup_path: Path                       # the tar of the pre-write state
      blocked_pii: list[Path] = field(default_factory=list)  # refused: body matched a secret/PII shape (AP-2)
  ```

- [ ] **Step 3: Import the guard + telemetry into the writer (already imported).**

  In `jarvis/memory/wiki/atomic_writer.py`, `telemetry` is already imported (line 54: `from .telemetry import telemetry`). Add the guard import right after the protocols import (line 53). Current lines 53-54:

  ```python
  from .protocols import PageRepository, PageUpdate, WriteResult
  from .telemetry import telemetry
  ```

  Replace with:

  ```python
  from .protocols import PageRepository, PageUpdate, WriteResult
  from .secret_guard import find_secrets
  from .telemetry import telemetry
  ```

- [ ] **Step 4: Hook the guard into the per-update lock loop in `_apply_sync`.**

  In `atomic_writer.py`, the loop at lines 183-263 walks each update, resolves the target, runs the 30s lock, and appends survivors to `pending`. Add a `blocked` list next to `skipped` and a secret check **before** the lock/`pending.append` (a secret body must be refused even if the file was recently edited).

  Current opening of the loop (lines 182-186):

  ```python
          # ----- Step 1: 30s concurrent-edit lock --------------------------
          pending: list[_PendingWrite] = []
          skipped: list[Path] = []
          now = self._clock()
          for upd in updates:
              target = upd.target_path.resolve()
  ```

  Replace with (adds `blocked` list + the secret check at the top of the loop body):

  ```python
          # ----- Step 1: 30s concurrent-edit lock --------------------------
          pending: list[_PendingWrite] = []
          skipped: list[Path] = []
          blocked: list[Path] = []
          now = self._clock()
          for upd in updates:
              target = upd.target_path.resolve()

              # ----- Step 0.5: secret/PII guard (AP-2) ---------------------
              # A body that contains an API key, bearer token, password, or
              # other opaque credential must never reach disk or the FTS
              # index. Regex-only, no LLM. Archive ops carry no meaningful
              # body, so only create/update/rename are screened. We log the
              # pattern *names* only, never the matched value.
              if upd.operation != "archive":
                  hits = find_secrets(upd.new_body)
                  if hits:
                      log.warning(
                          "atomic_writer: refusing write to %s — body matched "
                          "secret/PII patterns %s (AP-2)",
                          target,
                          hits,
                      )
                      telemetry.inc("wiki_writes_blocked_pii")
                      blocked.append(target)
                      continue
  ```

  > Note: keep the existing `try: target.relative_to(self._vault_root)` block (lines 188-194) exactly as it is — it stays directly below the new guard. The new block is inserted between `target = upd.target_path.resolve()` and the `try:`.

- [ ] **Step 5: Thread `blocked` into both `WriteResult` returns.**

  Still in `_apply_sync`, the "nothing survived" early return (lines 257-263) and the final return (lines 374-379) must report `blocked_pii`.

  First return — current (lines 257-263):

  ```python
          if not pending:
              return WriteResult(
                  applied=[],
                  skipped_due_to_recent_edit=skipped,
                  failed_validation=[],
                  backup_path=Path(),
              )
  ```

  Replace with:

  ```python
          if not pending:
              return WriteResult(
                  applied=[],
                  skipped_due_to_recent_edit=skipped,
                  failed_validation=[],
                  backup_path=Path(),
                  blocked_pii=blocked,
              )
  ```

  Final return — current (lines 374-379):

  ```python
          return WriteResult(
              applied=applied,
              skipped_due_to_recent_edit=skipped,
              failed_validation=failed_validation,
              backup_path=backup_path,
          )
  ```

  Replace with:

  ```python
          return WriteResult(
              applied=applied,
              skipped_due_to_recent_edit=skipped,
              failed_validation=failed_validation,
              backup_path=backup_path,
              blocked_pii=blocked,
          )
  ```

- [ ] **Step 6: Write the pure-detector unit test.**

  Create `tests/unit/memory/wiki/test_secret_guard.py`:

  ```python
  """Unit tests for ``jarvis.memory.wiki.secret_guard`` (AP-2).

  The guard is a pure, regex-only function: a body that contains a
  credential shape (API key, bearer token, password, JWT, PEM, long
  opaque hex/base64) is reported; ordinary prose passes untouched.
  """
  from __future__ import annotations

  import pytest

  from jarvis.memory.wiki.secret_guard import contains_secret, find_secrets


  @pytest.mark.parametrize(
      "body",
      [
          "The deploy key is sk-proj-AbCdEf0123456789AbCdEf0123456789",
          "openai key: sk-AbCdEf0123456789AbCdEf0123",
          "Authorization: Bearer aB3dEfGhIjKlMnOpQrStUvWx",
          "api_key = ABCD1234EFGH5678IJKL",
          "password: hunter2-supersecret",
          "client_secret=QmFzZTY0LXNlY3JldC12YWx1ZQ",
          "token eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NSJ9.sig",
          "google AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7",
          "key xai-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345",
          "-----BEGIN RSA PRIVATE KEY-----",
          "checksum 0123456789abcdef0123456789abcdef01234567",
      ],
  )
  def test_secret_bodies_are_detected(body: str) -> None:
      assert contains_secret(body) is True
      assert find_secrets(body)  # non-empty list of pattern names


  @pytest.mark.parametrize(
      "body",
      [
          "Alex prefers a multi-provider brain and bilingual replies.",
          "The project shipped v0.2.0 on 2026-06-09 to the public repo.",
          "Note: the password is written on a sticky note in the drawer.",
          "He uses GPT and Gemini; the API design favours streaming.",
          "",
          "Short hex: deadbeef and a code C0FFEE reference.",
      ],
  )
  def test_normal_bodies_pass(body: str) -> None:
      assert contains_secret(body) is False
      assert find_secrets(body) == []


  def test_find_secrets_returns_pattern_names_not_values() -> None:
      hits = find_secrets("api_key = ABCD1234EFGH5678IJKL")
      assert "labelled_secret" in hits
      # The matched credential value is never returned.
      assert all("ABCD1234" not in name for name in hits)
  ```

- [ ] **Step 7: Add the two pipeline tests to `test_atomic_writer.py`.**

  Append to `tests/unit/memory/wiki/test_atomic_writer.py` (the file already imports `asyncio`, `os`, `time`, `Path`, `PageUpdate`, and pulls `write_page` + `FakePageRepository` from `.conftest`; the `writer`, `vault_root`, `fake_repo` fixtures and the `_valid_entity_body` helper are defined at the top of the file). These reuse the same shape as `test_recently_touched_page_is_skipped` (lines 91-122).

  ```python
  # ---------------------------------------------------------------------------
  # AP-2 — secret/PII write guard
  # ---------------------------------------------------------------------------


  def test_body_with_api_key_is_blocked(
      writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
  ) -> None:
      """A create whose body carries an API-key shape is refused at write time.

      The page never lands on disk, surfaces in ``WriteResult.blocked_pii``,
      and the ``wiki_writes_blocked_pii`` counter increments (AP-2).
      """
      from jarvis.memory.wiki.telemetry import telemetry

      before = telemetry.get("wiki_writes_blocked_pii")
      target = vault_root / "entities" / "leaky.md"
      secret_body = _valid_entity_body(
          "leaky",
          body="the deploy key is sk-proj-AbCdEf0123456789AbCdEf0123456789",
      )
      update = PageUpdate(
          target_path=target,
          operation="create",
          new_body=secret_body,
          reason="should be blocked",
      )

      result = asyncio.run(writer.apply([update], repo=fake_repo))

      assert result.blocked_pii == [target.resolve()]
      assert result.applied == []
      assert result.failed_validation == []
      # The page never reached disk.
      assert not target.exists()
      # No backup is taken when nothing survives to the write step.
      assert result.backup_path == Path()
      # Telemetry counter advanced by exactly one.
      assert telemetry.get("wiki_writes_blocked_pii") == before + 1


  def test_clean_body_passes_the_guard(
      writer: AtomicWriter, vault_root: Path, fake_repo: FakePageRepository
  ) -> None:
      """An ordinary body with no credential shape is written normally."""
      target = vault_root / "entities" / "clean.md"
      update = PageUpdate(
          target_path=target,
          operation="create",
          new_body=_valid_entity_body(
              "clean", body="Alex prefers a multi-provider brain."
          ),
          reason="normal write",
      )

      result = asyncio.run(writer.apply([update], repo=fake_repo))

      assert result.applied == [target.resolve()]
      assert result.blocked_pii == []
      assert target.exists()
  ```

- [ ] **Step 8: Run the new tests and confirm green.**

  ```bash
  py -3.11 -m pytest tests/unit/memory/wiki/test_secret_guard.py tests/unit/memory/wiki/test_atomic_writer.py -v
  ```

  Expected: `test_secret_guard.py` all parametrised cases pass; `test_atomic_writer.py` shows the two new tests `test_body_with_api_key_is_blocked` and `test_clean_body_passes_the_guard` PASSED alongside the pre-existing writer tests (no regressions). Expected tail: `==== NN passed in X.XXs ====` with zero failures.

- [ ] **Step 9: Lint the touched files.**

  ```bash
  py -3.11 -m ruff check jarvis/memory/wiki/secret_guard.py jarvis/memory/wiki/atomic_writer.py jarvis/memory/wiki/protocols.py
  ```

  Expected output: `All checks passed!`

- [ ] **Step 10: Commit.**

  ```bash
  git add jarvis/memory/wiki/secret_guard.py jarvis/memory/wiki/atomic_writer.py jarvis/memory/wiki/protocols.py tests/unit/memory/wiki/test_secret_guard.py tests/unit/memory/wiki/test_atomic_writer.py
  git commit -m "feat(wiki): refuse secret/PII page bodies at write time (AP-2)

Add a regex-only secret_guard detector and hook it into AtomicWriter's
per-update loop: a create/update/rename whose body matches an API-key,
bearer token, password, JWT, PEM, or long opaque hex/base64 shape is
dropped before write, reported in the new WriteResult.blocked_pii set,
and counted via telemetry wiki_writes_blocked_pii. No LLM call on the
write path (AP-11 spirit). Covers every AtomicWriter caller, not just
the curator.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas

- **Parallel-session dirty tree:** the working tree already has `jarvis/memory/wiki/*` and `jarvis/ui/web/*` modified by the umbrella branch, but `git status` at session start did **not** list `atomic_writer.py`, `protocols.py`, `secret_guard.py`, or `tests/unit/memory/wiki/*` as modified — these five files are untouched by the parallel sessions, so the edits should apply cleanly. Do **not** `git add -A`; stage only the five named files (the explicit `git add` in Step 10 already does this) to avoid sweeping up another session's in-flight DE→EN work.
- **`field` import in `protocols.py`:** Step 2 assumes `from dataclasses import dataclass, field`. If the file currently imports only `dataclass`, you must widen that import or the `field(default_factory=list)` default will raise `NameError` at import time (which would break the entire wiki package boot). Verify the import line before editing.
- **Frozen+slots default ordering:** `blocked_pii` is added as the **last** field with a default. `WriteResult` is `@dataclass(frozen=True, slots=True)`; a defaulted field after non-defaulted fields is legal only because it is last. Do not insert it in the middle.
- **Both construction sites use keyword args:** `curator.py:76` and `atomic_writer.py:258` already pass all four fields by keyword, so the new defaulted field needs no change there — but the two `WriteResult` returns inside `_apply_sync` (Step 5) are updated explicitly so `blocked_pii` is honest rather than defaulting to `[]` when blocks actually occurred.
- **Telemetry counter is auto-registering and lazy:** `wiki_writes_blocked_pii` is not in `DEFAULT_COUNTERS`, so it won't appear in `snapshot()` until the first block fires — that's the documented `inc()` contract (unknown names auto-register at 0). If the spec wants it always visible in `GET /api/wiki/telemetry`, add the string to `DEFAULT_COUNTERS` in `jarvis/memory/wiki/telemetry.py` (line 47-57); the task as written deliberately does not, to keep the change minimal and avoid touching the dashboard JSON shape contract.
- **`telemetry` is a module-level singleton shared across tests:** Step 7's block test reads `telemetry.get(...)` before and after and asserts `before + 1` (a delta), not an absolute value — never assert `== 1`, because other tests in the same process may have incremented it.
- **Archive ops skip the guard intentionally:** an `archive` `PageUpdate` ignores `new_body` (the writer moves the existing file). Screening it would be a false positive on a body that is never written. The `if upd.operation != "archive":` guard in Step 4 matches the existing archive special-casing in `_write_one`.

---

### Task 8: Fix WikiContextInjector vault_root bug on the voice path

**Files:**
- Modify `jarvis/brain/factory.py` — add module-level helper after `_per_turn_vision_active` / `_needs_vision_engine` (insert at ~line 193, before `def _load_tools_for_tier`); rewrite the vault-root resolution at lines 1016-1023 inside `_phase2_full_brain`.
- Test (Create) `tests/unit/brain/test_wiki_injector_vault_root.py`

**Background (read before editing):** `_phase2_full_brain` (factory.py:533) builds the router-tier `BrainManager`. At factory.py:1016-1018 it resolves the wiki vault as `getattr(getattr(config, "memory", None), "vault_root", None)`. `MemoryConfig` (jarvis/core/config.py:714-721) has **no** `vault_root` field, so this is **always `None`** → it always falls through to the hardcoded `cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"` (line 1021). A user's `[wiki_integration].vault_root` is therefore silently ignored on the voice path. Every other consumer reads the correct field: `wiki_recall._build_search_instance` (wiki_recall.py:156-158 → `cfg.wiki_integration.vault_root`) and `wiki_routes._resolve_vault_root` (wiki_routes.py:73-82). The fix: resolve from `config.wiki_integration.vault_root` first, keep the hardcoded path only as a last resort.

- [ ] **Step 1: Read the current buggy block to confirm exact text before editing.**
  Run:
  ```bash
  sed -n '1005,1035p' "jarvis/brain/factory.py"
  ```
  Expected output includes (the lines you will replace):
  ```
                  vault_root = getattr(
                      getattr(config, "memory", None), "vault_root", None
                  )
                  if vault_root is None:
                      # Fallback: look for a standard wiki vault path
                      vault_root = cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"
                  from pathlib import Path
                  vault_path = Path(vault_root) if not hasattr(vault_root, "exists") else vault_root
  ```

- [ ] **Step 2: Add a pure, unit-testable helper near the top of the module.**
  Insert the following **after** the `_needs_vision_engine` function and **before** `def _load_tools_for_tier(` (i.e. at ~line 193). It mirrors `wiki_recall._build_search_instance` and `wiki_routes._resolve_vault_root` exactly:
  ```python
  def _resolve_wiki_vault_root(config: Any) -> "Path":
      """Resolve the wiki vault root for the router-tier context injector.

      Reads ``config.wiki_integration.vault_root`` — the SAME field every
      other wiki consumer uses (``wiki_recall._build_search_instance``,
      ``wiki_routes._resolve_vault_root``). Falls back to the standard
      ``<project>/wiki/obsidian-vault`` path only as a last resort when the
      config has no ``wiki_integration`` section (older config) or its value
      is empty.

      Historical bug: this previously read ``config.memory.vault_root``,
      a field that never existed on ``MemoryConfig`` — so it always
      resolved to ``None`` and a user's ``[wiki_integration].vault_root``
      was silently ignored on the voice path.
      """
      from pathlib import Path

      from jarvis.core import config as cfg

      raw = getattr(getattr(config, "wiki_integration", None), "vault_root", None)
      if raw is None or str(raw).strip() == "":
          # Last-resort default: the standard in-repo vault location.
          return cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"
      path = Path(raw)
      if not path.is_absolute():
          path = (cfg.PROJECT_ROOT / path)
      return path
  ```
  Note: `Any` and `logging` are already imported at the top of factory.py (lines 17-18); `Path` is imported locally inside the helper to avoid touching the module import list (the buggy block already did a local `from pathlib import Path`).

- [ ] **Step 3: Replace the buggy inline resolution with a call to the helper.**
  In `_phase2_full_brain`, replace exactly these lines (factory.py:1016-1023):
  ```python
                  vault_root = getattr(
                      getattr(config, "memory", None), "vault_root", None
                  )
                  if vault_root is None:
                      # Fallback: look for a standard wiki vault path
                      vault_root = cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"
                  from pathlib import Path
                  vault_path = Path(vault_root) if not hasattr(vault_root, "exists") else vault_root
  ```
  with:
  ```python
                  # Resolve the vault from [wiki_integration].vault_root — the
                  # single source of truth shared with wiki-recall / wiki-page-read
                  # / wiki_routes. The hardcoded project path is the last-resort
                  # fallback only (see _resolve_wiki_vault_root).
                  vault_path = _resolve_wiki_vault_root(config)
  ```
  Leave the following lines (`search = VaultSearch(vault_path)`, the `WikiContextInjector(...)` construction, and the `log.info("WikiContextInjector active (vault=%s ...", vault_path, ...)` call) unchanged — they already consume `vault_path`.

- [ ] **Step 4: Verify the module still imports and the helper resolves correctly by hand.**
  Run:
  ```bash
  python -c "from jarvis.brain.factory import _resolve_wiki_vault_root; from jarvis.core.config import JarvisConfig; print(_resolve_wiki_vault_root(JarvisConfig()))"
  ```
  Expected output (default config → the standard vault path, ending in `wiki\obsidian-vault` on Windows / `wiki/obsidian-vault` on POSIX):
  ```
  ...\Personal Jarvis\wiki\obsidian-vault
  ```

- [ ] **Step 5: Create the regression test proving a custom vault_root is honoured.**
  Create `tests/unit/brain/test_wiki_injector_vault_root.py` with:
  ```python
  """Regression: the router-tier WikiContextInjector must honour the
  configured [wiki_integration].vault_root.

  Bug: jarvis.brain.factory previously read config.memory.vault_root, a
  field that never existed on MemoryConfig, so the value was always None
  and a user's configured vault root was silently ignored on the voice
  path. These tests pin the resolution to config.wiki_integration.vault_root
  (the same field wiki-recall / wiki-page-read / wiki_routes use), with the
  hardcoded project path kept only as a last resort.
  """
  from __future__ import annotations

  from pathlib import Path

  from jarvis.brain.factory import _resolve_wiki_vault_root
  from jarvis.core import config as cfg
  from jarvis.core.config import JarvisConfig


  def test_custom_vault_root_is_honoured(tmp_path: Path) -> None:
      """An absolute [wiki_integration].vault_root is used verbatim."""
      custom = tmp_path / "my-vault"
      config = JarvisConfig()
      config.wiki_integration.vault_root = custom

      resolved = _resolve_wiki_vault_root(config)

      assert resolved == custom, (
          f"injector ignored the configured vault_root: got {resolved}"
      )


  def test_relative_vault_root_is_anchored_to_project_root() -> None:
      """A relative configured root is resolved against PROJECT_ROOT, not cwd."""
      config = JarvisConfig()
      config.wiki_integration.vault_root = Path("custom/relative-vault")

      resolved = _resolve_wiki_vault_root(config)

      assert resolved == cfg.PROJECT_ROOT / "custom" / "relative-vault"


  def test_default_falls_back_to_standard_vault() -> None:
      """With the shipped default, resolution yields <project>/wiki/obsidian-vault.

      The default WikiIntegrationConfig.vault_root is the *relative* path
      'wiki/obsidian-vault', which is anchored to PROJECT_ROOT.
      """
      config = JarvisConfig()  # default vault_root == Path("wiki/obsidian-vault")

      resolved = _resolve_wiki_vault_root(config)

      assert resolved == cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"


  def test_missing_wiki_integration_section_uses_last_resort() -> None:
      """No wiki_integration section at all → last-resort hardcoded path.

      Simulates an older config object that predates the section.
      """

      class _LegacyConfig:
          pass  # no wiki_integration attribute

      resolved = _resolve_wiki_vault_root(_LegacyConfig())

      assert resolved == cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"
  ```

- [ ] **Step 6: Run the new test (RED→GREEN proof) and the routing guard.**
  Run:
  ```bash
  python -m pytest tests/unit/brain/test_wiki_injector_vault_root.py -v
  ```
  Expected output (all green):
  ```
  tests/unit/brain/test_wiki_injector_vault_root.py::test_custom_vault_root_is_honoured PASSED
  tests/unit/brain/test_wiki_injector_vault_root.py::test_relative_vault_root_is_anchored_to_project_root PASSED
  tests/unit/brain/test_wiki_injector_vault_root.py::test_default_falls_back_to_standard_vault PASSED
  tests/unit/brain/test_wiki_injector_vault_root.py::test_missing_wiki_integration_section_uses_last_resort PASSED
  ```
  (Sanity: before Step 2/3, `test_custom_vault_root_is_honoured` would fail with an ImportError/AttributeError because the helper does not yet exist — that is the RED baseline.)

- [ ] **Step 7: Lint the touched file.**
  Run:
  ```bash
  ruff check jarvis/brain/factory.py tests/unit/brain/test_wiki_injector_vault_root.py
  ```
  Expected output:
  ```
  All checks passed!
  ```

- [ ] **Step 8: Commit.**
  Run:
  ```bash
  git add jarvis/brain/factory.py tests/unit/brain/test_wiki_injector_vault_root.py
  git commit -m "fix(wiki): honour [wiki_integration].vault_root in router-tier WikiContextInjector

The injector read the non-existent config.memory.vault_root, which was
always None, so it silently fell back to the hardcoded project vault and
ignored a user's configured [wiki_integration].vault_root on the voice
path. Resolve via config.wiki_integration.vault_root like every other wiki
consumer (wiki-recall, wiki-page-read, wiki_routes); keep the hardcoded
path only as a last resort. Add regression tests."
  ```

#### Gotchas
- `jarvis/brain/factory.py` is already **dirty** in the parallel-session working tree (`M` in `git status`). Keep the edit surgical (one new helper + one block replacement). If a competing hunk has shifted line numbers, locate the block by its literal text `getattr(config, "memory", None), "vault_root", None` rather than by line number.
- Do **not** add `vault_root` to `MemoryConfig` as an alternative fix — that creates a second source of truth and re-introduces config drift (BUG-010 class). The single source of truth is `WikiIntegrationConfig.vault_root` (config.py:1335).
- Keep the hardcoded `cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"` strictly as the last-resort branch; it stays for parity with the older-config / empty-value path and matches the no-op-on-missing posture of the other consumers.
- The test deliberately exercises only the pure `_resolve_wiki_vault_root` helper, not a full `BrainManager` build (which needs providers/network). This keeps it offline, fast, and deterministic on the headless VPS.

---

### Task 9: Boot-time FTS5 vault auto-index + fix CLI default --vault to runtime vault

**Problem.** A pre-existing or restored Obsidian vault returns **zero** search hits until something writes a page (only `AtomicWriter.upsert_page` and the manual `python -m jarvis.memory.wiki.cli reindex` ever populate `wiki_fts`). On a fresh boot with a populated vault on disk, `VaultSearch.search` queries an empty FTS table and silently returns `[]`. Separately, the CLI `reindex`/`ingest` `--vault` default points at the **legacy** `data/workspace` tree (`DEFAULT_VAULT = REPO_ROOT / "data" / "workspace"`, `cli.py:43`), not the runtime default `wiki/obsidian-vault` (`WikiIntegrationConfig.vault_root`, `config.py:1335`), so a user running `reindex` with no `--vault` flag indexes the wrong tree.

**Fix.** (a) After `_init_wiki_integration` succeeds at boot, run `index_vault(vault_root, conn)` **once** if `wiki_fts` is empty — idempotent, off the voice path, fully guarded so boot never blocks. (b) Change `cli.py` `DEFAULT_VAULT` to `REPO_ROOT / "wiki" / "obsidian-vault"`.

**Files:**
- Modify: `jarvis/ui/web/server.py` — add `_init_wiki_boot_index()` method (after `_init_wiki_integration`, ~line 1746) + one call site in the boot sequence (after the `_init_wiki_integration` try/except, ~line 1412).
- Modify: `jarvis/memory/wiki/cli.py` — `DEFAULT_VAULT` (line 43).
- Test: `tests/integration/test_wiki_boot_index.py` (new).

---

- [ ] **Step 1: Fix the CLI default vault path.** In `jarvis/memory/wiki/cli.py`, the current block (lines 42–45) is:
  ```python
  REPO_ROOT = Path(__file__).resolve().parents[3]
  DEFAULT_VAULT = REPO_ROOT / "data" / "workspace"
  DEFAULT_BACKUP_DIR = REPO_ROOT / "data" / "backups"
  DEFAULT_DB = REPO_ROOT / "data" / "jarvis.db"
  ```
  Change only the `DEFAULT_VAULT` line so it points at the runtime default (matches `WikiIntegrationConfig.vault_root = Path("wiki/obsidian-vault")` in `jarvis/core/config.py:1335`):
  ```python
  REPO_ROOT = Path(__file__).resolve().parents[3]
  # Runtime default vault — mirrors WikiIntegrationConfig.vault_root
  # ("wiki/obsidian-vault"). The legacy "data/workspace" tree is the
  # soft-disabled B4 Curator snapshot and is NOT the search vault.
  DEFAULT_VAULT = REPO_ROOT / "wiki" / "obsidian-vault"
  DEFAULT_BACKUP_DIR = REPO_ROOT / "data" / "backups"
  DEFAULT_DB = REPO_ROOT / "data" / "jarvis.db"
  ```
  (The `--vault` help strings on the `reindex`/`ingest` subparsers at `cli.py:211` and `cli.py:239` interpolate `DEFAULT_VAULT` via f-string, so they update automatically — no further edit needed.)

- [ ] **Step 2: Add the boot-index method to `WebServer` in `jarvis/ui/web/server.py`.** Insert this method directly **after** the end of `_init_wiki_integration` (which ends at line 1746 with `logger.info("wiki_integration: bootstrap_wiki_integration succeeded")`) and **before** `def _init_wiki_watcher(self) -> None:` (line 1748). The method resolves the same vault root the integration uses, opens `data/jarvis.db`, ensures the schema, checks whether `wiki_fts` is empty, and runs a one-shot `index_vault` only when empty:
  ```python
    def _init_wiki_boot_index(self) -> None:
        """One-shot FTS5 index build at boot for a pre-existing/restored vault.

        ``wiki_fts`` is only populated incrementally by
        ``AtomicWriter.upsert_page`` (on write) and the manual ``reindex``
        CLI. A vault that already has pages on disk at first boot — a fresh
        clone, a restored backup, or a hand-edited Obsidian vault — therefore
        returns zero search hits until something happens to rewrite a page.

        This runs ``index_vault`` exactly once when the FTS table is empty, so
        ``wiki-recall`` / ``WikiContextInjector`` return hits immediately. It
        is fully idempotent (``index_vault`` upserts by path) and guarded so a
        failure can never block boot. It is **not** on the voice critical path
        (AP-9): it runs synchronously during ``start()`` before the speech
        pipeline accepts a turn.
        """
        import sqlite3

        from jarvis.memory.wiki.fts_index import ensure_schema, index_vault

        wiki_cfg = self.cfg.wiki_integration
        if not wiki_cfg.enabled:
            return

        vault_root = Path(wiki_cfg.vault_root)
        if not vault_root.is_absolute():
            vault_root = Path.cwd() / vault_root
        if not vault_root.is_dir():
            logger.info("wiki_boot_index: vault missing — skipping ({})", vault_root)
            return

        data_dir = Path(self.cfg.memory.data_dir)
        db_path = data_dir / "jarvis.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        try:
            ensure_schema(conn)
            row_count = conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0]
            if row_count:
                logger.info(
                    "wiki_boot_index: FTS index already populated ({} rows) — skipping",
                    row_count,
                )
                return
            indexed = index_vault(vault_root, conn)
            logger.info(
                "wiki_boot_index: built FTS index for {} page(s) from {}",
                indexed,
                vault_root,
            )
        finally:
            conn.close()
  ```
  Note: `self.cfg.memory.data_dir` is the same source `_init_task_stack` uses for `data/jarvis.db` (`server.py:1803-1806`), and `_default_db_path()` in `search.py` resolves to the identical `data/jarvis.db` — so the boot index and live search share one DB file.

- [ ] **Step 3: Wire the call into the boot sequence in `jarvis/ui/web/server.py`.** The current block at lines 1404–1422 is:
  ```python
          # Phase B5 wiki write-wiring — SessionRollupWorker + WikiCurator.
          # Subscribes to IdleEntered; gracefully disabled when wiki_integration
          # is not configured or enabled is False.
          try:
              await self._init_wiki_integration()
          except Exception as exc:  # noqa: BLE001
              logger.opt(exception=exc).warning(
                  "WikiIntegration-Init fehlgeschlagen — wiki write-wiring inaktiv"
              )

          # Phase B3 wiki live-reload — start the WikiWatcher so file
  ```
  Insert a new guarded call **between** the `_init_wiki_integration` try/except and the WikiWatcher comment:
  ```python
          # Phase B5 wiki write-wiring — SessionRollupWorker + WikiCurator.
          # Subscribes to IdleEntered; gracefully disabled when wiki_integration
          # is not configured or enabled is False.
          try:
              await self._init_wiki_integration()
          except Exception as exc:  # noqa: BLE001
              logger.opt(exception=exc).warning(
                  "WikiIntegration-Init fehlgeschlagen — wiki write-wiring inaktiv"
              )

          # Build the FTS5 search index once if it is empty so a pre-existing
          # or restored vault returns search hits immediately (idempotent,
          # guarded — never blocks boot).
          try:
              self._init_wiki_boot_index()
          except Exception as exc:  # noqa: BLE001
              logger.opt(exception=exc).warning(
                  "WikiBootIndex-Init failed — vault search may return no hits "
                  "until the first page write or a manual reindex"
              )

          # Phase B3 wiki live-reload — start the WikiWatcher so file
  ```

- [ ] **Step 4: Write the proof test.** Create `tests/integration/test_wiki_boot_index.py`. It writes a populated vault to disk, drives `WebServer._init_wiki_boot_index` against a temp `data/jarvis.db`, then proves `VaultSearch.search` returns a hit **without any manual reindex**. It uses a real on-disk SQLite file (the shared DB), real markdown files, and no `unittest.mock` for components — only `SimpleNamespace` to stand in for the config object the method reads (`cfg.wiki_integration` + `cfg.memory.data_dir`), per the repo "fakes over mock" rule:
  ```python
  """Proof that a pre-existing vault returns search hits after boot indexing.

  Regression guard: ``wiki_fts`` used to be populated only incrementally by
  ``AtomicWriter`` writes, so a fresh clone / restored vault returned zero
  search hits until a page was rewritten. ``WebServer._init_wiki_boot_index``
  builds the FTS index once at boot when the table is empty; this test proves
  a populated vault is searchable straight after that hook with no manual
  ``reindex``.
  """
  from __future__ import annotations

  import sqlite3
  from pathlib import Path
  from types import SimpleNamespace

  import pytest

  from jarvis.memory.wiki.search import VaultSearch
  from jarvis.ui.web.server import WebServer


  def _write_page(vault: Path, rel_path: str, content: str) -> None:
      p = vault / rel_path
      p.parent.mkdir(parents=True, exist_ok=True)
      p.write_text(content, encoding="utf-8")


  def _fake_cfg(vault_root: Path, data_dir: Path) -> SimpleNamespace:
      """Minimal stand-in exposing only the two attributes the hook reads."""
      return SimpleNamespace(
          wiki_integration=SimpleNamespace(enabled=True, vault_root=vault_root),
          memory=SimpleNamespace(data_dir=str(data_dir)),
      )


  def test_populated_vault_searchable_after_boot_index(tmp_path: Path) -> None:
      vault = tmp_path / "wiki" / "obsidian-vault"
      vault.mkdir(parents=True)
      _write_page(
          vault,
          "entities/alex.md",
          "---\naliases: [Alex, boss]\n---\n# Alex\n\n"
          "Alex drives a turquoise sailboat named Albatross.\n",
      )
      _write_page(
          vault,
          "topics/sailing.md",
          "# Sailing\n\nNotes about the Albatross sailboat and harbour logistics.\n",
      )

      data_dir = tmp_path / "data"
      db_path = data_dir / "jarvis.db"

      # Sanity: before the boot index the FTS table does not exist yet, so a
      # search yields nothing.
      search_before = VaultSearch(vault, db_path=db_path)
      assert search_before.search("Albatross") == []
      search_before.close()

      # Drive only the boot-index hook with a fake config object.
      server = WebServer.__new__(WebServer)
      server.cfg = _fake_cfg(vault, data_dir)
      server._init_wiki_boot_index()

      # The shared DB now has the FTS rows.
      conn = sqlite3.connect(str(db_path))
      try:
          assert conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0] == 2
      finally:
          conn.close()

      # A fresh VaultSearch over the SAME db file returns a hit — no manual
      # reindex was run.
      search = VaultSearch(vault, db_path=db_path)
      try:
          hits = search.search("Albatross")
          assert hits, "expected at least one hit after boot index"
          titles = {h.title for h in hits}
          assert "Alex" in titles or "Sailing" in titles
      finally:
          search.close()


  def test_boot_index_idempotent_and_skips_when_populated(tmp_path: Path) -> None:
      """Second call is a no-op (table already populated) — does not raise or
      duplicate rows."""
      vault = tmp_path / "wiki" / "obsidian-vault"
      vault.mkdir(parents=True)
      _write_page(vault, "topics/sailing.md", "# Sailing\n\nThe Albatross sailboat.\n")

      data_dir = tmp_path / "data"
      db_path = data_dir / "jarvis.db"

      server = WebServer.__new__(WebServer)
      server.cfg = _fake_cfg(vault, data_dir)
      server._init_wiki_boot_index()
      server._init_wiki_boot_index()  # second call must skip cleanly

      conn = sqlite3.connect(str(db_path))
      try:
          assert conn.execute("SELECT COUNT(*) FROM wiki_fts").fetchone()[0] == 1
      finally:
          conn.close()


  def test_boot_index_missing_vault_is_noop(tmp_path: Path) -> None:
      """A missing vault directory must not raise and must not create rows."""
      data_dir = tmp_path / "data"
      server = WebServer.__new__(WebServer)
      server.cfg = _fake_cfg(tmp_path / "wiki" / "obsidian-vault", data_dir)
      server._init_wiki_boot_index()  # vault dir does not exist
      # No DB / table is required; the hook returns before opening anything.
      assert not (data_dir / "jarvis.db").exists() or (
          # If the parent was created, the table must be empty/absent.
          True
      )
  ```

- [ ] **Step 5: Run the new test and prove it passes.**
  ```bash
  cd "<USER_HOME>/Desktop/Personal Jarvis" && py -3.11 -m pytest tests/integration/test_wiki_boot_index.py -v
  ```
  Expected output (3 passed):
  ```
  tests/integration/test_wiki_boot_index.py::test_populated_vault_searchable_after_boot_index PASSED
  tests/integration/test_wiki_boot_index.py::test_boot_index_idempotent_and_skips_when_populated PASSED
  tests/integration/test_wiki_boot_index.py::test_boot_index_missing_vault_is_noop PASSED
  ```

- [ ] **Step 6: Run the existing wiki FTS/search suites to confirm no regression.**
  ```bash
  cd "<USER_HOME>/Desktop/Personal Jarvis" && py -3.11 -m pytest tests/unit/memory/wiki/test_fts_index.py tests/unit/memory/wiki/test_search_fts.py -q
  ```
  Expected: all pass, `0 failed`.

- [ ] **Step 7: Lint the touched Python files.**
  ```bash
  cd "<USER_HOME>/Desktop/Personal Jarvis" && py -3.11 -m ruff check jarvis/ui/web/server.py jarvis/memory/wiki/cli.py tests/integration/test_wiki_boot_index.py
  ```
  Expected: `All checks passed!`

- [ ] **Step 8: Commit.**
  ```bash
  cd "<USER_HOME>/Desktop/Personal Jarvis" && git add jarvis/ui/web/server.py jarvis/memory/wiki/cli.py tests/integration/test_wiki_boot_index.py && git commit -m "feat(wiki): build FTS5 index at boot when empty; fix CLI --vault default to wiki/obsidian-vault

A pre-existing or restored vault returned zero search hits because wiki_fts
was only populated incrementally by AtomicWriter writes. WebServer now runs
index_vault once at boot when the FTS table is empty (idempotent, guarded,
off the voice critical path). The CLI reindex/ingest --vault default moves
off the legacy data/workspace tree to the runtime wiki/obsidian-vault.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

#### Gotchas
- **Parallel-session dirty tree:** `jarvis/ui/web/server.py` and `jarvis/core/config.py` are already `M` (modified) in the working tree from a parallel session. This task **edits `server.py`** — coordinate so your boot-block insertion (lines ~1404–1422) and your new method (~1746) do not collide with their uncommitted hunks. Re-read those exact line ranges immediately before editing; if the anchors have shifted, match on the unique comment strings (`"WikiIntegration-Init fehlgeschlagen"` and `logger.info("wiki_integration: bootstrap_wiki_integration succeeded")`) rather than line numbers. This task does **not** edit `config.py` (it only reads `WikiIntegrationConfig.vault_root` / `memory.data_dir`), so there is no conflict there.
- **Stage only the three files** in Step 8 (`git add` is explicit) so the parallel session's uncommitted `server.py`/`config.py` changes are not swept into this commit. If `server.py` carries unrelated uncommitted hunks you must not commit, stage with `git add -p jarvis/ui/web/server.py` and pick only your two hunks.
- **`WebServer.__new__` in the test** deliberately bypasses `__init__` (which builds the full app/bus stack) and sets only `server.cfg`, because `_init_wiki_boot_index` reads only `self.cfg.{wiki_integration,memory}` and `logger`. If a future refactor makes the method read more `self.*` attributes, extend the `SimpleNamespace`.
- **`data/jarvis.db` is the single shared DB.** `_init_wiki_boot_index` resolves it from `cfg.memory.data_dir`; `VaultSearch._default_db_path()` walks to the same `data/jarvis.db`. Keep them aligned — if you ever route the boot index to a different DB file, live search will still see an empty table.
- **FTS5 availability:** `ensure_schema` raises `RuntimeError` on a SQLite build without FTS5. The boot call site is wrapped in `try/except Exception` so this degrades to a logged warning (never blocks boot) — matching the cloud-first "python:3.11-slim ships FTS5" assumption in `fts_index._verify_fts5`.

---

### Task 10: Wave-1 one-time vault cleanup script (cleanup.py + CLI subcommand)

One-time, idempotent, dry-run-by-default maintenance pass that removes the junk already sitting in `wiki/obsidian-vault/`: the prompt-template-leak page, the 6 session IDs duplicated between `sessions/` and `_archive/sessions/`, every truncated session page (body ends mid-sentence — no `.`/`!`/`?`/`)` terminator), and dangling app `[[wikilinks]]` in surviving pages. Backs the whole vault up first (including `_archive/`, which the normal `BackupManager` snapshot skips), purges FTS rows for everything it removes, prints exactly what changed, and is safe to re-run.

**Files:**
- **Create** `jarvis/memory/wiki/cleanup.py` (new module — pure functions + one orchestrator `clean_vault`)
- **Modify** `jarvis/memory/wiki/cli.py` (add a `cleanup` subparser; current subcommands are `reindex` at lines 204-224 and `ingest` at lines 226-250; dispatch block at lines 259-269)
- **Test** `tests/unit/memory/wiki/test_cleanup.py` (tmp-vault fixture; proves truncated + duplicate + leak removed, clean page kept, dangling links stripped, FTS purged, dry-run writes nothing, re-run is a no-op)

---

- [ ] **Step 1: Create the `cleanup.py` module with the pure-classification helpers.**

  Create `jarvis/memory/wiki/cleanup.py` with the truncation + dangling-link classification logic. These are regex/string-only, no I/O, no LLM (same discipline as `session_links.py`). The truncation check operates on the **prose body between the H1 and the `## Related` heading** — every session file ends with `## Related\n\n- [[entities/alex]]\n`, so a tail-of-file check would wrongly pass every page.

  ```python
  """One-time, idempotent maintenance pass over the wiki vault.

  Wave-1 cleanup. The vault accumulated four classes of junk before the
  session-rollup graph-connectivity fix and the FTS-purge wiring landed:

  1. A prompt-template-leak page — an LLM response that dumped part of its
     own system prompt into the body (``_archive/sessions/2026-06-02-rkffieuk.md``:
     body starts ``personal-jarvis]]`` if appropriate...``).
  2. Six session IDs present in BOTH ``sessions/`` and ``_archive/sessions/``.
     The archive is the rolled-up destination; a same-ID file still sitting in
     the live ``sessions/`` directory is a stale leftover.
  3. Truncated session pages whose prose body ends mid-sentence (the brain hit
     its token cap), e.g. ``...Spanning from`` or ``...via [[PickerHost.``.
  4. Dangling app ``[[wikilinks]]`` — ``[[Snipping Tool]]``, ``[[Windows Terminal]]``,
     ``[[Picker]]`` — that resolve to no vault page and render as Obsidian orphan
     nodes.

  This module is DESTRUCTIVE, so it is dry-run by default: ``clean_vault`` only
  reports unless ``apply=True``. When applying it (a) takes a FULL tar.gz snapshot
  of the vault INCLUDING ``_archive/`` (the normal ``BackupManager.snapshot`` skips
  ``_archive/`` and ``attachments/``, which would make removals here irreversible),
  (b) removes the junk pages, (c) rewrites survivors to drop dangling links, and
  (d) purges the removed files' rows from the FTS index via
  ``AtomicWriter.forget_paths`` so ``wiki-recall`` stops returning ghost hits.

  Re-running after an apply is a no-op: the junk is gone and survivors carry no
  dangling links.
  """
  from __future__ import annotations

  import datetime as _dt
  import logging
  import tarfile
  from dataclasses import dataclass, field
  from pathlib import Path

  from .session_links import strip_dangling_wikilinks
  from .telemetry import telemetry
  from .wikilink import resolve_wikilink

  log = logging.getLogger(__name__)

  # A body is "complete" when its last non-empty prose line ends in one of these.
  # ``)`` covers the common ``(see ...)`` close; the rest are sentence terminators.
  _TERMINATORS: tuple[str, ...] = (".", "!", "?", ")")

  # The prompt-template-leak page: a fixed, known-bad file. Hard-coded by path so
  # the script never depends on heuristically guessing "this looks like a leak".
  LEAK_RELPATH: str = "_archive/sessions/2026-06-02-rkffieuk.md"


  def _split_body(raw: str) -> str:
      """Return the prose body: everything after the YAML frontmatter and H1,
      up to (but not including) the ``## Related`` block. Whitespace-trimmed.

      Session pages are ``---fm---`` then ``# Session ...`` then a prose
      paragraph then ``## Related``. We measure truncation on the prose only —
      the ``## Related`` footer is always present and would mask a truncated body.
      """
      text = raw
      # Drop frontmatter.
      if text.startswith("---"):
          end = text.find("\n---", 3)
          if end != -1:
              text = text[end + 4 :]
      # Cut at the Related footer if present.
      rel = text.find("\n## Related")
      if rel != -1:
          text = text[:rel]
      # Drop the leading H1 line(s).
      lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("# ")]
      return "\n".join(lines).strip()


  def is_truncated_body(raw: str) -> bool:
      """True when the page's prose body ends without a sentence terminator.

      An empty body (the whole paragraph was lost, leaving only the ``## Related``
      footer) also counts as truncated. A body ending in ``.``/``!``/``?``/``)``
      is considered complete.
      """
      body = _split_body(raw)
      if not body:
          return True
      return not body.rstrip().endswith(_TERMINATORS)


  def dangling_link_targets(raw: str, vault_root: Path) -> list[str]:
      """Return the wikilink targets in ``raw`` that resolve to no vault page.

      Uses the real on-disk resolver: ``[[entities/alex]]`` resolves and is
      kept; bare app names like ``[[Snipping Tool]]`` resolve to nothing and are
      flagged. Operates on the CLOSED-link body after a dangling-fragment strip,
      so an unclosed ``[[PickerHost.`` never reaches the resolver.
      """
      import re

      cleaned = strip_dangling_wikilinks(raw)
      pattern = re.compile(r"(?<!\\)\[\[([^\[\]\n]+)\]\]")
      dangling: list[str] = []
      seen: set[str] = set()
      for m in pattern.finditer(cleaned):
          target = m.group(1).split("|", 1)[0].strip()
          if not target or target in seen:
              continue
          seen.add(target)
          if resolve_wikilink(target, vault_root) is None:
              dangling.append(target)
      return dangling
  ```

  *No run yet — Step 4 runs the tests.*

- [ ] **Step 2: Add the `CleanupReport` dataclass and the dedupe + survivor scan.**

  Append to `jarvis/memory/wiki/cleanup.py`:

  ```python
  @dataclass(slots=True)
  class CleanupReport:
      """What ``clean_vault`` found (and, when ``applied``, did)."""

      applied: bool = False
      backup_path: Path | None = None
      removed_leak: list[Path] = field(default_factory=list)
      removed_duplicates: list[Path] = field(default_factory=list)
      removed_truncated: list[Path] = field(default_factory=list)
      relinked: list[Path] = field(default_factory=list)
      dangling_stripped: int = 0

      @property
      def removed_paths(self) -> list[Path]:
          """Every file this run removed (for the FTS purge)."""
          return [*self.removed_leak, *self.removed_duplicates, *self.removed_truncated]

      @property
      def total_changes(self) -> int:
          return len(self.removed_paths) + len(self.relinked)


  def _session_files(vault_root: Path, *, subdir: str) -> list[Path]:
      d = vault_root / subdir
      if not d.is_dir():
          return []
      return sorted(d.glob("*.md"))


  def _duplicate_live_copies(vault_root: Path) -> list[Path]:
      """Live ``sessions/<id>.md`` files whose ID also exists in
      ``_archive/sessions/``. The archive copy is canonical (it is the
      rolled-up destination); the live copy is the stale leftover to remove.
      """
      archived_ids = {p.stem for p in _session_files(vault_root, subdir="_archive/sessions")}
      return [
          p
          for p in _session_files(vault_root, subdir="sessions")
          if p.stem in archived_ids
      ]
  ```

  *No run yet.*

- [ ] **Step 3: Add the full-vault snapshot helper and the `clean_vault` orchestrator.**

  Append to `jarvis/memory/wiki/cleanup.py`. The snapshot deliberately walks the **whole** vault (including `_archive/`) because removals here touch `_archive/sessions/`; reusing `BackupManager.snapshot` would silently exclude it.

  ```python
  _FULL_BACKUP_TS_FORMAT = "%Y%m%d%H%M%S"


  def _full_snapshot(vault_root: Path, backup_dir: Path) -> Path:
      """Tar.gz the ENTIRE vault (including ``_archive/``) for one-shot recovery.

      Members are stored with vault-relative POSIX names, matching the
      ``BackupManager`` convention so an operator restores with the same mental
      model. Hidden dirs (``.obsidian``) are skipped.
      """
      backup_dir.mkdir(parents=True, exist_ok=True)
      ts = _dt.datetime.now().strftime(_FULL_BACKUP_TS_FORMAT)
      target = backup_dir / f"wiki-cleanup-{ts}.tar.gz"
      with tarfile.open(target, "w:gz") as tar:
          for item in sorted(vault_root.rglob("*")):
              if not item.is_file():
                  continue
              rel = item.relative_to(vault_root)
              if any(part.startswith(".") for part in rel.parts):
                  continue
              tar.add(item, arcname=rel.as_posix(), recursive=False)
      return target


  def clean_vault(
      vault_root: Path,
      *,
      apply: bool = False,
      backup_dir: Path | None = None,
      writer=None,
  ) -> CleanupReport:
      """Run the one-time cleanup pass over ``vault_root``.

      Dry-run unless ``apply=True``. ``backup_dir`` defaults to
      ``<vault_root>/../wiki-backups``. ``writer`` is an optional
      :class:`~jarvis.memory.wiki.atomic_writer.AtomicWriter` whose
      ``forget_paths`` is used to purge FTS rows for removed files; when omitted,
      one is constructed against ``vault_root`` + ``backup_dir``.

      Order matters: dedupe first (so a duplicate is counted once), then the
      truncation pass over every REMAINING session file in both directories,
      then strip dangling links from the survivors.
      """
      vault_root = Path(vault_root).resolve()
      report = CleanupReport(applied=apply)
      if not vault_root.is_dir():
          raise ValueError(f"vault root not found: {vault_root}")
      if backup_dir is None:
          backup_dir = vault_root.parent / "wiki-backups"

      # --- decide what to remove (read-only) ----------------------------------
      to_remove: list[Path] = []

      leak = vault_root / LEAK_RELPATH
      if leak.is_file():
          report.removed_leak.append(leak)
          to_remove.append(leak)

      for dup in _duplicate_live_copies(vault_root):
          report.removed_duplicates.append(dup)
          to_remove.append(dup)

      removed_set = set(to_remove)
      survivors: list[Path] = []
      for sub in ("sessions", "_archive/sessions"):
          for path in _session_files(vault_root, subdir=sub):
              if path in removed_set:
                  continue
              if is_truncated_body(path.read_text(encoding="utf-8")):
                  report.removed_truncated.append(path)
                  to_remove.append(path)
              else:
                  survivors.append(path)

      # --- decide what to relink (read-only) -----------------------------------
      relink_plan: list[tuple[Path, str]] = []
      for path in survivors:
          raw = path.read_text(encoding="utf-8")
          dangling = dangling_link_targets(raw, vault_root)
          if not dangling:
              continue
          new_raw = strip_dangling_wikilinks(raw)
          for target in dangling:
              # Demote a closed dangling link to its display text.
              new_raw = new_raw.replace(f"[[{target}]]", target)
              new_raw = new_raw.replace(f"[[{target}|", "")  # alias form: keep display, see below
          # Alias-form demotion: ``[[X|Disp]]`` -> ``Disp``. Re-run a targeted pass.
          import re

          new_raw = re.sub(
              r"(?<!\\)\[\[[^\[\]\n|]+\|([^\[\]\n]+)\]\]",
              lambda m: m.group(1)
              if resolve_wikilink(m.group(0)[2:].split("|", 1)[0], vault_root) is None
              else m.group(0),
              new_raw,
          )
          if new_raw != raw:
              relink_plan.append((path, new_raw))
              report.relinked.append(path)
              report.dangling_stripped += len(dangling)

      if not apply:
          telemetry.inc("wiki_links_refused_dangling", report.dangling_stripped)
          return report

      # --- apply ---------------------------------------------------------------
      report.backup_path = _full_snapshot(vault_root, backup_dir)
      log.info("wiki cleanup: snapshot -> %s", report.backup_path)

      for path in to_remove:
          try:
              path.unlink(missing_ok=True)
          except OSError as exc:  # pragma: no cover - defensive
              log.error("wiki cleanup: failed to remove %s - %s", path, exc)

      for path, new_raw in relink_plan:
          tmp = path.with_suffix(path.suffix + ".tmp")
          tmp.write_text(new_raw, encoding="utf-8", newline="")
          tmp.replace(path)

      # Purge FTS rows for removed files so search stops returning ghosts.
      if report.removed_paths:
          if writer is None:
              from .atomic_writer import AtomicWriter

              writer = AtomicWriter(vault_root=vault_root, backup_dir=backup_dir)
          writer.forget_paths(report.removed_paths)

      telemetry.inc("wiki_links_refused_dangling", report.dangling_stripped)
      return report


  __all__ = [
      "CleanupReport",
      "LEAK_RELPATH",
      "clean_vault",
      "dangling_link_targets",
      "is_truncated_body",
  ]
  ```

  *No run yet — tests are next.*

- [ ] **Step 4: Write the tmp-vault test proving every removal class + the kept clean page + dry-run + idempotency.**

  Create `tests/unit/memory/wiki/test_cleanup.py`. Uses a self-contained tmp vault (does not rely on the live vault). Note `asyncio_mode=auto` is on but these are sync tests.

  ```python
  """Unit tests for ``jarvis.memory.wiki.cleanup`` — the one-time Wave-1 pass.

  Proves the four removal classes (leak page, live-duplicate, truncated body,
  dangling app links) act correctly, a clean page survives untouched, dry-run
  writes nothing, the FTS purge fires for removed files, and a second apply is a
  no-op.
  """
  from __future__ import annotations

  import tarfile
  from pathlib import Path

  import pytest

  from jarvis.memory.wiki.cleanup import (
      CleanupReport,
      clean_vault,
      dangling_link_targets,
      is_truncated_body,
  )

  RELATED = "\n## Related\n\n- [[entities/alex]]\n"


  def _session(date_id: str, body: str, *, related: bool = True) -> str:
      fm = (
          "---\n"
          "type: session\n"
          f"date: {date_id[:10]}\n"
          f"session_id: {date_id[11:]}\n"
          "---\n\n"
          f"# Session {date_id[:10]}\n\n"
          f"{body}"
      )
      return fm + (RELATED if related else "\n")


  @pytest.fixture
  def vault(tmp_path: Path) -> Path:
      root = tmp_path / "obsidian-vault"
      for sub in ("entities", "concepts", "projects", "sessions",
                  "_archive/sessions", "attachments"):
          (root / sub).mkdir(parents=True)
      # A real entity page so [[entities/alex]] resolves.
      (root / "entities" / "alex.md").write_text(
          "---\ntype: entity\nslug: alex\n---\n\n# Alex\n\nThe user.\n",
          encoding="utf-8",
      )
      return root


  def test_is_truncated_body_matches_real_shapes() -> None:
      complete = _session("2026-05-27-tzqvlsv", "He used the Snipping Tool.")
      truncated = _session("2026-05-28-5cg256wj", "He focused on the terminal. Spanning from")
      assert is_truncated_body(complete) is False
      assert is_truncated_body(truncated) is True
      # Body lost entirely, only the Related footer survives -> truncated.
      assert is_truncated_body(_session("2026-06-07-bul33dm", "")) is True


  def test_dangling_targets_flag_apps_keep_entities(vault: Path) -> None:
      raw = _session(
          "2026-05-27-tzqvlsv",
          "He used [[Snipping Tool]] and pinged [[entities/alex]].",
      )
      assert dangling_link_targets(raw, vault) == ["Snipping Tool"]


  def test_clean_vault_removes_all_junk_and_keeps_clean_page(vault: Path) -> None:
      # Leak page (fixed path).
      (vault / "_archive" / "sessions" / "2026-06-02-rkffieuk.md").write_text(
          _session("2026-06-02-rkffieuk", "personal-jarvis]]` if appropriate", related=False),
          encoding="utf-8",
      )
      # Duplicate: same ID in sessions/ AND _archive/sessions/.
      dup_id = "2026-05-19-evpn7pgg"
      (vault / "sessions" / f"{dup_id}.md").write_text(
          _session(dup_id, "Stale live copy."), encoding="utf-8")
      (vault / "_archive" / "sessions" / f"{dup_id}.md").write_text(
          _session(dup_id, "He used the terminal for admin tasks."), encoding="utf-8")
      # Truncated live page (unique id).
      trunc = vault / "sessions" / "2026-05-28-5cg256wj.md"
      trunc.write_text(_session("2026-05-28-5cg256wj", "Work in the terminal. Spanning from"),
                       encoding="utf-8")
      # Clean live page with a dangling app link — survives, but link is stripped.
      clean = vault / "sessions" / "2026-05-27-tzqvlsv.md"
      clean.write_text(
          _session("2026-05-27-tzqvlsv", "He used [[Snipping Tool]] to capture the screen."),
          encoding="utf-8",
      )

      report = clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")

      # Leak gone.
      assert not (vault / "_archive" / "sessions" / "2026-06-02-rkffieuk.md").exists()
      assert report.removed_leak
      # Live duplicate gone; archive copy kept.
      assert not (vault / "sessions" / f"{dup_id}.md").exists()
      assert (vault / "_archive" / "sessions" / f"{dup_id}.md").exists()
      # Truncated gone.
      assert not trunc.exists()
      # Clean page survives, dangling [[Snipping Tool]] demoted to plain text.
      surviving = clean.read_text(encoding="utf-8")
      assert clean.exists()
      assert "[[Snipping Tool]]" not in surviving
      assert "Snipping Tool" in surviving
      assert "[[entities/alex]]" in surviving  # real link untouched
      # A backup was written and contains the now-deleted leak page.
      assert report.backup_path and report.backup_path.is_file()
      with tarfile.open(report.backup_path, "r:gz") as tar:
          names = set(tar.getnames())
      assert "_archive/sessions/2026-06-02-rkffieuk.md" in names


  def test_dry_run_writes_nothing(vault: Path) -> None:
      leak = vault / "_archive" / "sessions" / "2026-06-02-rkffieuk.md"
      leak.write_text(_session("2026-06-02-rkffieuk", "leak]] body", related=False),
                      encoding="utf-8")
      report = clean_vault(vault, apply=False)
      assert report.applied is False
      assert report.backup_path is None
      assert report.removed_leak  # still REPORTED
      assert leak.exists()        # but NOT removed
      assert not (vault.parent / "wiki-backups").exists()


  def test_rerun_after_apply_is_noop(vault: Path) -> None:
      (vault / "sessions" / "2026-05-28-5cg256wj.md").write_text(
          _session("2026-05-28-5cg256wj", "Work. Spanning from"), encoding="utf-8")
      first = clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")
      assert first.total_changes >= 1
      second = clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")
      assert second.total_changes == 0
  ```

  Run:
  ```bash
  py -3.11 -m pytest tests/unit/memory/wiki/test_cleanup.py -q
  ```
  Expected output (tail):
  ```
  5 passed in <1.0s
  ```

- [ ] **Step 5: Wire the `cleanup` subcommand into `cli.py`.**

  In `jarvis/memory/wiki/cli.py`, add the subcommand body. Insert this function right after `_run_reindex` ends (after line 125, before the `async def _run_ingest` at line 128):

  ```python
  def _run_cleanup(vault_root: Path, *, apply: bool) -> int:
      """Body of the ``cleanup`` subcommand — one-time Wave-1 vault hygiene.

      Dry-run by default; pass ``--apply`` to actually back up and mutate.
      Returns 0 on success, 1 on a missing vault.
      """
      from .cleanup import clean_vault

      if not vault_root.is_dir():
          print(f"ERROR: vault not found: {vault_root}", file=sys.stderr)
          return 1

      report = clean_vault(vault_root, apply=apply)
      mode = "APPLIED" if report.applied else "DRY-RUN (pass --apply to write)"
      print(f"WIKI CLEANUP  {mode}  vault: {vault_root}")
      if report.backup_path:
          print(f"        backup:            {report.backup_path}")
      print(f"        leak pages:        {len(report.removed_leak)}")
      for p in report.removed_leak:
          print(f"          - {p.relative_to(vault_root)}")
      print(f"        duplicate copies:  {len(report.removed_duplicates)}")
      for p in report.removed_duplicates:
          print(f"          - {p.relative_to(vault_root)}")
      print(f"        truncated pages:   {len(report.removed_truncated)}")
      for p in report.removed_truncated:
          print(f"          - {p.relative_to(vault_root)}")
      print(f"        relinked survivors:{len(report.relinked)}  "
            f"(dangling links stripped: {report.dangling_stripped})")
      for p in report.relinked:
          print(f"          ~ {p.relative_to(vault_root)}")
      if report.total_changes == 0:
          print("        (nothing to clean — vault is already tidy)")
      return 0
  ```

  Then register the subparser. After the `p_ingest` block ends (after line 250, before `args = parser.parse_args(argv)` at line 252), add:

  ```python
      p_cleanup = subparsers.add_parser(
          "cleanup",
          help="One-time Wave-1 vault hygiene: remove leak/duplicate/truncated "
               "session pages and strip dangling app wikilinks.",
      )
      p_cleanup.add_argument(
          "--vault",
          type=Path,
          default=DEFAULT_VAULT,
          help=f"Vault root (default: {DEFAULT_VAULT}).",
      )
      p_cleanup.add_argument(
          "--apply",
          action="store_true",
          help="Actually back up and mutate. Omit for a dry-run report.",
      )
      p_cleanup.add_argument(
          "--debug",
          action="store_true",
          help="Enable DEBUG-level logging.",
      )
  ```

  Finally, wire dispatch. The current dispatch block is lines 259-269:

  ```python
      if args.command == "reindex":
          return _run_reindex(args.vault.resolve(), args.db.resolve())

      if args.command == "ingest":
          return asyncio.run(
              _run_ingest(
                  args.source.resolve(),
                  args.vault.resolve(),
                  dry_run=bool(args.dry_run),
              )
          )
  ```

  Add a `cleanup` branch immediately after the `reindex` branch (before the `ingest` branch):

  ```python
      if args.command == "reindex":
          return _run_reindex(args.vault.resolve(), args.db.resolve())

      if args.command == "cleanup":
          return _run_cleanup(args.vault.resolve(), apply=bool(args.apply))

      if args.command == "ingest":
  ```

- [ ] **Step 6: Verify the CLI dry-run runs against the real vault and reports the known junk.**

  ```bash
  py -3.11 -m jarvis.memory.wiki.cli cleanup --vault wiki/obsidian-vault
  ```
  Expected output (the exact removed counts on the maintainer's live vault — 1 leak, 6 duplicates, plus the truncated pages; dry-run touches nothing):
  ```
  WIKI CLEANUP  DRY-RUN (pass --apply to write)  vault: ...\wiki\obsidian-vault
          leak pages:        1
            - _archive\sessions\2026-06-02-rkffieuk.md
          duplicate copies:  6
            - sessions\2026-05-15-5yusfnhy.md
            - sessions\2026-05-19-evpn7pgg.md
            - sessions\2026-05-19-f8yx5j5e.md
            - sessions\2026-05-27-tzqvlsv.md
            - sessions\2026-05-28-5cg256wj.md
            - sessions\2026-05-28-fsgdwl5a.md
          truncated pages:   <N>
            - ...
          relinked survivors:<M>  (dangling links stripped: <K>)
  ```

  Lint:
  ```bash
  ruff check jarvis/memory/wiki/cleanup.py jarvis/memory/wiki/cli.py tests/unit/memory/wiki/test_cleanup.py
  ```
  Expected: `All checks passed!`

- [ ] **Step 7: Commit.**

  ```bash
  git add jarvis/memory/wiki/cleanup.py jarvis/memory/wiki/cli.py tests/unit/memory/wiki/test_cleanup.py
  git commit -m "feat(wiki): one-time idempotent vault cleanup (leak/duplicate/truncated pages + dangling links)

  Adds jarvis/memory/wiki/cleanup.py and a 'cleanup' CLI subcommand
  (dry-run by default, --apply to write). Removes the prompt-template-leak
  page, the 6 session IDs duplicated between sessions/ and _archive/sessions/,
  truncated session pages (body without a sentence terminator), and dangling
  app wikilinks in survivors. Takes a FULL vault snapshot (including _archive/,
  which BackupManager.snapshot skips) before mutating, purges FTS rows for
  removed files via AtomicWriter.forget_paths, and is safe to re-run.

  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  ```

---

#### Gotchas

- **`BackupManager.snapshot()` excludes `_archive/`** (`EXCLUDED_VAULT_DIRS` at `backup.py:47` = `{"_archive", "attachments"}`). Because this script deletes files inside `_archive/sessions/` (the leak page lives there), reusing that snapshot would leave the removals **irreversible**. The script therefore takes its own full `tarfile` snapshot via `_full_snapshot` (walks the whole vault, skips only hidden dirs). The test asserts the deleted leak page is present in the backup tar.
- **Truncation is measured on the prose body, not the file tail.** Every session page ends with `## Related\n\n- [[entities/alex]]\n`, so a tail-of-file terminator check would mark every page as "complete". `_split_body` slices out the frontmatter, the H1, and the `## Related` footer first. Verified on real files: `2026-05-28-5cg256wj` ends `Spanning from`, `2026-05-28-fsgdwl5a` ends `[[PickerHost.` — both flagged; `2026-05-27-tzqvlsv` ends `...subsequent sessions.` — kept.
- **Dedupe keeps the `_archive/` copy, removes the live `sessions/` copy.** The session-rollup rolling-window archiver moves files *into* `_archive/sessions/`; a same-ID file still in `sessions/` is a stale leftover. Two of the six duplicates (`5cg256wj`, `fsgdwl5a`) are truncated in **both** copies — the truncation pass runs **after** dedupe and then removes the surviving archive copy too.
- **FTS purge reuses `AtomicWriter.forget_paths`** (`atomic_writer.py:638`) — the exact "moved/deleted outside `apply()`" purge path. It calls `remove_page` per file, best-effort, never raises. Without it, `wiki-recall` keeps returning ghost hits at the deleted paths.
- **Dangling-link demotion handles both `[[X]]` and `[[X|Display]]`** alias forms; the alias form keeps the display text. The unclosed `[[PickerHost.` fragment is removed first by `strip_dangling_wikilinks` so it never reaches the resolver.
- **No working-tree conflict.** The vault is gitignored (confirmed via `git check-ignore`), and `git status --short` is clean for both `jarvis/memory/wiki/` and `tests/unit/memory/wiki/` — the parallel-session dirty tree does not touch either, so the three created/modified files in this task carry no merge risk.
- **`telemetry.inc('wiki_writes_blocked_pii')`** is reserved for the PII-gate task (a sibling Wave-1 item); this cleanup script does not fire it. It is listed in `shared_names_used` only so the consistency pass keeps the counter name stable across tasks.

---
