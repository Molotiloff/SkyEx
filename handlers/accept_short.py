from __future__ import annotations

from typing import Iterable
from typing import cast

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from db_asyncpg.ports import ExchangeWorkflowRepositoryPort
from db_asyncpg.repo import Repo
from services.exchange import AcceptShortService
from utils.auth import (
    require_manager_or_admin_message,
    require_manager_or_admin_callback,
)


class AcceptShortHandler:
    """
    /пд|/пе|/пт|/пр|/пб <recv_amount_expr> <од|ое|от|ор|об> <pay_amount_expr> [комментарий]

    Принимаем слева — СПИСЫВАЕМ у клиента; отдаём справа — ЗАЧИСЛЯЕМ клиенту.
    Если команда отправлена ответом на карточку бота — редактируем заявку.
    """
    def __init__(
            self,
            repo: Repo,
            admin_chat_ids: Iterable[int] | None = None,
            admin_user_ids: Iterable[int] | None = None,
            request_chat_id: int | None = None,
            *,
            ignore_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.service = AcceptShortService(
            cast(ExchangeWorkflowRepositoryPort, repo),
            request_chat_id=request_chat_id,
        )
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or set())
        self.router = Router()
        self._register()

    async def _cmd_accept_short(self, message: Message) -> None:
        # Игнорируем в "шумных" чатах
        if self.ignore_chat_ids and message.chat and message.chat.id in self.ignore_chat_ids:
            return

        # доступ
        if not await require_manager_or_admin_message(
                self.repo, message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return
        try:
            await self.service.handle_command(message)
        except ValueError as exc:
            await message.answer(str(exc))

    # ====== КОЛЛБЭК ОТМЕНЫ ЗАЯВКИ (делегируем в базовый класс) ======
    async def _cb_cancel(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
                self.repo, cq,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return
        await self.service.handle_cancel(cq)

    def _register(self) -> None:
        self.router.message.register(self._cmd_accept_short, Command("пд"))
        self.router.message.register(self._cmd_accept_short, Command("пе"))
        self.router.message.register(self._cmd_accept_short, Command("пт"))
        self.router.message.register(self._cmd_accept_short, Command("пр"))
        self.router.message.register(self._cmd_accept_short, Command("пб"))
        self.router.message.register(self._cmd_accept_short, Command("пп"))
        self.router.message.register(self._cmd_accept_short, Command("прмск"))
        self.router.message.register(self._cmd_accept_short, Command("прспб"))
        self.router.message.register(self._cmd_accept_short, Command("прпер"))
        self.router.message.register(
            self._cmd_accept_short,
            F.text.regexp(r"(?iu)^/(пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\b"),
        )
        self.router.callback_query.register(self._cb_cancel, F.data.startswith("req_cancel:"))
