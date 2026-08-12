from __future__ import annotations

import logging
from collections.abc import Iterable

from db_asyncpg.ports import ExchangeWorkflowRepositoryPort
from services.act_counter import ActCounterService
from services.act_counter.models import AppliedExchangeMovement
from services.act_counter.text_builder import ActCounterTextBuilder
from services.exchange.balance_service import ExchangeBalanceService
from services.exchange.calculator import ExchangeCalculator
from services.exchange.text_builder import ExchangeTextBuilder

log = logging.getLogger(__name__)


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
        """Метаданные привязки заявки (опциональное обогащение) или None.

        «Не найдено» возвращается как None самим запросом (нет строки).
        Операционный сбой БД тоже деградирует в None — вызыватели корректно
        работают без меты, — но, в отличие от «не найдено», логируется с
        трейсбэком, чтобы сбой не оставался невидимым.
        """
        try:
            return await self.repo.get_exchange_request_link(client_req_id=str(client_req_id))
        except Exception:
            log.exception(
                "Failed to load exchange request meta for client_req_id=%s",
                client_req_id,
            )
            return None

    async def _notify_act_current_amount(
        self,
        *,
        bot,
        request_chat_id: int | None,
        movements: Iterable[AppliedExchangeMovement] | None = None,
        currency_codes: set[str] | None = None,
    ) -> None:
        if not self.act_counter_service or not request_chat_id:
            return
        try:
            displayed_codes = {ActCounterService.DEFAULT_CURRENCY_CODE}
            displayed_codes.update(currency_codes or set())
            displayed_codes.update(
                movement.currency_code.upper()
                for movement in movements or []
                if self.act_counter_service.is_tracked_currency(movement.currency_code)
            )
            current_amounts = await self.act_counter_service.get_current_amounts(
                request_chat_id=int(request_chat_id),
                currency_codes=displayed_codes,
            )
            await bot.send_message(
                chat_id=int(request_chat_id),
                text=self.act_text_builder.build_current_amounts_text(current_amounts),
                parse_mode="HTML",
            )
        except Exception:
            # Side-channel уведомление об остатке акта — best-effort: не должно
            # ломать основной поток, но сбой логируем (раньше молча гасился).
            log.exception(
                "Failed to notify act current amount for chat_id=%s",
                request_chat_id,
            )
