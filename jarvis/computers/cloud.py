"""Import servers from a hosting account: Hostinger, Hetzner Cloud, DigitalOcean.

The user saves one API token per provider (keyring, never ``jarvis.toml``);
Jarvis lists the account's servers so adding one is a click instead of copying
an IP. Hostinger can also plant Jarvis's public key on an existing VM through
its API — no password ever needed. Hetzner and DigitalOcean only accept keys
when a server is created, so an imported server there finishes with a one-time
password step or a pasted key.

Provider error bodies never leave this module (AP-34): a failure becomes a
sentence chosen from the HTTP status alone.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Final, Literal

from jarvis.core.config import delete_secret, get_secret, set_secret
from jarvis.core.http_pool import HttpClientPool

log = logging.getLogger(__name__)

CloudProviderId = Literal["hostinger", "hetzner", "digitalocean", "vultr", "linode"]

_POOL = HttpClientPool(timeout_s=20.0)
_MAX_PAGES = 20


@dataclass(frozen=True)
class CloudProvider:
    id: CloudProviderId
    name: str
    keyring_slot: str
    env_var: str
    console_url: str
    setup_hint: str
    #: True when Jarvis can put its key on an EXISTING server through the API.
    attaches_keys: bool
    base_url: str
    #: What the provider calls the credential, as its own panel names it.
    credential_label: str = "API token"


PROVIDERS: Final[dict[CloudProviderId, CloudProvider]] = {
    "hostinger": CloudProvider(
        id="hostinger",
        name="Hostinger",
        keyring_slot="hostinger_api_token",
        env_var="HOSTINGER_API_TOKEN",
        console_url="https://hpanel.hostinger.com/profile/api",
        setup_hint="hPanel -> Profile -> API -> New token",
        attaches_keys=True,
        base_url="https://developers.hostinger.com",
    ),
    "hetzner": CloudProvider(
        id="hetzner",
        name="Hetzner Cloud",
        keyring_slot="hetzner_api_token",
        env_var="HCLOUD_TOKEN",
        console_url="https://console.hetzner.cloud/",
        setup_hint="Console -> your project -> Security -> API tokens (Read is enough)",
        attaches_keys=False,
        base_url="https://api.hetzner.cloud",
    ),
    "digitalocean": CloudProvider(
        id="digitalocean",
        name="DigitalOcean",
        keyring_slot="digitalocean_api_token",
        env_var="DIGITALOCEAN_TOKEN",
        console_url="https://cloud.digitalocean.com/account/api/tokens",
        setup_hint="Control panel -> API -> Tokens (scope droplet:read)",
        attaches_keys=False,
        base_url="https://api.digitalocean.com",
        credential_label="Personal access token",
    ),
    "vultr": CloudProvider(
        id="vultr",
        name="Vultr",
        keyring_slot="vultr_api_token",
        env_var="VULTR_API_KEY",
        console_url="https://my.vultr.com/settings/#settingsapi",
        setup_hint="Account -> API -> Enable API, then allow your IP (or all IPv4)",
        attaches_keys=False,
        base_url="https://api.vultr.com",
        credential_label="API key",
    ),
    "linode": CloudProvider(
        id="linode",
        name="Akamai Cloud (Linode)",
        keyring_slot="linode_api_token",
        env_var="LINODE_TOKEN",
        console_url="https://cloud.linode.com/profile/tokens",
        setup_hint="Profile -> API Tokens -> Create a Personal Access Token (Linodes: Read Only)",
        attaches_keys=False,
        base_url="https://api.linode.com",
        credential_label="Personal access token",
    ),
}


class CloudError(Exception):
    """A provider call failed; ``message`` is safe to show the user."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class CloudServer:
    """One server as the provider lists it."""

    id: str
    name: str
    host: str | None
    status: str
    running: bool
    os: str | None
    plan: str | None
    region: str | None
    cpus: int | None
    memory_mb: int | None
    disk_gb: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- tokens ------------------------------------------------------------------


def provider(provider_id: str) -> CloudProvider:
    if provider_id not in PROVIDERS:
        raise CloudError(f"Unknown provider: {provider_id}")
    return PROVIDERS[provider_id]  # type: ignore[index]


def token(provider_id: str) -> str | None:
    spec = provider(provider_id)
    return get_secret(spec.keyring_slot, env_fallback=spec.env_var)


def save_token(provider_id: str, value: str) -> None:
    spec = provider(provider_id)
    if not set_secret(spec.keyring_slot, value.strip()):
        raise CloudError("The token could not be saved to the keyring.")


def forget_token(provider_id: str) -> None:
    spec = provider(provider_id)
    if not delete_secret(spec.keyring_slot):
        raise CloudError("The token could not be removed from the keyring.")


# -- HTTP --------------------------------------------------------------------


def _status_message(spec: CloudProvider, status: int) -> str:
    if status in (401, 403):
        return f"{spec.name} refused the API token. Create a new one and save it again."
    if status == 404:
        return f"{spec.name} does not know this server any more."
    if status == 429:
        return f"{spec.name} is rate-limiting requests. Try again in a minute."
    if status >= 500:
        return f"{spec.name} is having trouble right now (HTTP {status})."
    return f"{spec.name} rejected the request (HTTP {status})."


async def _request(
    spec: CloudProvider,
    api_token: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    client: Any | None = None,
) -> Any:
    import httpx

    http = client if client is not None else _POOL.client()
    url = path if path.startswith("http") else f"{spec.base_url}{path}"
    try:
        response = await http.request(
            method,
            url,
            params=params,
            json=body,
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
        )
    except httpx.TimeoutException as exc:
        raise CloudError(f"{spec.name} did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise CloudError(f"{spec.name} could not be reached.") from exc
    if response.status_code >= 400:
        # The body may echo the token or account details: log the status only.
        log.info("computers: %s %s -> HTTP %s", spec.id, path, response.status_code)
        raise CloudError(_status_message(spec, response.status_code), status=response.status_code)
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise CloudError(f"{spec.name} sent an answer Jarvis cannot read.") from exc


# -- parsers (pure; covered by unit tests) ------------------------------------


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):  # a missing number is shown as unknown
        return None


def parse_hostinger(vms: Any, data_centers: dict[int, str] | None = None) -> list[CloudServer]:
    servers: list[CloudServer] = []
    for vm in vms if isinstance(vms, list) else []:
        if not isinstance(vm, dict) or vm.get("id") is None:
            continue
        ipv4 = vm.get("ipv4") or []
        host = next(
            (ip.get("address") for ip in ipv4 if isinstance(ip, dict) and ip.get("address")),
            None,
        )
        template = vm.get("template") or {}
        disk_mb = _as_int(vm.get("disk"))
        dc_id = _as_int(vm.get("data_center_id"))
        state = str(vm.get("state") or "unknown")
        servers.append(
            CloudServer(
                id=str(vm["id"]),
                name=str(vm.get("hostname") or f"vps-{vm['id']}"),
                host=host,
                status=state,
                running=state == "running",
                os=template.get("name") if isinstance(template, dict) else None,
                plan=vm.get("plan"),
                region=(data_centers or {}).get(dc_id) if dc_id is not None else None,
                cpus=_as_int(vm.get("cpus")),
                memory_mb=_as_int(vm.get("memory")),
                disk_gb=round(disk_mb / 1024, 1) if disk_mb else None,
            )
        )
    return servers


def parse_hetzner(payload: Any) -> list[CloudServer]:
    servers: list[CloudServer] = []
    for row in (payload or {}).get("servers", []) if isinstance(payload, dict) else []:
        public = (row.get("public_net") or {}).get("ipv4") or {}
        server_type = row.get("server_type") or {}
        location = row.get("location") or {}
        image = row.get("image") or {}
        memory_gb = server_type.get("memory")
        status = str(row.get("status") or "unknown")
        servers.append(
            CloudServer(
                id=str(row.get("id")),
                name=str(row.get("name") or row.get("id")),
                host=public.get("ip"),
                status=status,
                running=status == "running",
                os=image.get("description") or image.get("name"),
                plan=server_type.get("name"),
                region=location.get("city") or location.get("name"),
                cpus=_as_int(server_type.get("cores")),
                memory_mb=int(float(memory_gb) * 1024) if memory_gb is not None else None,
                disk_gb=float(server_type["disk"]) if server_type.get("disk") is not None else None,
            )
        )
    return servers


def parse_digitalocean(payload: Any) -> list[CloudServer]:
    servers: list[CloudServer] = []
    for row in (payload or {}).get("droplets", []) if isinstance(payload, dict) else []:
        networks = ((row.get("networks") or {}).get("v4")) or []
        host = next(
            (
                n.get("ip_address")
                for n in networks
                if isinstance(n, dict) and n.get("type") == "public"
            ),
            None,
        )
        image = row.get("image") or {}
        region = row.get("region") or {}
        status = str(row.get("status") or "unknown")
        os_label = " ".join(p for p in (image.get("distribution"), image.get("name")) if p) or None
        servers.append(
            CloudServer(
                id=str(row.get("id")),
                name=str(row.get("name") or row.get("id")),
                host=host,
                status=status,
                running=status == "active",
                os=os_label,
                plan=row.get("size_slug"),
                region=region.get("name") or region.get("slug"),
                cpus=_as_int(row.get("vcpus")),
                memory_mb=_as_int(row.get("memory")),
                disk_gb=float(row["disk"]) if row.get("disk") is not None else None,
            )
        )
    return servers


def parse_vultr(payload: Any) -> list[CloudServer]:
    servers: list[CloudServer] = []
    for row in (payload or {}).get("instances", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        ip = row.get("main_ip")
        status = str(row.get("power_status") or row.get("status") or "unknown")
        servers.append(
            CloudServer(
                id=str(row["id"]),
                name=str(row.get("label") or row.get("hostname") or row["id"]),
                host=ip if ip and ip != "0.0.0.0" else None,  # noqa: S104 — Vultr's "no IP yet"
                status=status,
                running=status == "running" and row.get("status") == "active",
                os=row.get("os") or None,
                plan=row.get("plan") or None,
                region=row.get("region") or None,
                cpus=_as_int(row.get("vcpu_count")),
                memory_mb=_as_int(row.get("ram")),
                disk_gb=float(row["disk"]) if row.get("disk") is not None else None,
            )
        )
    return servers


def parse_linode(payload: Any) -> list[CloudServer]:
    servers: list[CloudServer] = []
    for row in (payload or {}).get("data", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict) or row.get("id") is None:
            continue
        ipv4 = [ip for ip in row.get("ipv4") or [] if isinstance(ip, str)]
        public = next((ip for ip in ipv4 if not _is_private(ip)), None)
        specs = row.get("specs") or {}
        disk_mb = _as_int(specs.get("disk"))
        image = str(row.get("image") or "")
        status = str(row.get("status") or "unknown")
        servers.append(
            CloudServer(
                id=str(row["id"]),
                name=str(row.get("label") or row["id"]),
                host=public,
                status=status,
                running=status == "running",
                os=image.split("/", 1)[-1] if image else None,
                plan=row.get("type") or None,
                region=row.get("region") or None,
                cpus=_as_int(specs.get("vcpus")),
                memory_mb=_as_int(specs.get("memory")),
                disk_gb=round(disk_mb / 1024, 1) if disk_mb else None,
            )
        )
    return servers


_INTERNAL_NETS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
)


def _is_private(ip: str) -> bool:
    """A provider-internal address (not reachable from here), by RFC 1918 & co."""
    import ipaddress

    try:
        address = ipaddress.ip_address(ip)
    except ValueError:  # not an IP at all: never offer it as the address
        return True
    return any(address in ipaddress.ip_network(net) for net in _INTERNAL_NETS)


# -- listing -----------------------------------------------------------------


async def list_servers(provider_id: str, *, client: Any | None = None) -> list[CloudServer]:
    """Every server in the account behind the saved token."""
    spec = provider(provider_id)
    api_token = token(provider_id)
    if not api_token:
        raise CloudError(f"Save a {spec.name} API token first.")

    if spec.id == "hostinger":
        vms = await _request(spec, api_token, "GET", "/api/vps/v1/virtual-machines", client=client)
        data_centers: dict[int, str] = {}
        try:
            rows = await _request(spec, api_token, "GET", "/api/vps/v1/data-centers", client=client)
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("id") is not None:
                    label = row.get("city") or row.get("name")
                    if label:
                        data_centers[int(row["id"])] = str(label)
        except CloudError as exc:
            # Region names are decoration; the server list is what matters.
            log.info("computers: hostinger data centers unavailable: %s", exc.message)
        return parse_hostinger(vms, data_centers)

    if spec.id == "hetzner":
        servers: list[CloudServer] = []
        page: int | None = 1
        while page and page <= _MAX_PAGES:
            payload = await _request(
                spec,
                api_token,
                "GET",
                "/v1/servers",
                params={"page": page, "per_page": 50},
                client=client,
            )
            servers.extend(parse_hetzner(payload))
            pagination = ((payload or {}).get("meta") or {}).get("pagination") or {}
            page = _as_int(pagination.get("next_page"))
        return servers

    if spec.id == "vultr":
        servers = []
        cursor: str | None = ""
        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"per_page": 100}
            if cursor:
                params["cursor"] = cursor
            payload = await _request(
                spec, api_token, "GET", "/v2/instances", params=params, client=client
            )
            servers.extend(parse_vultr(payload))
            links = ((payload or {}).get("meta") or {}).get("links") or {}
            cursor = links.get("next") or None
            if not cursor:
                break
        return servers

    if spec.id == "linode":
        servers = []
        for page_no in range(1, _MAX_PAGES + 1):
            payload = await _request(
                spec,
                api_token,
                "GET",
                "/v4/linode/instances",
                params={"page": page_no, "page_size": 100},
                client=client,
            )
            servers.extend(parse_linode(payload))
            if page_no >= (_as_int((payload or {}).get("pages")) or 1):
                break
        return servers

    servers = []
    page = 1
    while page and page <= _MAX_PAGES:
        payload = await _request(
            spec,
            api_token,
            "GET",
            "/v2/droplets",
            params={"page": page, "per_page": 100},
            client=client,
        )
        servers.extend(parse_digitalocean(payload))
        links = ((payload or {}).get("links") or {}).get("pages") or {}
        page = page + 1 if links.get("next") else None
    return servers


def _same_key(a: str, b: str) -> bool:
    """Compare two OpenSSH key lines by algorithm + body, ignoring the comment."""
    return a.split()[:2] == b.split()[:2]


async def attach_public_key(
    provider_id: str, server_id: str, public_key: str, *, client: Any | None = None
) -> None:
    """Plant ``public_key`` on an existing server (Hostinger only)."""
    spec = provider(provider_id)
    if not spec.attaches_keys:
        raise CloudError(f"{spec.name} cannot add a key to an existing server.")
    api_token = token(provider_id)
    if not api_token:
        raise CloudError(f"Save a {spec.name} API token first.")

    key_id: int | None = None
    for page in range(1, _MAX_PAGES + 1):
        payload = await _request(
            spec, api_token, "GET", "/api/vps/v1/public-keys", params={"page": page}, client=client
        )
        rows = (payload or {}).get("data", []) if isinstance(payload, dict) else []
        for row in rows:
            if isinstance(row, dict) and _same_key(str(row.get("key", "")), public_key):
                key_id = _as_int(row.get("id"))
                break
        meta = (payload or {}).get("meta") or {} if isinstance(payload, dict) else {}
        per_page = _as_int(meta.get("per_page")) or 0
        total = _as_int(meta.get("total")) or 0
        if key_id is not None or not rows or page * max(per_page, 1) >= total:
            break
    if key_id is None:
        created = await _request(
            spec,
            api_token,
            "POST",
            "/api/vps/v1/public-keys",
            body={"name": "Personal Jarvis", "key": public_key.strip()},
            client=client,
        )
        key_id = _as_int((created or {}).get("id")) if isinstance(created, dict) else None
        if key_id is None:
            raise CloudError(f"{spec.name} did not confirm the new key.")
    action = await _request(
        spec,
        api_token,
        "POST",
        f"/api/vps/v1/public-keys/attach/{server_id}",
        body={"ids": [key_id]},
        client=client,
    )
    state = (action or {}).get("state") if isinstance(action, dict) else None
    if state == "error":
        raise CloudError(f"{spec.name} could not add the key to this server.")
