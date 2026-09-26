"""Focused checks for the read-only live resource sampler."""

from scripts.perf_live_snapshot import _role, _summary


class _Process:
    def __init__(self, pid: int, name: str, args: list[str]) -> None:
        self.pid = pid
        self._name = name
        self._args = args

    def name(self) -> str:
        return self._name

    def cmdline(self) -> list[str]:
        return self._args


def test_roles_keep_hosted_agent_and_webview_separate() -> None:
    assert _role(_Process(10, "python.exe", []), 10) == "backend"
    assert _role(_Process(11, "msedgewebview2.exe", ["--type=gpu-process"]), 10) == (
        "webview:gpu-process"
    )
    assert _role(_Process(12, "codex.exe", []), 10) == "coding-agent"


def test_summary_aggregates_multiple_processes_in_one_role() -> None:
    def row(pid: int, cpu: float, rss: float) -> dict:
        return {
            "pid": pid,
            "scope": "jarvis-family",
            "role": "webview:renderer",
            "cpu_one_core_pct": cpu,
            "rss_mb": rss,
            "commit_mb": 20.0,
            "connections": {"established": 1, "listen": 0, "other": 0},
        }

    result = {
        "root_pid": 10,
        "duration_seconds": 2.0,
        "logical_cpus": 16,
        "other_backend_pids": [20],
        "health_probes": [],
        "samples": [
            {"processes": [row(11, 2, 100), row(12, 3, 200)]},
            {"processes": [row(11, 4, 110), row(12, 6, 210)]},
        ],
    }
    summary = _summary(result)
    line = next(line for line in summary.splitlines() if "webview:renderer" in line)
    assert "2  " in line
    assert "7.50/ 10.00" in line
    assert "320.0" in line
    assert "40.0" in line
