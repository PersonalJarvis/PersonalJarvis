"""Hand-built macOS Accessibility (AX) API fake (Wave 2.1, EK-3).

Per CLAUDE.md the project uses real fakes, never ``unittest.mock``. The real
``pyobjc`` stack cannot be installed on the Windows dev box, and the real
AX-tree capture only runs on a macOS leg under a granted Accessibility
permission (``skip_ci``). This fake gives the flatten + role-normalization +
permission-degrade logic a deterministic, OS-free AX tree.

``FakeAXElement`` is duck-typed to the seam ``jarvis.vision.ax_tree`` reads:
it exposes ``copy_attribute_value(attr)`` returning the canned ``AXRole`` /
``AXTitle`` / ``AXValue`` / ``AXPosition`` / ``AXSize`` / ``AXEnabled`` /
``AXChildren`` values, which ``_ax_copy_attr`` prefers over the real
``HIServices`` call. A test wires ``AXTreeSource(traverser=...)`` to a function
that walks a ``FakeAXElement`` root, and ``permission_check=...`` to flip the
AD-13 gate.
"""

from __future__ import annotations

from typing import Any

# AX attribute string constants (identical to the real kAX*Attribute values
# the flatten helper keys on).
_ROLE = "AXRole"
_TITLE = "AXTitle"
_VALUE = "AXValue"
_DESCRIPTION = "AXDescription"
_IDENTIFIER = "AXIdentifier"
_POSITION = "AXPosition"
_SIZE = "AXSize"
_ENABLED = "AXEnabled"
_CHILDREN = "AXChildren"


class FakeAXElement:
    """A canned ``AXUIElement`` node read via ``copy_attribute_value``."""

    def __init__(
        self,
        *,
        role: str,
        title: str = "",
        value: str = "",
        description: str = "",
        identifier: str = "",
        position: tuple[int, int] = (0, 0),
        size: tuple[int, int] = (0, 0),
        enabled: bool = True,
        children: list[FakeAXElement] | None = None,
    ) -> None:
        self.role = role
        self.title = title
        self.value = value
        self.description = description
        self.identifier = identifier
        self.position = position
        self.size = size
        self.enabled = enabled
        self.children = children or []

    def copy_attribute_value(self, attribute: str) -> Any:
        if attribute == _ROLE:
            return self.role
        if attribute == _TITLE:
            return self.title
        if attribute == _VALUE:
            return self.value
        if attribute == _DESCRIPTION:
            return self.description
        if attribute == _IDENTIFIER:
            return self.identifier
        if attribute == _POSITION:
            return {"x": self.position[0], "y": self.position[1]}
        if attribute == _SIZE:
            return {"w": self.size[0], "h": self.size[1]}
        if attribute == _ENABLED:
            return self.enabled
        if attribute == _CHILDREN:
            return self.children
        return None


def build_fake_ax_traverser(
    root: FakeAXElement,
    *,
    window_title: str = "Fake.app",
    pid: int = 4242,
):
    """Return a ``traverser(depth, window_title_filter)`` for ``AXTreeSource``.

    It runs the module-level ``_ax_flatten`` against the canned ``FakeAXElement``
    root, so the test exercises the *real* flatten + role normalization while
    feeding scripted AX data.
    """
    from jarvis.vision.ax_tree import _ax_flatten  # local import: prod code under test

    def _traverse(max_depth: int, _window_title_filter: str | None):
        nodes: list[Any] = []
        _ax_flatten(root, depth=0, max_depth=max_depth, parent_index=-1, out=nodes)
        return (window_title, pid, nodes)

    return _traverse


def make_canned_ax_tree() -> FakeAXElement:
    """A small representative AX tree: window > {button, text field, link, label}.

    Native AX roles intentionally span the role-map table so the test can assert
    each normalizes to its canonical UIA role.
    """
    return FakeAXElement(
        role="AXWindow",
        title="Demo Window",
        position=(0, 0),
        size=(800, 600),
        children=[
            FakeAXElement(
                role="AXButton",
                title="Save",
                position=(10, 10),
                size=(80, 30),
            ),
            FakeAXElement(
                role="AXTextField",
                value="hello",
                identifier="search-box",
                position=(10, 50),
                size=(200, 24),
            ),
            FakeAXElement(
                role="AXLink",
                title="Learn more",
                position=(10, 90),
                size=(120, 20),
            ),
            FakeAXElement(
                role="AXCheckBox",
                title="Remember me",
                position=(10, 120),
                size=(160, 20),
            ),
            # A structural container that the role map drops.
            FakeAXElement(
                role="AXGroup",
                position=(10, 150),
                size=(300, 200),
                children=[
                    FakeAXElement(
                        role="AXStaticText",
                        title="A static label",
                        position=(20, 160),
                        size=(180, 18),
                    ),
                ],
            ),
        ],
    )


__all__ = [
    "FakeAXElement",
    "build_fake_ax_traverser",
    "make_canned_ax_tree",
]
