from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from services.payment_watch.models import PaymentWatchNotification
from services.payment_watch.service import PaymentWatchService

log = logging.getLogger("payment_watch")


def build_stop_keyboard(watch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отменить ожидание",
                    callback_data=f"paywatch:stop:{watch_id}",
                ),
            ]
        ]
    )


def build_timeout_keyboard(watch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Продолжить",
                    callback_data=f"paywatch:continue:{watch_id}",
                ),
                InlineKeyboardButton(
                    text="Остановить",
                    callback_data=f"paywatch:stop:{watch_id}",
                ),
            ]
        ]
    )


class PaymentWatchPoller:
    def __init__(
        self,
        *,
        bot: Bot,
        service: PaymentWatchService,
        interval_seconds: int = 30,
    ) -> None:
        self.bot = bot
        self.service = service
        self.interval_seconds = int(interval_seconds)
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="payment_watch_poller")
        log.info("Payment watch poller started")

    async def stop(self) -> None:
        self._stopped = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Payment watch poller stopped")

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                notifications = await self.service.poll_once()
                for item in notifications:
                    await self._send(item)
            except Exception:
                log.exception("Payment watch poll iteration failed")
            await asyncio.sleep(self.interval_seconds)

    async def _send(self, item: PaymentWatchNotification) -> None:
        if item.delete_message_id:
            try:
                await self.bot.delete_message(chat_id=item.chat_id, message_id=item.delete_message_id)
            except Exception:
                log.exception("Failed to delete previous payment watch message chat_id=%s msg_id=%s", item.chat_id, item.delete_message_id)
        kwargs = {
            "chat_id": item.chat_id,
            "text": item.text,
            "parse_mode": "HTML",
        }
        if item.reply_message_id:
            kwargs["reply_to_message_id"] = item.reply_message_id
        if item.with_timeout_actions and item.watch_id is not None:
            kwargs["reply_markup"] = build_timeout_keyboard(item.watch_id)
        await self.bot.send_message(**kwargs)
