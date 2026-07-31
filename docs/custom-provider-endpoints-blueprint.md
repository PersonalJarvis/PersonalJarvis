# Architecture blueprint — user-defined providers (custom API key + custom endpoint)

Status: **proposal / recommendation**. Not implemented.
Scope: let a user register an arbitrary LLM provider — their own key, their own
base URL — and use it everywhere a built-in provider works: Pipeline Mode
(brain / deep / router / tool model / mission worker) **and** Realtime Mode
(full-duplex voice + realtime tool calls).

Companion documents: [`docs/architecture-overview.md`](architecture-overview.md),
[`docs/anti-drift-three-layer.md`](anti-drift-three-layer.md),
[`CLAUDE.md`](../CLAUDE.md) §3 (open-source universality), §5 (architecture
essentials), AP-21/AP-22 (capability gating, cross-family fallback).

---

## 1. Recommendation in one paragraph

**Do not add a second provider system. Add user-defined provider *instances*
that are synthesized into the four registries the codebase already treats as
single sources of truth.** A custom provider is a row in a new `jarvis.toml`
table, `[brain.custom_providers.<id>]`, that names an **adapter kind** (which
wire protocol to speak), a **base URL**, and a **credential slot**. Six small
"overlay" seams make every existing lookup — `get_spec()`,
`get_provider_secret()`, `BrainProviderRegistry`, the realtime plugin factory,
`ALLOWED_SECRET_KEYS`, and the model catalog — return the custom rows alongside
the built-ins. After that, everything downstream works untouched: provider
cards, the brain switch, the Tool Model route, `switch-provider`, the mission
worker, the realtime session builder. The alternative — a parallel
"custom provider" code path with its own routes, its own resolution and its own
UI — duplicates every one of those behaviours and is exactly the BUG-008
multi-layer drift trap this repo already pays for four times over.

The single most important existing asset: **`config.resolve_provider_endpoint()`
already is the endpoint indirection.** It resolves team proxy → per-provider
`base_url` override → vendor default, and it is already threaded through the
brain, STT and TTS plugins and the model catalog. Custom endpoints are not a new
mechanism — they are that mechanism, reached by provider ids the registries do
not yet admit.

---

## 2. What blocks a custom provider today

Four independent gates reject an unknown provider id. All four must be opened;
opening three of them produces a card that saves nothing, or a key that no
brain reads.

| # | Gate | Location | Failure when the id is unknown |
|---|------|----------|-------------------------------|
| G1 | `PROVIDERS` frozen tuple → `get_spec()` | `jarvis/ui/web/provider_spec.py:520+` | `get_spec()` → `None`; `PUT /api/tool-model` 404 `unknown_provider`; `PUT /providers/{id}/base-url` 404; card never rendered |
| G2 | `ALLOWED_SECRET_KEYS` (derived from `jarvis.setup.wizard.SECRETS`) | `jarvis/ui/web/provider_routes.py:54` | `POST /api/secrets/{key}` 404 — the key cannot even be stored |
| G3 | `PROVIDER_SECRET_CANDIDATES` dict | `jarvis/core/config.py:132` | `get_provider_secret(custom_id)` → `""`; `resolve_provider_endpoint()` returns a credential-less endpoint |
| G4 | entry-point registries (`jarvis.brain`, `jarvis.realtime`) | `jarvis/brain/provider_registry.py`, `jarvis/core/registry.py` | `BrainProviderRegistry.instantiate()` raises `KeyError`; realtime `list_plugins()` never yields the id. Entry points are fixed at `pip install` time — a user cannot add one at runtime |

Two more surfaces reject it *by construction*, not by lookup:

- `jarvis/missions/workers/api_agent_worker.py:97-112` — `_BRAIN_BY_PROVIDER` is
  a literal dict; `supports_api_agent_worker()` returns `False` for anything
  else, so a custom provider silently cannot run a mission worker.
- `jarvis/plugins/realtime/openai_realtime.py:1052` and
  `jarvis/plugins/realtime/gemini_live.py:452` — both construct their SDK client
  with `api_key=` only. There is **no** `base_url` parameter anywhere on the
  realtime path, and `resolve_provider_endpoint()` is never called there. The
  realtime tier is the one tier with zero endpoint indirection today.

Everything else is already generic. `ProviderCard` in `ApiKeysView.tsx` renders
whatever `/api/providers` returns; `BaseUrlField` (line 1731) already exists and
already persists through `PUT /api/providers/{id}/base-url`; `switch_provider.py`
resolves purely through `apply_provider_switch` → `get_spec` +
`is_credential_present`; `is_credential_present` (`app_control.py:190`) already
falls through to `all(get_secret(k) for k in spec.secret_keys)` when a provider
has no alias entry — which is exactly the custom case.

---

## 3. The model: a custom provider is an *instance of an adapter*

A user does not write a plugin. They pick one of a small closed set of **adapter
kinds** — the wire protocols Jarvis already implements — and supply the
instance data.

```
adapter kind (closed enum, ships with Jarvis)   +   instance data (user)
─────────────────────────────────────────────       ────────────────────
openai-chat        → _openai_base.stream_complete   id, label
anthropic-messages → _anthropic_base                base_url
gemini-generative  → plugins.brain.gemini           credential (keyring slot)
ollama-native      → plugins.brain.ollama           model / deep_model / tool_model
openai-realtime    → plugins.realtime.openai_...    capability flags
gemini-live        → plugins.realtime.gemini_live   tier
```

This is the capability-gating mandate applied literally (AP-21): the runtime
never branches on the provider *name*, it branches on the declared adapter and
the declared capabilities. A custom provider called `acme-proxy` speaking
`openai-chat` is indistinguishable from `nvidia` to every consumer.

**Why an enum and not "just a URL":** the four wire protocols differ in
authentication header, base-URL convention (`/v1` included or not), streaming
frame shape, and tool-call encoding. `_ENDPOINTS` in
`jarvis/brain/model_catalog.py:110` already documents these conventions per
provider; the adapter kind is that knowledge made selectable.

### 3.1 Adapter kind is a five-layer value — treat it as one

`adapter` crosses Python → TOML → Pydantic → REST/OpenAPI → TypeScript → UI
dropdown. That is precisely the shape that produced BUG-008 four times. It gets
the five-layer pattern **preemptively** (CLAUDE.md §5), with one source of truth
and a parity test:

```
jarvis/core/provider_adapters.py :: ADAPTERS          (Python, canonical)
    ↓ exported by GET /api/providers/custom/adapters   (REST)
    ↓ typed in  src/lib/customProviderApi.ts           (TS union, generated-checked)
    ↓ rendered  CustomProviderDialog                   (UI select)
tests/unit/core/test_adapter_kind_parity.py            (parity gate)
```

---

## 4. State schema

### 4.1 Python — `jarvis/core/config.py`

```python
# jarvis/core/provider_adapters.py  (new, tiny, import-light — AP-26)
AdapterKind = Literal[
    "openai-chat",         # OpenAI Chat-Completions wire format (vLLM, LM Studio,
                           # LiteLLM, Together, DeepSeek, Fireworks, most proxies)
    "anthropic-messages",  # Anthropic Messages API wire format
    "gemini-generative",   # Google Generative Language API
    "ollama-native",       # Ollama /api/chat + /api/tags
    "openai-realtime",     # OpenAI Realtime WebSocket
    "gemini-live",         # Gemini Live WebSocket
]

@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    kind: AdapterKind
    label: str
    tier: Literal["brain", "realtime"]
    module: str                  # dotted path of the adapter class
    cls: str
    base_url_required: bool      # True: no vendor default is guessable
    base_url_convention: str     # human text: "include /v1" / "server root" / …
    supports_tools: bool
    supports_vision_default: bool
    catalog_path: str | None     # e.g. "/models" — None = no model listing
    catalog_auth: Literal["bearer", "x-api-key", "query", "bearer_opt", "none"]
```

```python
# jarvis/core/config.py
class CustomProviderConfig(BaseModel):
    """One user-registered provider instance."""
    model_config = ConfigDict(extra="allow")   # AP-16: forward-compatible

    label: str = ""                    # UI display name; "" → the id
    adapter: str = "openai-chat"       # AdapterKind; validated at read time
    tier: Literal["brain", "realtime"] = "brain"
    base_url: str = ""                 # REQUIRED in practice; "" = not yet set
    secret_slot: str = ""              # keyring slot name; "" = keyless
    # Capability declarations — the user's honest statement about their server.
    # Gated on here, never on the id (AP-21).
    supports_tools: bool = True
    supports_vision: bool = False
    context_window: int = 32_768
    # Model pins. `model` doubles as the realtime model for realtime-tier rows.
    model: str = ""
    deep_model: str = ""
    tool_model: str = ""
    voice: str = ""                    # realtime tier only
    # Extra request headers (e.g. an org id, a gateway route header).
    # Values are NOT secrets — a secret belongs in the keyring slot.
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True

class BrainConfig(BaseModel):
    ...
    custom_providers: dict[str, CustomProviderConfig] = Field(default_factory=dict)
```

The row deliberately **does not hold the key**. `secret_slot` names a keyring
slot; the value lives in keyring → ENV → `.env` → 0600 file, exactly like every
built-in (AP-12, §5 "Secrets"). Default slot name is derived, never free-form:
`custom_<sanitized_id>_api_key`.

### 4.2 TOML — mock configuration

```toml
[brain]
primary       = "acme-proxy"       # a custom provider as the MAIN brain
deep_brain    = "gemini"           # cross-family fallback preserved (AP-22)

[brain.tool_model]
provider      = "acme-proxy"

[brain.realtime]
provider          = "self-hosted-realtime"
fallback_provider = "gemini-live"

[brain.worker]
provider = "acme-proxy"
model    = "qwen3.5-72b-instruct"

# ── Custom provider 1: a private OpenAI-compatible inference proxy ─────────
[brain.custom_providers.acme-proxy]
label            = "Acme internal gateway"
adapter          = "openai-chat"
tier             = "brain"
base_url         = "https://llm.acme.internal/v1"   # convention: include /v1
secret_slot      = "custom_acme_proxy_api_key"
supports_tools   = true
supports_vision  = true
context_window   = 128000
model            = "qwen3.5-72b-instruct"
deep_model       = "qwen3.5-235b-instruct"
tool_model       = "qwen3.5-72b-instruct"
enabled          = true

[brain.custom_providers.acme-proxy.headers]
"X-Acme-Route" = "eu-central"

# ── Custom provider 2: a LAN vLLM box, keyless ────────────────────────────
[brain.custom_providers.lab-vllm]
label           = "Lab vLLM (RTX box)"
adapter         = "openai-chat"
tier            = "brain"
base_url        = "http://192.168.1.40:8000/v1"
secret_slot     = ""                 # keyless — no credential required
supports_tools  = true
supports_vision = false
model           = "meta-llama/Llama-4-70B-Instruct"

# ── Custom provider 3: a self-hosted OpenAI-Realtime-compatible server ────
[brain.custom_providers.self-hosted-realtime]
label       = "Realtime gateway (on-prem)"
adapter     = "openai-realtime"
tier        = "realtime"
base_url    = "https://rt.acme.internal/v1"
secret_slot = "custom_self_hosted_realtime_api_key"
model       = "gpt-realtime-compatible"
voice       = "alloy"

# A LAN custom provider must stay OUT of the team proxy, or the proxy would
# hijack its endpoint (resolve_provider_endpoint precedence).
[team_proxy]
local_providers = ["ollama", "local-openai", "lab-vllm"]
```

### 4.3 Identifier validation (fail-closed)

`id` is used as a TOML table key, a URL path segment, a keyring slot fragment and
a plugin lookup key. Constrain it hard at the write boundary:

```python
_CUSTOM_ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
# and, additionally:
#  - must NOT collide with any built-in spec id or entry-point name
#  - must NOT start with a built-in family prefix that AUTH_PROVIDER_ALIASES
#    would then shadow
```

Base URL validation (`http(s)` only, no embedded credentials, no whitespace) is
covered in §9.

---

## 5. The six seams

Each seam is a *widening* of an existing lookup, not a new path. Every seam is
lazily evaluated and cached against the config mtime — nothing is loaded at
import or on the startup critical path (AP-26).

### S1 — Spec overlay: `get_spec()` sees custom rows

`jarvis/ui/web/provider_spec.py` gets a synthesizer. `PROVIDERS` stays exactly as
it is; every *consumer* moves from the tuple to a function.

```python
# jarvis/ui/web/provider_spec.py
def custom_specs(config=None) -> tuple[ProviderSpec, ...]:
    """Synthesize a ProviderSpec for every enabled [brain.custom_providers.*]."""
    out = []
    for pid, row in (config or load_config()).brain.custom_providers.items():
        if not row.enabled:
            continue
        ad = ADAPTERS[row.adapter]                    # unknown adapter → skip + log
        out.append(ProviderSpec(
            id=pid,
            label=row.label or pid,
            tier=row.tier,                            # "brain" | "realtime"
            auth_mode="api_key" if row.secret_slot else "none",
            secret_keys=(row.secret_slot,) if row.secret_slot else (),
            dashboard_url=None,
            credential_help=(
                f"Your own {ad.label} endpoint. Jarvis sends requests to the "
                f"server URL below using the {ad.label} wire format. The API key "
                f"is stored in your OS credential store, never in jarvis.toml."
            ),
            brain_switchable=(row.tier == "brain"),
            supports_base_url=True,                   # always editable
            default_base_url=None,                    # no vendor default exists
        ))
    return tuple(out)

def all_specs(config=None) -> tuple[ProviderSpec, ...]:
    return (*PROVIDERS, *custom_specs(config))

def get_spec(provider_id: str) -> ProviderSpec | None:
    for spec in all_specs():
        if spec.id == provider_id:
            return spec
    return None
```

`get_spec()` keeps its signature, so **every existing caller is fixed by this one
change**: `tool_model_routes._static_candidate_status`, `set_tool_model`,
`provider_routes._spec_to_payload`, `app_control.is_credential_present`,
`app_control.apply_provider_switch`, `set_provider_base_url`.

`all_secret_keys()` and `secret_slot_consumers()` switch from `PROVIDERS` to
`all_specs()` — which also closes G2 for free (§S3).

Two consumers must move from `for spec in PROVIDERS` to `all_specs()`
explicitly: the `/api/providers` list builder in `provider_routes.py:620` and
the wizard's provider enumeration.

### S2 — Endpoint resolution: already done, one line to add

`resolve_provider_endpoint()` reads `cfg.brain.providers[id].base_url`. Custom
rows store their URL in `cfg.brain.custom_providers[id].base_url`. Two options;
**recommended: mirror on write** — `set_provider_base_url()` writes the custom
row when the id is custom, and the resolver falls back:

```python
# jarvis/core/config.py :: resolve_provider_endpoint, after the team-proxy branch
    override = None
    prov = cfg_obj.brain.providers.get(provider_id)
    if prov is not None and prov.base_url:
        override = prov.base_url
    if not override:                                   # NEW
        custom = cfg_obj.brain.custom_providers.get(provider_id)
        if custom is not None and custom.base_url:
            override = custom.base_url
    base_url = override or vendor_default_base_url
```

Team-proxy precedence is unchanged and still wins — which is correct: a team
deployment routes custom providers through the proxy too, unless the id is
listed in `team_proxy.local_providers`.

### S3 — Credential resolution: dict → function

`PROVIDER_SECRET_CANDIDATES` is read in two places
(`get_provider_secret`, `provider_spec.secret_slot_consumers`) and iterated in
one. Keep the dict as the built-in table; add a resolver:

```python
# jarvis/core/config.py
def provider_secret_candidates(provider: str, config=None) -> tuple[tuple[str, str], ...]:
    builtin = PROVIDER_SECRET_CANDIDATES.get(provider)
    if builtin:
        return builtin
    row = (config or load_config()).brain.custom_providers.get(provider)
    if row is not None and row.secret_slot:
        return ((row.secret_slot, row.secret_slot.upper()),)
    return ()

def get_provider_secret(provider: str) -> str:
    overrides = _PROVIDER_SECRET_OVERRIDES.get()        # unchanged
    ...
    return get_secret_any(provider_secret_candidates(provider))
```

And G2 opens by widening the allowlist to spec-derived slots:

```python
# jarvis/ui/web/provider_routes.py
def _allowed_secret_keys() -> frozenset[str]:
    """Wizard slots + every slot declared by a spec, incl. custom rows."""
    return frozenset(s.key for s in WIZARD_SECRETS) | all_secret_keys()
```

`POST /api/secrets/{key}` then accepts `custom_acme_proxy_api_key` — and only
after the custom row declaring it exists. That ordering is deliberate: the key
slot is never free-form, so `POST /api/secrets/<arbitrary>` still 404s.

### S4 — Brain instantiation: adapters become instance-parameterized

Today every brain plugin hardcodes its own provider id when resolving its
endpoint (`local_openai.py:74`, `openrouter.py:60`, `nvidia.py:68`, …). Add one
optional constructor parameter, defaulting to the class's own name — backward
compatible, no call site changes:

```python
class LocalOpenAIBrain:
    name: str = "local-openai"

    def __init__(
        self,
        model: str | None = None,
        *,
        provider_id: str | None = None,        # NEW
        base_url: str | None = None,           # NEW — explicit override
        supports_tools: bool | None = None,    # NEW — declared capability
        supports_vision: bool | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._provider_id = provider_id or self.name
        ...

    def _resolve_root(self) -> str:
        ep = cfg.resolve_provider_endpoint(
            self._provider_id, vendor_default_base_url=self._base_url_default
        )
```

Then the registry resolves custom ids to a *bound* adapter:

```python
# jarvis/brain/provider_registry.py
class BrainProviderRegistry:
    def available(self) -> list[str]:
        self._load()
        return sorted({*self._classes, *_custom_brain_ids()})

    def get_class(self, name: str) -> type:
        self._load()
        if name in self._classes:
            return self._classes[name]
        row = _custom_row(name)                      # brain-tier custom rows only
        if row is None:
            raise KeyError(f"Brain provider '{name}' not found.")
        return _adapter_class(row.adapter)           # the shared adapter class

    def instantiate(self, name: str, **kwargs) -> Brain:
        row = _custom_row(name)
        if row is not None:
            kwargs.setdefault("provider_id", name)
            kwargs.setdefault("supports_tools", row.supports_tools)
            kwargs.setdefault("supports_vision", row.supports_vision)
            kwargs.setdefault("extra_headers", dict(row.headers))
            kwargs.setdefault("model", row.model or None)
        return self.get_class(name)(**kwargs)
```

`BrainManager`, `switch_provider`, `apply_provider_switch` and
`tool_model_routes._static_candidate_status` (which calls
`BrainProviderRegistry().available()` and `.get_class()`) all now resolve custom
ids with no further change.

`supports_tools` / `supports_vision` must be **instance** attributes on the
adapters, not only class attributes — `_static_candidate_status` reads
`getattr(provider_class, "supports_tools", None)` at line 127. Change that read
to prefer a spec/row-declared capability when the provider is custom, else the
class attribute; otherwise every custom `openai-chat` provider inherits whatever
the base class declares.

### S5 — Realtime: give the two realtime plugins an endpoint

This is the largest genuinely-new piece, because the realtime tier has no
endpoint indirection at all today.

**`jarvis/plugins/realtime/openai_realtime.py`:**

```python
class OpenAIRealtimeProvider:
    name = "openai-realtime"
    credential_family = "openai"
    supports_realtime = True
    credential_candidates = (...)                       # unchanged

    def __init__(
        self,
        *,
        api_key: str | None = None,
        provider_id: str | None = None,   # NEW
        base_url: str | None = None,      # NEW
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._provider_id = provider_id or self.name
        self._base_url = (base_url or "").strip() or None

    async def open_session(self, cfg: Any) -> _OpenAIRealtimeSession:
        ...
        ep = config.resolve_provider_endpoint(
            self._provider_id, vendor_default_base_url=self._base_url
        )
        client_kwargs: dict[str, Any] = {"api_key": self._api_key}
        if ep.base_url:
            # openai-python derives the realtime wss:// URL from base_url:
            # https://host/v1 → wss://host/v1/realtime. Http(s) in, ws(s) out.
            client_kwargs["base_url"] = ep.base_url
        client = AsyncOpenAI(**client_kwargs)
        connection_cm = client.realtime.connect(model=connect_model)
```

`_OpenAIRealtimeSession` needs **no change**: it only ever touches `connection`
and `client`, never a hostname. The session's rebuild path
(`_rebuild_transport`, line 911) re-enters through the same provider object, so
the custom endpoint survives a mid-call transport rebuild — verify this
explicitly in test, it is the subtle failure mode.

**`jarvis/plugins/realtime/gemini_live.py`:** same shape, different SDK call —
`genai.Client(api_key=..., http_options=types.HttpOptions(base_url=ep.base_url))`.

**`jarvis/realtime/factory.py`:** `_configured_provider_ids` currently intersects
with `list_plugins("jarvis.realtime")`, which is entry-point-only. Widen it:

```python
def _installed_realtime_ids(cfg) -> list[str]:
    return [*list_plugins(_GROUP), *_custom_realtime_ids(cfg)]

def _provider_candidates(cfg) -> list[Any]:
    for provider_id in _configured_provider_ids(cfg):
        row = _custom_row(cfg, provider_id)
        if row is not None:
            provider_cls = _adapter_class(row.adapter)          # realtime adapter
            api_key = cfg_mod.get_provider_secret(provider_id)  # custom slot
            if row.secret_slot and not api_key:
                continue                                        # keyless is allowed
            provider = provider_cls(
                api_key=api_key, provider_id=provider_id, base_url=row.base_url
            )
        else:
            ...                                                  # unchanged path
```

Everything downstream — `RealtimeVoiceSession`, the cross-family fallback list,
tool dispatch, the scrub gate — is provider-object-driven and needs no change.

**`_provider_family()`** (factory.py:79) derives a credential family from the id
prefix for the AP-22 same-family warning. A custom id has no meaningful prefix;
return the row's adapter kind as the family, or `"custom:<id>"` so two distinct
custom providers are never treated as one quota pool.

### S6 — Mission worker + model catalog

**`api_agent_worker.py`** — three literal tables become registry lookups:

```python
def supports_api_agent_worker(provider: str | None) -> bool:
    pid = (provider or "").strip().lower()
    if pid in _BRAIN_BY_PROVIDER:
        return True
    row = _custom_row(pid)
    return row is not None and row.tier == "brain" and row.supports_tools

def _build_brain(provider: str, model: str) -> Any:
    row = _custom_row(provider)
    if row is not None:
        return BrainProviderRegistry().instantiate(provider, model=model)
    mod_name, cls_name = _BRAIN_BY_PROVIDER[provider]
    ...
```

`_resolve_worker_model()` already reads `[brain.providers.<id>].model` at
precedence step 3 — extend that step to fall back to
`[brain.custom_providers.<id>].model`, and let `_DEFAULT_MODEL.get(prov, "")`
return `""` for custom providers. `""` is the right answer: an
`openai-chat` adapter with no model configured should surface the honest
"no model configured" error, never guess a foreign-family paid id (the AP-22
trap that once drained real credit).

The tool-capability guard (`_tool_incapable_message`) already fires honestly
when a custom model cannot call tools — no change, and it is the main safety net
for a user who declares `supports_tools = true` on a server that cannot.

**`model_catalog.py`** — `_ENDPOINTS` is keyed by provider id. Add a synthesizer
that derives a `_CatalogEndpoint` from the adapter descriptor:

```python
def _endpoint_for(provider: str) -> _CatalogEndpoint | None:
    ep = _ENDPOINTS.get(provider)
    if ep is not None:
        return ep
    row = _custom_row(provider)
    if row is None:
        return None
    ad = ADAPTERS[row.adapter]
    if ad.catalog_path is None:
        return None
    return _CatalogEndpoint(
        None, ad.catalog_path, ad.catalog_auth,
        secret_slot=(row.secret_slot, row.secret_slot.upper()) if row.secret_slot else None,
    )
```

A custom provider whose server has no `/models` endpoint degrades to a free-text
model field in the UI — honest, not broken.

---

## 6. REST surface

New router section in `jarvis/ui/web/provider_routes.py` (keeps the existing
`providers` tag, so CLI coverage is automatic per the CLI-first mandate):

| Method | Path | Purpose | Notes |
|---|---|---|---|
| `GET` | `/api/providers/custom/adapters` | The adapter catalog (the enum + its help text) | Powers the UI dropdown; the parity source for the TS union |
| `GET` | `/api/providers/custom` | List custom rows (never returns key values) | Masked preview via existing `masked_secret_preview` |
| `POST` | `/api/providers/custom` | Create a row | `x-jarvis-dangerous: True` |
| `PATCH` | `/api/providers/custom/{id}` | Update label / base URL / models / capabilities | `x-jarvis-dangerous: True` |
| `DELETE` | `/api/providers/custom/{id}` | Remove the row **and** its keyring slot | `x-jarvis-dangerous: True`; 409 if the id is an active tier |
| `POST` | `/api/providers/custom/{id}/probe` | One live round-trip against the endpoint | Reuses the existing `POST /providers/{id}/test` machinery |

The existing `POST /api/secrets/{slot}` handles the key — no new secret route.

**Curated CLI commands** (`docs/jarvis-cli.md`, per the generate-cli-command
checklist):

```
jarvis provider custom list
jarvis provider custom add --id acme-proxy --adapter openai-chat \
       --base-url https://llm.acme.internal/v1 --label "Acme internal gateway"
jarvis provider custom set-key acme-proxy      # prompts; never an argv value
jarvis provider custom probe acme-proxy
jarvis provider custom remove acme-proxy
```

`set-key` reads the secret from a TTY prompt or stdin, never from `argv` — an
argv key lands in shell history and the process table.

### Mock payloads

`POST /api/providers/custom`:

```json
{
  "id": "acme-proxy",
  "label": "Acme internal gateway",
  "adapter": "openai-chat",
  "tier": "brain",
  "base_url": "https://llm.acme.internal/v1",
  "supports_tools": true,
  "supports_vision": true,
  "context_window": 128000,
  "model": "qwen3.5-72b-instruct",
  "deep_model": "qwen3.5-235b-instruct",
  "headers": { "X-Acme-Route": "eu-central" }
}
```

→ `201`:

```json
{
  "ok": true,
  "id": "acme-proxy",
  "secret_slot": "custom_acme_proxy_api_key",
  "key_required": true,
  "key_present": false,
  "warnings": []
}
```

`GET /api/providers` then returns the custom row as an **ordinary descriptor** —
byte-identical in shape to a built-in card, plus one discriminator:

```json
{
  "id": "acme-proxy",
  "label": "Acme internal gateway",
  "tier": "brain",
  "auth_mode": "api_key",
  "secret_keys": ["custom_acme_proxy_api_key"],
  "secrets_set": { "custom_acme_proxy_api_key": true },
  "secrets_effective": { "custom_acme_proxy_api_key": true },
  "secret_shared_with": { "custom_acme_proxy_api_key": [] },
  "dashboard_url": null,
  "brain_switchable": true,
  "billing": "api",
  "supports_base_url": true,
  "default_base_url": null,
  "base_url": "https://llm.acme.internal/v1",
  "configured": true,
  "active": false,
  "recommended": false,
  "caution": null,
  "custom": true,
  "adapter": "openai-chat"
}
```

`"custom": true` exists **only** so the UI can show Edit/Remove affordances. No
backend logic may branch on it (AP-21) — behaviour branches on `adapter`,
`auth_mode`, `supports_base_url` and the declared capabilities.

---

## 7. Frontend

### 7.1 Type additions — `src/hooks/useProviders.ts`

```ts
export interface ProviderDescriptor {
  // … existing fields unchanged …
  /** User-registered provider. Presentation only — enables Edit/Remove. */
  custom?: boolean;
  /** Wire protocol this custom provider speaks. Absent on built-ins. */
  adapter?: AdapterKind;
}
```

### 7.2 New client — `src/lib/customProviderApi.ts`

Modelled on `agentAccountsApi.ts` (same `send<T>` + `detail()` shape, same
`cache: "no-store"` discipline — a stale provider list here shows the wrong
endpoint as active):

```ts
export type AdapterKind =
  | "openai-chat" | "anthropic-messages" | "gemini-generative"
  | "ollama-native" | "openai-realtime" | "gemini-live";

export interface AdapterInfo {
  kind: AdapterKind;
  label: string;
  tier: "brain" | "realtime";
  base_url_required: boolean;
  base_url_convention: string;   // "Include /v1" | "Server root, no /v1" | …
  supports_tools: boolean;
}

export interface CustomProvider {
  id: string;
  label: string;
  adapter: AdapterKind;
  tier: "brain" | "realtime";
  base_url: string;
  secret_slot: string;
  key_present: boolean;
  key_preview: string | null;    // "sk-…x9Q" — never the full value
  supports_tools: boolean;
  supports_vision: boolean;
  context_window: number;
  model: string;
  deep_model: string;
  tool_model: string;
  voice: string;
  headers: Record<string, string>;
  enabled: boolean;
}

export function fetchAdapters(): Promise<{ adapters: AdapterInfo[] }>;
export function fetchCustomProviders(): Promise<{ providers: CustomProvider[] }>;
export function createCustomProvider(body: NewCustomProvider): Promise<CustomProvider>;
export function updateCustomProvider(id: string, patch: Partial<CustomProvider>): Promise<CustomProvider>;
export function deleteCustomProvider(id: string): Promise<{ ok: true }>;
export function probeCustomProvider(id: string): Promise<{ ok: boolean; detail: string; models?: string[] }>;
```

**Note on the task brief:** `src/lib/agentBrand.ts` is *not* the provider brand
list — it derives the user-visible agent name from the configured wake word
(CLAUDE.md §4). It needs **no change**, and must not gain provider knowledge.
The provider list the brief means is the backend `provider_spec.py` catalog,
surfaced through `useProviders.ts`.

### 7.3 `ApiKeysView.tsx` changes

Deliberately small — the card machinery is already generic:

1. **`ProviderCategory`** (line 854) gains an `"Add custom provider"` button in
   the Brain and Realtime sections. Custom cards render through the *existing*
   `TierSection` → `ProviderCard` path with no special-casing, because the
   descriptor shape is identical.
2. **`ProviderCard`** (line 1177) renders `BaseUrlField` whenever
   `descriptor.supports_base_url` — already true, no change; and shows an
   overflow menu (Edit / Remove) when `descriptor.custom`.
3. **New `CustomProviderDialog`** — the one genuinely new component:
   id, label, adapter select (from `fetchAdapters()`), base URL with the
   adapter's convention as inline help, optional API key (posted to
   `/api/secrets/{slot}`, never into the row), capability toggles, model fields,
   optional headers, and a **Test connection** button wired to `/probe`.
4. **`RealtimeCategory`** (line 935) lists realtime-tier custom providers the
   same way; the realtime model + voice fields reuse the existing
   `/providers/{id}/realtime-options` route, which is spec-driven and therefore
   already works once S1 lands.

New i18n keys (English source, all supported locales per §1 of CLAUDE.md):
`apikeys_custom.add`, `.edit`, `.remove`, `.adapter`, `.adapter_help`,
`.base_url_required`, `.probe_ok`, `.probe_failed`, `.insecure_http_warning`,
`.remove_confirm`.

---

## 8. Behaviour matrix — what works after each seam

| Surface | Entry point | Seam(s) needed |
|---|---|---|
| Provider card renders, key saves | `GET /api/providers`, `POST /api/secrets/{k}` | S1, S3 |
| Base URL persists | `PUT /providers/{id}/base-url` | S1, S2 |
| Main brain / deep brain | `POST /api/brain/switch` → `apply_provider_switch` | S1, S3, S4 |
| Tool Model | `PUT /api/tool-model` | S1, S3, S4 |
| Router tier | `BrainManager` tier resolution | S1, S3, S4 |
| Model picker | `GET /providers/{id}/models` | S6 |
| Voice `switch-provider` (tts/stt/subagent) | `switch_provider.py` → `apply_provider_switch` | S1, S3, S4 |
| Mission worker (in-process API agent) | `api_agent_worker._build_brain` | S4, S6 |
| **Realtime session** | `realtime/factory.build_realtime_session` | S1, S3, S5 |
| Realtime model + voice pick | `PUT /providers/{id}/realtime-options` | S1 |
| Wiki / awareness provider chain | `memory/wiki/provider_chain.py` | S2, S3 (already calls `resolve_provider_endpoint`) |

Note the brain tier stays **locked against Jarvis-initiated switching**
(`switch_provider.py:102-121`) — a custom provider does not change that. The user
selects it in the app or via the CLI; the assistant never switches its own brain.

---

## 9. Security, safety and doctrine compliance

| Concern | Decision |
|---|---|
| **AP-2** — no secrets via voice/chat | Custom provider CRUD is REST/UI/CLI only. The `switch-provider` tool may *select* a custom provider (it already only takes an id); it may never create one or set a key. Nothing in the tool broker exports the custom-provider routes to a worker (config-mutation tools are never exported, ADR-0025). |
| **AP-12** — no keys in `jarvis.toml` | The row stores a slot *name*; the value goes through `set_secret` → keyring → ENV → `.env` → 0600 file. A `POST /providers/custom` body carrying an `api_key` field is rejected at the schema level, not silently persisted. |
| **AP-7** — atomic config writes | All writes go through a new `config_writer.set_custom_provider()` / `delete_custom_provider()` following the existing lock + tempfile + BOM discipline. Never a direct TOML write. |
| **AP-16** — self-mod pre-validate | `CustomProviderConfig` carries `ConfigDict(extra="allow")`, and `BrainConfig.custom_providers` is a plain dict field, so an unknown future key never fails pre-validate and bricks boot. |
| **AP-21 / AP-22** | The runtime branches on `adapter` + declared capabilities, never on the custom id. A custom provider participates in the normal key-aware fallback chain and can be crossed *from* and *to*. The UI warns when a custom provider is the sole entry across primary/deep/router (same-family brick). |
| **AP-26** — startup budget | Custom rows are read from the already-loaded config; the spec overlay is a pure function with an mtime-keyed cache. No import, no probe, no network call on the boot path. The `/probe` round-trip is explicit and user-initiated. |
| **Arbitrary egress (SSRF-shaped)** | The user is configuring *their own* client to talk to *their own* server, so this is not privilege escalation — but the guard rails matter: reject non-`http(s)` schemes, reject credentials embedded in the URL (`https://user:pass@host`), reject whitespace/control characters, cap length at 500. Warn (do not block) on plain `http://` to a **non-loopback, non-private** host: the API key would cross the network in cleartext. Loopback and RFC1918 over `http://` is the normal LAN case and stays warning-free. |
| **Header injection** | `headers` values are validated against `^[\x20-\x7E]*$` and a denylist of `authorization` / `x-api-key` / `cookie` keys — the credential path is the keyring slot, not a free-form header. |
| **Deletion** | `DELETE` removes the row **and** its keyring slot, and 409s if the id is currently the active brain / tool model / realtime provider — mirroring how the built-in cards warn before deleting a shared key. |
| **Trust boundary reminder** | A custom endpoint receives full conversation content, tool schemas and tool results. The dialog states this in one plain sentence before the first save. |

### Open-source universality (§3 "definition of done")

1. **Fresh install, one arbitrary key** — a user with *only* a custom endpoint
   (say a local vLLM box, keyless) reaches a working chat + voice + worker path.
   This is the strongest argument for the feature: it removes the last hard
   dependency on a vendor account.
2. **Headless Linux (`python:3.11-slim`)** — pure config + HTTP; the keyring
   falls back to the 0600 file via `_ensure_keyring_backend`. Works.
3. **macOS** — identical. No OS-specific code is introduced, so no
   `docs/os-parity.md` entry is required.
4. **Cross-family fallback** — a dead custom endpoint fails fast (connect
   timeout on the `openai-chat` adapter is already 2s, mirroring
   `local_openai.CLIENT_TIMEOUT`) and the chain crosses to whatever else the
   user has.

---

## 10. Test matrix

| Test | Guards |
|---|---|
| `tests/unit/core/test_adapter_kind_parity.py` | Python enum ≡ REST payload ≡ TS union ≡ UI options (BUG-008 class, five-layer) |
| `tests/unit/core/test_custom_provider_config.py` | id regex, collision with built-ins, base-URL validation, header denylist, `extra="allow"` round-trip |
| `tests/unit/core/test_provider_endpoint.py` (extend) | custom `base_url` override; team proxy still wins; `local_providers` opt-out |
| `tests/unit/web/test_custom_provider_routes.py` | CRUD, 409 on active-tier delete, secret slot allowlist widening, `x-jarvis-dangerous` metadata |
| `tests/unit/brain/test_custom_provider_registry.py` | `available()` / `get_class()` / `instantiate()` for custom ids; capability flags reach the instance |
| `tests/unit/brain/test_routing.py` (extend) | a custom provider as tool model / router tier; `ROUTER_TOOLS` unchanged (ADR-0011) |
| `tests/unit/realtime/test_custom_realtime_endpoint.py` | `base_url` reaches `AsyncOpenAI` / `genai.Client`; **survives `_rebuild_transport`**; keyless custom realtime is skipped, not crashed |
| `tests/unit/missions/test_api_agent_custom_provider.py` | `supports_api_agent_worker`, `_build_brain`, `_resolve_worker_model` precedence, `""` default model |
| `tests/unit/brain/test_model_catalog_local.py` (extend) | custom catalog URL composition per adapter convention; no-catalog adapter degrades to free text |
| `tests/unit/web/test_provider_switch_custom.py` | `apply_provider_switch` accepts a custom brain id; the brain-tier lock for the *tool* still holds |
| Frontend: `ApiKeysView.custom.test.tsx` | dialog validation, card renders through the generic path, delete confirm, http-warning copy |
| `scripts/ci/check_cli_coverage.py` | the new routes are mounted + tagged |

---

## 11. Implementation waves

**W1 — Schema + spec overlay (no behaviour change yet).**
`provider_adapters.py`, `CustomProviderConfig`, `config_writer` setters, S1
(`all_specs`/`get_spec`), S2 (endpoint fallback), S3 (secret candidates +
allowlist). Deliverable: a hand-written TOML row produces a working provider
card whose key saves and whose base URL persists. Fully testable without any UI.

**W2 — Pipeline mode.** S4 (adapter `provider_id`/capability parameters +
registry overlay), S6 model catalog. Deliverable: a custom provider is
selectable as brain, deep brain, router and Tool Model, and the model picker
fills in.

**W3 — Realtime mode.** S5: `base_url` on both realtime adapters, factory
widening, `_provider_family` for custom ids, transport-rebuild coverage.
Deliverable: a self-hosted realtime endpoint carries a full voice call including
tool calls and mid-call fallback.

**W4 — Mission workers.** `api_agent_worker` registry lookups and model
precedence. Deliverable: a mission runs end-to-end on a custom provider, with
the honest tool-incapable failure preserved.

**W5 — Frontend + CLI + docs.** `customProviderApi.ts`,
`CustomProviderDialog`, `ApiKeysView` hooks, i18n keys in all locales, curated
CLI commands, `docs/jarvis-cli.md` + `docs/commands-reference.md` regeneration,
and an ADR (next free number: **ADR-0032**) recording the adapter-instance
decision and the rejected alternatives.

W1–W2 are independently shippable and already deliver most of the user value
(local LLMs and private proxies in Pipeline Mode). W3 is the only wave with real
protocol risk, because it is the first endpoint indirection on the realtime path.

---

## 12. Explicitly out of scope

- `jarvis/missions/workers/api_agent_tools.py` — worker tool security stays as
  is; custom providers change *which model* runs the loop, never *which tools*
  it may call.
- `jarvis/realtime/tools.py` — realtime tool dispatch is credential-free and
  provider-neutral; it needs no change and must not learn about custom
  credentials.
- Refactoring the built-in `PROVIDERS` tuple into config. The static catalog
  stays: it carries curated help text, billing semantics, dashboard links and
  recommendation badges that a synthesized row cannot have.
- Custom **TTS** and **STT** providers. The same seam pattern extends there
  later (both tiers already route through `resolve_provider_endpoint`), but
  their capability surfaces — voice rosters, audio formats — need their own
  design pass.
- OAuth / subscription-login custom providers. `auth_mode` stays `api_key` or
  `none`; a custom OAuth flow is a plugin, not a config row.

---

## 13. Open decisions for the maintainer

1. **Adapter breadth at launch.** Recommended: ship `openai-chat` alone in W1–W2
   and add the other five in W3+. `openai-chat` covers vLLM, LM Studio,
   llama.cpp, LiteLLM, Together, DeepSeek, Fireworks and essentially every
   proxy — the remaining kinds serve narrower cases and each carries its own
   base-URL convention to get wrong.
2. **Where custom cards appear.** Recommended: inline in the existing Brain and
   Realtime sections, sorted after the built-ins. A separate "Custom" tab hides
   them from exactly the comparison the user is making when choosing a provider.
3. **Should a custom provider be allowed as `brain.primary` on a fresh install?**
   Recommended: yes, with no extra gate. Blocking it would re-create the
   vendor dependency this feature exists to remove; the honest-failure paths
   (`_tool_incapable_message`, the fast-fail timeout, cross-family fallback)
   are already the safety net.
