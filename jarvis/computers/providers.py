"""The provider catalog: where a computer can come from, and how to reach it.

One entry per place a user might rent or own a machine. Every entry carries
the SSH facts a first-time user needs (which login the provider gives you,
where the IP is shown, where a public SSH key goes, where the root password
arrives); the few providers whose account API Jarvis can read also point at
:mod:`jarvis.computers.cloud` for a server list and token setup.

The catalog is data, ordered for display. Adding a provider is one entry here;
an API integration additionally needs its parser in ``cloud.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

from jarvis.computers import cloud

Category = Literal["cloud", "hosting", "home", "other"]


@dataclass(frozen=True)
class SshGuide:
    default_username: str
    ip_hint: str
    key_hint: str
    key_url: str | None
    #: Where the first login (root password) comes from.
    login_hint: str


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    name: str
    category: Category
    ssh: SshGuide
    #: The id in ``cloud.PROVIDERS`` when Jarvis can read this account's API.
    api: str | None = None


CATALOG: Final[tuple[CatalogEntry, ...]] = (
    CatalogEntry(
        id="hostinger",
        name="Hostinger",
        category="hosting",
        api="hostinger",
        ssh=SshGuide(
            default_username="root",
            ip_hint="hPanel -> VPS -> your server -> Overview shows the IP address.",
            key_hint="hPanel -> VPS -> Settings -> SSH keys -> Add SSH key.",
            key_url="https://hpanel.hostinger.com/vps",
            login_hint="You set the root password when creating the VPS; "
            "change it under VPS -> Settings -> Root password.",
        ),
    ),
    CatalogEntry(
        id="hetzner",
        name="Hetzner Cloud",
        category="cloud",
        api="hetzner",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Cloud Console -> your project -> Servers lists the public IPv4.",
            key_hint="Cloud Console -> Security -> SSH keys. Keys are added when a server "
            "is created or rebuilt.",
            key_url="https://console.hetzner.cloud/",
            login_hint="Without an SSH key, Hetzner emails the root password "
            "when the server is created.",
        ),
    ),
    CatalogEntry(
        id="digitalocean",
        name="DigitalOcean",
        category="cloud",
        api="digitalocean",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Control panel -> Droplets shows the ipv4 address next to each droplet.",
            key_hint="Settings -> Security -> SSH keys. Keys are added when a droplet is created.",
            key_url="https://cloud.digitalocean.com/account/security",
            login_hint="With password login chosen, you set the root password "
            "while creating the droplet.",
        ),
    ),
    CatalogEntry(
        id="vultr",
        name="Vultr",
        category="cloud",
        api="vultr",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Products -> your instance -> Overview shows the IP address.",
            key_hint="Account -> SSH keys. Keys are added when an instance is deployed.",
            key_url="https://my.vultr.com/sshkeys/",
            login_hint="The root password is on the instance's Overview page (click the eye icon).",
        ),
    ),
    CatalogEntry(
        id="linode",
        name="Akamai Cloud (Linode)",
        category="cloud",
        api="linode",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Cloud Manager -> Linodes shows the public IPv4 address.",
            key_hint="Profile -> SSH keys. Keys are added when a Linode is created or rebuilt.",
            key_url="https://cloud.linode.com/profile/keys",
            login_hint="You set the root password while creating the Linode.",
        ),
    ),
    CatalogEntry(
        id="aws_lightsail",
        name="AWS Lightsail",
        category="cloud",
        ssh=SshGuide(
            default_username="ubuntu",
            ip_hint="Lightsail -> Instances shows the public IP under each instance "
            "(attach a static IP so it never changes).",
            key_hint="Download the instance's key pair from Account -> SSH keys and "
            "use it as your own key.",
            key_url="https://lightsail.aws.amazon.com/ls/webapp/account/keys",
            login_hint="Lightsail has no password login; use its SSH key.",
        ),
    ),
    CatalogEntry(
        id="contabo",
        name="Contabo",
        category="hosting",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Customer Control Panel -> Your services shows the IP address.",
            key_hint="Add the key when installing or reinstalling the server in the control panel.",
            key_url="https://my.contabo.com/",
            login_hint="You set the root password at order time; reset it in the control panel.",
        ),
    ),
    CatalogEntry(
        id="ionos",
        name="IONOS",
        category="hosting",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Cloud Panel -> Servers & Cloud -> your server shows the IP.",
            key_hint="Cloud Panel -> Network -> SSH keys; choose the key when "
            "setting the server up.",
            key_url="https://my.ionos.com/",
            login_hint="The initial root password is shown under the server's access data.",
        ),
    ),
    CatalogEntry(
        id="strato",
        name="Strato",
        category="hosting",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Customer login -> Server -> your server's overview shows the IP.",
            key_hint="Paste the key into ~/.ssh/authorized_keys once logged in, "
            "or let Jarvis do it with the password.",
            key_url=None,
            login_hint="The root password is in the server's access data in the customer login.",
        ),
    ),
    CatalogEntry(
        id="ovhcloud",
        name="OVHcloud",
        category="hosting",
        ssh=SshGuide(
            default_username="ubuntu",
            ip_hint="Control Panel -> Bare Metal Cloud -> VPS -> your VPS shows the IPv4.",
            key_hint="Add the key when installing the OS, or let Jarvis add it with the password.",
            key_url="https://www.ovh.com/manager/",
            login_hint="OVHcloud emails the login and password after installation.",
        ),
    ),
    CatalogEntry(
        id="oracle_cloud",
        name="Oracle Cloud",
        category="cloud",
        ssh=SshGuide(
            default_username="ubuntu",
            ip_hint="Compute -> Instances -> your instance shows the public IP address "
            "(the default user is opc on Oracle Linux).",
            key_hint="Paste or upload the public key in 'Add SSH keys' while creating "
            "the instance.",
            key_url="https://cloud.oracle.com/compute/instances",
            login_hint="Oracle Cloud has no password login; use the key you gave the instance.",
        ),
    ),
    CatalogEntry(
        id="raspberry_pi",
        name="Raspberry Pi",
        category="home",
        ssh=SshGuide(
            default_username="pi",
            ip_hint="Your router's device list shows it, or run hostname -I on the Pi.",
            key_hint="Turn on SSH in Raspberry Pi Imager or raspi-config; Jarvis adds "
            "its key with the password.",
            key_url=None,
            login_hint="The user and password you chose in Raspberry Pi Imager.",
        ),
    ),
    CatalogEntry(
        id="home_server",
        name="Home server or NAS",
        category="home",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Your router's device list shows the local IP address.",
            key_hint="Enable SSH in its settings; Jarvis adds its key with the password.",
            key_url=None,
            login_hint="The account you log in to the machine with.",
        ),
    ),
    CatalogEntry(
        id="generic",
        name="Other server",
        category="other",
        ssh=SshGuide(
            default_username="root",
            ip_hint="Your provider's panel or welcome email shows the IP address.",
            key_hint="Paste the public key into ~/.ssh/authorized_keys on the server.",
            key_url=None,
            login_hint="The login your provider gave you, usually in the welcome email.",
        ),
    ),
)

#: Ids that are valid on a record but never offered in the add flow.
INTERNAL_IDS: Final[frozenset[str]] = frozenset({"multipass"})

_BY_ID: Final[dict[str, CatalogEntry]] = {entry.id: entry for entry in CATALOG}


def entry(provider_id: str) -> CatalogEntry | None:
    return _BY_ID.get(provider_id)


def is_known(provider_id: str) -> bool:
    return provider_id in _BY_ID or provider_id in INTERNAL_IDS


def display_name(provider_id: str) -> str:
    found = _BY_ID.get(provider_id)
    if found is not None:
        return found.name
    if provider_id == "multipass":
        return "Local VM"
    return "Other server"


def catalog_rows() -> list[dict[str, Any]]:
    """The catalog as the REST route answers it (``GET /api/computers/providers``)."""
    rows: list[dict[str, Any]] = []
    for item in CATALOG:
        api: dict[str, Any] | None = None
        if item.api is not None:
            spec = cloud.PROVIDERS[item.api]  # type: ignore[index]
            api = {
                "connected": bool(cloud.token(item.api)),
                "console_url": spec.console_url,
                "setup_hint": spec.setup_hint,
                "attaches_keys": spec.attaches_keys,
                "token_label": spec.credential_label,
            }
        rows.append(
            {
                "id": item.id,
                "name": item.name,
                "category": item.category,
                "api": api,
                "ssh": {
                    "default_username": item.ssh.default_username,
                    "ip_hint": item.ssh.ip_hint,
                    "key_hint": item.ssh.key_hint,
                    "key_url": item.ssh.key_url,
                    "password_hint": item.ssh.login_hint,
                },
            }
        )
    return rows
