# === F-FRIENDS [F1] · feature/friends-section · alex-2026-04-30 ===
"""TelegramChannel: bidirectional ChannelAdapter via the Telegram Bot API.

Architecture (Phase F1):

- **Long-polling** — no public HTTPS endpoint required.
- **Token via Credential Manager** (``get_secret("telegram_bot_token", ...)``).
- **getMe validation on ``start()``**: raises ``ChannelStartError`` on InvalidToken.
- **Allowlist default**: empty ``allowed_user_ids`` = bot replies to nothing.
- **Outbound routing via InflightMap**: ``trace_id -> chat_id`` with TTL.
- **Privacy**: every outbound message passes through ``scrub_for_voice``.
- **FriendRegistry integration**: inbound reverse-lookup, optional auto-register.

OpenClaw pattern adopted: token hierarchy, ``group_policy``, ``require_mention``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.channels.base import ChannelMessage, ChannelSession

# === F-FRIENDS [F1] · feature/friends-section · alex-2026-04-30 ===
# Branch-portable import: ``output_filter`` exists in later branches
# (Phase 5+) but not yet in skills-brain-integration. Fallback is identity —
# Telegram outbound then goes out unscrubbed (acceptable for F2 scope).
try:
    from jarvis.brain.output_filter import scrub_for_voice as _scrub_impl
except ImportError:  # pragma: no cover

    class _IdentityScrubResult:
        def __init__(self, text: str) -> None:
            self.cleaned = text
            self.fallback_used = False

    def _scrub_impl(text: str, *, language: str = "de") -> Any:  # type: ignore[no-redef]
        return _IdentityScrubResult(text or "")


def scrub_for_voice(text: str, *, language: str = "de") -> Any:
    return _scrub_impl(text, language=language)

from jarvis.channels.manager import ChannelContext, ChannelStartError
from jarvis.core.bus import EventBus
from jarvis.core.config import TelegramConfig, get_secret
from jarvis.core.events import Event, ResponseGenerated
from jarvis.friends.models import Friend, FriendChannel

if TYPE_CHECKING:  # pragma: no cover
    from jarvis.friends.registry import FriendRegistry
    from jarvis.friends.schemas import StatusUpdate

log = logging.getLogger(__name__)


__all__ = ["TelegramChannel", "InflightMap"]


class InflightMap:
    """Mapping ``trace_id -> chat_id`` with TTL-based GC."""

    def __init__(self, ttl_s: float = 1800.0) -> None:
        self._ttl_ns = int(ttl_s * 1_000_000_000)
        self._map: dict[UUID, tuple[int, int]] = {}

    def set(self, trace_id: UUID, chat_id: int) -> None:
        expiry_ns = time.time_ns() + self._ttl_ns
        self._map[trace_id] = (chat_id, expiry_ns)
        self._gc()

    def get(self, trace_id: UUID) -> int | None:
        item = self._map.get(trace_id)
        if item is None:
            return None
        chat_id, expiry_ns = item
        if time.time_ns() > expiry_ns:
            self._map.pop(trace_id, None)
            return None
        return chat_id

    def _gc(self) -> None:
        now_ns = time.time_ns()
        expired = [t for t, (_, e) in self._map.items() if e < now_ns]
        for t in expired:
            self._map.pop(t, None)

    def __len__(self) -> int:
        return len(self._map)


class TelegramChannel:
    """Bidirectional Telegram bot channel."""

    name = "telegram"

    def __init__(
        self,
        bus: EventBus,
        config: TelegramConfig,
        friend_registry: "FriendRegistry | None" = None,
    ) -> None:
        self._bus = bus
        self._cfg = config
        self._friends = friend_registry
        self._app: Any = None
        self._inbox: asyncio.Queue[ChannelMessage] = asyncio.Queue()
        self._inflight = InflightMap(ttl_s=1800.0)
        self._sessions_by_chat: dict[int, ChannelSession] = {}
        self._event_handler_ref: Any = None
        self._bot_username: str = ""
        self._started = False

    @classmethod
    def from_context(cls, ctx: ChannelContext) -> "TelegramChannel":
        cfg = ctx.config.get("telegram_config")
        if not isinstance(cfg, TelegramConfig):
            cfg = TelegramConfig()
        return cls(bus=ctx.bus, config=cfg, friend_registry=ctx.friend_registry)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        if not self._cfg.enabled:
            log.info("TelegramChannel disabled (config.enabled=False) — skipping")
            return

        token = get_secret("telegram_bot_token", env_fallback="TELEGRAM_BOT_TOKEN")
        if not token:
            raise ChannelStartError(
                "Telegram-Token fehlt. Setup: 'python -m jarvis --wizard' und "
                "Token von @BotFather (https://t.me/BotFather) eintragen."
            )

        try:
            from telegram.ext import (  # noqa: WPS433
                ApplicationBuilder,
                MessageHandler,
                filters,
            )
        except ImportError as exc:
            raise ChannelStartError(
                "python-telegram-bot nicht installiert. "
                "Installiere via: pip install 'python-telegram-bot>=22,<23'"
            ) from exc

        await self._validate_token(token)

        self._app = ApplicationBuilder().token(token).build()
        self._app.add_handler(MessageHandler(filters.ALL, self._on_telegram_msg))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(
            poll_interval=self._cfg.polling_interval_s
        )

        self._event_handler_ref = self._on_bus_event
        self._bus.subscribe_all(self._event_handler_ref)

        self._started = True
        log.info("TelegramChannel started (bot=@%s)", self._bot_username)

    async def stop(self) -> None:
        if not self._started:
            return

        if self._event_handler_ref is not None:
            wildcards = getattr(self._bus, "_wildcard_subscribers", None)
            if wildcards is not None and self._event_handler_ref in wildcards:
                wildcards.remove(self._event_handler_ref)
            self._event_handler_ref = None

        if self._app is not None:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram stop raised: %s", exc)
            self._app = None

        self._started = False
        log.info("TelegramChannel stopped")

    async def _validate_token(self, token: str) -> None:
        from telegram import Bot  # noqa: WPS433
        from telegram.error import InvalidToken, TelegramError  # noqa: WPS433

        bot = Bot(token=token)
        try:
            me = await bot.get_me()
            self._bot_username = (me.username or "").lower() or "<unknown>"
        except InvalidToken as exc:
            raise ChannelStartError(
                "Telegram-Token ungueltig (InvalidToken). "
                "Pruefe Token in @BotFather oder erneuere ihn via Wizard."
            ) from exc
        except TelegramError as exc:
            raise ChannelStartError(f"Telegram getMe fehlgeschlagen: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise ChannelStartError(f"Telegram getMe fehlgeschlagen: {exc}") from exc

    # ------------------------------------------------------------------
    # Inbound
    # ------------------------------------------------------------------

    async def _on_telegram_msg(self, update: Any, _ctx: Any) -> None:
        try:
            msg_obj = getattr(update, "message", None)
            if msg_obj is None:
                return
            if not self._is_allowed(update):
                log.debug(
                    "Telegram message dropped (not allowed): chat=%s",
                    getattr(getattr(msg_obj, "chat", None), "id", "?"),
                )
                return

            text = msg_obj.text or ""
            chat_id = msg_obj.chat.id
            user = getattr(msg_obj, "from_user", None)
            user_id = user.id if user is not None else None

            friend = await self._resolve_friend(chat_id, user)
            session = self._session_for_chat(chat_id, user)

            channel_msg = ChannelMessage(
                session_id=session.session_id,
                kind="text",
                content=text,
                metadata={
                    "telegram_chat_id": chat_id,
                    "telegram_user_id": user_id,
                    "telegram_username": getattr(user, "username", None),
                    "friend_id": str(friend.id) if friend else None,
                    "friend_display_name": friend.display_name if friend else None,
                },
            )
            self._inflight.set(channel_msg.trace_id, chat_id)
            await self._inbox.put(channel_msg)
        except Exception as exc:  # noqa: BLE001
            log.exception("TelegramChannel._on_telegram_msg failed: %s", exc)

    def _is_allowed(self, update: Any) -> bool:
        msg = update.message
        if msg is None or msg.from_user is None:
            return False
        user_id = msg.from_user.id
        chat = msg.chat
        chat_type = getattr(chat, "type", "private")
        text = msg.text or ""

        if chat_type == "private":
            return user_id in self._cfg.allowed_user_ids

        if self._cfg.group_policy == "disabled":
            return False
        if (
            self._cfg.group_policy == "allowlist"
            and chat.id not in self._cfg.allowed_chat_ids
        ):
            return False

        if self._cfg.require_mention and self._bot_username:
            mention = f"@{self._bot_username}"
            if mention not in text.lower():
                return False

        return True

    async def _resolve_friend(self, chat_id: int, user: Any) -> Friend | None:
        if self._friends is None:
            return None
        existing = await self._friends.find_friend_by_channel("telegram", str(chat_id))
        if existing is not None:
            return existing
        if not self._cfg.auto_register_friends:
            return None
        display = (
            getattr(user, "full_name", None)
            or getattr(user, "username", None)
            or f"telegram:{chat_id}"
        )
        friend = Friend(display_name=display)
        await self._friends.add_friend(friend)
        await self._friends.link_channel(
            FriendChannel(
                friend_id=friend.id,
                channel="telegram",
                handle=str(chat_id),
                is_primary=True,
            )
        )
        log.info("Telegram-Friend auto-registered: %s (chat=%s)", display, chat_id)
        return friend

    def _session_for_chat(self, chat_id: int, user: Any) -> ChannelSession:
        existing = self._sessions_by_chat.get(chat_id)
        if existing is not None:
            return existing
        handle = (
            getattr(user, "username", None)
            or getattr(user, "full_name", None)
            or str(chat_id)
        )
        session = ChannelSession(
            session_id=uuid4(),
            channel_name=self.name,
            user_handle=handle,
            locale="de",
        )
        self._sessions_by_chat[chat_id] = session
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
        chat_id = self._inflight.get(trace_id)
        if chat_id is None:
            return
        text = event.text or ""
        await self._send_text(chat_id, text, language=event.language or "de")

    async def send_message(self, msg: ChannelMessage) -> None:
        chat_id_raw = msg.metadata.get("telegram_chat_id")
        if chat_id_raw is None:
            log.warning(
                "TelegramChannel.send_message ohne telegram_chat_id (session=%s); drop",
                msg.session_id,
            )
            return
        try:
            chat_id = int(chat_id_raw)
        except (TypeError, ValueError):
            log.warning("Invalider telegram_chat_id: %r — drop", chat_id_raw)
            return
        await self._send_text(chat_id, msg.content, language="de")

    async def broadcast_event(self, event: Event) -> None:
        """No-op: Telegram routing goes through InflightMap, not broadcast."""

    async def _send_text(self, chat_id: int, text: str, *, language: str) -> None:
        if self._app is None:
            log.debug("TelegramChannel send aborted: not started")
            return
        scrub = scrub_for_voice(text, language=language)
        cleaned = scrub.cleaned
        if not cleaned.strip():
            log.debug("TelegramChannel send aborted: empty after scrub")
            return
        try:
            await self._app.bot.send_message(chat_id=chat_id, text=cleaned)
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram send_message failed (chat=%s): %s", chat_id, exc)

    # === F-FRIENDS [F4] · feature/friends-section · alex-2026-05-01 ===
    async def send_status_card(
        self, chat_id: int, update: "StatusUpdate"
    ) -> None:
        """Sends a formatted status card as a Telegram Markdown message.

        Layout:

            *[Status] <event_type>*
            - <field>: <value>
            ...

        ``timestamp_ns`` is omitted (implied by the header). On error the
        failure is logged but not propagated — a blocked friend must not
        stop the bus dispatch.
        """
        if self._app is None:
            log.debug("send_status_card aborted: not started")
            return
        text_lines = [f"*[Status] {update.event_type}*"]
        for key, value in update.fields.items():
            if key == "timestamp_ns":
                continue
            text_lines.append(f"- {key}: {value}")
        text = "\n".join(text_lines)
        try:
            await self._app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown"
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Telegram send_status_card failed (chat=%s): %s", chat_id, exc
            )

    async def sessions(self) -> list[ChannelSession]:
        return list(self._sessions_by_chat.values())
