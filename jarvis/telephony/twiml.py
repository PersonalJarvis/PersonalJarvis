"""TwiML generation for the inbound voice webhook.

The ``/api/telephony/voice`` webhook answers Twilio with TwiML that opens a
bidirectional Media Streams socket:

    <Response>
      <Connect>
        <Stream url="wss://{public}/api/telephony/media">
          <Parameter name="secret" value="..."/>
          <Parameter name="call_sid" value="..."/>
          <Parameter name="language" value="de-DE"/>
        </Stream>
      </Connect>
    </Response>

``<Connect><Stream>`` (as opposed to ``<Start><Stream>``) gives a *bidirectional*
stream: Twilio sends caller audio AND plays back the audio we send on the same
socket. That is what lets Jarvis speak in its own Charon voice.

This module builds the XML with the ``twilio`` SDK when available, and falls
back to a hand-written but valid TwiML string when the SDK is absent — so the
webhook still returns correct XML on a base install without the extra (AD-T8).
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr


def build_connect_stream_twiml(
    *,
    wss_url: str,
    secret: str,
    call_sid: str = "",
    language_code: str = "de-DE",
    direction: str = "",
    opening: str = "",
) -> str:
    """Return TwiML opening a bidirectional Media Stream to ``wss_url``.

    Custom parameters (secret, call_sid, language) ride along in the ``start``
    event's ``customParameters`` so the WS handler can authenticate and
    configure the call without a separate lookup.

    For an *outbound* call (Chunk C) two extra parameters ride along:
    ``direction="outbound"`` and ``opening`` (the line Jarvis speaks first). Both
    default to empty and are filtered out below, so the inbound TwiML is
    byte-identical to before — outbound is purely additive.
    """
    parameters = {
        "secret": secret,
        "call_sid": call_sid,
        "language": language_code,
        "direction": direction,
        "opening": opening,
    }
    try:
        from twilio.twiml.voice_response import (  # type: ignore[import-untyped]
            Connect,
            Stream,
            VoiceResponse,
        )
    except ImportError:
        return _build_twiml_fallback(wss_url, parameters)

    response = VoiceResponse()
    connect = Connect()
    stream = Stream(url=wss_url)
    for name, value in parameters.items():
        if value:
            stream.parameter(name=name, value=value)
    connect.append(stream)
    response.append(connect)
    return str(response)


def _build_twiml_fallback(wss_url: str, parameters: dict[str, str]) -> str:
    """Hand-built TwiML used when the ``twilio`` SDK is not installed."""
    params_xml = "".join(
        f"<Parameter name={quoteattr(name)} value={quoteattr(value)}/>"
        for name, value in parameters.items()
        if value
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Connect>"
        f"<Stream url={quoteattr(wss_url)}>{params_xml}</Stream>"
        "</Connect></Response>"
    )


def build_reject_twiml(message: str = "") -> str:
    """Return TwiML that hangs up the call (telephony disabled / misconfigured).

    Spoken with Twilio's own ``<Say>`` because at this point Jarvis cannot open
    a media stream — this is the graceful-degradation path, not the normal one.
    """
    try:
        from twilio.twiml.voice_response import VoiceResponse
    except ImportError:
        say = f"<Say>{message}</Say>" if message else ""
        return f'<?xml version="1.0" encoding="UTF-8"?><Response>{say}<Hangup/></Response>'
    response = VoiceResponse()
    if message:
        response.say(message)
    response.hangup()
    return str(response)


__all__ = ["build_connect_stream_twiml", "build_reject_twiml"]
