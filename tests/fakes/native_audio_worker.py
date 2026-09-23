"""Real subprocess fixture for native IPC failure and lifecycle contracts."""

from __future__ import annotations

import base64
import json
import sys
import threading
import time

output = threading.Lock()
stop = threading.Event()
active = ""
mode = sys.argv[1] if len(sys.argv) > 1 else "normal"


def emit(event):
    with output:
        print(json.dumps(event), flush=True)


def generate(request):
    global active
    identifier = request["id"]
    active = identifier
    stop.clear()
    if request["text"] == "flood":
        for _ in range(500):
            emit({"kind": "text", "id": identifier, "text": "queued"})
    if request["text"] == "crash":
        import os

        os._exit(7)
    if request["text"] == "foreign":
        emit({"kind": "text", "id": "foreign", "text": "unrelated"})
    else:
        emit({"kind": "text", "id": identifier, "text": "hello"})
    if request["text"] in {"slow", "wedged"}:
        stop.wait(timeout=60)
    if stop.is_set():
        emit({"kind": "cancelled", "id": identifier})
    else:
        emit(
            {
                "kind": "audio",
                "id": identifier,
                "sample_rate": 24000,
                "pcm": base64.b64encode(b"\0\1" * 20).decode(),
            }
        )
        emit({"kind": "done", "id": identifier})
    active = ""


if mode == "slow_start":
    time.sleep(0.3)
emit(
    {
        "kind": "loaded",
        "protocol": 1,
        "revision": "bad" if mode == "bad" else "test",
        "engine": "fake",
        "tools": False,
        "full_duplex": False,
    }
)
for line in sys.stdin:
    request = json.loads(line)
    if request["command"] == "generate":
        threading.Thread(target=generate, args=(request,), daemon=True).start()
    elif request["command"] == "cancel":
        if mode == "ignore_cancel":
            continue
        if request["id"] == active:
            stop.set()
        else:
            emit({"kind": "error", "id": request["id"], "code": "not_active"})
    elif request["command"] == "shutdown":
        if mode == "ignore_cancel":
            continue
        break
