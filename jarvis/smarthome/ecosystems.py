"""The honest map of the smart-home world, and how each corner is reachable.

This is a PRODUCT decision table, not a config file. It exists because the
question "does Jarvis work with my stuff?" has three very different answers, and
hiding the difference behind one uniform grid of logos would be a lie:

* **direct** — Jarvis speaks the platform itself. A provider exists in
  :mod:`jarvis.smarthome.providers`.
* **via_hub** — Jarvis does not speak it, but Home Assistant does, and Home
  Assistant is a supported provider. The devices appear in the Devices tab like
  any other; only the setup happens in the hub.
* **unavailable** — no third party can reach it, by the vendor's design. Saying
  so plainly beats a "coming soon" that never comes.

Every entry also states how long a connection LASTS before someone has to log in
again, using the marketplace's own three words (``jarvis.marketplace.catalog``
``Longevity``). That is the field the maintainer cares about most: a connection
that dies every seven days is not an integration, it is a chore.

Sources are the vendors' own developer documentation. When an entry changes,
re-check it against the live docs — a stale claim here is worse than no claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Reachability(StrEnum):
    DIRECT = "direct"
    VIA_HUB = "via_hub"
    PLANNED = "planned"
    UNAVAILABLE = "unavailable"


class Tier(StrEnum):
    """How prominently an entry is shown — a CURATION decision, not a capability.

    Twenty equal cards in one grid is not an answer to "does this work with my
    stuff?", it is a wall the reader has to search. So the list is ranked by how
    likely someone is to recognise the name:

    * ``HUB`` — the one that reaches nearly everything. Exactly one entry, shown
      on its own as the recommended route, because for most houses it IS the
      answer and burying it among nineteen peers hides that.
    * ``POPULAR`` — the household names people actually own or shop for.
      Shown by default.
    * ``TECHNICAL`` — protocols and enthusiast kit (Matter, Thread, KNX, MQTT).
      Real, supported, and hidden behind "show everything" — someone who runs a
      Zigbee coordinator will look for it; someone who bought a smart plug at
      the supermarket should not have to scroll past it.
    """

    HUB = "hub"
    POPULAR = "popular"
    TECHNICAL = "technical"


class ConnectionKind(StrEnum):
    """HOW the credential is obtained — this is what decides the longevity."""

    #: A hub on the user's own network; the address is user data.
    HUB = "hub"
    #: A bridge on the local network, paired by pressing a physical button.
    #: The best kind: the key never expires and no vendor cloud is involved.
    LOCAL_BUTTON_PAIRING = "local_button_pairing"
    #: Plain local HTTP, optionally with a password the user sets.
    LOCAL_HTTP = "local_http"
    #: A local MQTT broker with username/password.
    LOCAL_MQTT = "local_mqtt"
    #: Vendor cloud, OAuth 2.0 with a refresh token.
    CLOUD_OAUTH = "cloud_oauth"
    #: Vendor cloud, a pasted API key or personal access token.
    CLOUD_TOKEN = "cloud_token"  # noqa: S105 — a connection KIND, not a secret
    #: No inbound path exists for a third-party controller at all.
    NONE = "none"


@dataclass(frozen=True, slots=True)
class Ecosystem:
    id: str
    display_name: str
    #: simpleicons slug for the card logo; the frontend falls back to a lettered
    #: tile when the slug is unknown to the CDN.
    logo_slug: str
    reachability: Reachability
    connection: ConnectionKind
    #: "permanent" | "self_renewing" | "provider_limited" — same words as the
    #: marketplace catalog uses, so the two surfaces cannot contradict.
    longevity: str
    #: One plain sentence: what the user actually has to do, or why they cannot.
    note: str
    #: Roughly what shows up once connected. Shown as a subtitle on the card.
    covers: str
    tier: Tier = Tier.TECHNICAL
    #: The brand's own colour, hex without "#". Drives the logo tile exactly as
    #: the marketplace does it: a white glyph on the brand colour reads as a
    #: product decision, where one uniform grey across twenty cards reads as a
    #: placeholder nobody finished.
    logo_color: str = "3F3F46"
    #: What the user actually DOES, step by step. Shown when a card is opened.
    #: Empty for an ecosystem no third party can reach — there is no procedure
    #: to give, and inventing one would send people hunting for a menu that
    #: does not exist.
    setup_steps: tuple[str, ...] = ()
    #: The vendor's own documentation for that procedure.
    docs_url: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "logo_slug": self.logo_slug,
            "reachability": str(self.reachability),
            "connection": str(self.connection),
            "longevity": self.longevity,
            "note": self.note,
            "covers": self.covers,
            "tier": str(self.tier),
            "logo_color": self.logo_color,
            "setup_steps": list(self.setup_steps),
            "docs_url": self.docs_url,
        }


# Order is display order. Hubs first (one connection, most devices), then the
# local bridges (durable and cloud-free), then the cloud ecosystems, then the
# ones that genuinely cannot be reached — last, but still listed, because "not
# possible" is an answer people are looking for.
ECOSYSTEMS: tuple[Ecosystem, ...] = (
    Ecosystem(
        id="home_assistant",
        display_name="Home Assistant",
        logo_slug="homeassistant",
        reachability=Reachability.DIRECT,
        connection=ConnectionKind.HUB,
        longevity="permanent",
        note=(
            "Connect once with a long-lived access token from your Home "
            "Assistant profile. The token is valid for ten years and is never "
            "rotated, so this connection needs no maintenance."
        ),
        covers=(
            "Around 2000 integrations — Hue, Zigbee, Z-Wave, Matter, Thread, "
            "KNX, Shelly, Tuya, Nest, HomeKit, Sonos and the rest"
        ),
        tier=Tier.HUB,
        logo_color="18BCF2",
        setup_steps=(
            "Open Home Assistant in your browser and sign in.",
            "Click your name at the bottom left, then the Security tab.",
            (
                "Scroll to Long-lived access tokens and create one. It is shown only once, so "
                "copy it "
                "straight away."
            ),
            (
                "Paste it here together with your Home Assistant address (for example "
                "http://192.168.1.20:8123)."
            ),
        ),
        docs_url="https://www.home-assistant.io/docs/authentication/",
    ),
    Ecosystem(
        id="matter",
        display_name="Matter",
        logo_slug="matter",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.HUB,
        longevity="permanent",
        note=(
            "Matter devices are commissioned to a controller with a pairing "
            "code, and a controller may hold that fabric only once. Home "
            "Assistant is that controller here; Jarvis then sees the devices "
            "through it rather than competing for the same slot."
        ),
        covers="Any Matter-certified light, plug, sensor, lock or thermostat",
        tier=Tier.TECHNICAL,
        logo_color="000000",
        setup_steps=(
            "Connect Home Assistant first — it holds the Matter fabric.",
            "In Home Assistant add the Matter integration.",
            (
                "Scan the device's pairing code. A device may belong to one controller at a time, "
                "so "
                "pair it here rather than in a phone app."
            ),
        ),
        docs_url="https://www.home-assistant.io/integrations/matter/",
    ),
    Ecosystem(
        id="thread",
        display_name="Thread",
        logo_slug="thread",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.HUB,
        longevity="permanent",
        note=(
            "Thread is the radio underneath many Matter devices and needs a "
            "border router in the house (a HomePod, an Apple TV, a Nest Hub or "
            "a Home Assistant SkyConnect). Control still runs over Matter."
        ),
        covers="Battery-powered Matter devices on a Thread mesh",
        tier=Tier.TECHNICAL,
        logo_color="000000",
        setup_steps=(
            (
                "You need a Thread border router in the house — a HomePod, an Apple TV, a Nest "
                "Hub or "
                "a Home Assistant SkyConnect."
            ),
            "Pair the device over Matter; Thread is only the radio underneath.",
        ),
        docs_url="https://www.home-assistant.io/integrations/thread/",
    ),
    Ecosystem(
        id="philips_hue",
        display_name="Philips Hue",
        logo_slug="philipshue",
        reachability=Reachability.PLANNED,
        connection=ConnectionKind.LOCAL_BUTTON_PAIRING,
        longevity="permanent",
        note=(
            "The Hue Bridge sits on your own network. Pairing means pressing "
            "the round button on the bridge once; the key it hands out never "
            "expires and no Philips account is involved."
        ),
        covers="Hue lights, plugs, motion sensors, dimmer switches",
        tier=Tier.POPULAR,
        logo_color="0065D3",
        setup_steps=(
            "Make sure the Hue Bridge and this computer are on the same network.",
            "Press the round button on top of the bridge.",
            "Confirm here within 30 seconds — the bridge hands out a key that never expires.",
        ),
        docs_url="https://developers.meethue.com/develop/get-started-2/",
    ),
    Ecosystem(
        id="ikea_dirigera",
        display_name="IKEA DIRIGERA",
        logo_slug="ikea",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_BUTTON_PAIRING,
        longevity="permanent",
        note=(
            "The DIRIGERA hub pairs locally by pressing its action button. IKEA "
            "has not published a stable public API, so the reliable path today "
            "is through Home Assistant's integration."
        ),
        covers="TRÅDFRI and DIRIGERA lights, plugs, blinds, air purifiers",
        tier=Tier.POPULAR,
        logo_color="0058A3",
        setup_steps=(
            "Connect Home Assistant first — IKEA publishes no stable public API of its own.",
            "In Home Assistant add the DIRIGERA integration.",
            "Press the action button on the underside of the DIRIGERA hub when asked.",
            "Your TRADFRI lights, plugs and blinds then appear here.",
        ),
        docs_url="https://www.home-assistant.io/integrations/dirigera/",
    ),
    Ecosystem(
        id="shelly",
        display_name="Shelly",
        logo_slug="shelly",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_HTTP,
        longevity="permanent",
        note=(
            "Shelly relays answer plain HTTP on your own network with no cloud "
            "account at all. They are discovered automatically by Home "
            "Assistant; a direct connector is a small step from there."
        ),
        covers="Shelly relays, dimmers, roller shutters, energy meters",
        tier=Tier.POPULAR,
        logo_color="4495D1",
        setup_steps=(
            "Connect Home Assistant first.",
            "Shelly relays are discovered automatically on your network — no cloud account at all.",
            "Confirm them in Home Assistant and they appear here.",
        ),
        docs_url="https://www.home-assistant.io/integrations/shelly/",
    ),
    Ecosystem(
        id="zigbee2mqtt",
        display_name="Zigbee2MQTT",
        logo_slug="mqtt",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_MQTT,
        longevity="permanent",
        note=(
            "A local MQTT broker with a username and password you choose "
            "yourself. Nothing expires because nobody outside the house issues "
            "the credential."
        ),
        covers="Any Zigbee device, regardless of brand, via a USB coordinator",
        tier=Tier.TECHNICAL,
        logo_color="660066",
        setup_steps=(
            "Run Zigbee2MQTT with a USB Zigbee coordinator plugged into the machine.",
            "Point it at a local MQTT broker with a username and password you choose.",
            "Connect Home Assistant to the same broker; the devices appear here.",
        ),
        docs_url="https://www.zigbee2mqtt.io/guide/getting-started/",
    ),
    Ecosystem(
        id="knx",
        display_name="KNX",
        logo_slug="knx",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_HTTP,
        longevity="permanent",
        note=(
            "The wired building-automation standard. It talks to an IP gateway "
            "in the fuse box; there is no account and no cloud."
        ),
        covers="Wired lighting, blinds, heating and ventilation in a fitted house",
        tier=Tier.TECHNICAL,
        logo_color="00A2E1",
        setup_steps=(
            "You need a KNX IP gateway in the fuse box.",
            "Connect Home Assistant and add the KNX integration with the gateway's address.",
            "Import your ETS project file so the addresses have names.",
        ),
        docs_url="https://www.home-assistant.io/integrations/knx/",
    ),
    Ecosystem(
        id="google_nest",
        display_name="Google Nest",
        logo_slug="googlenest",
        reachability=Reachability.PLANNED,
        connection=ConnectionKind.CLOUD_OAUTH,
        longevity="self_renewing",
        note=(
            "Google's Smart Device Management API reaches Nest thermostats, "
            "cameras, doorbells and locks over OAuth, and the refresh token "
            "stays alive once the OAuth app is published. It requires a one-off "
            "registration in Google's Device Access Console, which costs 5 US "
            "dollars — that fee is Google's, not ours."
        ),
        covers="Nest thermostats, cameras, doorbells, locks",
        tier=Tier.POPULAR,
        logo_color="4285F4",
        setup_steps=(
            (
                "Register once in Google's Device Access Console (a one-off 5 US dollar fee, "
                "charged "
                "by Google)."
            ),
            "Create a project and allow it to reach your Nest devices.",
            "Sign in with the Google account your Nest devices belong to.",
        ),
        docs_url="https://developers.google.com/nest/device-access",
    ),
    Ecosystem(
        id="google_home",
        display_name="Google Home",
        logo_slug="googlehome",
        reachability=Reachability.UNAVAILABLE,
        connection=ConnectionKind.NONE,
        longevity="permanent",
        note=(
            "Google's Home APIs ship only as Android and iOS SDKs — there is no "
            "server or desktop route to a Google Home structure. Nest hardware "
            "is reachable through the separate Nest entry above; everything "
            "else in a Google Home is reachable by talking to the device's own "
            "maker instead."
        ),
        covers="—",
        tier=Tier.POPULAR,
        logo_color="4285F4",
        setup_steps=(),
        docs_url="https://developers.home.google.com/apis",
    ),
    Ecosystem(
        id="samsung_smartthings",
        display_name="SmartThings",
        logo_slug="samsung",
        reachability=Reachability.PLANNED,
        connection=ConnectionKind.CLOUD_OAUTH,
        longevity="self_renewing",
        note=(
            "Samsung's cloud API covers a wide range of brands. Personal access "
            "tokens issued since late 2024 last only 24 hours, so the usable "
            "path is the OAuth flow, whose refresh token renews itself in the "
            "background."
        ),
        covers="SmartThings hubs, Samsung appliances and partnered brands",
        tier=Tier.POPULAR,
        logo_color="1428A0",
        setup_steps=(
            "Sign in with the Samsung account your SmartThings hub belongs to.",
            "Approve access for Jarvis. The session renews itself in the background.",
            (
                "Personal access tokens issued since late 2024 expire after 24 hours, so use this "
                "sign-in rather than a pasted token."
            ),
        ),
        docs_url="https://developer.smartthings.com/docs/getting-started/overview",
    ),
    Ecosystem(
        id="tuya",
        display_name="Tuya / Smart Life",
        logo_slug="tuya",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.CLOUD_TOKEN,
        longevity="self_renewing",
        note=(
            "Tuya is the white-label platform behind hundreds of no-name "
            "brands. Its cloud API requires a free Tuya IoT developer project, "
            "which is a fair amount of setup — Home Assistant's integration "
            "wraps that, and local control is possible for many devices."
        ),
        covers="Most inexpensive WLAN plugs, bulbs and sensors sold online",
        tier=Tier.POPULAR,
        logo_color="FF4800",
        setup_steps=(
            "Connect Home Assistant first — it wraps Tuya's developer setup for you.",
            (
                "In Home Assistant add the Tuya integration and sign in with your Smart Life app "
                "account."
            ),
            (
                "Many Tuya devices can also be controlled locally, without the cloud, once they "
                "are "
                "set up."
            ),
        ),
        docs_url="https://www.home-assistant.io/integrations/tuya/",
    ),
    Ecosystem(
        id="apple_home",
        display_name="Apple Home (HomeKit)",
        logo_slug="apple",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_BUTTON_PAIRING,
        longevity="permanent",
        note=(
            "Apple publishes no API for controlling a home from outside its own "
            "platforms. HomeKit accessories themselves speak an open local "
            "protocol, though, and Home Assistant pairs with them directly "
            "using the same eight-digit code the iPhone uses."
        ),
        covers="HomeKit-certified accessories, paired locally",
        tier=Tier.POPULAR,
        logo_color="000000",
        setup_steps=(
            "Connect Home Assistant first — it is the part that speaks HomeKit.",
            "In Home Assistant add the HomeKit Device integration.",
            (
                "Enter the eight-digit code printed on the accessory, the same one the iPhone asks "
                "for."
            ),
            "The accessory then appears here like any other device.",
        ),
        docs_url="https://www.home-assistant.io/integrations/homekit_controller/",
    ),
    Ecosystem(
        id="amazon_alexa",
        display_name="Amazon Alexa",
        logo_slug="amazonalexa",
        reachability=Reachability.UNAVAILABLE,
        connection=ConnectionKind.NONE,
        longevity="permanent",
        note=(
            "Alexa's smart-home API points the other way: it lets a device "
            "maker expose hardware TO Alexa, not a third party read devices out "
            "of someone's Alexa account. The useful move here is the reverse — "
            "publishing Jarvis as an Alexa skill later on."
        ),
        covers="—",
        tier=Tier.POPULAR,
        logo_color="00CAFF",
        setup_steps=(),
        docs_url="https://developer.amazon.com/en-US/docs/alexa/smarthome/understand-the-smart-home-skill-api.html",
    ),
    Ecosystem(
        id="sonos",
        display_name="Sonos",
        logo_slug="sonos",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_HTTP,
        longevity="permanent",
        note=(
            "Sonos speakers are controllable on the local network without a "
            "cloud account, which is how Home Assistant reaches them."
        ),
        covers="Sonos speakers: playback, volume, grouping",
        tier=Tier.POPULAR,
        logo_color="000000",
        setup_steps=(
            "Connect Home Assistant first.",
            "Sonos speakers are found automatically on your own network — no account needed.",
            "Confirm the discovered speakers in Home Assistant and they appear here.",
        ),
        docs_url="https://www.home-assistant.io/integrations/sonos/",
    ),
    Ecosystem(
        id="netatmo",
        display_name="Netatmo",
        logo_slug="netatmo",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.CLOUD_OAUTH,
        longevity="self_renewing",
        note=(
            "OAuth against Netatmo's cloud with a refresh token that renews "
            "itself. Weather and heating are the common cases."
        ),
        covers="Weather stations, thermostats, radiator valves, cameras",
        tier=Tier.TECHNICAL,
        logo_color="55B0D3",
        setup_steps=(
            "Connect Home Assistant first.",
            "Add the Netatmo integration and sign in with your Netatmo account.",
            "The session renews itself, so this stays connected.",
        ),
        docs_url="https://www.home-assistant.io/integrations/netatmo/",
    ),
    Ecosystem(
        id="tado",
        display_name="tado°",
        logo_slug="tado",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.CLOUD_OAUTH,
        longevity="self_renewing",
        note="OAuth device flow against tado°'s cloud; the session renews itself.",
        covers="Radiator valves, room thermostats, air-conditioning control",
        tier=Tier.TECHNICAL,
        logo_color="5A9FD4",
        setup_steps=(
            "Connect Home Assistant first.",
            "Add the tado integration and confirm the sign-in on tado's website.",
            "The session renews itself.",
        ),
        docs_url="https://www.home-assistant.io/integrations/tado/",
    ),
    Ecosystem(
        id="fritzbox",
        display_name="FRITZ!Box Smart Home",
        logo_slug="avm",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_HTTP,
        longevity="permanent",
        note=(
            "AVM's DECT accessories are controlled through the router itself "
            "with the router password — local, and nothing to renew."
        ),
        covers="FRITZ!DECT plugs, radiator valves, buttons",
        tier=Tier.TECHNICAL,
        logo_color="E2001A",
        setup_steps=(
            "Connect Home Assistant first.",
            "Add the FRITZ!SmartHome integration with your router's address and password.",
            "Local only — nothing to renew.",
        ),
        docs_url="https://www.home-assistant.io/integrations/fritzbox/",
    ),
    Ecosystem(
        id="homematic",
        display_name="Homematic IP",
        logo_slug="eq3",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_HTTP,
        longevity="permanent",
        note=(
            "A local CCU or Access Point holds the devices; the connection is "
            "on your own network and does not expire."
        ),
        covers="Radiator valves, window contacts, wall switches, alarm sensors",
        tier=Tier.TECHNICAL,
        logo_color="6C2C2F",
        setup_steps=(
            "Connect Home Assistant first.",
            "Add the Homematic IP integration and enter the access point's SGTIN from its sticker.",
            "Press the blue button on the access point to confirm.",
        ),
        docs_url="https://www.home-assistant.io/integrations/homematicip_cloud/",
    ),
    Ecosystem(
        id="wled",
        display_name="WLED",
        logo_slug="wled",
        reachability=Reachability.VIA_HUB,
        connection=ConnectionKind.LOCAL_HTTP,
        longevity="permanent",
        note="An open local JSON API with no account at all.",
        covers="DIY LED strips and effect controllers",
        tier=Tier.TECHNICAL,
        logo_color="FF5722",
        setup_steps=(
            "Connect Home Assistant first.",
            "WLED controllers are discovered automatically on your network.",
            "Confirm them and the strips appear here.",
        ),
        docs_url="https://kno.wled.ge/",
    ),
)

_BY_ID: dict[str, Ecosystem] = {eco.id: eco for eco in ECOSYSTEMS}


def get_ecosystem(ecosystem_id: str) -> Ecosystem | None:
    return _BY_ID.get(ecosystem_id)


def ecosystems_json() -> list[dict[str, Any]]:
    return [eco.to_json() for eco in ECOSYSTEMS]
