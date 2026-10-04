"""Native browser fixtures that never call a platform API."""

from types import SimpleNamespace


class BrowserWindow:
    def __init__(self):
        self.calls = []
        self.observation = {
            "width": 1600,
            "height": 1000,
            "geometry_id": "original",
            "bytes": b"image",
        }

    def frame(self):
        return dict(self.observation)

    def input(self, op, args):
        if args.get("geometry_id", "original") != self.observation["geometry_id"]:
            raise ValueError("Browser geometry changed; wait for a new frame")
        self.calls.append((op, args))


def native_worker():
    page = SimpleNamespace(url="https://x.com/home")

    async def focused(**kwargs):
        return page

    return SimpleNamespace(
        native=BrowserWindow(),
        manual=False,
        visual_action=True,
        browser_args={},
        focused=focused,
        page=page,
    )


class BrowserTools:
    def __init__(self):
        self.actions = {}

    def action(self, description, *, param_model):
        def register(fn):
            self.actions[fn.__name__] = (fn, param_model, description)
            return fn

        return register
