---
schema_version: "1"
name: plugin-youtube_music
description: Play and control the user's music on YouTube Music.
when_to_use: Use when the user wants music on YouTube Music — start a song, artist, album, one of their playlists or their liked songs, pause, resume, skip, go back, like a song, add a song to a playlist, or ask what is playing right now.
category: media
plugin_id: youtube_music
intent_verbs: [spiel, spiele, abspielen, pausier, pausiere, stopp, stoppe, überspring, überspringe, skip, weiter, zurück, like, liken, play, pause, resume, next, previous, add, pon, reproduce, pausa, salta, siguiente]  # i18n-allow: spoken-input vocabulary, de/en/es
intent_objects: [youtube music, youtube, yt music, musik, music, música, lied, song, canción, track, titel, album, álbum, playlist, wiedergabeliste, lista, künstler, artist, artista, band, radio, likes]  # i18n-allow: spoken-input vocabulary, de/en/es
triggers:
  - type: voice
    pattern: '(youtube ?music|yt ?music|ytmusic)'  # brand-only: generic "musik"/"music" is shared with Spotify and routes by relevance + connection state
requires_tools: [youtube_music]
risk_policy:
  default_tier: monitor
---

Use the connected YouTube Music to play and steer the user's music.

- Play by name, not by id: pass what the user said as `query` and set `type` to what they meant — `song`, `artist`, `album`, `playlist` for "my running playlist", `liked` for "my liked songs". The tool searches and opens the top hit in YouTube Music in one step; a song opens as its own radio so playback continues.
- "Play" with no name resumes what is paused. Do not search for something to fill the gap.
- Say what actually started. The result carries the track and artist YouTube matched, which is often not literally what the user said — reporting it is how they catch a wrong hit immediately.
- Music plays in a background player window (default) or in the browser, per the user's Settings — never in this app's own audio. When the result carries `needs_attention` (YouTube's one-time cookie choice, or a player that did not start), the player window has already come forward: tell the user in one sentence what to do there. It is not an error and not a broken connection.
- Pause, next, previous, volume and "what is playing" steer the background player when it runs, else the system's media session. If the result names another app (Spotify, a YouTube video), report that honestly. If the machine cannot control playback, say what the result says would fix it (playerctl on Linux, nowplaying-cli on macOS).
- Volume works in the background player (`set_volume`, 0-100). Elsewhere it cannot be changed here — say so and point to the system or app volume.
- "Open YouTube Music" / "show the player" → `open` brings the player forward (that is where the user logs in once); "hide the player" → `hide_player`.
- "Like this", "add this to my X playlist": call without a `query` to act on the song playing now; the result names the song that was actually liked or added.
- Google allows 100 searches a day for this app. When they are used up, say so plainly and offer what still works: their own playlists, pause, skip, what is playing.
