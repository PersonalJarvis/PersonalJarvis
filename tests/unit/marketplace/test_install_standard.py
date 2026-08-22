"""The install standard: three surfaces, one source, shell-safe by rule."""

from __future__ import annotations

import pytest

from jarvis.marketplace.install_standard import install_block


def test_plugin_block_contains_the_three_surfaces() -> None:
    block = install_block("todo-fox", "plugin")
    assert block is not None
    assert block["cli"] == "jarvis marketplace install todo-fox"
    assert block["runner"] == "uvx --from personal-jarvis jarvis marketplace install todo-fox"
    assert block["prompt"] == 'Install the "todo-fox" plugin from the community marketplace.'


def test_skill_prompt_names_the_kind() -> None:
    block = install_block("three-point-check", "skill")
    assert block is not None
    assert '"three-point-check" skill' in block["prompt"]
    # CLI and runner are kind-agnostic: one command resolves either.
    assert block["cli"] == "jarvis marketplace install three-point-check"


@pytest.mark.parametrize(
    "name",
    ["../../evil", "UPPER", "has space", "under_score", "-lead", "trail-", "a..b", ""],
)
def test_unsafe_names_get_no_block(name: str) -> None:
    assert install_block(name, "plugin") is None


def test_valid_names_never_need_shell_quoting() -> None:
    block = install_block("a1.b-2", "skill")
    assert block is not None
    for line in (block["cli"], block["runner"]):
        # The property the copy-paste standard rests on: the two lines typed
        # at a shell never require quoting or escaping in any shell. (The
        # prompt is pasted at the assistant, not a shell — it may quote.)
        assert not set(line) & set("\"'\\$`;|&<>(){}!\n")
