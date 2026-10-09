"""Several addresses per computer, the on/off switch and the check's trace id.

Runs against real in-process SSH servers: a closed port stands in for an
address that is down, a second server with its own host key for a stranger
answering on an address.
"""

from __future__ import annotations

import json
import socket

import pytest

from jarvis.computers import identity
from jarvis.computers.models import PRIMARY_ROUTE_ID, Computer
from jarvis.computers.service import ComputerError, ComputerService
from jarvis.computers.store import ComputerStore
from tests.fakes.fake_ssh_server import FakeSshServer


@pytest.fixture
async def ssh_server():  # noqa: ANN201
    server = FakeSshServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def stranger():  # noqa: ANN201
    server = FakeSshServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


def _closed_port() -> int:
    """A local port nothing listens on (bound, read, released)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _online(service: ComputerService, server: FakeSshServer) -> Computer:
    server.state.authorized.add(identity.public_key_line())
    computer = await service.add_server(name="box", host="127.0.0.1", port=server.port)
    assert computer.health.status == "online", computer.health.message
    return computer


# -- the record ---------------------------------------------------------------


def test_a_record_from_before_routes_gets_its_address_as_the_only_route(tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "computers.json"
    legacy = {"id": "c_1", "name": "old", "host": "203.0.113.5", "port": 2222, "created_at": 1.0}
    path.write_text(json.dumps({"version": 1, "computers": [legacy]}), encoding="utf-8")

    (computer,) = ComputerStore(path).all()

    assert computer.enabled is True
    assert [(r.id, r.host, r.port) for r in computer.routes] == [
        (PRIMARY_ROUTE_ID, "203.0.113.5", 2222)
    ]
    assert computer.active_route_id == PRIMARY_ROUTE_ID


def test_routes_survive_a_round_trip_through_the_store(tmp_path) -> None:  # noqa: ANN001
    store = ComputerStore(tmp_path / "computers.json")
    computer = Computer(id="c_1", name="n", host="10.0.0.2", created_at=1.0)
    store.add(computer)
    store.update(
        "c_1",
        lambda row: row.model_copy(
            update={
                "routes": [
                    *row.routes,
                    row.routes[0].model_copy(update={"id": "r_x", "host": "box.ts.net"}),
                ]
            }
        ),
    )

    (again,) = ComputerStore(tmp_path / "computers.json").all()
    assert [r.host for r in again.routes] == ["10.0.0.2", "box.ts.net"]


# -- switching off ------------------------------------------------------------


async def test_a_switched_off_computer_is_not_connected_or_checked(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)

    off = computer_service.update(computer.id, enabled=False)
    assert off.enabled is False

    with pytest.raises(ComputerError) as caught:
        await computer_service.run(computer.id, "uptime")
    assert caught.value.kind == "disabled" and caught.value.status == 409
    with pytest.raises(ComputerError):
        await computer_service.connect(computer.id)
    # A check leaves the record as it was instead of reporting it offline.
    assert (await computer_service.check(computer.id)).health == off.health
    assert [c.id for c in await computer_service.check_all()] == [computer.id]

    on = computer_service.update(computer.id, enabled=True)
    assert on.enabled is True
    assert (await computer_service.run(computer.id, "uptime")).exit_status == 0


async def test_check_all_skips_switched_off_computers(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    computer_service.update(computer.id, enabled=False)
    await ssh_server.stop()  # would turn an ordinary check "offline"

    (after,) = await computer_service.check_all()

    assert after.health.status == "online"


# -- routes -------------------------------------------------------------------


def _set_port(service: ComputerService, computer_id: str, route_id: str, port: int) -> None:
    """Point one stored route at another port without the edit's identity reset."""

    def change(row: Computer) -> Computer:
        routes = [
            r.model_copy(update={"port": port}) if r.id == route_id else r for r in row.routes
        ]
        return row.model_copy(update={"routes": routes})

    service._store.update(computer_id, change)  # noqa: SLF001 — simulate a network change


async def test_an_unreachable_preferred_route_falls_through_to_the_next(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    both = await computer_service.add_route(computer.id, host="localhost", port=ssh_server.port)
    preferred, fallback = both.routes
    _set_port(computer_service, computer.id, preferred.id, _closed_port())

    checked = await computer_service.check(computer.id)

    assert checked.health.status == "online", checked.health.message
    assert checked.active_route_id == fallback.id
    assert (checked.host, checked.port) == ("localhost", ssh_server.port)
    # An ordinary connection now skips the dead address straight away.
    assert (await computer_service.run(computer.id, "uptime")).exit_status == 0


async def test_a_check_moves_back_to_the_preferred_route_once_it_answers(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    both = await computer_service.add_route(computer.id, host="localhost", port=ssh_server.port)
    preferred, fallback = both.routes
    _set_port(computer_service, computer.id, preferred.id, _closed_port())
    assert (await computer_service.check(computer.id)).active_route_id == fallback.id

    _set_port(computer_service, computer.id, preferred.id, ssh_server.port)
    checked = await computer_service.check(computer.id)

    assert checked.active_route_id == preferred.id
    assert checked.host == "127.0.0.1"


async def test_every_route_down_reports_the_preferred_address(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    both = await computer_service.add_route(computer.id, host="localhost", port=ssh_server.port)
    dead = _closed_port()
    _set_port(computer_service, computer.id, both.routes[0].id, dead)
    _set_port(computer_service, computer.id, both.routes[1].id, _closed_port())

    checked = await computer_service.check(computer.id)

    assert checked.health.status == "offline"
    assert str(dead) in (checked.health.message or "")


async def test_a_stranger_on_a_new_address_is_refused(
    computer_service: ComputerService, ssh_server: FakeSshServer, stranger: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)

    with pytest.raises(ComputerError) as caught:
        await computer_service.add_route(computer.id, host="127.0.0.1", port=stranger.port)

    assert caught.value.kind == "host_key_changed"
    assert len(computer_service.get(computer.id).routes) == 1


async def test_a_stranger_behind_a_stored_route_is_never_trusted(
    computer_service: ComputerService, ssh_server: FakeSshServer, stranger: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    added = await computer_service.add_route(computer.id, host="localhost", port=ssh_server.port)
    # The machine moves away from its preferred address and a stranger takes it.
    computer_service.order_routes(computer.id, [added.routes[1].id, added.routes[0].id])
    stranger.state.authorized.add(identity.public_key_line())
    second_id = added.routes[1].id

    def swap(row: Computer) -> Computer:
        routes = [
            r.model_copy(update={"port": stranger.port}) if r.id == second_id else r
            for r in row.routes
        ]
        return row.model_copy(update={"routes": routes})

    computer_service._store.update(computer.id, swap)  # noqa: SLF001 — simulate the swap

    checked = await computer_service.check(computer.id)

    assert checked.health.status == "host_key_changed"
    assert checked.health.trace_id and checked.health.trace_id.startswith("cmp-")


async def test_an_unreachable_new_address_is_not_added(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)

    with pytest.raises(ComputerError) as caught:
        await computer_service.add_route(computer.id, host="127.0.0.1", port=_closed_port())

    assert caught.value.kind == "unreachable"


async def test_route_add_needs_a_pinned_identity(computer_service: ComputerService) -> None:
    computer = Computer(id="c_new", name="n", host="203.0.113.9", created_at=1.0)
    computer_service._store.add(computer)  # noqa: SLF001 — a never-reached record

    with pytest.raises(ComputerError) as caught:
        await computer_service.add_route("c_new", host="203.0.113.10")

    assert caught.value.status == 409


async def test_duplicate_and_last_routes_are_refused(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)

    with pytest.raises(ComputerError):
        await computer_service.add_route(computer.id, host="127.0.0.1", port=ssh_server.port)
    with pytest.raises(ComputerError) as caught:
        computer_service.remove_route(computer.id, computer.routes[0].id)
    assert caught.value.status == 409


async def test_removing_the_active_route_falls_back_to_the_next(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    added = await computer_service.add_route(computer.id, host="localhost", port=ssh_server.port)

    left = computer_service.remove_route(computer.id, added.routes[0].id)

    assert [r.host for r in left.routes] == ["localhost"]
    assert left.active_route_id == left.routes[0].id
    assert left.host == "localhost"


async def test_order_must_name_every_route_once(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)
    added = await computer_service.add_route(computer.id, host="localhost", port=ssh_server.port)
    ids = [r.id for r in added.routes]

    with pytest.raises(ComputerError):
        computer_service.order_routes(computer.id, ids[:1])
    reordered = computer_service.order_routes(computer.id, list(reversed(ids)))

    assert [r.id for r in reordered.routes] == list(reversed(ids))


async def test_editing_the_address_moves_the_active_route(
    computer_service: ComputerService, ssh_server: FakeSshServer
) -> None:
    computer = await _online(computer_service, ssh_server)

    edited = computer_service.update(computer.id, host="localhost")

    assert edited.routes[0].host == "localhost"
    assert edited.host_key is None  # a new address is a new machine until proven


async def test_a_failed_check_carries_a_trace_id(
    computer_service: ComputerService, ssh_server: FakeSshServer, caplog: pytest.LogCaptureFixture
) -> None:
    computer = await _online(computer_service, ssh_server)
    await ssh_server.stop()

    checked = await computer_service.check(computer.id)

    assert checked.health.status == "offline"
    assert checked.health.trace_id
    assert checked.health.trace_id in caplog.text


async def test_keep_working_never_moves_panes_to_a_switched_off_computer(
    tmp_path,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from jarvis.agentic_ide import offload_on_quit
    from jarvis.computers import store as store_module

    path = tmp_path / "computers.json"
    monkeypatch.setattr(store_module, "default_path", lambda: path)
    ComputerStore(path).add(
        Computer(id="c_off", name="off", host="203.0.113.8", created_at=1.0, enabled=False)
    )
    monkeypatch.setattr(offload_on_quit, "target", lambda: "c_off")
    placed: list[str] = []

    async def place_workspace(workspace_id: str, *, computer_id: str) -> None:
        placed.append(workspace_id)

    running = SimpleNamespace(pty_id="p1", computer_id="")
    registry = SimpleNamespace(
        sessions=[SimpleNamespace(id="ws1", name="app", terminals=[running])],
        place_workspace=place_workspace,
    )

    assert await offload_on_quit.offload_before_quit(registry) == []
    assert placed == []
