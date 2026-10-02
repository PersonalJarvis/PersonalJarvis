"""Browser protocol double: records cold starts without Chrome or model calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    log_path = None
    for line in sys.stdin:
        message = json.loads(line)
        op, args = message["op"], message["args"]
        if op == "ensure":
            workspace = Path(args["workspace"])
            workspace.mkdir(parents=True, exist_ok=True)
            log_path = workspace / "browser-start.jsonl"
        if log_path is not None:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"op": op, "args": args}) + "\n")
        result = (
            {"generation": "fake", "full_window": True}
            if op == "ensure"
            else {"ok": True, "final_result": "Browser task completed"}
        )
        print(
            json.dumps({"kind": "response", "id": message["id"], "ok": True, "result": result}),
            flush=True,
        )
        if op == "shutdown":
            break


if __name__ == "__main__":
    main()
