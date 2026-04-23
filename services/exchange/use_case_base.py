from __future__ import annotations

from db_asyncpg.repo import Repo
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.text_builder import ExchangeTextBuilder


class _ExchangeUseCaseBase:
    def __init__(
        self,
        *,
        repo: Repo,
        request_chat_id: int | None,
        balance_service: ExchangeBalanceService,
        calculator: ExchangeCalculator,
        text_builder: ExchangeTextBuilder,
    ) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id
        self.balance_service = balance_service
        self.calculator = calculator
        self.text_builder = text_builder

    async def _get_exchange_request_meta(self, client_req_id: str) -> dict | None:
        try:
            return await self.repo.get_exchange_request_link(client_req_id=str(client_req_id))
        except Exception:
            return None
