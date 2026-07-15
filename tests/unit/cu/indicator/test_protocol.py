"""IPC protocol round-trip + garbage tolerance for the CU screen indicator."""
from __future__ import annotations

from jarvis.cu.indicator import protocol


def test_command_round_trip_with_fields() -> None:
    line = protocol.encode_command(protocol.CMD_SHOW, hint="Esc to cancel")
    assert line.endswith("\n")
    payload = protocol.decode_command(line)
    assert payload == {"cmd": "show", "hint": "Esc to cancel"}


def test_every_command_round_trips() -> None:
    for cmd in protocol.ALL_COMMANDS:
        assert protocol.decode_command(protocol.encode_command(cmd)) == {
            "cmd": cmd
        }


def test_decode_command_tolerates_garbage() -> None:
    assert protocol.decode_command("") is None
    assert protocol.decode_command("   \n") is None
    assert protocol.decode_command("not json") is None
    assert protocol.decode_command('"a bare string"') is None
    assert protocol.decode_command('{"cmd": "reboot"}') is None
    assert protocol.decode_command("[1, 2, 3]") is None


def test_ack_round_trip_and_garbage() -> None:
    assert protocol.decode_ack(protocol.encode_ack("blank")) == "blank"
    assert protocol.decode_ack("nonsense") is None
    assert protocol.decode_ack('{"err": "x"}') is None


def test_hint_survives_non_ascii() -> None:
    line = protocol.encode_command(protocol.CMD_SHOW, hint="Esc zum Abbrechen — läuft")  # noqa: E501  # i18n-allow: localized pill copy under test
    payload = protocol.decode_command(line)
    assert payload is not None
    assert payload["hint"].endswith("läuft")  # i18n-allow: assertion on the same fixture
