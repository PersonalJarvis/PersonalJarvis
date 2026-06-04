"""Chunk C — outbound telephony engine tests (twilio mocked, no PSTN).

Covers the three new surfaces:

* ``jarvis.telephony.outbound.place_call`` — Contract 2 (frozen). The twilio
  REST ``Client`` is faked, so ``calls.create`` is asserted against without any
  network call. The twilio-missing / unconfigured paths raise
  ``TelephonyProvisionError`` (clear English, same guard as ``provisioning.py``).
* the outbound ``<Connect><Stream>`` TwiML (``build_connect_stream_twiml`` with
  the new ``direction``/``opening`` parameters) — and a regression check that the
  inbound TwiML stays byte-identical.
* the session speaking the opening first on an outbound call
  (``speak_opening`` / ``speak_intro``), reusing the existing conversation loop.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest

from jarvis.telephony.provisioning import TelephonyProvisionError

# --------------------------------------------------------------------------- #
# Fake twilio REST client (records calls.create kwargs, returns a CallSid)
# --------------------------------------------------------------------------- #


class _FakeCalls:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create(self, **kwargs):  # noqa: ANN003, ANN201
        self.created.append(kwargs)
        return SimpleNamespace(sid="CA_OUTBOUND_TEST")


class _FakeClient:
    instances: list[_FakeClient] = []

    def __init__(self, account_sid: str, auth_token: str) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.calls = _FakeCalls()
        _FakeClient.instances.append(self)


@pytest.fixture
def fake_twilio(monkeypatch):
    """Patch ``twilio.rest.Client`` so ``place_call``'s lazy import resolves it."""
    _FakeClient.instances = []
    import twilio.rest  # noqa: PLC0415 - twilio is installed in this env

    monkeypatch.setattr(twilio.rest, "Client", _FakeClient)
    return _FakeClient


_VALID = {
    "account_sid": "AC" + "0" * 32,
    "auth_token": "supersecrettoken",
    "from_number": "+49301112222",
    "public_base_url": "https://jarvis.example.com",
}


# --------------------------------------------------------------------------- #
# place_call — Contract 2
# --------------------------------------------------------------------------- #


def test_place_call_dials_raw_number_and_returns_call_sid(fake_twilio):
    from jarvis.telephony.outbound import place_call

    sid = place_call(to="+4915112345678", opening="Hallo Christoph.", **_VALID)

    assert sid == "CA_OUTBOUND_TEST"
    assert len(fake_twilio.instances) == 1
    client = fake_twilio.instances[0]
    assert client.account_sid == _VALID["account_sid"]
    assert len(client.calls.created) == 1
    kwargs = client.calls.created[0]
    assert kwargs["to"] == "+4915112345678"
    # twilio's keyword is ``from_`` (``from`` is a reserved word).
    assert kwargs["from_"] == "+49301112222"


def test_place_call_points_url_at_existing_voice_webhook(fake_twilio):
    from jarvis.telephony.outbound import place_call

    place_call(to="+4915112345678", opening="Guten Tag.", **_VALID)
    kwargs = fake_twilio.instances[0].calls.created[0]
    url = kwargs["url"]
    assert url.startswith("https://jarvis.example.com/api/telephony/voice")


def test_place_call_carries_opening_in_url_querystring(fake_twilio):
    from urllib.parse import parse_qs, urlsplit

    from jarvis.telephony.outbound import place_call

    place_call(to="+4915112345678", opening="Hallo Welt & Co", **_VALID)
    url = fake_twilio.instances[0].calls.created[0]["url"]
    query = parse_qs(urlsplit(url).query)
    assert query.get("opening") == ["Hallo Welt & Co"]


def test_place_call_without_opening_still_dials(fake_twilio):
    from jarvis.telephony.outbound import place_call

    sid = place_call(to="+4915112345678", **_VALID)
    assert sid == "CA_OUTBOUND_TEST"
    assert fake_twilio.instances[0].calls.created[0]["to"] == "+4915112345678"


def test_place_call_rejects_non_e164_number(fake_twilio):
    from jarvis.telephony.outbound import place_call

    with pytest.raises(TelephonyProvisionError) as exc:
        place_call(to="0151 1234", opening="Hi", **_VALID)
    assert "E.164" in str(exc.value)
    # Never reached the SDK.
    assert fake_twilio.instances == []


def test_place_call_requires_from_number(fake_twilio):
    from jarvis.telephony.outbound import place_call

    args = {**_VALID, "from_number": ""}
    with pytest.raises(TelephonyProvisionError):
        place_call(to="+4915112345678", opening="Hi", **args)


def test_place_call_requires_public_base_url(fake_twilio):
    from jarvis.telephony.outbound import place_call

    args = {**_VALID, "public_base_url": ""}
    with pytest.raises(TelephonyProvisionError):
        place_call(to="+4915112345678", opening="Hi", **args)


def test_place_call_requires_credentials(fake_twilio):
    from jarvis.telephony.outbound import place_call

    args = {**_VALID, "account_sid": "", "auth_token": ""}
    with pytest.raises(TelephonyProvisionError):
        place_call(to="+4915112345678", opening="Hi", **args)


def test_place_call_raises_clear_error_when_twilio_missing(monkeypatch):
    """When the twilio SDK is absent, raise a clear English provisioning error."""
    from jarvis.telephony.outbound import place_call

    # Force ``from twilio.rest import Client`` to raise ImportError.
    monkeypatch.setitem(sys.modules, "twilio.rest", None)
    with pytest.raises(TelephonyProvisionError) as exc:
        place_call(to="+4915112345678", opening="Hi", **_VALID)
    assert "telephony" in str(exc.value).lower()


# --------------------------------------------------------------------------- #
# Outbound TwiML (build_connect_stream_twiml direction/opening)
# --------------------------------------------------------------------------- #


def test_outbound_twiml_carries_direction_and_opening():
    from jarvis.telephony.twiml import build_connect_stream_twiml

    xml = build_connect_stream_twiml(
        wss_url="wss://jarvis.example.com/api/telephony/media",
        secret="s3cr3t",
        call_sid="CAOUT",
        language_code="de-DE",
        direction="outbound",
        opening="Hallo Christoph, hier ist Jarvis.",
    )
    root = ET.fromstring(xml)
    params = {p.get("name"): p.get("value") for p in root.iter("Parameter")}
    assert params["direction"] == "outbound"
    assert params["opening"] == "Hallo Christoph, hier ist Jarvis."
    # Still the existing machinery: a Stream pointing at the media socket.
    stream = root.find("Connect/Stream")
    assert stream is not None
    assert stream.get("url") == "wss://jarvis.example.com/api/telephony/media"
    assert params["secret"] == "s3cr3t"


def test_inbound_twiml_has_no_outbound_params_regression():
    """Inbound TwiML must stay byte-identical (no direction/opening leak)."""
    from jarvis.telephony.twiml import build_connect_stream_twiml

    xml = build_connect_stream_twiml(
        wss_url="wss://x/api/telephony/media",
        secret="abc",
        call_sid="CA9",
        language_code="de-DE",
    )
    root = ET.fromstring(xml)
    names = {p.get("name") for p in root.iter("Parameter")}
    assert "direction" not in names
    assert "opening" not in names
    assert names == {"secret", "call_sid", "language"}


def test_outbound_twiml_with_special_chars_in_opening_is_escaped():
    from jarvis.telephony.twiml import build_connect_stream_twiml

    xml = build_connect_stream_twiml(
        wss_url="wss://x/api/telephony/media",
        secret="s",
        direction="outbound",
        opening='Sag "Hallo" & <Tschüss>',
    )
    root = ET.fromstring(xml)  # parses cleanly -> escaping correct
    params = {p.get("name"): p.get("value") for p in root.iter("Parameter")}
    assert params["opening"] == 'Sag "Hallo" & <Tschüss>'


# --------------------------------------------------------------------------- #
# Session: outbound opening spoken first (reuses the conversation loop)
# --------------------------------------------------------------------------- #


def _sink():
    msgs: list[dict] = []

    async def send(msg: dict) -> None:
        msgs.append(msg)

    return msgs, send


def _make_session(send, **kw):
    from jarvis.telephony.session import TelephonyCallSession
    from tests.fakes.fake_telephony_stack import FakeBrain, FakeSTT, FakeTTS

    params = {
        "call_sid": "CAOUT",
        "stream_sid": "MZOUT",
        "send": send,
        "stt": FakeSTT(["Hallo?"]),
        "brain": FakeBrain("Antwort."),
        "tts": FakeTTS(ms_per_char=2),
        "language_code": "de-DE",
    }
    params.update(kw)
    return TelephonyCallSession(**params)


async def test_outbound_opening_is_spoken_first():
    msgs, send = _sink()
    session = _make_session(send, direction="outbound", opening="Guten Tag, hier ist Jarvis.")
    n = await session.speak_intro()
    assert n > 0
    media = [m for m in msgs if m.get("event") == "media"]
    assert len(media) == n
    # The opening (scrubbed) was the first — and only — thing synthesized.
    assert session._tts.calls, "opening must be synthesized"
    first_text = session._tts.calls[0][0]
    assert "Guten Tag" in first_text


async def test_speak_intro_inbound_speaks_greeting_unchanged():
    msgs, send = _sink()
    session = _make_session(send, greeting="Willkommen bei Jarvis.")
    # Default direction is inbound -> intro must speak the greeting.
    n = await session.speak_intro()
    assert n > 0
    assert session._tts.calls
    assert "Willkommen" in session._tts.calls[0][0]


async def test_outbound_without_opening_falls_back_to_greeting():
    msgs, send = _sink()
    session = _make_session(send, direction="outbound", opening="", greeting="Standardbegrüßung.")
    n = await session.speak_intro()
    assert n > 0
    assert "Standardbegrüßung" in session._tts.calls[0][0]
