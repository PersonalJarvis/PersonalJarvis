"""The read-only health scripts and their parser.

A check runs ONE script over SSH that prints labelled sections (hostname, OS,
cores, memory, disk, uptime, load). It only reads — nothing is installed or
changed. Linux answers every section of :data:`PROBE_SCRIPT`; macOS answers
through its own tools; a Windows OpenSSH server gets
:data:`WINDOWS_PROBE_SCRIPT` (PowerShell, sent on stdin), which prints the
same sections in the same shapes, so one parser reads all three. Windows has
no load average; that section stays empty.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.computers.models import ComputerFacts

PROBE_SCRIPT = r"""
echo "@@hostname"; hostname 2>/dev/null
echo "@@uname"; uname -srm 2>/dev/null
echo "@@os"; cat /etc/os-release 2>/dev/null
echo "@@darwin"; sw_vers 2>/dev/null
echo "@@nproc"; nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null \
  || sysctl -n hw.ncpu 2>/dev/null
echo "@@meminfo"; head -n 3 /proc/meminfo 2>/dev/null
echo "@@memsize"; sysctl -n hw.memsize 2>/dev/null
echo "@@df"; df -Pk / 2>/dev/null | tail -n 1
echo "@@uptime"; cat /proc/uptime 2>/dev/null
echo "@@loadavg"; cat /proc/loadavg 2>/dev/null || sysctl -n vm.loadavg 2>/dev/null
echo "@@end"
""".strip()

#: The same sections from a Windows computer, in the shapes the Linux tools
#: print (``/etc/os-release`` lines, ``/proc/meminfo`` kB, ``df -Pk`` columns).
WINDOWS_PROBE_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
'@@hostname'; $env:COMPUTERNAME
'@@uname'; "Windows $($os.Version) $env:PROCESSOR_ARCHITECTURE"
'@@os'; 'ID=windows'; "PRETTY_NAME=$($os.Caption -replace '^Microsoft ', '')"
'@@nproc'; [Environment]::ProcessorCount
'@@meminfo'
"MemTotal: $($os.TotalVisibleMemorySize) kB"
"MemAvailable: $($os.FreePhysicalMemory) kB"
'@@df'
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
if ($drive -and $drive.Size) {
  $total = [int64]($drive.Size / 1KB); $free = [int64]($drive.FreeSpace / 1KB)
  "$env:SystemDrive $total $($total - $free) $free - $env:SystemDrive\"
}
'@@uptime'
if ($os.LastBootUpTime) { [int64]((Get-Date) - $os.LastBootUpTime).TotalSeconds }
'@@loadavg'
'@@end'
""".strip()


@dataclass(frozen=True)
class ProbeReading:
    """What one probe run found."""

    facts: ComputerFacts
    load_1m: float | None
    mem_used_pct: float | None
    disk_used_pct: float | None
    uptime_s: int | None


def _sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in output.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("@@"):
            current = line[2:].strip()
            sections.setdefault(current, [])
        elif current is not None and line.strip():
            sections[current].append(line.strip())
    return sections


def _first(lines: list[str] | None) -> str | None:
    return lines[0] if lines else None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:  # a non-number field just means the fact is unknown
        return None


def _os_release(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _meminfo_kb(lines: list[str]) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in lines:
        key, sep, rest = line.partition(":")
        number = _int(rest.split()[0]) if sep and rest.split() else None
        if number is not None:
            values[key.strip()] = number
    return values


def parse_probe(output: str) -> ProbeReading:
    """Turn the script's output into facts and live numbers; never raises."""
    s = _sections(output)

    hostname = _first(s.get("hostname"))
    kernel = arch = None
    uname = _first(s.get("uname"))
    if uname:
        parts = uname.split()
        if len(parts) >= 3:
            kernel = f"{parts[0]} {parts[1]}"
            arch = parts[-1]
        else:
            kernel = uname

    os_id = os_name = None
    release = _os_release(s.get("os", []))
    if release:
        os_id = release.get("ID") or None
        os_name = release.get("PRETTY_NAME") or release.get("NAME") or None
    darwin = _os_release([line.replace(":", "=", 1) for line in s.get("darwin", [])])
    if not os_name and darwin.get("ProductName"):
        os_id = "macos"
        os_name = f"{darwin['ProductName']} {darwin.get('ProductVersion', '')}".strip()

    cpu_count = _int(_first(s.get("nproc")))

    mem_total_mb = None
    mem_used_pct = None
    meminfo = _meminfo_kb(s.get("meminfo", []))
    if meminfo.get("MemTotal"):
        total_kb = meminfo["MemTotal"]
        mem_total_mb = total_kb // 1024
        available = meminfo.get("MemAvailable", meminfo.get("MemFree"))
        if available is not None and total_kb > 0:
            mem_used_pct = round(100.0 * (total_kb - available) / total_kb, 1)
    elif (memsize := _int(_first(s.get("memsize")))) is not None:
        mem_total_mb = memsize // (1024 * 1024)

    disk_total_gb = None
    disk_used_pct = None
    df = _first(s.get("df"))
    if df:
        cols = df.split()
        if len(cols) >= 5:
            total_k, used_k = _int(cols[1]), _int(cols[2])
            if total_k:
                disk_total_gb = round(total_k / (1024 * 1024), 1)
                if used_k is not None:
                    disk_used_pct = round(100.0 * used_k / total_k, 1)

    uptime_s = None
    uptime = _first(s.get("uptime"))
    if uptime:
        try:
            uptime_s = int(float(uptime.split()[0]))
        except (ValueError, IndexError):  # unknown uptime, shown as a dash
            uptime_s = None

    load_1m = None
    load = _first(s.get("loadavg"))
    if load:
        try:
            load_1m = float(load.strip("{} ").split()[0])
        except (ValueError, IndexError):  # unknown load, shown as a dash
            load_1m = None

    return ProbeReading(
        facts=ComputerFacts(
            hostname=hostname,
            os_id=os_id,
            os_name=os_name,
            kernel=kernel,
            arch=arch,
            cpu_count=cpu_count,
            mem_total_mb=mem_total_mb,
            disk_total_gb=disk_total_gb,
        ),
        load_1m=load_1m,
        mem_used_pct=mem_used_pct,
        disk_used_pct=disk_used_pct,
        uptime_s=uptime_s,
    )
