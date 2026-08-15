---
schema_version: "1"
name: plugin-home_assistant
description: Read and control the user's smart home across every connected platform.
when_to_use: Use when the user asks about or wants to change something physical at home — lights, heating, doors, blinds, sockets, scenes or a room sensor.
category: home
plugin_id: home_assistant
intent_verbs: [schalte, mach, dimm, stell, öffne, schließ, zeig, turn, switch, dim, set, open, close, show, enciende, apaga, abre, cierra, pon]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [home assistant, homeassistant, licht, lichter, lampe, light, lights, luz, luces, heizung, heating, calefacción, thermostat, temperatur, temperature, temperatura, rollladen, blinds, persiana, garagentor, garage door, steckdose, socket, enchufe, szene, scene, escena, smart home]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: '(home ?assistant|smart ?home|hausautomation|dom[oó]tica)'  # i18n-allow: spoken-input vocabulary
requires_tools: [smart_home]
risk_policy:
  default_tier: ask
---

Use the `smart_home` tool to read and control the user's home. It speaks one
vocabulary across every connected platform, so the same steps apply whether the
devices sit behind a hub, a local bridge, or several at once.

- Find the device before acting: `list_devices`, optionally filtered by `room`
  or `kind` (light, switch, climate, cover, lock, sensor, media_player, scene).
  Match what the user actually said — they name a room and a thing, not an id.
- If a name matches several devices, the tool returns the candidates instead of
  choosing. Ask the user which one; a guess on a lock is not recoverable.
- Only send a command the device reports under `can`. Anything else is refused
  before it reaches the hardware.
- Acting on a home is physical and asks for confirmation. State plainly what you
  are about to change — which device, in which room, to what.
- `unlock` and `open` need the user's explicit spoken agreement, and only then
  the `confirm` flag. Never set it because the sentence sounded like a yes.
- Report the result from the `changed` devices the tool hands back, not from the
  fact that the call returned.
- Prefer the narrowest action: one device over a whole room, a scene the user
  already has over a hand-built set of commands.
- If a hub cannot be reached, say so as a network problem — it lives on the home
  network and a machine elsewhere will not see it.
