---
schema_version: "1"
name: plugin-spotify
description: Play and control the user's music on Spotify.
when_to_use: Use when the user wants music — start a song, artist, album or playlist, pause, skip, go back, change the volume, queue something up, or ask what is playing right now.
category: media
plugin_id: spotify
intent_verbs: [spiel, spiele, abspielen, pausier, pausiere, stopp, stoppe, überspring, überspringe, skip, weiter, zurück, lauter, leiser, play, pause, resume, next, previous, queue, turn, pon, reproduce, pausa, salta, siguiente, sube, baja]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [spotify, musik, music, música, lied, song, canción, track, titel, album, álbum, playlist, wiedergabeliste, lista, künstler, artist, artista, band, radio, lautstärke, volume, volumen]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: '(spotify|musik|music|música)'  # i18n-allow: spoken-input vocabulary
requires_tools: [spotify]
risk_policy:
  default_tier: monitor
---

Use the connected Spotify to play and steer the user's music.

- Play by name, not by id: pass what the user said as `query` and set `type` to what they meant — `track` for a song, `artist` for a band, `album`, or `playlist` for "my running playlist". The tool searches and plays the top hit in one step.
- "Play" with no name resumes what is paused. Do not search for something to fill the gap.
- Say what actually started. The result carries the track and artist Spotify matched, which is often not literally what the user said — reporting it is how they catch a wrong hit immediately.
- Spotify plays through a device, not through this app. If nothing is open anywhere, say exactly that and offer to open Spotify — do not report it as an error or a broken connection. When several devices are idle, ask which one instead of picking.
- Controlling playback needs Spotify Premium. On that refusal, name it as Spotify's rule and stay useful: reading what is playing still works on a free account.
- Volume is a percentage from 0 to 100. "Quieter" is a step down from the current level, not a jump to zero — read the state first if you do not know it.
