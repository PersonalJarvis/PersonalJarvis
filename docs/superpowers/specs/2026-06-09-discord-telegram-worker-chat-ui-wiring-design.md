# Discord/Telegram worker-chat — Plugins-UI wiring (live, owner-locked)

- **Date:** 2026-06-09
- **Status:** Approved (Approach A), implementation in progress
- **Author:** assistant (brainstorming → writing-plans)

## Problem

A user wants to chat with the assistant — and through it, OpenClaw workers — from
Discord and Telegram. The bidirectional channel adapters that make this possible
already exist (`jarvis/channels/{discord,telegram}.py`) and are fully wired into
the brain/worker pipeline through `ChannelChatBridge` → `MessageSent` →
`BrainManager.generate` (force-spawn → OpenClaw) → `ResponseGenerated` → channel
outbound. But the feature is hard to reach and inconsistent:

1. **Telegram** is wired into the Plugins UI: pasting a bot token calls
   `on_telegram_connected()`, which stores the `telegram_bot_token` secret and sets
   `[integrations.telegram].enabled = true`. However the change only takes effect on
   the **next restart**, and the connect form cannot capture an owner allowlist.
2. **Discord** is *not* wired: connecting it in the UI only stores a marketplace
   token; it never sets `discord_bot_token` nor enables the channel. Its catalog
   entry also ships a competing `mcp-discord` MCP server that opens its own Discord
   gateway — two systems fighting over one bot token.
3. There is no way, from the UI, to lock either bot to the owner only. The channel
   default is trust-on-first-contact (`pair_on_first_dm` / `pair_on_first_private_message`),
   which is unsafe for a bot that can trigger real OpenClaw actions.

## Goal

From the Plugins UI, connecting Discord or Telegram should:

- **enable the bidirectional chat channel** (parity between the two platforms),
- **go live immediately, without a restart** (start/stop the single channel at runtime),
- **lock the bot to the owner** via a fixed numeric user ID entered at connect time
  (no trust-on-first-contact),

and disconnecting should reverse all of it (stop the live bot + disable + clear token).

## Non-goals

- No live end-to-end test in this change (deferred — see runbook below). We ship the
  feature plus automated tests; the real bot-token click-through happens later.
- No new "send an arbitrary Discord message" tool. Discord becomes a chat channel
  exactly like Telegram; the `mcp-discord` server is removed to avoid the
  double-gateway conflict (AD-3).
- No change to the brain/worker/OpenClaw path. Everything routes through the existing
  EventBus; we only turn the channel on/off.

## Approach (A — targeted single-channel live restart)

Connecting/disconnecting persists config + secret (as Telegram does today) and then
applies the change to the **running** `ChannelManager` by reloading just that one
channel — leaving the Web channel and the other bot untouched. Rejected alternatives:
full channel-stack re-bootstrap (too disruptive — drops the Web channel) and
config-only-plus-restart (rejected by the user; not live).

## Architecture decisions

- **AD-1 — Persist first, then apply live.** Connect writes the secret + the
  `[integrations.<platform>]` config (enabled, allowlist, pairing-off) through
  `config_writer` (lock + tempfile + BOM-safe), then triggers a live reload. If the
  process has no live `ChannelManager` (headless/early boot), the persisted config
  still takes effect on next start. Secret/config write failures are hard errors
  (HTTP 500 + cleanup, mirroring Telegram today); the live-apply step is best-effort
  (logged, reported as `live_applied: false`) because the config already guarantees
  correctness on the next restart.

- **AD-2 — Owner lock by fixed ID, pairing off.** Connect accepts an optional numeric
  `allowed_user_id`. When present, it is appended to `[integrations.<platform>].allowed_user_ids`
  and trust-on-first-contact is turned **off** (`pair_on_first_private_message=false`
  for Telegram, `pair_on_first_dm=false` for Discord). The numeric user ID is not a
  secret and lives in `jarvis.toml`; the bot token stays in the credential store.

- **AD-3 — One Discord integration = the chat channel.** Remove the `mcp_server` block
  from the Discord catalog entry and reword its description to match Telegram
  ("Chat with Jarvis from Discord (your bot)"). A single bot token, one gateway, no
  competition for inbound messages.

- **AD-4 — Live reload rebuilds the cached instance and the bridge consumer.** The
  `ChannelManager` caches a channel instance whose config was frozen at boot, and the
  `ChannelChatBridge` holds a consumer task bound to that instance's `messages()`
  iterator. A live reload must therefore (a) rebuild the manager context from current
  config, (b) stop + drop + re-instantiate + start the single channel, and (c) refresh
  the bridge consumer to bind to the new instance. Skipping (c) would leave inbound
  messages flowing into a dead iterator.

## Components

### 1. `jarvis/core/config_writer.py` (extend)
New comment-preserving, lock-guarded, BOM-safe setters mirroring `set_telegram_enabled`:
- `set_discord_enabled(enabled: bool)` → `[integrations.discord].enabled`
- `set_telegram_pairing(on: bool)` → `[integrations.telegram].pair_on_first_private_message`
- `set_discord_pairing(on: bool)` → `[integrations.discord].pair_on_first_dm`

Implemented on a small private helper `_set_integration_value(platform, key, value, *, path)`
that walks/creates the nested `[integrations.<platform>]` table. (`set_telegram_enabled`,
`add_*_allowed_user_id` stay as-is — no unrelated refactor.)

### 2. `jarvis/marketplace/discord_connect.py` (new — mirrors `telegram_connect.py`)
- `on_discord_connected(token: str, allowed_user_id: int | None) -> None`: `set_secret("discord_bot_token", token)` (raise on failure) → `set_discord_enabled(True)` → if `allowed_user_id`: `add_discord_allowed_user_id(id)` + `set_discord_pairing(False)`.
- `on_discord_disconnected() -> None`: `delete_secret("discord_bot_token")` → `set_discord_enabled(False)`.

### 3. `jarvis/marketplace/telegram_connect.py` (extend)
- `on_telegram_connected(token, allowed_user_id: int | None = None)`: existing behavior + if `allowed_user_id`: `add_telegram_allowed_user_id(id)` + `set_telegram_pairing(False)`. (Signature stays backward-compatible.)

### 4. `jarvis/channels/manager.py` (extend `ChannelManager`)
- `context` property (read-only accessor for the current `ChannelContext`).
- `set_context(ctx: ChannelContext)` — swap the context used for future instantiation.
- `async reload(name)` — `await stop(name)`; drop `_instances[name]` + `_start_errors[name]`; `await start(name)`. Re-instantiates from the current context, so a fresh config takes effect.

### 5. `jarvis/channels/chat_bridge.py` (extend `ChannelChatBridge`)
- `async refresh(name)` — cancel + await the existing consumer task for `name` (if any), then spawn a new `_consume(name, manager.get(name))` bound to the current instance. Idempotent; no-op-safe if the channel is unknown.

### 6. `jarvis/marketplace/channel_runtime.py` (new — the live seam)
- `async apply_channel_live(app_state, name: str) -> bool` — read `channel_manager` + `channel_chat_bridge` from `app_state`; if absent, log + return `False`. Otherwise build a fresh `ChannelContext` (reuse the manager's bus + friend_registry, refresh `config` from `load_config().integrations`), `manager.set_context(ctx)`, `await manager.reload(name)`, `await bridge.refresh(name)`, return `True`. Catches and logs failures, returns `False`.

### 7. `jarvis/ui/web/marketplace_routes.py` (wire)
- `PatConnectBody`: add `allowed_user_id: int | None = None` (validated ≥ 0).
- `connect_pat(plugin_id, body, request: Request)`: after the existing token store, branch on `plugin_id`: `telegram` → `on_telegram_connected(token, body.allowed_user_id)`; `discord` → `on_discord_connected(token, body.allowed_user_id)` (same strict 500+cleanup contract Telegram uses). Then `live = await apply_channel_live(request.app.state, plugin_id)` for both. Return includes `live_applied: live`.
- `disconnect(plugin_id, request: Request)`: existing + `discord` → `on_discord_disconnected()`; then `await apply_channel_live(request.app.state, plugin_id)` for telegram + discord (reload now-disabled channel; bridge consumer rebinds to the disabled instance's empty inbox).

### 8. `jarvis/marketplace/seed_catalog.json` (edit Discord entry — AD-3)
Remove `mcp_server`; description → "Chat with Jarvis from Discord (your bot)";
`post_install_hint_md` → mirror Telegram's ("This enables the Discord channel — message your bot to talk to Jarvis. Outbound replies pass through the voice scrubber and your allowlist.").

### 9. `jarvis/ui/web/frontend/src/views/PluginsView.tsx` (UI)
- `connectMutation` sends `{ token, allowed_user_id }`.
- `PatConnectDialog`: for an owner-lock plugin set (`telegram`, `discord`), render an extra optional numeric field "Your numeric user ID (only you can command the bot)" with a short helper line on how to find it. `onSubmit(token, allowedUserId)`.
- Plugins outside that set are unaffected (field hidden, `allowed_user_id` omitted).

## Data flow

**Connect (Discord):** UI POST `{token, allowed_user_id}` → validate token (Bot scheme, existing) → `TokenStore.save` → `on_discord_connected` (secret + enabled + allowlist + pairing-off) → `apply_channel_live(app.state,"discord")` (rebuild ctx → `manager.reload` → `bridge.refresh`) → bot connects to the gateway and starts routing inbound DMs from the owner into the brain. Outbound replies already route back via the existing `ResponseGenerated`/`trace_id` path.

**Disconnect:** UI DELETE → `TokenStore.delete` → `on_discord_disconnected` (clear secret + disable) → `apply_channel_live` (reload → channel starts in disabled mode, bot stopped; bridge rebinds to the empty inbox).

## Security

- Owner lock (AD-2) is the headline guarantee: a fixed allowlist + pairing off means
  only the owner's numeric ID can command a worker-capable bot.
- The bot token never leaves the credential store; only the non-secret numeric user ID
  is written to `jarvis.toml`.
- Group/guild policy stays `allowlist` with empty channel/chat allowlists → DM-only by
  default; servers/groups require explicit opt-in (unchanged).
- Outbound still passes through `scrub_for_voice` (unchanged).

## Error handling

- Secret/config write failure on connect → HTTP 500 + token cleanup (parity with the
  existing Telegram path).
- Live-apply failure (e.g., bad token surfaced only at gateway connect, missing
  `discord.py`) → logged, `live_applied: false` in the response; config persisted so it
  works on next restart. The connect is not rolled back for a live-apply-only failure.
- `apply_channel_live` with no live manager (headless) → returns `False`, no error.
- `manager.reload` / `bridge.refresh` are exception-safe and idempotent.

## Testing plan (automated — this change)

- `config_writer`: new setters create/patch nested table, preserve comments + BOM, idempotent.
- `discord_connect` / `telegram_connect`: connected/disconnected write the right
  secret + config; allowlist + pairing-off applied when an ID is given; secret-store
  failure raises.
- `ChannelManager.reload` + `set_context`: dropped instance re-instantiated with fresh
  config (disabled→enabled transition observable via a fake channel).
- `ChannelChatBridge.refresh`: old consumer cancelled, new one bound; a message on the
  new instance reaches the bus, a message on the old instance does not.
- `apply_channel_live`: no-op without manager; full path with fakes.
- `marketplace_routes`: connect/disconnect for discord + telegram call the right
  bridge functions and pass `allowed_user_id`; response carries `live_applied`.
- Catalog: a guard test asserting the Discord entry has no `mcp_server` and is a
  Communication channel (locks AD-3).
- Contract test still green: all registered channels satisfy `ChannelAdapter`.
- Frontend: `PatConnectDialog` shows the ID field only for telegram/discord and
  forwards it; existing PAT plugins unchanged.

## Deferred — live test runbook (Goal 1, later)

1. Discord: create an application + bot at the Developer Portal, enable **Message
   Content Intent**, invite to a server, copy the bot token.
2. Telegram: create a bot via @BotFather, copy the token, `/start` the bot.
3. Find your numeric user ID (Telegram: @userinfobot; Discord: enable Developer Mode →
   right-click yourself → Copy User ID).
4. Connect each in the Plugins UI with token + your ID; confirm it goes live without a
   restart; DM the bot "build a small thing" and confirm a worker spins up and the
   reply comes back in the chat.
