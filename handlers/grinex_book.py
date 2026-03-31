from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from services.rate_order.grinex_orderbook_service import GrinexOrderbookService
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
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

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
        await message.answer(text, parse_mode="HTML")

    async def _cmd_gar_minus(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        text = self.orderbook_service.build_first_bid_text()
        await message.answer(text, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_gar, Command("гар"))
        self.router.message.register(self._cmd_gar_minus, Command("гар-"))