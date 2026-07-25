---
schema_version: "1"
name: plugin-home_assistant
description: Read and control the user's Home Assistant smart home.
when_to_use: Use when the user asks about or wants to change something physical at home — lights, heating, doors, blinds, sockets, scenes or a room sensor.
category: home
plugin_id: home_assistant
intent_verbs: [schalte, mach, dimm, stell, öffne, schließ, zeig, turn, switch, dim, set, open, close, show, enciende, apaga, abre, cierra, pon]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [home assistant, homeassistant, licht, lichter, lampe, light, lights, lamp, luz, luces, heizung, heating, calefacción, thermostat, temperatur, temperature, temperatura, rollladen, blinds, persiana, garagentor, garage door, steckdose, socket, enchufe, szene, scene, escena, smart home]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: '(home ?assistant|smart ?home|hausautomation|dom[oó]tica)'  # i18n-allow: spoken-input vocabulary
requires_tools: [home_assistant]
risk_policy:
  default_tier: ask
---

Use the connected Home Assistant to read and control the user's home.

- Find the entity before acting: list the relevant domain (light, switch, climate, cover, lock) and match the room or device the user named. A guessed `entity_id` silently does nothing.
- Acting on a home is physical and asks for confirmation. State plainly what you are about to change — which device, in which room, to what.
- Report the result from what actually changed, not from the fact that the call returned.
- Prefer the narrowest action: one entity over a whole area, a scene the user already has over a hand-built set of calls.
- If the server cannot be reached, say so as a network problem — Home Assistant lives on the home network and a machine elsewhere will not see it.
