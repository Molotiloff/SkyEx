from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from services.rate_order.rate_order_service import RateOrderService
from utils.auth import require_manager_or_admin_message
from utils.info import get_chat_name

_RE_ORDER = re.compile(r"^/ордер(?:@\w+)?\s+([0-9]+(?:[.,][0-9]+)?)\s*$", re.IGNORECASE)
_RE_RATE = re.compile(r"^/курс(?:@\w+)?\s+([+\-]?[0-9]+(?:[.,][0-9]+)?)\s*$", re.IGNORECASE)


class RateOrderHandler:
    def __init__(
            self,
            repo: Repo,
            *,
            rate_order_service: RateOrderService,
            admin_chat_ids: Iterable[int] | None = None,
            admin_user_ids: Iterable[int] | None = None,
            orders_chat_id: int,
    ) -> None:
        self.repo = repo
        self.rate_order_service = rate_order_service
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.orders_chat_id = int(orders_chat_id)
        self.router = Router()
        self._register()

    async def _cmd_order(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
                self.repo,
                message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return

        m = _RE_ORDER.match((message.text or "").strip())
        if not m:
            await message.answer("Формат: /ордер 81.5")
            return

        try:
            requested_rate = Decimal(m.group(1).replace(",", "."))
        except InvalidOperation:
            await message.answer("Некорректный курс.")
            return

        client_chat_id = message.chat.id
        client_name = get_chat_name(message)
        created_by_user_id = message.from_user.id if message.from_user else None

        order_id = await self.rate_order_service.create_order(
            bot=message.bot,
            client_chat_id=client_chat_id,
            client_name=client_name,
            requested_rate=requested_rate,
            created_by_user_id=created_by_user_id,
        )

        await message.answer(
            f"✅ Ордер создан",
            parse_mode="HTML",
        )

    async def _cmd_rate(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
                self.repo,
                message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return

        if message.chat.id != self.orders_chat_id:
            await message.answer("Команда /курс работает только в чате ордеров.")
            return

        reply = getattr(message, "reply_to_message", None)
        if not reply:
            await message.answer("Нужно ответить командой /курс на сообщение ордера.")
            return

        m = _RE_RATE.match((message.text or "").strip())
        if not m:
            await message.answer("Формат: /курс -0.5")
            return

        try:
            order = await self.rate_order_service.activate_order_from_reply(
                bot=message.bot,
                reply_chat_id=reply.chat.id,
                reply_message_id=reply.message_id,
                commission_text=m.group(1),
                activated_by_user_id=message.from_user.id if message.from_user else None,
            )
        except ValueError as e:
            await message.answer(str(e))
            return

        if not order:
            await message.answer("Не удалось найти ордер по этому сообщению.")
            return

    def _register(self) -> None:
        self.router.message.register(self._cmd_order, Command("ордер"))
        self.router.message.register(self._cmd_rate, Command("курс"))
