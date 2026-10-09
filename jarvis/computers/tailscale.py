"""This PC's Tailscale network, read to suggest a second address per computer.

Portions adapted from pingdotgg/t3code @ 12069ee
(apps/desktop/src/backend/tailscaleEndpointProvider.ts: reading
``tailscale status --json`` and preferring the MagicDNS name), MIT License,
Copyright (c) 2026 T3 Tools Inc. Full text: third_party/t3code/LICENSE.

A machine reached on its LAN address at home can still be reached on its
Tailscale name (``box.tail1234.ts.net``) from anywhere. When this PC runs
Tailscale, :func:`peers` lists the machines in the tailnet and
:func:`suggestions` matches them to the user's computers — by an address the
computer already has, or by the host name the computer reported. Nothing is
ever added on its own: a suggestion is a button the user presses.

Without Tailscale (no CLI, not logged in, any error) every function returns
nothing; this is an optional extra on every OS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.computers.models import Computer
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

log = logging.getLogger(__name__)

_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class TailscalePeer:
    host_name: str
    #: The MagicDNS name without its trailing dot; empty when MagicDNS is off.
    dns_name: str
    ips: tuple[str, ...]
    online: bool
    os: str

    @property
    def address(self) -> str:
        """The address to connect to: MagicDNS name, else the first tailnet IP."""
        return self.dns_name or (self.ips[0] if self.ips else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "host_name": self.host_name,
            "dns_name": self.dns_name,
            "ips": list(self.ips),
            "online": self.online,
            "os": self.os,
            "address": self.address,
        }


def binary() -> str | None:
    """The Tailscale CLI: on PATH, else where the installers put it."""
    found = shutil.which("tailscale")
    if found:
        return found
    candidates: list[Path] = []
    if sys.platform == "win32":
        candidates.append(Path(r"C:\Program Files\Tailscale\tailscale.exe"))
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"))
    return next((str(p) for p in candidates if p.is_file()), None)


def parse_status(raw: str) -> list[TailscalePeer]:
    """The peers in ``tailscale status --json`` output; ``[]`` when unreadable."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:  # unreadable status output means no peers
        return []
    if not isinstance(data, dict):
        return []
    peers: list[TailscalePeer] = []
    for item in (data.get("Peer") or {}).values():
        if not isinstance(item, dict):
            continue
        ips = tuple(str(ip) for ip in item.get("TailscaleIPs") or [] if isinstance(ip, str))
        peer = TailscalePeer(
            host_name=str(item.get("HostName") or ""),
            dns_name=str(item.get("DNSName") or "").rstrip("."),
            ips=ips,
            online=bool(item.get("Online")),
            os=str(item.get("OS") or ""),
        )
        if peer.address:
            peers.append(peer)
    return sorted(peers, key=lambda p: (not p.online, p.host_name.lower()))


async def peers() -> list[TailscalePeer]:
    """The tailnet's other machines, or ``[]`` without a running Tailscale."""
    exe = binary()
    if exe is None:
        return []
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except OSError as exc:
        log.info("computers: tailscale could not be started: %s", exc)
        return []
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        log.info("computers: tailscale status did not answer within %s s", _TIMEOUT_S)
        return []
    if proc.returncode != 0:
        # Logged out or the daemon is stopped: simply no tailnet to offer.
        return []
    return parse_status(out.decode("utf-8", errors="replace"))


def _first_label(name: str) -> str:
    return name.split(".", 1)[0].lower()


def match(computer: Computer, peer: TailscalePeer) -> bool:
    """Whether ``peer`` is the same machine as ``computer``."""
    known = {r.host.lower() for r in computer.routes}
    if known & ({peer.dns_name.lower()} | {ip.lower() for ip in peer.ips}):
        return True
    reported = (computer.facts.hostname if computer.facts else None) or ""
    return bool(reported) and _first_label(reported) == peer.host_name.lower()


def suggestions(
    computers: list[Computer], tailnet: list[TailscalePeer]
) -> dict[str, list[dict[str, Any]]]:
    """Per computer id: tailnet addresses it does not have as a route yet."""
    out: dict[str, list[dict[str, Any]]] = {}
    for computer in computers:
        if computer.kind == "local_vm":
            continue
        have = {r.host.lower() for r in computer.routes}
        for peer in tailnet:
            if not match(computer, peer) or peer.address.lower() in have:
                continue
            out.setdefault(computer.id, []).append(
                {
                    "host": peer.address,
                    # sshd listens on the same port on every interface.
                    "port": computer.routes[0].port,
                    "label": "Tailscale",
                    "online": peer.online,
                }
            )
    return out
