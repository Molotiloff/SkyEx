from __future__ import annotations

from abc import ABC
from decimal import Decimal

from aiogram.types import CallbackQuery, Message

from db_asyncpg.ports import ExchangeWorkflowRepositoryPort
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.cancel_exchange_request import CancelExchangeRequest
from services.exchange.create_exchange_request import CreateExchangeRequest
from services.exchange.edit_exchange_request import EditExchangeRequest
from services.exchange.text_builder import ExchangeTextBuilder


class AbstractExchangeHandler(ABC):
    """
    Базовый фасад обмена.

    Конкретные handlers сохраняют прежний API, а бизнес-сценарии живут в use-case классах.
    """

    def __init__(self, repo: ExchangeWorkflowRepositoryPort, request_chat_id: int | None = None) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id
        self.balance_service = ExchangeBalanceService(repo)
        self.calculator = ExchangeCalculator()
        self.text_builder = ExchangeTextBuilder()

        common = {
            "repo": self.repo,
            "request_chat_id": self.request_chat_id,
            "balance_service": self.balance_service,
            "calculator": self.calculator,
            "text_builder": self.text_builder,
        }
        self.create_exchange_request = CreateExchangeRequest(**common)
        self.edit_exchange_request = EditExchangeRequest(**common)
        self.cancel_exchange_request = CancelExchangeRequest(**common)

    async def apply_edit_delta(
        self,
        *,
        client_id: int,
        old_request_text: str,
        recv_code_new: str,
        pay_code_new: str,
        recv_amount_new: Decimal,
        pay_amount_new: Decimal,
        recv_prec: int,
        pay_prec: int,
        chat_id: int,
        target_bot_msg_id: int,
        cmd_msg_id: int,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
    ) -> bool:
        return await self.balance_service.apply_edit_delta(
            client_id=client_id,
            old_request_text=old_request_text,
            recv_code_new=recv_code_new,
            pay_code_new=pay_code_new,
            recv_amount_new=recv_amount_new,
            pay_amount_new=pay_amount_new,
            recv_prec=recv_prec,
            pay_prec=pay_prec,
            chat_id=chat_id,
            target_bot_msg_id=target_bot_msg_id,
            cmd_msg_id=cmd_msg_id,
            recv_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
        )

    async def try_edit_request(
        self,
        *,
        message: Message,
        recv_code: str,
        pay_code: str,
        recv_amount: Decimal,
        pay_amount: Decimal,
        recv_prec: int,
        pay_prec: int,
        rate_str: str,
        user_note: str | None,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
    ) -> bool:
        return await self.edit_exchange_request.execute(
            message=message,
            recv_code=recv_code,
            pay_code=pay_code,
            recv_amount=recv_amount,
            pay_amount=pay_amount,
            recv_prec=recv_prec,
            pay_prec=pay_prec,
            rate_str=rate_str,
            user_note=user_note,
            recv_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
        )

    async def handle_cancel_callback(
        self,
        cq: CallbackQuery,
        *,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
    ) -> None:
        await self.cancel_exchange_request.execute(
            cq,
            recv_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
        )

    async def process(
        self,
        message: Message,
        recv_code: str,
        recv_amount_expr: str,
        pay_code: str,
        pay_amount_expr: str,
        *,
        recv_is_deposit: bool = True,
        pay_is_withdraw: bool = True,
        note: str | None = None,
    ) -> None:
        await self.create_exchange_request.execute(
            message,
            recv_code,
            recv_amount_expr,
            pay_code,
            pay_amount_expr,
            recv_is_deposit=recv_is_deposit,
            pay_is_withdraw=pay_is_withdraw,
            note=note,
        )
