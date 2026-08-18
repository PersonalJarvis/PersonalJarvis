---
plugin_id: youtube_music
keywords: youtube music, youtube, yt music, ytmusic, musik, music, música, lied, song, canción, track, titel, album, álbum, playlist, wiedergabeliste, lista de reproducción, künstler, artist, artista, band, grupo, radio, spiel, spiele, play, pon, reproduce, pausier, pause, pausa, stopp, stop, überspring, skip, salta, next, siguiente, weiter, zurück, previous, anterior, like, liken, gefällt mir, me gusta, was läuft, what is playing, qué suena  # i18n-allow: spoken-input matching vocabulary (de/en/es), not prose
---
Use the youtube_music tool to play and control the user's music on YouTube Music.

- To start something, pass what the user said as `query` and set `type`: `song` for a track, `artist` for a band (their top song starts and the radio continues with them), `album`, `playlist` for one of their own lists, `liked` for their liked songs. The tool searches and opens the hit in YouTube Music in one step; a song opens as its own radio, so music keeps coming.
- `play` without a query resumes what is paused; `pause`, `next` and `previous` steer what is already running. There is no volume action — say the system or app volume does that.
- `now_playing` answers "what song is this" — it reports track, artist and which app it comes out of. If it names another player (Spotify, a YouTube video), say so; it is not a wrong answer.
- Report the track the response names, not the words the user used. YouTube's top hit is sometimes not what they meant, and saying it aloud is how they notice.
- Playing opens YouTube Music in the browser. When the response says playback is not confirmed, tell the user to press play once — the browser blocks autoplay on a site until it has been used there. That is the browser's rule, not a broken connection.
- `like`, `add_to_playlist` and `create_playlist` change their library; without a `query` they act on the song playing right now.
- Google allows 100 searches a day for this app. When the response says they are used up, say so plainly — playing their own playlists, pausing and skipping still work.
