"""Best-effort Linux host telemetry for an authenticated OtterNest Runtime heartbeat.

Measurements describe the collector's OS view, which may be container-scoped.
No hostnames, IPs, environment variables, processes or secrets are reported.
"""
from __future__ import annotations

import os
from pathlib import Path
import time


def _cpu_ticks(path: Path) -> tuple[int, int]:
    first = path.read_text(encoding="ascii").splitlines()[0].split()
    if first[0] != "cpu" or len(first) < 5:
        raise ValueError("CPU stat unavailable")
    values = [int(v) for v in first[1:9]]
    total = sum(values)
    idle = values[3] + values[4]
    return total, idle


def _memory_kib(path: Path) -> tuple[int, int]:
    entries: dict[str, int] = {}
    for line in path.read_text(encoding="ascii").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in {"MemTotal:", "MemAvailable:"}:
            entries[parts[0]] = int(parts[1])
    total = entries["MemTotal:"]
    available = entries["MemAvailable:"]
    if total <= 0 or available < 0 or available > total:
        raise ValueError("Invalid memory counters")
    return total, total - available


def collect_host_health(*, proc_root: Path = Path("/proc"), disk_path: Path = Path("/"),
                        cpu_interval_seconds: float = 0.1) -> dict[str, str]:
    """Return measured metrics only. Missing or unsupported values are omitted."""
    if os.name != "posix" or not (proc_root / "stat").exists():
        return {}
    health: dict[str, str] = {"telemetry_scope": "collector_os_view", "telemetry_source": "linux_procfs"}
    try:
        total_a, idle_a = _cpu_ticks(proc_root / "stat")
        time.sleep(cpu_interval_seconds)
        total_b, idle_b = _cpu_ticks(proc_root / "stat")
        delta = total_b - total_a
        if delta > 0:
            usage = max(0.0, min(100.0, 100.0 * (1 - (idle_b - idle_a) / delta)))
            health["cpu_percent"] = f"{usage:.1f}"
    except (OSError, ValueError, KeyError):
        pass
    try:
        total, used = _memory_kib(proc_root / "meminfo")
        health["memory_total_bytes"] = str(total * 1024)
        health["memory_used_bytes"] = str(used * 1024)
        health["memory_percent"] = f"{100 * used / total:.1f}"
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        pass
    try:
        usage = os.statvfs(disk_path)
        total = usage.f_blocks * usage.f_frsize
        used = (usage.f_blocks - usage.f_bfree) * usage.f_frsize
        if total > 0:
            health["disk_total_bytes"] = str(total)
            health["disk_used_bytes"] = str(used)
            health["disk_percent"] = f"{100 * used / total:.1f}"
    except (OSError, ValueError):
        pass
    return health
