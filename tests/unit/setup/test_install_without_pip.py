"""In-app installs recover when the environment has no pip module (BUG-073).

``uv venv`` creates environments WITHOUT pip by design, so the local-speech
install died with ``pythonw.exe: No module named pip`` on the maintainer's
Windows box and again on the real-Mac test run. Contracts locked here:

1. A "No module named pip" failure bootstraps pip via ``ensurepip`` and
   retries — a permanent repair of the environment.
2. When ensurepip cannot help, the install falls back to
   ``uv pip install --python <sys.executable>`` with a uv binary on PATH,
   propagating ``--only-binary``.
3. When neither escape exists, the message names the manual fix and keeps
   the raw cause.
4. Environments WITH pip never pay for the recovery: the happy path stays
   a single subprocess call.
5. ``classify_pip_failure`` understands uv's wordings for the no-wheel and
   network cases (captured empirically from uv 0.11).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

from jarvis.setup import dependencies as deps

_NO_PIP_STDERR = "/opt/jarvis/.venv/bin/python: No module named pip"


def _scripted_run(outcomes: list[tuple[int, str, str]], calls: list[list[str]]):
    """Fake subprocess.run returning the scripted outcomes in call order."""
    queue = iter(outcomes)

    def _run(cmd, **_kw):
        calls.append(list(cmd))
        rc, out, err = next(queue)
        return SimpleNamespace(returncode=rc, stdout=out, stderr=err)

    return _run


def test_no_pip_bootstraps_ensurepip_then_retries(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        deps.subprocess,
        "run",
        _scripted_run(
            [
                (1, "", _NO_PIP_STDERR),  # pip install: env has no pip module
                (0, "", ""),              # ensurepip --upgrade repairs it
                (0, "ok", ""),            # pip install retry succeeds
            ],
            calls,
        ),
    )
    ok, msg = deps.install_pip_package("faster-whisper>=1.0", only_binary=True)
    assert ok is True
    assert [c[1:3] for c in calls] == [
        ["-m", "pip"],
        ["-m", "ensurepip"],
        ["-m", "pip"],
    ]
    assert "--upgrade" in calls[1]


def test_no_pip_falls_back_to_uv_when_ensurepip_cannot_help(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        deps.subprocess,
        "run",
        _scripted_run(
            [
                (1, "", _NO_PIP_STDERR),        # pip install: no pip module
                (1, "", "no module named ensurepip"),  # stripped system Python
                (0, "", "Installed 1 package"),  # uv pip install succeeds
            ],
            calls,
        ),
    )
    monkeypatch.setattr(
        deps, "_resolve_binary", lambda name: "/fake/uv" if name == "uv" else None
    )
    ok, _ = deps.install_pip_package("faster-whisper>=1.0", only_binary=True)
    assert ok is True
    uv_cmd = calls[2]
    assert uv_cmd[:3] == ["/fake/uv", "pip", "install"]
    assert "--python" in uv_cmd
    assert sys.executable in uv_cmd
    assert "--only-binary" in uv_cmd
    assert ":all:" in uv_cmd


def test_no_pip_and_no_recovery_names_the_manual_fix(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        deps.subprocess,
        "run",
        _scripted_run(
            [
                (1, "", _NO_PIP_STDERR),
                (1, "", "no module named ensurepip"),
            ],
            calls,
        ),
    )
    monkeypatch.setattr(deps, "_resolve_binary", lambda name: None)
    ok, msg = deps.install_pip_package("rich")
    assert ok is False
    assert "ensurepip" in msg
    assert "no module named pip" in msg.lower()  # raw cause preserved


def test_env_with_pip_stays_single_subprocess(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        deps.subprocess, "run", _scripted_run([(0, "ok", "")], calls)
    )
    ok, _ = deps.install_pip_package("rich")
    assert ok is True
    assert len(calls) == 1


def test_uv_failure_message_names_uv_not_pip(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        deps.subprocess,
        "run",
        _scripted_run(
            [
                (1, "", _NO_PIP_STDERR),
                (1, "", "no module named ensurepip"),
                (1, "", "some uv failure"),
            ],
            calls,
        ),
    )
    monkeypatch.setattr(
        deps, "_resolve_binary", lambda name: "/fake/uv" if name == "uv" else None
    )
    ok, msg = deps.install_pip_package("rich")
    assert ok is False
    assert "uv exited 1" in msg


def test_uv_no_wheel_wording_gets_the_prebuilt_diagnosis() -> None:
    msg = deps.classify_pip_failure(
        "No solution found when resolving dependencies: Because av==10.0.0 "
        "has no usable wheels and you require av==10.0.0, we can conclude "
        "that your requirements are unsatisfiable."
    )
    assert msg is not None
    assert "prebuilt" in msg.lower()


def test_uv_network_wording_gets_the_network_diagnosis() -> None:
    msg = deps.classify_pip_failure(
        "error: Request failed after 3 retries\n"
        "Caused by: Failed to fetch: `https://pypi.org/simple/cowsay/`\n"
        "Caused by: error sending request for url"
    )
    assert msg is not None
    assert "network" in msg.lower()
