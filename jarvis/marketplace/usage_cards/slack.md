---
plugin_id: slack
keywords: slack, nachricht, message, nachrichten, messages, channel, kanal, kanäle, dm, direktnachricht, direct message, team, posten, post, senden, send, schreiben, write, suchen, search  # i18n-allow
---
Use the slack tool to search, read and post in the user's Slack workspace.
- Jarvis acts as the signed-in user (user-level scopes), not as a bot, and sees only conversations the user belongs to.
- `search_messages` takes Slack search syntax (`in:#channel`, `from:@name`, `after:2026-10-01`); answer with channel, author and the gist.
- Pass a channel as `#name`, an existing DM as `@person`, or an id; the tool resolves names itself and never guesses. Several matching people come back as candidates — ask which one.
- `read_history` lists newest first; `read_thread` needs the parent message's `thread_ts`.
- `post_message` needs the user's confirmation before it is sent; afterwards name the channel and repeat what was posted.
- Starting a brand-new DM is not possible; only existing direct messages can be read or posted to.
