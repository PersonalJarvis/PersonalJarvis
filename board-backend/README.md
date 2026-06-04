# Jarvis Board — Federation Backend

Standalone FastAPI service that accepts signed Board aggregates from a
Jarvis instance. Phase C of the Board plan.

> **Privacy is mandatory:** The server **never** stores
> voice transcripts, tool inputs/outputs, or raw event content.
> Pydantic `extra='forbid'` rejects such pushes with 422 — see
> `tests/test_routes.py::test_no_pii_in_sync_payload`.

---

## TL;DR — three setup scenarios

| Where? | Effort | When it makes sense |
|---|---|---|
| **Localhost** | 5 minutes | First trial, local friend federation on the same machine. |
| **Raspberry Pi (arm64)** | 30 minutes | Always-on home backend, no cloud vendor. |
| **Hetzner VPS + Caddy (TLS)** | 1 hour | Public friend federation over the internet, automatic Let's Encrypt. |

All three use the same container — the differences are in the
reverse proxy + DNS.

---

## What you need

- **Docker** + **Docker Compose**.
- An admin token. Recommended: 32+ random bytes hex.
  ```sh
  python -c "import secrets; print(secrets.token_hex(32))"
  ```
- Your Jarvis instance — it then registers itself once with the backend
  using the admin token.

---

## Scenario A — Localhost

Fastest start on the machine on which Jarvis itself runs.

```sh
cd board-backend
cp .env.example .env
# edit .env: JARVIS_BOARD_ADMIN_TOKEN=<your-hex>
docker compose up -d --build

# Smoke test
curl http://localhost:8765/healthz
# -> {"ok":true,"version":"0.1.0","schema_ok":true}
```

Then, in the local Jarvis:

```sh
# Write the admin token into the Credential Manager
python -c "import keyring; keyring.set_password('jarvis-board', 'admin_token', '<your-hex>')"
```

Plus in `jarvis.toml`:

```toml
[board.federation]
enabled = true
backend_url = "http://localhost:8765"
sync_interval_s = 60
display_name = "Mein Desktop"
```

Restart Jarvis — the `SyncClient` registers on the first tick and
pushes every 60 s.

---

## Scenario B — Raspberry Pi (arm64)

The container builds **multi-arch** — on a Pi 4/5 (aarch64) the
same image runs directly.

```sh
# On the Pi:
git clone <your-jarvis-repo> jarvis
cd jarvis/board-backend
cp .env.example .env
# set token + port, port 8765 is also OK on the LAN
docker compose up -d --build
```

Test from the desktop (same LAN):

```sh
curl http://raspi.local:8765/healthz
```

And in the Jarvis `jarvis.toml`:

```toml
[board.federation]
enabled = true
backend_url = "http://raspi.local:8765"
```

> If `mDNS` does not work, replace `raspi.local` with the
> IP address (`hostname -I` on the Pi).

### Push the multi-arch build (optional)

If you want to push the image to your own registry:

```sh
docker buildx create --use   # one-time
docker buildx build --platform linux/amd64,linux/arm64 \
  -t ghcr.io/<your-user>/jarvis-board-backend:0.1.0 . --push
```

---

## Scenario C — Hetzner VPS with Caddy (TLS)

For friend federation over the real internet you need HTTPS — and
that without manual cert renewal.

### One-stop `docker-compose.yml` with Caddy

Create on the VPS:

```
/opt/jarvis-board/
  docker-compose.yml
  .env
  Caddyfile
```

`docker-compose.yml`:

```yaml
services:
  backend:
    image: ghcr.io/<your-user>/jarvis-board-backend:0.1.0
    restart: unless-stopped
    environment:
      JARVIS_BOARD_ADMIN_TOKEN: ${JARVIS_BOARD_ADMIN_TOKEN:?}
      JARVIS_BOARD_DB_PATH: /data/board.db
    volumes:
      - db_data:/data
    expose:
      - "8765"

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config

volumes:
  db_data:
  caddy_data:
  caddy_config:
```

`Caddyfile`:

```
board.deine-domain.tld {
    reverse_proxy backend:8765
    encode zstd gzip

    # Optional: additional rate limit on register.
    @register path /api/v1/identity/register
    handle @register {
        header Cache-Control "no-store"
    }
}
```

Setup:

```sh
# Point a DNS A record for board.deine-domain.tld at the VPS,
# then:
cd /opt/jarvis-board
echo "JARVIS_BOARD_ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > .env
docker compose up -d
```

Caddy automatically obtains a Let's Encrypt certificate. Test:

```sh
curl https://board.deine-domain.tld/healthz
# -> {"ok":true,"version":"0.1.0","schema_ok":true}
```

In the local Jarvis `jarvis.toml`:

```toml
[board.federation]
enabled = true
backend_url = "https://board.deine-domain.tld"
sync_interval_s = 60
display_name = "Mein Desktop"
```

---

## Ops

### Logs

```sh
docker compose logs -f backend
```

### Backup

The DB is `db_data:/data/board.db` (named volume). Snapshot:

```sh
docker run --rm -v jarvis-board_db_data:/src -v "$PWD":/dst \
  alpine sh -c 'cp /src/board.db /dst/board.db.$(date +%Y-%m-%d)'
```

### Update to a new version

```sh
git pull
docker compose pull           # for remote image
docker compose up -d --build  # for local build
```

The schema is additive (`create_all`), data is preserved.

---

## Security at a glance

| Risk | Mitigation |
|---|---|
| Brute force against the admin token | Constant-time comparison + rate limit (10/min/IP). |
| Replay of old pushes | `payload.ts_ms` must lie within +/- 5 min. |
| Tampering after signing | Re-canonicalize + Ed25519 verify on the server. |
| PII leak on a bug in the local filter | Pydantic `extra='forbid'` on every schema layer. |
| Compromised container | Non-root user (uid 1000), only `/data` writable. |

The associated tests live in `tests/test_routes.py` —
``test_signed_sync_rejects_tampered_payload``, ``..._old_timestamp``,
``..._unregistered_pubkey``, ``test_no_pii_in_sync_payload`` and co.

---

## License

Apache-2.0. See `pyproject.toml`.
