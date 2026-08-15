---
plugin_id: home_assistant
keywords: home assistant, homeassistant, licht, lichter, light, lights, luz, luces, lampe, lamp, lámpara, heizung, heating, calefacción, temperatur, temperature, temperatura, thermostat, termostato, steckdose, socket, plug, enchufe, rollladen, blinds, persiana, garage, garagentor, garage door, puerta, tür, door, puerta, schloss, lock, cerradura, szene, scene, escena, sensor, schalte, switch, smart home, hausautomation, domótica  # i18n-allow: spoken-input matching vocabulary (de/en/es), not prose
---
Use the `smart_home` tool to read and control the user's smart home. It covers
every connected platform at once, so there is nothing Home-Assistant-specific to
do here.

- Name devices the way the USER names them ("kitchen lamp", "living room
  blinds"). The tool resolves names itself; never invent a technical entity id.
- If a name matches several devices the tool answers with the candidates. Ask
  which one — do not pick.
- `list_devices` (optionally filtered by `room` or `kind`) is how you find out
  what exists. Each device reports what it `can` do; only send a command from
  that list.
- Acting on a home is physical. Say plainly what you are about to change —
  which device, in which room, to what.
- `unlock` and `open` are refused unless `confirm` is set. Ask the user out
  loud first; never set `confirm` on your own initiative.
- Report the result from the `changed` devices the tool returns, not from the
  fact that the call succeeded.
- A hub runs on the user's home network. If it cannot be reached, say the
  server is unreachable from this machine instead of implying the credential is
  wrong.
