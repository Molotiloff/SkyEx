from __future__ import annotations

from db_asyncpg.ports import ExchangeWorkflowRepositoryPort
from services.act_counter import ActCounterService
from services.act_counter.text_builder import ActCounterTextBuilder
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.text_builder import ExchangeTextBuilder


class _ExchangeUseCaseBase:
    def __init__(
        self,
        *,
        repo: ExchangeWorkflowRepositoryPort,
        request_chat_id: int | None,
        balance_service: ExchangeBalanceService,
        calculator: ExchangeCalculator,
        text_builder: ExchangeTextBuilder,
        act_counter_service: ActCounterService | None = None,
    ) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id
        self.balance_service = balance_service
        self.calculator = calculator
        self.text_builder = text_builder
        self.act_counter_service = act_counter_service
        self.act_text_builder = ActCounterTextBuilder()

    async def _get_exchange_request_meta(self, client_req_id: str) -> dict | None:
        try:
            return await self.repo.get_exchange_request_link(client_req_id=str(client_req_id))
        except Exception:
            return None

    async def _notify_act_current_amount(self, *, bot, request_chat_id: int | None) -> None:
        if not self.act_counter_service or not request_chat_id:
            return
        try:
            report = await self.act_counter_service.build_report(request_chat_id=int(request_chat_id))
            await bot.send_message(
                chat_id=int(request_chat_id),
                text=self.act_text_builder.build_current_amount_text(report),
                parse_mode="HTML",
            )
        except Exception:
            return
