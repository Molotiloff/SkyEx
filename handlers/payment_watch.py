from __future__ import annotations

from typing import Iterable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from services.payment_watch import PaymentWatchError, PaymentWatchService, build_stop_keyboard
from utils.auth import (
    manager_or_admin_callback_required,
    manager_or_admin_message_required,
)


class PaymentWatchHandler:
    def __init__(
        self,
        *,
        repo,
        payment_watch_service: PaymentWatchService,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.payment_watch_service = payment_watch_service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    @manager_or_admin_message_required
    async def _cmd_payment(self, message: Message) -> None:
        raw = (message.text or "").strip()
        parts = raw.split(maxsplit=1)
        test_mode = len(parts) > 1 and parts[1].strip().lower() == "тест"
        try:
            watch_id, text = await self.payment_watch_service.start_watch_from_reply(
                message=message,
                test_mode=test_mode,
            )
        except PaymentWatchError as exc:
            await message.answer(str(exc))
            return
        sent = await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=build_stop_keyboard(watch_id),
        )
        await self.payment_watch_service.set_notice_message_id(
            watch_id=watch_id,
            message_id=sent.message_id,
        )

    @manager_or_admin_callback_required
    async def _cb_continue(self, cq: CallbackQuery) -> None:
        data = cq.data or ""
        try:
            watch_id = int(data.rsplit(":", 1)[-1])
            text = await self.payment_watch_service.continue_watch(watch_id=watch_id)
        except (ValueError, PaymentWatchError) as exc:
            await cq.answer(str(exc), show_alert=True)
            return
        await cq.answer("Ожидание продлено")
        if cq.message:
            await cq.message.edit_text(text, parse_mode="HTML")

    @manager_or_admin_callback_required
    async def _cb_stop(self, cq: CallbackQuery) -> None:
        data = cq.data or ""
        try:
            watch_id = int(data.rsplit(":", 1)[-1])
            text = await self.payment_watch_service.stop_watch(watch_id=watch_id)
        except (ValueError, PaymentWatchError) as exc:
            await cq.answer(str(exc), show_alert=True)
            return
        await cq.answer("Ожидание остановлено")
        if cq.message:
            await cq.message.edit_text(text, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_payment, Command("оплата"))
        self.router.callback_query.register(self._cb_continue, F.data.startswith("paywatch:continue:"))
        self.router.callback_query.register(self._cb_stop, F.data.startswith("paywatch:stop:"))


__all__ = ["PaymentWatchHandler"]
