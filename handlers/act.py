from __future__ import annotations

from decimal import InvalidOperation
from typing import Iterable

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from services.act_counter import ActCounterService
from services.act_counter.text_builder import ActCounterTextBuilder
from utils.auth import manager_or_admin_callback_required, manager_or_admin_message_required
from utils.calc import CalcError, evaluate


class ActHandler:
    _CB_LAST = "act:stmt:last"
    _CB_ALL = "act:stmt:all"

    def __init__(
        self,
        *,
        repo,
        act_counter_service: ActCounterService,
        request_chat_ids: Iterable[int] | None = None,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.act_counter_service = act_counter_service
        self.request_chat_ids = set(request_chat_ids or [])
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.text_builder = ActCounterTextBuilder()
        self.router = Router()
        self._register()

    @classmethod
    def _report_kb(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Выписка с последнего /акт", callback_data=cls._CB_LAST),
                ],
                [
                    InlineKeyboardButton(text="Выписка за все время", callback_data=cls._CB_ALL),
                ],
            ]
        )

    @manager_or_admin_message_required
    async def _cmd_act(self, message: Message) -> None:
        if self.request_chat_ids and message.chat.id not in self.request_chat_ids:
            await message.answer("Команда доступна только в чате заявок.")
            return

        raw = (message.text or "").strip()
        parts = raw.split(maxsplit=1)
        amount_expr = parts[1].strip() if len(parts) > 1 else ""

        report = await self.act_counter_service.build_report(request_chat_id=message.chat.id)

        if not amount_expr:
            await message.answer(
                self.text_builder.build_report_text(report),
                parse_mode="HTML",
                reply_markup=self._report_kb(),
            )
            return

        try:
            actual_amount = evaluate(amount_expr)
        except (CalcError, InvalidOperation) as exc:
            await message.answer(f"Ошибка в выражении суммы: {exc}")
            return

        delta = actual_amount - report.expected_amount
        await self.act_counter_service.set_checkpoint(
            chat_id=message.chat.id,
            baseline_amount=actual_amount,
            set_by_user_id=(message.from_user.id if message.from_user else None),
            comment=f"/акт {amount_expr}",
        )
        await message.answer(
            self.text_builder.build_reconcile_text(
                report=report,
                actual_amount=actual_amount,
                delta=delta,
            ),
            parse_mode="HTML",
            reply_markup=self._report_kb(),
        )

    @manager_or_admin_callback_required
    async def _cb_statement(self, cq: CallbackQuery) -> None:
        if self.request_chat_ids and (not cq.message or cq.message.chat.id not in self.request_chat_ids):
            await cq.answer("Команда доступна только в чате заявок.", show_alert=True)
            return

        if not cq.message:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        if cq.data == self._CB_ALL:
            report = await self.act_counter_service.build_all_time_report(request_chat_id=cq.message.chat.id)
            text = self.text_builder.build_statement_text(report, title="Выписка за все время")
        else:
            report = await self.act_counter_service.build_report(request_chat_id=cq.message.chat.id)
            text = self.text_builder.build_statement_text(report, title="Выписка с последнего /акт")

        await cq.message.answer(text, parse_mode="HTML")
        await cq.answer()

    def _register(self) -> None:
        self.router.message.register(self._cmd_act, Command("акт"))
        self.router.callback_query.register(self._cb_statement, F.data.in_({self._CB_LAST, self._CB_ALL}))
