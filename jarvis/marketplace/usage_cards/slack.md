---
plugin_id: slack
keywords: slack, nachricht, message, nachrichten, messages, channel, kanal, kanäle, dm, direktnachricht, direct message, team, posten, post, senden, send, schreiben, write, suchen, search  # i18n-allow
---
Use slack/* tools to read channels, search messages, and send messages or DMs.
- Jarvis acts as the authenticated user (user-level scopes), not as a bot.
- Resolve channel names to IDs before posting; never guess an ID.
- Send messages directly (full autonomy); confirm channel name and message sent afterwards.
- Search is available across public and private channels the user is a member of.
