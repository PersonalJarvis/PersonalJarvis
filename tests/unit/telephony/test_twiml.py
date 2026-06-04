"""TwiML generation tests for the voice webhook."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from jarvis.telephony.twiml import build_connect_stream_twiml, build_reject_twiml


def test_connect_stream_twiml_is_well_formed_and_has_stream():
    xml = build_connect_stream_twiml(
        wss_url="wss://jarvis.example.com/api/telephony/media",
        secret="s3cr3t",
        call_sid="CA123",
        language_code="de-DE",
    )
    root = ET.fromstring(xml)
    assert root.tag == "Response"
    connect = root.find("Connect")
    assert connect is not None
    stream = connect.find("Stream")
    assert stream is not None
    assert stream.get("url") == "wss://jarvis.example.com/api/telephony/media"


def test_connect_stream_embeds_secret_parameter():
    xml = build_connect_stream_twiml(
        wss_url="wss://x/api/telephony/media",
        secret="abc-secret-xyz",
        call_sid="CA9",
        language_code="en-US",
    )
    root = ET.fromstring(xml)
    params = {p.get("name"): p.get("value") for p in root.iter("Parameter")}
    assert params["secret"] == "abc-secret-xyz"
    assert params["call_sid"] == "CA9"
    assert params["language"] == "en-US"


def test_secret_with_xml_special_chars_is_escaped():
    xml = build_connect_stream_twiml(
        wss_url="wss://x/api/telephony/media",
        secret='a&b"<c>',
        call_sid="CA1",
    )
    # Parsing succeeds -> escaping is correct.
    root = ET.fromstring(xml)
    params = {p.get("name"): p.get("value") for p in root.iter("Parameter")}
    assert params["secret"] == 'a&b"<c>'


def test_reject_twiml_hangs_up():
    xml = build_reject_twiml("Telephony disabled.")
    root = ET.fromstring(xml)
    assert root.find("Hangup") is not None
    say = root.find("Say")
    assert say is not None and say.text == "Telephony disabled."


def test_reject_twiml_without_message():
    xml = build_reject_twiml()
    root = ET.fromstring(xml)
    assert root.find("Hangup") is not None
    assert root.find("Say") is None
