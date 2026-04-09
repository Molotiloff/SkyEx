from __future__ import annotations

import logging
from typing import Iterable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from db_asyncpg.repo import Repo
from models.wallet import WalletError
from services.wallets import WalletService
from utils.auth import (
    require_manager_or_admin_callback,
    require_manager_or_admin_message,
)
from utils.info import get_chat_name
from utils.locks import chat_locks
from utils.statements import handle_stmt_callback

log = logging.getLogger("wallets")


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
        self.wallet_service = WalletService(
            repo=repo,
            city_cash_chat_ids=city_cash_chat_ids,
        )
        self.router = Router()
        self._register()

    async def _cmd_wallet(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        text = await self.wallet_service.build_wallet_text(chat_id=chat_id, chat_name=chat_name)

        from utils.statements import statements_kb
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=statements_kb(),
        )

    async def _cmd_rmcur(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer("Использование: /удали КОД\nПримеры: /удали USD, /удали дол, /удали юсдт")
            return

        result = await self.wallet_service.build_remove_currency_confirmation(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            raw_code=parts[1],
        )
        await message.answer(result.message_text, reply_markup=result.reply_markup)

    async def _cmd_addcur(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        parts = (message.text or "").split()
        if len(parts) < 2:
            await message.answer(
                "Использование: /добавь КОД [точность]\n"
                "Примеры: /добавь USD 2, /добавь дол 2, /добавь юсдт 0, /добавь доллбел 2"
            )
            return

        precision = 2
        if len(parts) >= 3:
            try:
                precision = int(parts[2])
            except ValueError:
                await message.answer("Ошибка: точность должна быть целым числом 0..8")
                return

        result = await self.wallet_service.add_currency(
            chat_id=message.chat.id,
            chat_name=get_chat_name(message),
            raw_code=parts[1],
            precision=precision,
        )
        await message.answer(result.message_text)

    async def _on_currency_change(self, message: Message) -> None:
        if message.from_user and message.bot and message.from_user.id == message.bot.id:
            return

        if message.chat and message.chat.id in self.ignore_chat_ids:
            return

        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        async with chat_locks.for_chat(message.chat.id):
            try:
                parsed = await self.wallet_service.parse_currency_change(message)
                if not parsed:
                    return

                result = await self.wallet_service.apply_currency_change(
                    message=message,
                    parsed=parsed,
                )
                await message.answer(
                    result.message_text,
                    reply_markup=result.reply_markup,
                )
            except ValueError as e:
                await message.answer(str(e))
            except WalletError as we:
                log.exception("WalletError in _on_currency_change chat_id=%s msg_id=%s", message.chat.id, message.message_id)
                await message.answer(f"Ошибка: {we}")
            except Exception as e:
                log.exception("Exception in _on_currency_change chat_id=%s msg_id=%s", message.chat.id, message.message_id)
                await message.answer(f"Не удалось обработать операцию: {e}")

    async def _cb_rmcur(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            self.repo,
            cq,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        try:
            _, code_raw, answer = (cq.data or "").split(":")
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет чата", show_alert=True)
            return

        if answer == "no":
            await cq.message.edit_text(f"Удаление {self.wallet_service.normalize_code_alias(code_raw)} отменено.")
            await cq.answer("Отмена")
            return

        result = await self.wallet_service.remove_currency_confirmed(
            chat_id=cq.message.chat.id,
            chat_name=get_chat_name(cq.message),
            code_raw=code_raw,
        )
        await cq.message.edit_text(result.message_text)
        await cq.answer("Удалено" if result.ok else "Отклонено", show_alert=not result.ok)

    async def _cb_undo(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            self.repo,
            cq,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        try:
            kind, code_raw, sign, amt_str = (cq.data or "").split(":")
            if kind != "undo":
                return
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        async with chat_locks.for_chat(cq.message.chat.id):
            result = await self.wallet_service.undo_operation(
                chat_id=cq.message.chat.id,
                chat_name=get_chat_name(cq.message),
                message_id=cq.message.message_id,
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