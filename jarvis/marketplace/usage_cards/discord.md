---
plugin_id: discord
keywords: discord, server, channel, kanäle, channels, kanal, nachricht, nachrichten, message, messages, dm, direktnachricht, guild, guilds, member, mitglied, senden, send, schreiben, write  # i18n-allow: German keyword-matching vocabulary for plugin relevance routing
---
Use discord/* tools to read and send messages in servers the bot has been invited to.
- The bot can only see servers where it has been explicitly added and given permissions.
- Resolve server (guild) and channel names to IDs before sending; never guess an ID.
- Send messages directly (full autonomy); confirm channel name and message sent afterwards.
- Bot token auth: the bot acts as the application, not as a personal user account.
