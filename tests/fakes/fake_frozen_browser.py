"""An API-only frozen app with explicitly requested browser provisioning."""


class FrozenBrowserApp:
    def __init__(self, *, installed=False, running=False, refuse=False):
        self.installed = installed
        self.running = running
        self.refuse = refuse
        self.polls = 0
        self.requests = []
        self.frames = []
        self.stopped = []

    def poll(self):
        return None

    def request(self, port, path, key, *, method="GET"):
        self.requests.append((method, path, key))
        if path == "/api/health":
            return {"ok": True, "instance": "dev"}
        if path == "/api/society/browser/install" and method == "POST":
            if self.refuse:
                return {"started": False, "phase": "idle"}
            self.running = True
            return {"started": True, "running": True, "phase": "checking"}
        if path == "/api/society/browser/status" and method == "GET":
            if self.running:
                self.polls += 1
                if self.polls >= 2:
                    self.installed = True
                    self.running = False
            return {
                "installed": self.installed,
                "running": self.running,
                "phase": "done" if self.installed else "checking" if self.running else "idle",
            }
        raise AssertionError(f"Unexpected probe request: {method} {path}")

    def capture(self, port, key, output):
        assert self.installed, "The probe must await installation before checking frames"
        self.frames.append(output)
        return {"width": 640, "height": 480}
