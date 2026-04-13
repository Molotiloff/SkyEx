from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from services.rate_order import GrinexOrderbookService
from utils.auth import require_manager_or_admin_message


class GrinexBookHandler:
    def __init__(
        self,
        repo: Repo,
        *,
        orderbook_service: GrinexOrderbookService,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.orderbook_service = orderbook_service
        self.admin_chat_ids = set(int(x) for x in (admin_chat_ids or []))
        self.admin_user_ids = set(int(x) for x in (admin_user_ids or []))
        self.router = Router()
        self._register()

    def _is_admin_chat(self, chat_id: int) -> bool:
        return int(chat_id) in self.admin_chat_ids

    async def _cmd_gar(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        text = self.orderbook_service.build_asks_depth_text(
            min_total_volume=Decimal("500000"),
        )
        await message.answer(text)

    async def _cmd_gar_minus(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        text = self.orderbook_service.build_first_bid_text()
        await message.answer(text)

    async def _cmd_gar_live(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        if not self._is_admin_chat(message.chat.id):
            await message.answer("Команда /гарред доступна только в админском чате.")
            return

        text = self.orderbook_service.build_live_text(
            min_total_volume=Decimal("500000"),
        )
        sent = await message.answer(text)
        await self.orderbook_service.set_live_message(
            chat_id=sent.chat.id,
            message_id=sent.message_id,
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_gar, Command("гар"))
        self.router.message.register(self._cmd_gar_minus, Command("гар-"))
        self.router.message.register(self._cmd_gar_live, Command("гарред"))