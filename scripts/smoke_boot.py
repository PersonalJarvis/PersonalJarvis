#!/usr/bin/env python
"""Anti-gaming functional smoke test for the Personal Jarvis headless app.

Why this exists
---------------
``scripts/measure_boot.py`` answers "how fast does the app print
``BOOT_READY_MS=``?". That is a *timing* number and nothing else. A future boot
optimization that defers subsystems to the background could make the app print
``BOOT_READY_MS=`` early while the chat brain, the wiki FTS index, or the mission
stack are still half-wired (or silently broken) — a fast but DISHONEST "ready".

This script is the functional counterweight: it spawns the SAME isolated cold
boot as the timing harness, waits for the ``BOOT_READY_MS=`` sentinel, and then
exercises three real end-to-end features against the running instance. If any of
them fails, the boot was not *functionally* ready no matter how fast the number
looked. Each check is a hard pass/fail:

1. **chat**       — open the chat WebSocket (``/ws``), send one user message, and
                    confirm a real assistant reply comes back (non-empty and not
                    a brain-unavailable / error diagnostic). This proves the
                    launcher's ``_on_user_message`` → ``brain.generate`` →
                    ``ResponseGenerated`` chain is live.
2. **wiki-recall**— ``GET /api/wiki/search?q=...`` for a term that appears in the
                    seeded bench vault, asserting HTTP 200 + at least one hit.
                    This proves the boot-time vault index is queryable.
3. **mission spawn**— ``POST /api/missions/dispatch`` creates a mission record and
                    returns its id, then we immediately ``POST
                    /api/missions/{id}/cancel`` so no heavyweight worker runs to
                    completion. This proves the Phase-6 mission stack is wired.

Isolation contract (NEVER touches the running production instance)
------------------------------------------------------------------
Identical to ``scripts/measure_boot.py``: a dedicated ``.boot-bench/`` directory
holds an isolated ``data/`` dir and a seeded ``vault/``; ``data/`` and the
mission isolation root are wiped before the run; every store, the vault, and the
mission worktree container are redirected into ``.boot-bench/`` via the same env
overrides; an ephemeral free port is used — never the production port. The vault
is *seeded but never wiped* (it is the corpus the wiki-recall check queries).

Usage
-----
    "C:\\Program Files\\Python311\\python.exe" scripts/smoke_boot.py
    "...python.exe" scripts/smoke_boot.py --pages 80 --boot-timeout 120

Exit code is 0 on all-pass (prints ``SMOKE PASS``), non-zero otherwise (prints
``SMOKE FAIL: <reason>``).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Windows console defaults to cp1252; the report uses a few non-ASCII glyphs
# (em dash). Reconfigure to UTF-8 so the summary renders cleanly (house rule for
# new CLI modules — see CLAUDE.md "Windows specifics").
if sys.platform == "win32":
    with contextlib.suppress(AttributeError, OSError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Reuse the timing harness's isolation primitives verbatim so the bench dirs,
# the seeded vault, the env contract, and the free-port helper stay in lock-step
# with measure_boot.py (a single source of truth for "an isolated cold boot").
import measure_boot  # noqa: E402  (same scripts/ dir; added to sys.path below)

REPO_ROOT = measure_boot.REPO_ROOT
DEFAULT_PYTHON = measure_boot.DEFAULT_PYTHON
NO_WINDOW_CREATIONFLAGS = measure_boot.NO_WINDOW_CREATIONFLAGS

# A term that the seeded bench vault is guaranteed to contain: every seeded page
# body is built from measure_boot._WORDS, and "mission" / "router" / "vault" /
# "index" are all in that pool. We query one of them for the wiki-recall check.
_WIKI_QUERY = "mission"


# ----------------------------------------------------------------------
# Tiny stdlib HTTP helpers (no third-party dependency required)
# ----------------------------------------------------------------------


def _http_get_json(url: str, *, timeout: float) -> tuple[int, dict | list | None]:
    """GET ``url`` and parse JSON. Returns (status, parsed-or-None)."""
    # URL is always a loopback http:// endpoint we construct ourselves — S310's
    # arbitrary-scheme concern does not apply.
    req = urllib.request.Request(url, method="GET")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, _safe_json(body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None
    return status, _safe_json(body)


def _http_post_json(
    url: str, payload: dict, *, timeout: float
) -> tuple[int, dict | list | None]:
    """POST ``payload`` as JSON to ``url``. Returns (status, parsed-or-None)."""
    data = json.dumps(payload).encode("utf-8")
    # Loopback http:// endpoint we construct ourselves — S310 N/A (see above).
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, _safe_json(body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None
    return status, _safe_json(body)


def _safe_json(body: str) -> dict | list | None:
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


def _wait_for_http(base_url: str, *, timeout: float) -> bool:
    """Poll ``GET /api/health`` until it answers or ``timeout`` elapses.

    The BOOT_READY_MS sentinel already implies uvicorn is listening, but a tiny
    grace poll closes the race between the stdout line and the socket fully
    accepting on a busy box.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _ = _http_get_json(f"{base_url}/api/health", timeout=3.0)
        if status == 200:
            return True
        time.sleep(0.2)
    return False


# ----------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455) over stdlib sockets
# ----------------------------------------------------------------------
#
# We prefer the third-party ``websocket-client`` lib when importable (it is in
# this environment), and fall back to a tiny hand-rolled client so the smoke
# test still runs on a trimmed VPS interpreter with only the stdlib. Both speak
# the exact same frames the React client uses: a text JSON "message" frame in,
# JSON event envelopes out.


def _chat_via_websocket_client(
    host: str, port: int, prompt: str, *, reply_timeout: float
) -> tuple[bool, str]:
    """Drive the chat round-trip via the ``websocket-client`` library."""
    import websocket  # type: ignore[import-not-found]

    url = f"ws://{host}:{port}/ws"
    ws = websocket.create_connection(url, timeout=10.0)
    try:
        ws.settimeout(reply_timeout)
        # Drain the welcome frame first (non-fatal if it is something else).
        with contextlib.suppress(Exception):
            ws.recv()
        ws.send(
            json.dumps(
                {
                    "type": "message",
                    "kind": "text",
                    "content": prompt,
                    "metadata": {"thread_id": "smoke-boot"},
                }
            )
        )
        deadline = time.monotonic() + reply_timeout
        while time.monotonic() < deadline:
            ws.settimeout(max(0.5, deadline - time.monotonic()))
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception:  # noqa: BLE001
                break
            ok, reply, terminal = _inspect_chat_frame(raw)
            if terminal:
                return ok, reply
        return False, "no assistant reply within timeout"
    finally:
        with contextlib.suppress(Exception):
            ws.close()


def _chat_via_stdlib_ws(
    host: str, port: int, prompt: str, *, reply_timeout: float
) -> tuple[bool, str]:
    """Hand-rolled RFC-6455 client fallback (no third-party dependency).

    Implements only what the chat path needs: a client handshake, masked text
    frames out, and unmasked text frames in. Control frames (ping/close) are
    handled minimally.
    """
    import base64
    import os
    import struct

    sock = socket.create_connection((host, port), timeout=10.0)
    try:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET /ws HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(handshake.encode("ascii"))

        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return False, "ws handshake closed early"
            buf += chunk
        header_blob, _, rest = buf.partition(b"\r\n\r\n")
        if b" 101 " not in header_blob.split(b"\r\n", 1)[0]:
            return False, f"ws handshake not 101: {header_blob[:80]!r}"

        recv_buf = bytearray(rest)

        def _send_text(text: str) -> None:
            payload = text.encode("utf-8")
            mask = os.urandom(4)
            length = len(payload)
            frame = bytearray()
            frame.append(0x81)  # FIN + text opcode
            if length < 126:
                frame.append(0x80 | length)
            elif length < (1 << 16):
                frame.append(0x80 | 126)
                frame += struct.pack("!H", length)
            else:
                frame.append(0x80 | 127)
                frame += struct.pack("!Q", length)
            frame += mask
            frame += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            sock.sendall(frame)

        def _recv_frame(deadline: float) -> tuple[int, bytes] | None:
            """Return (opcode, payload) or None on timeout/close."""
            nonlocal recv_buf

            def _read_exact(n: int) -> bytes | None:
                nonlocal recv_buf
                while len(recv_buf) < n:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    sock.settimeout(max(0.2, remaining))
                    try:
                        chunk = sock.recv(4096)
                    except (TimeoutError, OSError):
                        return None
                    if not chunk:
                        return None
                    recv_buf += chunk
                out = bytes(recv_buf[:n])
                del recv_buf[:n]
                return out

            head = _read_exact(2)
            if head is None:
                return None
            opcode = head[0] & 0x0F
            masked = bool(head[1] & 0x80)
            length = head[1] & 0x7F
            if length == 126:
                ext = _read_exact(2)
                if ext is None:
                    return None
                length = struct.unpack("!H", ext)[0]
            elif length == 127:
                ext = _read_exact(8)
                if ext is None:
                    return None
                length = struct.unpack("!Q", ext)[0]
            mask_key = b""
            if masked:
                mask_key = _read_exact(4) or b""
            payload = _read_exact(length) if length else b""
            if payload is None:
                return None
            if masked and mask_key:
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            return opcode, payload

        overall_deadline = time.monotonic() + reply_timeout

        # Drain the welcome frame (best-effort).
        _recv_frame(min(overall_deadline, time.monotonic() + 5.0))

        _send_text(
            json.dumps(
                {
                    "type": "message",
                    "kind": "text",
                    "content": prompt,
                    "metadata": {"thread_id": "smoke-boot"},
                }
            )
        )

        while time.monotonic() < overall_deadline:
            frame = _recv_frame(overall_deadline)
            if frame is None:
                break
            opcode, payload = frame
            if opcode == 0x8:  # close
                break
            if opcode in (0x9, 0xA):  # ping/pong — ignore
                continue
            if opcode not in (0x1, 0x2):
                continue
            # errors="replace" makes decode total — it cannot raise.
            text = payload.decode("utf-8", errors="replace")
            ok, reply, terminal = _inspect_chat_frame(text)
            if terminal:
                return ok, reply
        return False, "no assistant reply within timeout"
    finally:
        with contextlib.suppress(Exception):
            sock.close()


def _inspect_chat_frame(raw: str | bytes) -> tuple[bool, str, bool]:
    """Classify one inbound WS frame.

    Returns ``(ok, reply_or_reason, terminal)``:
      * ``terminal=False`` → keep listening (welcome / unrelated event).
      * ``terminal=True, ok=True`` → a real assistant reply (the reply text).
      * ``terminal=True, ok=False`` → a brain-unavailable / error diagnostic.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    obj = _safe_json(raw)
    if not isinstance(obj, dict):
        return False, "", False

    # An explicit backend error event ends the wait as a failure.
    if obj.get("type") == "event" and obj.get("event_name") == "ErrorOccurred":
        payload = obj.get("payload") or {}
        if payload.get("layer") == "brain" or payload.get("source_layer") == "brain":
            msg = payload.get("message") or payload.get("error_type") or "brain error"
            return False, f"brain error event: {msg}", True
        # Non-brain recoverable errors (e.g. a stray validation) are not terminal.
        return False, "", False

    # The assistant reply rides a ResponseGenerated event.
    if obj.get("type") == "event" and obj.get("event_name") == "ResponseGenerated":
        payload = obj.get("payload") or {}
        text = (payload.get("text") or "").strip()
        if not text:
            return False, "empty assistant reply", True
        if _is_diagnostic_reply(text):
            return False, f"diagnostic reply (not a real answer): {text[:200]}", True
        return True, text, True

    # Welcome frame and every other event → keep waiting.
    return False, "", False


def _is_diagnostic_reply(text: str) -> bool:
    """True for backend diagnostics that must NOT count as a real chat reply.

    Mirrors the launcher's own diagnostic markers (``_is_brain_diagnostic`` +
    the ``Brain unavailable:`` / ``Brain error:`` fallbacks it publishes when the
    brain is missing or raises) so a degraded boot cannot fake a passing chat.
    """
    t = text.strip().lower()
    return (
        t.startswith("brain unavailable")
        or t.startswith("brain error")
        or t.startswith("brain-fehler")
        or t.startswith("kein brain-key gefunden")
        or t.startswith("keine brain-provider")
        or t.startswith("brain nicht verfuegbar")
        or "brainunavailable" in t
    )


def _run_chat_check(host: str, port: int, *, reply_timeout: float) -> tuple[bool, str]:
    """Chat round-trip with the best available WS client."""
    prompt = "Reply with a short greeting. Are you online?"
    try:
        import websocket  # noqa: F401  (probe importability)

        return _chat_via_websocket_client(
            host, port, prompt, reply_timeout=reply_timeout
        )
    except ImportError:
        return _chat_via_stdlib_ws(host, port, prompt, reply_timeout=reply_timeout)


# ----------------------------------------------------------------------
# Functional checks (wiki-recall + mission spawn)
# ----------------------------------------------------------------------


def _run_wiki_check(base_url: str, *, timeout: float) -> tuple[bool, str]:
    """``GET /api/wiki/search`` must return 200 + >= 1 hit over the seeded vault."""
    qs = urllib.parse.urlencode({"q": _WIKI_QUERY, "k": 5})
    status, obj = _http_get_json(f"{base_url}/api/wiki/search?{qs}", timeout=timeout)
    if status != 200:
        return False, f"HTTP {status} from /api/wiki/search"
    if not isinstance(obj, dict):
        return False, "non-JSON wiki-search response"
    if not obj.get("ok"):
        return False, f"wiki search ok=false: {obj.get('error')!r}"
    hits = obj.get("hits")
    if not isinstance(hits, list) or len(hits) < 1:
        return False, f"no hits for q={_WIKI_QUERY!r} over the seeded vault"
    return True, f"{len(hits)} hit(s) for q={_WIKI_QUERY!r}"


def _run_mission_check(base_url: str, *, timeout: float) -> tuple[bool, str]:
    """Create a mission record, then immediately cancel it.

    The assertion is only that the mission stack is live and the dispatch
    endpoint accepts + persists a mission (HTTP 201 + a mission id). We cancel
    right away so no heavyweight worker runs to completion.
    """
    status, obj = _http_post_json(
        f"{base_url}/api/missions/dispatch",
        {
            "prompt": "smoke-boot health probe — do nothing of substance",
            "language": "en",
            "confirmed": True,
        },
        timeout=timeout,
    )
    if status != 201:
        return False, f"HTTP {status} from /api/missions/dispatch"
    if not isinstance(obj, dict):
        return False, "non-JSON dispatch response"
    mission_id = obj.get("mission_id")
    if not mission_id:
        return False, f"dispatch returned no mission_id: {obj!r}"

    # Best-effort immediate cancel so the worker subprocess is torn down.
    cancel_status, _ = _http_post_json(
        f"{base_url}/api/missions/{mission_id}/cancel", {}, timeout=timeout
    )
    cancel_note = "cancelled" if cancel_status == 200 else f"cancel→HTTP {cancel_status}"
    return True, f"mission {mission_id} created ({cancel_note})"


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


def _spawn_app(python: str, port: int) -> subprocess.Popen:
    """Spawn one isolated headless instance exactly like measure_boot.run_one."""
    cmd = [
        python,
        "-m",
        "jarvis.ui.web.launcher",
        "--headless",
        "--no-lock",
        "--port",
        str(port),
    ]
    return subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        env=measure_boot._bench_env(port),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )


def _wait_for_boot_ready(
    proc: subprocess.Popen, *, timeout: float
) -> tuple[bool, float | None, list[str]]:
    """Block until the BOOT_READY_MS= sentinel is seen on stdout.

    Returns (ready, boot_ready_ms, captured_tail). Uses the same reader-thread
    polling pattern as measure_boot.run_one. The captured tail is kept so a boot
    that never reaches ready can be diagnosed.
    """
    ready = threading.Event()
    boot_ready_ms: list[float | None] = [None]
    tail: list[str] = []

    def reader() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            tail.append(line)
            if len(tail) > 60:
                del tail[0]
            if line.startswith("BOOT_READY_MS="):
                with contextlib.suppress(ValueError):
                    boot_ready_ms[0] = float(line.split("=", 1)[1])
                ready.set()
                # Keep draining so the child's stdout pipe never fills and
                # blocks the server (a full pipe would wedge boot).
        # EOF — if we never set ready, unblock the waiter so it can report.
        ready.set()

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    got = ready.wait(timeout)
    return (got and boot_ready_ms[0] is not None), boot_ready_ms[0], tail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Anti-gaming functional boot smoke test for Personal Jarvis"
    )
    ap.add_argument(
        "--python", default=DEFAULT_PYTHON, help="interpreter for the spawned app"
    )
    ap.add_argument(
        "--pages", type=int, default=measure_boot.DEFAULT_PAGES, help="vault pages to seed"
    )
    ap.add_argument(
        "--boot-timeout",
        type=float,
        default=120.0,
        help="seconds to wait for BOOT_READY_MS=",
    )
    ap.add_argument(
        "--chat-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for the assistant reply (real LLM)",
    )
    ap.add_argument(
        "--http-timeout",
        type=float,
        default=30.0,
        help="per-request HTTP timeout for wiki/mission checks",
    )
    args = ap.parse_args(argv)

    if not Path(args.python).exists():
        print(f"WARNING: interpreter not found at {args.python}; using as-is", flush=True)

    host = "127.0.0.1"

    # Seed (idempotent, never wiped) so wiki-recall has a real corpus; wipe the
    # per-run dirs exactly like the timing harness so the boot does fresh work.
    pages = measure_boot.seed_vault(args.pages)
    print(f"[smoke] vault seeded: {pages} pages at {measure_boot.VAULT_DIR}", flush=True)

    import shutil

    shutil.rmtree(measure_boot.DATA_DIR, ignore_errors=True)
    shutil.rmtree(measure_boot.ISO_DIR, ignore_errors=True)
    measure_boot.DATA_DIR.mkdir(parents=True, exist_ok=True)
    measure_boot.ISO_DIR.mkdir(parents=True, exist_ok=True)

    port = measure_boot._free_port()
    base_url = f"http://{host}:{port}"
    print(f"[smoke] spawning isolated headless instance on {base_url} ...", flush=True)

    proc = _spawn_app(args.python, port)
    failure: str | None = None
    results: list[tuple[str, bool, str]] = []

    try:
        ready, boot_ready_ms, tail = _wait_for_boot_ready(
            proc, timeout=args.boot_timeout
        )
        if not ready:
            tail_txt = "\n    ".join(tail[-25:]) or "(no output captured)"
            return _fail(
                proc,
                f"app never printed BOOT_READY_MS= within {args.boot_timeout:.0f}s\n"
                f"  last output:\n    {tail_txt}",
            )
        print(
            f"[smoke] BOOT_READY_MS={boot_ready_ms:.0f} — running functional checks",
            flush=True,
        )

        if not _wait_for_http(base_url, timeout=15.0):
            return _fail(proc, "HTTP server did not accept after BOOT_READY")

        # --- Check 1: chat round-trip -----------------------------------
        chat_ok, chat_detail = _run_chat_check(
            host, port, reply_timeout=args.chat_timeout
        )
        results.append(("chat", chat_ok, chat_detail))
        print(
            f"[smoke] chat        : {'PASS' if chat_ok else 'FAIL'} — {chat_detail}",
            flush=True,
        )

        # --- Check 2: wiki-recall ---------------------------------------
        wiki_ok, wiki_detail = _run_wiki_check(base_url, timeout=args.http_timeout)
        results.append(("wiki-recall", wiki_ok, wiki_detail))
        print(
            f"[smoke] wiki-recall : {'PASS' if wiki_ok else 'FAIL'} — {wiki_detail}",
            flush=True,
        )

        # --- Check 3: mission spawn -------------------------------------
        mission_ok, mission_detail = _run_mission_check(
            base_url, timeout=args.http_timeout
        )
        results.append(("mission-spawn", mission_ok, mission_detail))
        print(
            f"[smoke] mission     : {'PASS' if mission_ok else 'FAIL'} — {mission_detail}",
            flush=True,
        )

        failed = [name for name, ok, _ in results if not ok]
        if failed:
            failure = "; ".join(
                f"{name}: {detail}" for name, ok, detail in results if not ok
            )
    finally:
        measure_boot._terminate(proc)

    print("", flush=True)
    if failure is None:
        print("SMOKE PASS", flush=True)
        return 0
    print(f"SMOKE FAIL: {failure}", flush=True)
    return 1


def _fail(proc: subprocess.Popen, reason: str) -> int:
    measure_boot._terminate(proc)
    print("", flush=True)
    print(f"SMOKE FAIL: {reason}", flush=True)
    return 1


if __name__ == "__main__":
    # Make ``import measure_boot`` resolve regardless of the caller's CWD.
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    raise SystemExit(main())
