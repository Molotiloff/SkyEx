from __future__ import annotations

from collections.abc import Iterable
from decimal import InvalidOperation

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from services.act_counter import ActCounterService
from services.act_counter.text_builder import ActCounterTextBuilder
from utils.auth import manager_or_admin_message_required
from utils.calc import CalcError, evaluate
from utils.info import get_chat_name


def parse_act_arguments(raw_text: str) -> tuple[str | None, str]:
    parts = (raw_text or "").strip().split()
    if len(parts) <= 1:
        return None, ""

    currency_code = ActCounterService.normalize_currency_code(parts[1])
    if currency_code is not None:
        return currency_code, " ".join(parts[2:])
    return ActCounterService.DEFAULT_CURRENCY_CODE, " ".join(parts[1:])


class ActHandler:
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

    @manager_or_admin_message_required
    async def _cmd_act(self, message: Message) -> None:
        if self.request_chat_ids and message.chat.id not in self.request_chat_ids:
            await message.answer("Команда доступна только в чате заявок.")
            return

        currency_code, amount_expr = parse_act_arguments(message.text or "")
        chat_name = get_chat_name(message)

        if currency_code is None:
            current_amounts = await self.act_counter_service.get_current_amounts(
                request_chat_id=message.chat.id,
                chat_name=chat_name,
            )
            await message.answer(
                self.text_builder.build_current_amounts_text(current_amounts),
                parse_mode="HTML",
            )
            return

        if not amount_expr:
            current_amount = await self.act_counter_service.get_current_amount(
                request_chat_id=message.chat.id,
                chat_name=chat_name,
                currency_code=currency_code,
            )
            await message.answer(
                self.text_builder.build_report_text(current_amount, currency_code),
                parse_mode="HTML",
            )
            return

        try:
            actual_amount = evaluate(amount_expr)
        except (CalcError, InvalidOperation) as exc:
            await message.answer(f"Ошибка в выражении суммы: {exc}")
            return

        previous_amount = await self.act_counter_service.get_current_amount(
            request_chat_id=message.chat.id,
            chat_name=chat_name,
            currency_code=currency_code,
        )
        delta = actual_amount - previous_amount
        await self.act_counter_service.set_current_amount(
            request_chat_id=message.chat.id,
            chat_name=chat_name,
            amount=actual_amount,
            currency_code=currency_code,
            comment="act",
            idempotency_key=(
                f"act:set:{message.chat.id}:{message.message_id}:{currency_code}"
            ),
        )
        await message.answer(
            self.text_builder.build_reconcile_text(
                previous_amount=previous_amount,
                current_amount=actual_amount,
                delta=delta,
                currency_code=currency_code,
            ),
            parse_mode="HTML",
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_act, Command("акт"))
