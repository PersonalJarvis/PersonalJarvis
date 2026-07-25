---
plugin_id: home_assistant
keywords: home assistant, homeassistant, licht, lichter, light, lights, luz, luces, lampe, lamp, lámpara, heizung, heating, calefacción, temperatur, temperature, temperatura, thermostat, termostato, steckdose, socket, plug, enchufe, rollladen, blinds, persiana, garage, garagentor, garage door, puerta, tür, door, puerta, schloss, lock, cerradura, szene, scene, escena, sensor, schalte, switch, smart home, hausautomation, domótica  # i18n-allow: spoken-input matching vocabulary (de/en/es), not prose
---
Use the home_assistant tool to read and control the user's smart home.

- List entities (optionally filtered by domain: light, switch, climate, cover, lock, sensor) to find the right `entity_id` before acting. Never guess one.
- Read a state before reporting it; say the friendly name, not the raw entity id.
- To act, call a service: domain + service + entity_id, e.g. `light` / `turn_off`. Extra data goes in `data`, e.g. `{"brightness_pct": 30}` or `{"temperature": 21}`.
- After a service call, report what actually changed rather than saying "done" — the response lists the affected entities.
- Home Assistant runs on the user's home network. If it cannot be reached, say that the server is unreachable from this machine instead of implying the credential is wrong.
