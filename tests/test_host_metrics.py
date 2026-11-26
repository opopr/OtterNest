"""Tests for the Linux metrics collector and its failure semantics."""
from pathlib import Path
from unittest.mock import patch

from otternest.host_metrics import _memory_kib, collect_host_health


def test_memory_parser_uses_available_not_free(tmp_path: Path):
    data = tmp_path / "meminfo"
    data.write_text("MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 250 kB\n")
    assert _memory_kib(data) == (1000, 750)


def test_collector_returns_measured_data_without_identity_leaks(tmp_path: Path):
    proc = tmp_path / "proc"
    proc.mkdir()
    cpu = proc / "stat"
    cpu.write_text("cpu 100 0 100 700 100 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal: 1024 kB\nMemAvailable: 256 kB\n")
    with patch("otternest.host_metrics.time.sleep", side_effect=lambda _: cpu.write_text(
            "cpu 200 0 200 800 100 0 0 0\n")):
        result = collect_host_health(proc_root=proc, disk_path=tmp_path, cpu_interval_seconds=0)
    assert float(result["cpu_percent"]) >= 0
    assert result["memory_total_bytes"] == str(1024 * 1024)
    assert result["memory_used_bytes"] == str(768 * 1024)
    assert result["telemetry_scope"] == "collector_os_view"
    assert "hostname" not in result
    assert "ip" not in result


def test_collector_fails_closed_to_partial_data(tmp_path: Path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "stat").write_text("invalid\n")
    (proc / "meminfo").write_text("invalid\n")
    result = collect_host_health(proc_root=proc, disk_path=tmp_path, cpu_interval_seconds=0)
    assert "cpu_percent" not in result
    assert "memory_percent" not in result
    assert "disk_percent" in result
