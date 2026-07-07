"""The fast-boot onboarding handler: stdlib-only, same payload as the API."""
import json
from pathlib import Path

from jarvis.setup import onboarding_fastpath as fp
from jarvis.setup import state as st


def _collector():
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    return sent, send


async def _receive_empty():
    return {"type": "http.request", "body": b"", "more_body": False}


def _scope(method: str, path: str) -> dict:
    return {"type": "http", "method": method, "path": path}


def _body_json(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"].decode("utf-8"))


async def test_state_incomplete_on_fresh_install(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        handled = await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send)
        assert handled is True
        assert sent[0]["status"] == 200
        payload = _body_json(sent)
        assert payload["completed"] is False
        assert payload["steps"]  # canonical step list present
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_state_completed_via_marker(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    (tmp_path / ".setup-complete").write_text("done\n", encoding="utf-8")
    try:
        sent, send = _collector()
        await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send)
        assert _body_json(sent)["completed"] is True
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_accept_terms_persists(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        handled = await fp.handle(
            _scope("POST", "/api/onboarding/accept-terms"), _receive_empty, send
        )
        assert handled is True and sent[0]["status"] == 200
        s = st.get_onboarding_state(tmp_path / "setup_state.json")
        assert s["terms_accepted_at"] is not None
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_complete_persists_and_next_state_is_completed(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        await fp.handle(_scope("POST", "/api/onboarding/complete"), _receive_empty, send)
        sent2, send2 = _collector()
        await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send2)
        assert _body_json(sent2)["completed"] is True
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_step_persists_progress(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()

        body = json.dumps({"step": "language", "skipped": ["welcome"]}).encode("utf-8")

        async def receive() -> dict:
            return {"type": "http.request", "body": body, "more_body": False}

        await fp.handle(_scope("POST", "/api/onboarding/step"), receive, send)
        assert sent[0]["status"] == 200
        s = st.get_onboarding_state(tmp_path / "setup_state.json")
        assert s["current_step"] == "language"
        assert s["skipped_steps"] == ["welcome"]
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_completed_survives_any_version_bump(tmp_path: Path) -> None:
    """The update contract: NOTHING version-shaped may re-open the gate.

    Completed markers set + a terms-version bump => still completed. If someone
    ever wires a version comparison into `completed`, this fails.
    """
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        st.accept_terms("0.1-ancient", path=tmp_path / "setup_state.json")
        st.mark_onboarding_complete(path=tmp_path / "setup_state.json")
        sent, send = _collector()
        await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send)
        payload = _body_json(sent)
        assert payload["completed"] is True
        assert payload["terms"]["accepted_version"] != payload["terms"]["current_version"]
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_non_onboarding_path_not_handled() -> None:
    sent, send = _collector()
    handled = await fp.handle(_scope("GET", "/api/health"), _receive_empty, send)
    assert handled is False and sent == []


async def test_unknown_onboarding_subpath_is_404(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        handled = await fp.handle(_scope("GET", "/api/onboarding/nope"), _receive_empty, send)
        assert handled is True and sent[0]["status"] == 404
    finally:
        fp._STATE_PATH_OVERRIDE = None


def test_fastpath_module_is_import_light() -> None:
    # A fresh subprocess, so other tests' imports cannot pollute the verdict —
    # the fast path must never pull fastapi/pydantic/config (AP-26).
    import subprocess
    import sys

    code = (
        "import sys; import jarvis.setup.onboarding_fastpath; "
        "banned = [m for m in ('fastapi', 'pydantic', 'jarvis.core.config') "
        "if m in sys.modules]; "
        "sys.exit(1 if banned else 0)"
    )
    rc = subprocess.run([sys.executable, "-c", code]).returncode
    assert rc == 0
