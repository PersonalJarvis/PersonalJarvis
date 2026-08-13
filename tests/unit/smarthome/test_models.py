"""The unified device vocabulary — the properties everything above depends on.

Three things are pinned here because breaking any of them is silent:

* a device only advertises commands its capabilities actually unlock, which is
  what lets the UI draw the right controls without a vendor branch;
* device ids stay provider-namespaced, so two ecosystems cannot collide;
* the capability -> command table stays TOTAL, so a capability added without a
  command entry is caught here rather than by a device that quietly gains no
  controls in the section.
"""

from __future__ import annotations

from jarvis.smarthome.models import (
    COMMANDS_BY_CAPABILITY,
    CONSEQUENTIAL_COMMANDS,
    Capability,
    CommandName,
    Device,
    DeviceKind,
)


def _light(**overrides: object) -> Device:
    base: dict[str, object] = {
        "id": "home_assistant:light.kitchen",
        "provider": "home_assistant",
        "native_id": "light.kitchen",
        "name": "Kitchen",
        "kind": DeviceKind.LIGHT,
        "capabilities": (Capability.ON_OFF, Capability.BRIGHTNESS),
    }
    base.update(overrides)
    return Device(**base)  # type: ignore[arg-type]


def test_every_capability_has_a_command_entry() -> None:
    """A capability with no entry would silently render an uncontrollable card."""
    missing = [cap for cap in Capability if cap not in COMMANDS_BY_CAPABILITY]
    assert missing == [], f"capabilities with no command mapping: {missing}"


def test_every_command_is_reachable_from_some_capability() -> None:
    """A verb no capability unlocks can never be sent — dead vocabulary."""
    reachable = {cmd for cmds in COMMANDS_BY_CAPABILITY.values() for cmd in cmds}
    assert set(CommandName) == reachable


def test_supports_follows_capabilities_not_kind() -> None:
    dimmable = _light()
    assert dimmable.supports(CommandName.SET_BRIGHTNESS)
    assert dimmable.supports(CommandName.TURN_OFF)

    plain = _light(capabilities=(Capability.ON_OFF,))
    assert plain.supports(CommandName.TURN_OFF)
    # Same kind, same domain — but this bulb cannot dim, and the model says so.
    assert not plain.supports(CommandName.SET_BRIGHTNESS)


def test_read_only_device_supports_nothing() -> None:
    sensor = _light(
        kind=DeviceKind.SENSOR,
        capabilities=(Capability.READ_ONLY,),
    )
    assert all(not sensor.supports(cmd) for cmd in CommandName)


def test_json_advertises_the_command_set() -> None:
    """The UI derives its controls from `commands`, so it must be complete."""
    payload = _light().to_json()
    assert set(payload["commands"]) == {
        "turn_on",
        "turn_off",
        "toggle",
        "set_brightness",
    }
    assert payload["kind"] == "light"
    assert payload["capabilities"] == ["on_off", "brightness"]


def test_device_id_is_provider_namespaced() -> None:
    """Bare native ids collide across ecosystems; the prefix is the routing key."""
    device = _light()
    assert device.id.startswith(f"{device.provider}:")
    assert device.id.partition(":")[2] == device.native_id


def test_consequential_commands_are_the_irreversible_ones() -> None:
    """Pinned so a later edit cannot quietly demote unlocking a door."""
    assert CommandName.UNLOCK in CONSEQUENTIAL_COMMANDS
    assert CommandName.TURN_OFF not in CONSEQUENTIAL_COMMANDS
