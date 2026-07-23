"""DiscordChannel: bidirectional ChannelAdapter via a Discord bot.

Mirrors :class:`jarvis.channels.telegram.TelegramChannel`. Discord is a
*communication channel*: a message to the bot (DM or guild channel) is placed
on an inbox; the runtime :class:`jarvis.channels.chat_bridge.ChannelChatBridge`
consumes that inbox and republishes it as a normal ``MessageSent`` user event,
so chatting with the bot is identical to prompting Jarvis in the web chat.

Design:

- **discord.py gateway** — long-lived WebSocket connection (``client.connect``)
  started as a background task; no public HTTPS endpoint required.
- **Token via Credential Manager** (``get_secret("discord_bot_token", ...)``).
- **Lazy import**: ``discord`` lives in the optional ``[channels]`` extra. A
  missing library degrades to a clear English ``ChannelStartError`` (cloud-first
  doctrine: the headless base install must boot without it).
- **Message Content Intent** is requested explicitly — without it Discord sends
  empty ``content`` and the bot looks broken.
- **Allowlist default**: empty ``allowed_user_ids`` + ``guild_policy=allowlist``
  = the bot replies to nothing. ``pair_on_first_dm`` claims the empty allowlist
  for the first DM sender (mirrors Telegram first-private-message pairing).
- **Outbound routing via InflightMap**: ``trace_id -> channel_id`` with a TTL;
  ``ResponseGenerated`` carries the originating ``trace_id`` (see
  ``BrainManager.generate``), so the reply lands back in the right channel.
- **Privacy**: every outbound message passes through ``scrub_for_voice``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.channels.base import ChannelMessage, ChannelSession
from jarvis.channels.manager import ChannelContext, ChannelStartError
from jarvis.core.bus import EventBus
from jarvis.core.config import DiscordConfig, get_secret
from jarvis.core.events import Event, ResponseGenerated
from jarvis.friends.models import Friend, FriendChannel

# Branch-portable import: identity fallback when the voice output filter is not
# present (keeps Discord working unscrubbed rather than crashing on import).
try:
    from jarvis.brain.output_filter import scrub_for_voice as _scrub_impl
except ImportError:  # pragma: no cover

    class _IdentityScrubResult:
        def __init__(self, text: str) -> None:
            self.cleaned = text
            self.fallback_used = False

    def _scrub_impl(text: str, *, language: str = "de") -> Any:  # type: ignore[no-redef]
        return _IdentityScrubResult(text or "")

if TYPE_CHECKING:  # pragma: no cover
    from jarvis.friends.registry import FriendRegistry

log = logging.getLogger(__name__)


def scrub_for_voice(text: str, *, language: str = "de") -> Any:
    return _scrub_impl(text, language=language)

__all__ = ["DiscordChannel", "InflightMap"]


class InflightMap:
    """Mapping ``trace_id -> channel_id`` with TTL-based GC.

    A local copy of the Telegram map so the Discord adapter has zero import
    coupling to the (separately evolving) ``telegram`` module.
    """

    def __init__(self, ttl_s: float = 1800.0) -> None:
        self._ttl_ns = int(ttl_s * 1_000_000_000)
        self._map: dict[UUID, tuple[int, int]] = {}

    def set(self, trace_id: UUID, channel_id: int) -> None:
        expiry_ns = time.time_ns() + self._ttl_ns
        self._map[trace_id] = (channel_id, expiry_ns)
        self._gc()

    def get(self, trace_id: UUID) -> int | None:
        item = self._map.get(trace_id)
        if item is None:
            return None
        channel_id, expiry_ns = item
        if time.time_ns() > expiry_ns:
            self._map.pop(trace_id, None)
            return None
        return channel_id

    def _gc(self) -> None:
        now_ns = time.time_ns()
        expired = [t for t, (_, e) in self._map.items() if e < now_ns]
        for t in expired:
            self._map.pop(t, None)

    def __len__(self) -> int:
        return len(self._map)


class DiscordChannel:
    """Bidirectional Discord bot channel."""

    name = "discord"

    def __init__(
        self,
        bus: EventBus,
        config: DiscordConfig | None = None,
        friend_registry: FriendRegistry | None = None,
    ) -> None:
        self._bus = bus
        self._cfg = config if config is not None else DiscordConfig()
        self._friends = friend_registry
        self._client: Any = None
        self._client_task: asyncio.Task[None] | None = None
        self._inbox: asyncio.Queue[ChannelMessage] = asyncio.Queue()
        self._inflight = InflightMap(ttl_s=1800.0)
        self._sessions_by_channel: dict[int, ChannelSession] = {}
        self._event_handler_ref: Any = None
        self._bot_user_id: int = 0
        self._started = False

    @classmethod
    def from_context(cls, ctx: ChannelContext) -> DiscordChannel:
        cfg = ctx.config.get("discord_config")
        if not isinstance(cfg, DiscordConfig):
            cfg = DiscordConfig()
        return cls(bus=ctx.bus, config=cfg, friend_registry=ctx.friend_registry)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        if not self._cfg.enabled:
            self._event_handler_ref = self._on_bus_event
            self._bus.subscribe_all(self._event_handler_ref)
            self._started = True
            log.info("DiscordChannel disabled (config.enabled=False) — skipping")
            return

        token = get_secret("discord_bot_token", env_fallback="DISCORD_BOT_TOKEN")
        if not token:
            raise ChannelStartError(
                "Discord token missing. Setup: run 'python -m jarvis --wizard' and "
                "enter the bot token from https://discord.com/developers/applications "
                "(enable the Message Content Intent)."
            )

        try:
            import discord  # noqa: WPS433
        except ImportError as exc:
            raise ChannelStartError(
                "discord.py not installed. Install via: "
                "pip install 'discord.py>=2,<3'"
            ) from exc

        intents = discord.Intents.default()
        intents.message_content = True  # required to read message text

        channel = self

        class _JarvisDiscordClient(discord.Client):  # type: ignore[misc, valid-type]
            async def on_ready(self) -> None:  # noqa: D401
                channel._handle_ready(getattr(self, "user", None))

            async def on_message(self, message: Any) -> None:  # noqa: D401
                await channel._on_discord_message(message)

        self._client = _JarvisDiscordClient(intents=intents)

        # ``login`` validates the token synchronously (raises LoginFailure on a
        # bad token); ``connect`` then runs the gateway loop as a background task
        # so ``start()`` does not block. On any login failure the client owns an
        # open aiohttp session that must be closed, or it leaks (this is the exact
        # path a misconfigured user hits on every boot attempt).
        try:
            await self._client.login(token)
        except discord.LoginFailure as exc:
            await self._safe_close_client()
            raise ChannelStartError(
                "Discord token invalid (LoginFailure). Check the bot token in the "
                "Developer Portal or renew it via the wizard."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            await self._safe_close_client()
            raise ChannelStartError(f"Discord login failed: {exc}") from exc

        self._client_task = asyncio.create_task(
            self._run_client(), name="discord-gateway"
        )

        self._event_handler_ref = self._on_bus_event
        self._bus.subscribe_all(self._event_handler_ref)

        self._started = True
        log.info("DiscordChannel started")

    async def _run_client(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.connect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Discord gateway loop ended: %s", exc)

    def _handle_ready(self, user: Any) -> None:
        self._bot_user_id = int(getattr(user, "id", 0) or 0)
        log.info("DiscordChannel ready (bot_user_id=%s)", self._bot_user_id)

    async def _safe_close_client(self) -> None:
        """Close the discord client (its aiohttp session) and drop the ref.

        Safe to call after a failed ``login`` — discord.py opens an HTTP session
        during login that leaks as an 'Unclosed connector' otherwise.
        """
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("Discord client close raised: %s", exc)

    async def stop(self) -> None:
        if not self._started:
            return

        if self._event_handler_ref is not None:
            wildcards = getattr(self._bus, "_wildcard_subscribers", None)
            if wildcards is not None and self._event_handler_ref in wildcards:
                wildcards.remove(self._event_handler_ref)
            self._event_handler_ref = None

        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:  # noqa: BLE001
                log.warning("Discord close raised: %s", exc)

        if self._client_task is not None:
            self._client_task.cancel()
            try:
                await self._client_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.debug("Discord gateway task cleanup raised: %s", exc)
            self._client_task = None

        self._client = None
        self._started = False
        log.info("DiscordChannel stopped")

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    async def _on_discord_message(self, message: Any) -> None:
        try:
            author = getattr(message, "author", None)
            if author is None:
                return
            author_id = getattr(author, "id", None)
            # Ignore our own messages and other bots (no echo / loop).
            if self._bot_user_id and author_id == self._bot_user_id:
                return
            if getattr(author, "bot", False):
                return

            if not self._is_allowed(message) and not self._pair_first_dm(message):
                log.debug(
                    "Discord message dropped (not allowed): channel=%s",
                    getattr(getattr(message, "channel", None), "id", "?"),
                )
                return

            text = getattr(message, "content", "") or ""
            channel = getattr(message, "channel", None)
            channel_id = getattr(channel, "id", None)
            if channel_id is None:
                return

            friend = await self._resolve_friend(author)
            session = self._session_for_channel(channel_id, author)

            channel_msg = ChannelMessage(
                session_id=session.session_id,
                kind="text",
                content=text,
                metadata={
                    "discord_channel_id": channel_id,
                    "discord_user_id": author_id,
                    "discord_username": getattr(author, "name", None),
                    # Stable thread key: the chat-bridge groups all messages of a
                    # Discord channel into one conversation thread (discord:<id>).
                    "channel_session_id": channel_id,
                    "friend_id": str(friend.id) if friend else None,
                    "friend_display_name": friend.display_name if friend else None,
                },
            )
            self._inflight.set(channel_msg.trace_id, channel_id)
            await self._inbox.put(channel_msg)
        except Exception as exc:  # noqa: BLE001
            log.exception("DiscordChannel._on_discord_message failed: %s", exc)

    def _is_allowed(self, message: Any) -> bool:
        author = getattr(message, "author", None)
        if author is None:
            return False
        guild = getattr(message, "guild", None)

        if guild is None:  # direct message
            return getattr(author, "id", None) in self._cfg.allowed_user_ids

        if self._cfg.guild_policy == "disabled":
            return False
        channel = getattr(message, "channel", None)
        channel_id = getattr(channel, "id", None)
        if (
            self._cfg.guild_policy == "allowlist"
            and channel_id not in self._cfg.allowed_channel_ids
        ):
            return False

        if self._cfg.require_mention and self._bot_user_id:
            mention_ids = {
                getattr(m, "id", None) for m in getattr(message, "mentions", []) or []
            }
            if self._bot_user_id not in mention_ids:
                return False

        return True

    def _pair_first_dm(self, message: Any) -> bool:
        """Claim an empty DM allowlist for the first sender.

        Mirrors Telegram first-private-message pairing: the marketplace can
        validate a bot token but cannot know which Discord user should be
        allowed, so without this the "invite + DM" setup looks broken because
        every message is silently dropped by the empty allowlist.
        """
        if not self._cfg.pair_on_first_dm:
            return False
        if self._cfg.allowed_user_ids or self._cfg.allowed_channel_ids:
            return False
        if getattr(message, "guild", None) is not None:
            return False
        author = getattr(message, "author", None)
        if author is None:
            return False
        try:
            user_id = int(author.id)
        except (TypeError, ValueError):
            return False

        self._cfg.allowed_user_ids.append(user_id)
        try:
            from jarvis.core.config_writer import add_discord_allowed_user_id

            add_discord_allowed_user_id(user_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Discord first-user pairing could not be persisted "
                "(user=%s): %s",
                user_id,
                exc,
            )
        log.info("Discord first-user pairing: user_id=%s allowed", user_id)
        return True

    async def _resolve_friend(self, author: Any) -> Friend | None:
        if self._friends is None:
            return None
        user_id = getattr(author, "id", None)
        if user_id is None:
            return None
        existing = await self._friends.find_friend_by_channel(
            "discord", str(user_id)
        )
        if existing is not None:
            return existing
        if not self._cfg.auto_register_friends:
            return None
        display = getattr(author, "name", None) or f"discord:{user_id}"
        friend = Friend(display_name=display)
        await self._friends.add_friend(friend)
        await self._friends.link_channel(
            FriendChannel(
                friend_id=friend.id,
                channel="discord",
                handle=str(user_id),
                is_primary=True,
            )
        )
        log.info("Discord-Friend auto-registered: %s (user=%s)", display, user_id)
        return friend

    def _session_for_channel(self, channel_id: int, author: Any) -> ChannelSession:
        existing = self._sessions_by_channel.get(channel_id)
        if existing is not None:
            return existing
        handle = getattr(author, "name", None) or str(channel_id)
        session = ChannelSession(
            session_id=uuid4(),
            channel_name=self.name,
            user_handle=handle,
            locale="de",
        )
        self._sessions_by_channel[channel_id] = session
        return session

    async def messages(self) -> AsyncIterator[ChannelMessage]:
        while True:
            msg = await self._inbox.get()
            yield msg

    # ------------------------------------------------------------------
    # Outbound
    # ------------------------------------------------------------------

    async def _on_bus_event(self, event: Event) -> None:
        if not isinstance(event, ResponseGenerated):
            return
        trace_id = getattr(event, "trace_id", None)
        if trace_id is None:
            return
        channel_id = self._inflight.get(trace_id)
        if channel_id is None:
            return
        await self._send_text(
            channel_id, event.text or "", language=event.language or "de"
        )

    async def send_message(self, msg: ChannelMessage) -> None:
        raw = msg.metadata.get("discord_channel_id")
        if raw is None:
            log.warning(
                "DiscordChannel.send_message without discord_channel_id (session=%s); drop",
                msg.session_id,
            )
            return
        try:
            channel_id = int(raw)
        except (TypeError, ValueError):
            log.warning("Invalid discord_channel_id: %r — drop", raw)
            return
        await self._send_text(channel_id, msg.content, language="de")

    async def broadcast_event(self, event: Event) -> None:
        """No-op: Discord routing goes through InflightMap, not broadcast."""

    async def _send_text(self, channel_id: int, text: str, *, language: str) -> None:
        client = self._client
        if client is None:
            log.debug("DiscordChannel send aborted: not started")
            return
        cleaned = scrub_for_voice(text, language=language).cleaned
        if not cleaned.strip():
            log.debug("DiscordChannel send aborted: empty after scrub")
            return

        target = client.get_channel(channel_id)
        if target is None:
            fetch = getattr(client, "fetch_channel", None)
            if fetch is not None:
                try:
                    target = await fetch(channel_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Discord channel %s not found: %s", channel_id, exc)
                    return
        if target is None:
            log.warning("Discord channel %s not resolvable — drop", channel_id)
            return
        try:
            await target.send(cleaned)
        except Exception as exc:  # noqa: BLE001
            log.warning("Discord send failed (channel=%s): %s", channel_id, exc)

    async def sessions(self) -> list[ChannelSession]:
        return list(self._sessions_by_channel.values())
