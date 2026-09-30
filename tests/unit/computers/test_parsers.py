"""Pure parsers: the probe script, the three provider APIs, Multipass JSON."""

from __future__ import annotations

from jarvis.computers import cloud, local_vm
from jarvis.computers.probe import parse_probe
from tests.fakes.fake_ssh_server import LINUX_PROBE_OUTPUT

MACOS_OUTPUT = """@@hostname
studio.local
@@uname
Darwin 24.1.0 arm64
@@os
@@darwin
ProductName:\t\tmacOS
ProductVersion:\t\t15.1
@@nproc
10
@@meminfo
@@memsize
34359738368
@@df
/dev/disk3s1s1 971350180 10485760 500000000 3% /
@@uptime
@@loadavg
{ 1.52 1.60 1.70 }
@@end
"""


def test_linux_probe() -> None:
    reading = parse_probe(LINUX_PROBE_OUTPUT)

    assert reading.facts.hostname == "srv-test"
    assert reading.facts.os_id == "ubuntu"
    assert reading.facts.kernel == "Linux 6.8.0-45-generic"
    assert reading.facts.arch == "x86_64"
    assert reading.facts.mem_total_mb == 7812
    assert reading.mem_used_pct == 25.0
    assert reading.uptime_s == 86400
    assert reading.load_1m == 0.42


def test_macos_probe() -> None:
    reading = parse_probe(MACOS_OUTPUT)

    assert reading.facts.os_name == "macOS 15.1"
    assert reading.facts.os_id == "macos"
    assert reading.facts.mem_total_mb == 32768
    assert reading.facts.cpu_count == 10
    assert reading.load_1m == 1.52


def test_non_posix_shell_yields_empty_reading() -> None:
    reading = parse_probe("'echo' is not recognized as an internal or external command")

    assert reading.facts.os_name is None
    assert reading.load_1m is None


def test_hostinger_parser_maps_ip_template_and_region() -> None:
    servers = cloud.parse_hostinger(
        [
            {
                "id": 17923,
                "hostname": "srv17923.hstgr.cloud",
                "state": "running",
                "plan": "KVM 4",
                "data_center_id": 521,
                "cpus": 4,
                "memory": 8192,
                "disk": 51200,
                "ipv4": [{"id": 1, "address": "203.0.113.15"}],
                "template": {"name": "Ubuntu 24.04 LTS"},
            },
            {"id": 2, "state": "stopped", "ipv4": None, "template": None},
        ],
        {521: "Phoenix"},
    )

    assert servers[0].host == "203.0.113.15"
    assert servers[0].region == "Phoenix" and servers[0].disk_gb == 50.0
    assert servers[0].running is True
    assert servers[1].host is None and servers[1].running is False


def test_hetzner_and_digitalocean_parsers() -> None:
    hetzner = cloud.parse_hetzner(
        {
            "servers": [
                {
                    "id": 42,
                    "name": "web",
                    "status": "running",
                    "public_net": {"ipv4": {"ip": "198.51.100.4"}},
                    "server_type": {"name": "cpx11", "cores": 2, "memory": 2.0, "disk": 40},
                    "location": {"name": "fsn1", "city": "Falkenstein"},
                    "image": {"description": "Ubuntu 24.04"},
                }
            ]
        }
    )
    droplets = cloud.parse_digitalocean(
        {
            "droplets": [
                {
                    "id": 3164444,
                    "name": "api",
                    "status": "active",
                    "memory": 1024,
                    "vcpus": 1,
                    "disk": 25,
                    "size_slug": "s-1vcpu-1gb",
                    "region": {"slug": "nyc3", "name": "New York 3"},
                    "image": {"distribution": "Ubuntu", "name": "24.04 (LTS) x64"},
                    "networks": {
                        "v4": [
                            {"ip_address": "10.0.0.2", "type": "private"},
                            {"ip_address": "192.0.2.9", "type": "public"},
                        ]
                    },
                }
            ]
        }
    )

    assert hetzner[0].host == "198.51.100.4" and hetzner[0].memory_mb == 2048
    assert hetzner[0].region == "Falkenstein"
    assert droplets[0].host == "192.0.2.9" and droplets[0].running is True
    assert droplets[0].os == "Ubuntu 24.04 (LTS) x64"


def test_multipass_parsers() -> None:
    listed = local_vm.parse_list(
        {
            "list": [
                {
                    "name": "dev",
                    "state": "Running",
                    "ipv4": ["10.1.2.3"],
                    "release": "Ubuntu 24.04 LTS",
                }
            ]
        }
    )
    info = local_vm.parse_info(
        {
            "errors": [],
            "info": {
                "dev": {
                    "state": "Running",
                    "cpu_count": "2",
                    "disks": {"sda1": {"used": "1932735283", "total": "10737418240"}},
                    "memory": {"used": 312471552, "total": 2147483648},
                    "ipv4": ["10.1.2.3"],
                    "release": "Ubuntu 24.04.1 LTS",
                }
            },
        },
        "dev",
    )

    assert listed[0].ipv4 == "10.1.2.3"
    assert info is not None
    assert info.cpus == 2 and info.memory_total_mb == 2048 and info.disk_total_gb == 10.0


def test_multipass_names_and_cloud_init() -> None:
    assert local_vm.valid_name("jarvis-vm")
    assert not local_vm.valid_name("Jarvis VM")
    assert not local_vm.valid_name("-x")
    assert "ssh_authorized_keys:\n  - ssh-ed25519 AAAA test" in local_vm.cloud_init(
        "ssh-ed25519 AAAA test\n"
    )
