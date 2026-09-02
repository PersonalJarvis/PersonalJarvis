"""The in-venv browser runner: browser-use behind a JSON-lines pipe.

Executed by the managed environment's Python (never the app's): reads ONE
request object from stdin, answers with JSON lines on stdout, exits. Three
modes:

* ``probe``  — import browser-use, report its version.
* ``login``  — open the agent's persistent profile HEADED on ``start_url``
               with no task, keep it open until stdin closes or the timeout
               ends, so the person can sign in once.
* ``run``    — run one browser-use ``Agent`` on ``task`` in the agent's
               profile (or attached to a running Chrome over CDP), streaming
               a ``step`` line per step and a final ``done`` line.

The script imports nothing from ``jarvis`` and must run on Python 3.11+
with browser-use 0.13 only. Keys arrive in the request and are used for the
LLM object only — they never touch the browser or the page.
"""

# mypy: ignore-errors
# (runs in the managed venv; browser-use is not importable from the app)
from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any

PROTOCOL_VERSION = 1


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_request() -> dict[str, Any]:
    raw = sys.stdin.readline()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _build_llm(spec: dict[str, Any]) -> Any:
    import browser_use

    cls_name = str(spec.get("class") or "")
    cls = getattr(browser_use, cls_name, None)
    if cls is None:
        raise RuntimeError(f"browser-use has no LLM class {cls_name!r}")
    kwargs: dict[str, Any] = {"model": spec.get("model") or None}
    if spec.get("api_key"):
        kwargs["api_key"] = spec["api_key"]
    if spec.get("base_url"):
        kwargs["base_url"] = spec["base_url"]
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    return cls(**kwargs)


def _build_browser(req: dict[str, Any], *, headless: bool) -> Any:
    from browser_use import Browser

    kwargs: dict[str, Any] = {"headless": headless}
    if req.get("cdp_url"):
        kwargs["cdp_url"] = req["cdp_url"]
    else:
        kwargs["user_data_dir"] = req["profile_dir"]
        kwargs["keep_alive"] = bool(req.get("keep_alive", False))
    if req.get("executable_path"):
        kwargs["executable_path"] = req["executable_path"]
    if req.get("allowed_domains"):
        kwargs["allowed_domains"] = list(req["allowed_domains"])
    return Browser(**kwargs)


async def _probe() -> int:
    import browser_use

    _emit({"kind": "done", "ok": True, "version": getattr(browser_use, "__version__", "?")})
    return 0


async def _login(req: dict[str, Any]) -> int:
    browser = _build_browser(req, headless=False)
    await browser.start()
    start_url = str(req.get("start_url") or "about:blank")
    try:
        page = await browser.new_page(start_url)  # type: ignore[attr-defined]
        _ = page
    except Exception:  # noqa: BLE001 — some versions open the URL via the agent only
        try:
            await browser.navigate(start_url)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — the window is open; the person can type the URL
            _emit({"kind": "warn", "text": f"start url not opened: {exc}"})
    _emit({"kind": "login_open", "start_url": start_url})
    deadline = time.monotonic() + float(req.get("timeout_s") or 900)
    loop = asyncio.get_running_loop()
    while time.monotonic() < deadline:
        # Stdin closes when the app tells us the person is done.
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line:
            break
        if line.strip() == "quit":
            break
    try:
        await browser.stop()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — closing is best effort
        _emit({"kind": "warn", "text": f"browser stop: {exc}"})
    _emit({"kind": "done", "ok": True, "profile_dir": req.get("profile_dir")})
    return 0


async def _run(req: dict[str, Any]) -> int:
    from browser_use import Agent

    llm = _build_llm(req.get("llm") or {})
    browser = _build_browser(req, headless=bool(req.get("headless", True)))
    max_steps = int(req.get("max_steps") or 25)
    steps_seen = 0

    async def _on_step_end(agent: Any) -> None:
        nonlocal steps_seen
        steps_seen += 1
        url = ""
        try:
            history = agent.history
            urls = history.urls()
            url = str(urls[-1]) if urls else ""
        except Exception:  # noqa: BLE001 — the step line is informational
            url = ""
        _emit({"kind": "step", "n": steps_seen, "url": url})

    kwargs: dict[str, Any] = {
        "task": str(req.get("task") or ""),
        "llm": llm,
        "browser": browser,
        "max_failures": int(req.get("max_failures") or 3),
    }
    if req.get("extend_system_message"):
        kwargs["extend_system_message"] = str(req["extend_system_message"])
    if req.get("sensitive_data"):
        kwargs["sensitive_data"] = dict(req["sensitive_data"])
    if req.get("calculate_cost"):
        kwargs["calculate_cost"] = True
    agent = Agent(**kwargs)
    started = time.monotonic()
    try:
        history = await agent.run(max_steps=max_steps, on_step_end=_on_step_end)
    except TypeError:
        # Older signature without hooks on run().
        history = await agent.run(max_steps=max_steps)
    result: dict[str, Any] = {
        "kind": "done",
        "ok": True,
        "final_result": None,
        "urls": [],
        "errors": [],
        "steps": steps_seen,
        "seconds": round(time.monotonic() - started, 1),
        "cost_usd": None,
        "is_done": None,
        "is_successful": None,
    }
    try:
        result["final_result"] = history.final_result()
        result["urls"] = [str(u) for u in history.urls() if u]
        result["errors"] = [str(e) for e in history.errors() if e]
        result["is_done"] = bool(history.is_done())
        result["is_successful"] = history.is_successful()
        if not result["steps"]:
            result["steps"] = int(history.number_of_steps())
    except Exception as exc:  # noqa: BLE001 — a partial result beats none
        result["errors"].append(f"history read failed: {exc}")
    usage = getattr(history, "usage", None)
    total_cost = getattr(usage, "total_cost", None)
    if isinstance(total_cost, int | float):
        result["cost_usd"] = float(total_cost)
    try:
        await browser.stop()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — closing is best effort
        _emit({"kind": "warn", "text": f"browser stop: {exc}"})
    _emit(result)
    return 0


async def _main() -> int:
    req = _read_request()
    mode = str(req.get("mode") or "")
    try:
        if mode == "probe":
            return await _probe()
        if mode == "login":
            return await _login(req)
        if mode == "run":
            return await _run(req)
        _emit({"kind": "done", "ok": False, "error": f"unknown mode {mode!r}"})
        return 2
    except Exception as exc:  # noqa: BLE001 — the pipe carries the failure, typed by the app
        _emit({"kind": "done", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
