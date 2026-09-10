"""Desktop ownership and capture guards tested without touching the user's desktop."""

from types import SimpleNamespace

import pytest

from jarvis.agent_screen.protocol import ForegroundInfo, RawFrame
from jarvis.machines.desktop import ConnectorDesktops
from jarvis.machines.models import MachineCommand, MachineGrant


class Screen:
    screen_id = "screen"
    title = "Editor"
    calls = 0

    def foreground(self):
        return ForegroundInfo(True, title=self.title, rect=(0, 0, 2, 1))

    def geometry(self):
        return (0, 0, 2, 1)

    def grab(self, bbox, *, rgb):
        return RawFrame(2, 1, "RGB", b"\0" * 6)

    def act(self, verb, params):
        self.calls += 1
        return SimpleNamespace(ok=True, detail="clicked")


class Manager:
    def __init__(self):
        self.screen = Screen()
        self.released = 0

    async def acquire(self, owner, **kwargs):
        return SimpleNamespace(session=self.screen)

    async def release(self, lease):
        self.released += 1

    async def shutdown(self):
        self.released += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("system", ["windows", "macos", "linux"])
async def test_one_owner_fresh_capture_and_explicit_stop(system):
    manager = Manager()
    desktops = ConnectorDesktops("attached", managers={"attached": manager})
    grant = MachineGrant(agent_id="one", machine_id=system, workspace="/work", desktop="attached")
    command = MachineCommand(
        job_id="j",
        agent_id="one",
        trace_id="t",
        operation="desktop",
        args={"verb": "observe"},
        grant=grant,
    )
    observed = await desktops.execute(command)
    assert observed["image_png"]
    other = command.model_copy(
        update={"agent_id": "two", "grant": grant.model_copy(update={"agent_id": "two"})}
    )
    with pytest.raises(PermissionError, match="busy"):
        await desktops.execute(other)
    action = command.model_copy(
        update={
            "args": {
                "verb": "click",
                "params": {"x": 1, "y": 0},
                "observation_id": observed["observation_id"],
            }
        }
    )
    manager.screen.title = "Different window"
    with pytest.raises(PermissionError, match="Foreground changed"):
        await desktops.execute(action)
    assert manager.screen.calls == 0
    await desktops.close(pause=True)
    with pytest.raises(PermissionError, match="stopped"):
        await desktops.execute(command)
    assert manager.released == 1


@pytest.mark.asyncio
async def test_attached_cannot_replace_own_desktop():
    desktop = ConnectorDesktops("attached", managers={"attached": Manager()})
    command = MachineCommand(
        job_id="j",
        agent_id="one",
        trace_id="t",
        operation="desktop",
        args={"verb": "observe"},
        grant=MachineGrant(agent_id="one", machine_id="m", workspace="/work", desktop="own"),
    )
    with pytest.raises(PermissionError, match="Enable this desktop mode"):
        await desktop.execute(command)
