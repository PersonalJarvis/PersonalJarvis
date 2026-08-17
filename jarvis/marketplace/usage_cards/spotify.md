---
plugin_id: spotify
keywords: spotify, musik, music, música, lied, song, canción, track, titel, album, álbum, playlist, wiedergabeliste, lista de reproducción, künstler, artist, artista, band, grupo, spiel, spiele, play, pon, reproduce, pausier, pause, pausa, stopp, stop, überspring, skip, salta, next, siguiente, weiter, zurück, previous, anterior, lauter, louder, sube, leiser, quieter, baja, lautstärke, volume, volumen, was läuft, what is playing, qué suena  # i18n-allow: spoken-input matching vocabulary (de/en/es), not prose
---
Use the spotify tool to play and control the user's music.

- To start something, pass what the user said as `query` and set `type`: `track` for a song, `artist` for a band, `album`, `playlist` for a named list of theirs.
- `play` without a query resumes paused playback; `pause`, `next`, `previous` and `set_volume` steer what is already running.
- `now_playing` answers "what song is this" — it reports the track, artist and which device it is coming out of.
- Report the track the response names, not the words the user used. Spotify's top hit is sometimes not what they meant, and saying it aloud is how they notice.
- Nothing open anywhere means there is no device to play on. Say so plainly and offer to open Spotify; it is not a broken connection.
- Playback control requires Spotify Premium. If Spotify refuses, name that as Spotify's own rule — reading what plays still works without it.
