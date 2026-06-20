# keyproxy

A small, vendor-aware **streaming reverse proxy for LLM API keys**. It holds the
real vendor keys server-side and lets team clients reach the vendors with a
**per-user token** instead of the real key. The proxy swaps the token for the
real key, forwards to the vendor, streams the response back unchanged, and
records best-effort usage per token.

It is a self-contained standalone service: **zero dependency on the `jarvis`
package**, boots on a fresh `python:3.11-slim` with only `fastapi`, `httpx`, and
`uvicorn`.

## Why

Without it, every client holds its own real vendor credentials. With it, the
real keys live in one place (this proxy), clients carry only a revocable
per-user token, and you get per-token usage attribution for free.

## How it works

```
client (base_url = https://keys.example.com/p/openai, api_key = <per-user token>)
   │  HTTPS
   ▼
keyproxy   /p/{provider_id}/{path}
   1. extract the inbound per-user token (per the vendor's credential slot)
   2. verify the token  → 401 if missing / unknown / revoked (constant-time)
   3. look up (vendor, real_base, real_key) for the provider → 404 if unknown
   4. rewrite the credential to the real key, forward to real_base + "/" + path
   5. stream the upstream response back unchanged (SSE-safe)
   6. best-effort: record usage (tokens, est. cost) — never blocks the response
```

### Supported providers (the wire contract)

| `provider_id` | vendor | default real base URL |
|---|---|---|
| `claude-api` | anthropic | `https://api.anthropic.com` |
| `openai` | openai-compatible | `https://api.openai.com/v1` |
| `openrouter` | openai-compatible | `https://openrouter.ai/api/v1` |
| `grok` | openai-compatible | `https://api.x.ai/v1` |
| `gemini` | gemini | `https://generativelanguage.googleapis.com` |
| `groq-api` | openai-compatible | `https://api.groq.com/openai/v1` |

Per-vendor credential placement:

| vendor | inbound token slot | outbound (real key) slot |
|---|---|---|
| openai-compatible | `Authorization: Bearer <token>` | `Authorization: Bearer <real>` |
| anthropic | `x-api-key: <token>` | `x-api-key: <real>` |
| gemini | `x-goog-api-key` header **or** `?key=` | `x-goog-api-key: <real>` (query `key` dropped) |

## Configuration (ENV only)

Real keys and the admin key are read from the environment and are **never**
written to disk or logs.

| Variable | Purpose |
|---|---|
| `KEYPROXY_<PROVIDER>_KEY` | real vendor key — enables that provider (e.g. `KEYPROXY_OPENAI_KEY`, `KEYPROXY_CLAUDE_API_KEY`, `KEYPROXY_GROQ_API_KEY`) |
| `KEYPROXY_<PROVIDER>_BASE` | optional base-URL override for that provider |
| `KEYPROXY_ADMIN_KEY` | bearer for the `/admin/*` endpoints (admin is disabled if unset) |
| `KEYPROXY_TLS_TERMINATED` | set to `1` once TLS is terminated by your platform / reverse proxy in front of the proxy |
| `KEYPROXY_ALLOW_INSECURE` | `1` to allow token auth over plain HTTP — **dev only** |
| `KEYPROXY_DB_PATH` | SQLite path (default `~/.keyproxy/keyproxy.sqlite`; the Docker image uses `/data/keyproxy.sqlite`) |

`<PROVIDER>` is the `provider_id` upper-cased with `-` → `_`
(e.g. `claude-api` → `KEYPROXY_CLAUDE_API_KEY`).

A known provider with no configured key is **not available** (fail closed — the
proxy never invents a key). Only providers with a real key respond.

## TLS (important)

The proxy authenticates clients with bearer tokens, which must not travel over
plaintext HTTP. **TLS is terminated by your platform / reverse proxy in front of
the container** (Caddy, nginx, a cloud load balancer, Fly/Render/Railway TLS,
etc.). Assert that with `KEYPROXY_TLS_TERMINATED=1`. If neither that nor
`KEYPROXY_ALLOW_INSECURE=1` is set, the proxy **refuses to start**.

## Run

### Docker (recommended)

```bash
docker build -t keyproxy ./keyproxy
docker run -p 8080:8080 \
  -v keyproxy-data:/data \
  -e KEYPROXY_ADMIN_KEY="$(openssl rand -hex 32)" \
  -e KEYPROXY_OPENAI_KEY="sk-..." \
  -e KEYPROXY_CLAUDE_API_KEY="sk-ant-..." \
  keyproxy
```

The image sets `KEYPROXY_TLS_TERMINATED=1` (you terminate TLS in front of it)
and stores the DB on the `/data` volume.

### Local (dev)

```bash
pip install -r keyproxy/requirements.txt
KEYPROXY_ALLOW_INSECURE=1 \
KEYPROXY_ADMIN_KEY=dev-admin \
KEYPROXY_OPENAI_KEY=sk-... \
python -m uvicorn keyproxy.app:app --host 0.0.0.0 --port 8080
```

## Admin

Issue / list / revoke tokens and read usage either over HTTP (bearer-guarded by
`KEYPROXY_ADMIN_KEY`) or with the bundled CLI (operates directly on the store).

### HTTP

```bash
# issue a token (plaintext returned ONCE)
curl -s -X POST https://keys.example.com/admin/tokens \
  -H "authorization: Bearer $KEYPROXY_ADMIN_KEY" \
  -H 'content-type: application/json' \
  -d '{"label":"alice-laptop"}'

curl -s https://keys.example.com/admin/tokens     -H "authorization: Bearer $KEYPROXY_ADMIN_KEY"
curl -s -X DELETE https://keys.example.com/admin/tokens/<id> -H "authorization: Bearer $KEYPROXY_ADMIN_KEY"
curl -s "https://keys.example.com/admin/usage?token_id=<id>" -H "authorization: Bearer $KEYPROXY_ADMIN_KEY"
# which providers have a real key loaded (admin-only — never on /healthz)
curl -s https://keys.example.com/admin/providers   -H "authorization: Bearer $KEYPROXY_ADMIN_KEY"
```

`GET /healthz` is the only unauthenticated endpoint; it returns exactly
`{"status": "ok"}` and reveals nothing about which providers or keys are loaded.

### CLI

```bash
python -m keyproxy issue-token --label alice-laptop
python -m keyproxy list-tokens
python -m keyproxy revoke <token-id>
python -m keyproxy usage [--token <id>] [--since <unix>] [--until <unix>]
# add --json for machine-readable output, --db <path> to target a specific store
```

In Docker, run the CLI against the same volume / DB path:

```bash
docker run --rm -v keyproxy-data:/data keyproxy \
  python -m keyproxy --db /data/keyproxy.sqlite issue-token --label alice
```

## Pointing a client at the proxy

A client uses the proxy by setting, per provider, its `base_url` to
`<proxy>/p/<provider_id>` and its API key to the per-user token. For example, an
OpenAI-SDK client:

```python
from openai import OpenAI
client = OpenAI(
    base_url="https://keys.example.com/p/openai",
    api_key="<per-user token issued by the proxy>",
)
```

The same shape works for every provider in the table above (the SDK places the
token in the vendor's normal credential slot; the proxy swaps it for the real
key).

## Security

- Real vendor keys never leave the proxy and are never logged.
- Tokens are stored as SHA-256 only; the plaintext is shown once at issue time
  and is unrecoverable afterwards. Verification is constant-time; revocation is
  instant.
- Fail-closed everywhere: missing/unknown/revoked token → 401; unknown provider
  → 404; the proxy never falls back to "no auth".
- Only an allowlist of headers is forwarded upstream; hop-by-hop and inbound
  auth headers are stripped before the real credential is set.

## Usage metering

Best-effort token counts are parsed from the upstream response (OpenAI/compatible
`usage`, Anthropic `message_start`/`message_delta` usage, Gemini
`usageMetadata`). Streaming OpenAI usage requires the client to send
`stream_options.include_usage`. A parse miss records the call with null counts —
it never fails the request. Estimated cost uses a small static per-model price
table; an unknown model records a null cost.

## Tests

```bash
py -3.11 -m pytest keyproxy/tests/ -q
py -3.11 -m ruff check keyproxy/
```
