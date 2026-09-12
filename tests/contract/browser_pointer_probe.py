"""Exercise the real Browser-Use CDP hook on a disposable local page."""

import asyncio
import json
import sys
from pathlib import Path


async def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "jarvis/society/browser"))
    output = sys.stdout
    import live_runner

    events = []
    live_runner.emit = lambda kind, **data: events.append({"kind": kind, **data})
    worker = live_runner.Worker()
    try:
        profile = Path(sys.argv[2])
        await worker.start({"profile_dir": str(profile), "workspace": str(profile / "workspace"),
                            "executable": sys.argv[1], "allowed_domains": []})
        await worker.page.goto("about:blank")
        await worker.page.set_content('<button style="width:200px;height:100px" onclick="window.clicked=true">Click probe</button>')
        await worker.focused()
        target = next(t for t, p in worker.tabs.items() if p is worker.page)
        client = worker.browser.cdp_client
        attached = await client.send.Target.attachToTarget({"targetId": target, "flatten": True})
        worker.visual_action = True  # The real gate enables this only after approval.
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await client.send.Input.dispatchMouseEvent(
                {"type": kind, "x": 40, "y": 40, "button": "left", "clickCount": 1},
                session_id=attached["sessionId"],
            )
        assert await worker.page.evaluate("window.clicked") is True
        pointers = [e for e in events if e["kind"] == "pointer"]
        assert len(pointers) == 2
        assert pointers[-1]["click_id"] == 1
        if worker.native:
            assert pointers[-1]["y"] > 40
        worker.pointer.clear()
        assert events[-1]["visible"] is False
        print(json.dumps({"clicked": True, "pointer_events": len(pointers)}), file=output)
    finally:
        worker.closed = True
        tasks = [t for t in (worker.stream, worker.state_task) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if worker.native:
            worker.native.close()
        if worker.browser:
            await worker.browser.stop()
        if worker.context:
            await worker.context.close()
        if worker.playwright:
            await worker.playwright.stop()


if __name__ == "__main__":
    asyncio.run(main())
