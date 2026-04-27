from __future__ import annotations

import re
from typing import Iterable
from typing import cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports import ClientTransferRepositoryPort
from db_asyncpg.repo import Repo
from services.wallets import WalletInteractionService, WalletService
from utils.auth import (
    manager_or_admin_callback_required,
    manager_or_admin_message_required,
    require_manager_or_admin_message,
)
from utils.locks import chat_locks
from utils.statements import handle_stmt_callback

_RE_PUBLIC_WALLET_CMD = r"(?iu)^/кош(?:@\w+)?(?:\s|$)"


class WalletsHandler:
    def __init__(
        self,
        repo: Repo,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        *,
        ignore_chat_ids: Iterable[int] | None = None,
        city_cash_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or [])
        self.city_cash_chat_ids = set(city_cash_chat_ids or [])
        wallet_repo = cast(ClientTransferRepositoryPort, repo)
        self.wallet_service = WalletService(
            repo=wallet_repo,
            city_cash_chat_ids=city_cash_chat_ids,
        )
        self.interaction_service = WalletInteractionService(wallet_service=self.wallet_service)
        self.router = Router()
        self._register()

    @manager_or_admin_message_required
    async def _cmd_wallet(self, message: Message) -> None:
        result = await self.interaction_service.build_wallet_response(message)
        await message.answer(
            result.message_text,
            parse_mode="HTML",
            reply_markup=result.reply_markup,
        )

    @manager_or_admin_message_required
    async def _cmd_rmcur(self, message: Message) -> None:
        result = await self.interaction_service.build_remove_currency_response(message)
        await message.answer(result.message_text, reply_markup=result.reply_markup)

    @manager_or_admin_message_required
    async def _cmd_addcur(self, message: Message) -> None:
        result = await self.interaction_service.build_add_currency_response(message)
        await message.answer(result.message_text)

    async def _on_currency_change(self, message: Message) -> None:
        if message.from_user and message.bot and message.from_user.id == message.bot.id:
            return

        if message.chat and message.chat.id in self.ignore_chat_ids:
            return

        text = message.text or message.caption or ""
        reply = getattr(message, "reply_to_message", None)
        if (
            reply
            and message.bot
            and reply.from_user
            and reply.from_user.id == message.bot.id
            and re.match(_RE_PUBLIC_WALLET_CMD, text.strip())
        ):
            return

        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        async with chat_locks.for_chat(message.chat.id):
            result = await self.interaction_service.build_currency_change_response(message)
            if not result:
                return

            await message.answer(
                result.message_text,
                reply_markup=result.reply_markup,
            )

    @manager_or_admin_callback_required
    async def _cb_rmcur(self, cq: CallbackQuery) -> None:
        try:
            code_raw, answer = self.interaction_service.parse_remove_currency_callback(cq.data)
        except ValueError:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет чата", show_alert=True)
            return

        edit_text, answer_text, show_alert = await self.interaction_service.build_remove_currency_callback_response(
            message=cq.message,
            code_raw=code_raw,
            answer=answer,
        )
        await cq.message.edit_text(edit_text)
        await cq.answer(answer_text, show_alert=show_alert)

    @manager_or_admin_callback_required
    async def _cb_undo(self, cq: CallbackQuery) -> None:
        try:
            parsed_undo = self.interaction_service.parse_undo_callback(cq.data)
            if not parsed_undo:
                return
            code_raw, sign, amt_str = parsed_undo
        except ValueError:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        async with chat_locks.for_chat(cq.message.chat.id):
            result = await self.interaction_service.build_undo_response(
                message=cq.message,
                code_raw=code_raw,
                sign=sign,
                amt_str=amt_str,
            )

            try:
                old_text = cq.message.text or ""
                if result.ok:
                    await cq.message.edit_text(old_text + "\n↩️ Отменено.")
            except Exception:
                pass

            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

            await cq.message.answer(result.message_text, parse_mode="HTML")
            await cq.answer("Откат выполнен" if result.ok else result.message_text[:100], show_alert=not result.ok)

    async def _cb_statement(self, cq: CallbackQuery) -> None:
        await handle_stmt_callback(cq, self.repo)

    def _register(self) -> None:
        self.router.message.register(self._cmd_wallet, Command("кошелек"))
        self.router.message.register(self._cmd_addcur, Command("добавь"))
        self.router.message.register(self._cmd_rmcur, Command("удали"))

        self.router.message.register(
            self._on_currency_change,
            F.text.regexp(r"^/[A-Za-zА-Яа-я0-9_]+\s+"),
        )
        self.router.message.register(
            self._on_currency_change,
            F.caption.regexp(r"^/[A-Za-zА-Яа-я0-9_]+\s+"),
        )

        self.router.callback_query.register(self._cb_rmcur, F.data.startswith("rmcur:"))
        self.router.callback_query.register(self._cb_undo, F.data.startswith("undo:"))
        self.router.callback_query.register(self._cb_statement, F.data.in_({"stmt:month", "stmt:all"}))
