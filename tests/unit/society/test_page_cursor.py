"""The real Chrome window gets the agent arrow, and can hide it again."""

from jarvis.society.browser.page_cursor import ARM_SOURCE, CURSOR_SCRIPT, arm_cursor, install_cursor


class Frame:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def evaluate(self, source: str, arg: object = None) -> None:
        self.calls.append((source, arg))


class Page:
    def __init__(self) -> None:
        self.frame = Frame()
        self.frames = [self.frame]
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed


class Context:
    def __init__(self) -> None:
        self.page = Page()
        self.pages = [self.page]
        self.scripts: list[str] = []

    async def add_init_script(self, source: str) -> None:
        self.scripts.append(source)


async def test_cursor_is_prepared_for_later_pages_and_can_be_shown() -> None:
    context = Context()
    await install_cursor(context)
    await arm_cursor(context, True)
    assert context.scripts == [CURSOR_SCRIPT]
    assert context.page.frame.calls[0][0] == CURSOR_SCRIPT
    assert context.page.frame.calls[1] == (ARM_SOURCE, True)
    assert "dataset.jarvisCursor" in CURSOR_SCRIPT
    assert "mousemove" in CURSOR_SCRIPT


async def test_a_closed_page_is_skipped_and_hiding_reaches_open_frames() -> None:
    context = Context()
    context.page.closed = True
    await arm_cursor(context, False)
    assert context.page.frame.calls == []
