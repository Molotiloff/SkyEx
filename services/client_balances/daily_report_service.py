from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from aiogram import Bot

from services.client_balances.filter_service import ClientBalancesFilterService
from services.client_balances.query_service import ClientBalancesQueryService
from services.client_balances.report_builder import ClientBalancesReportBuilder

SCHEDULED_RUB_DEBT_THRESHOLD = Decimal("-1000")
SCHEDULED_USDT_DEBT_THRESHOLD = Decimal("-1")
EXCLUDED_SCHEDULED_GROUP = "Балансы"


class DailyBalancesReportService:
    def __init__(
        self,
        *,
        query_service: ClientBalancesQueryService,
        filter_service: ClientBalancesFilterService,
        report_builder: ClientBalancesReportBuilder,
        admin_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.query_service = query_service
        self.filter_service = filter_service
        self.report_builder = report_builder
        self.admin_chat_ids = set(admin_chat_ids or [])

    async def build_report(
        self,
        *,
        code_filter: str | None = None,
        sign_filter: str | None = None,
        min_negative_balance: Decimal | None = None,
        min_positive_balance: Decimal | None = None,
        excluded_client_group: str | None = None,
    ) -> list[str]:
        rows = await self.query_service.balances_by_client()

        if code_filter and sign_filter:
            normalized_code, normalized_sign, filtered = self.filter_service.filter_by_code_and_sign(
                rows,
                code_filter=code_filter,
                sign_filter=sign_filter,
                min_negative_balance=min_negative_balance,
                min_positive_balance=min_positive_balance,
                excluded_client_group=excluded_client_group,
            )
            return self.report_builder.build_signed_report(
                code_filter=normalized_code,
                sign_filter=normalized_sign,
                rows=filtered,
                min_negative_balance=min_negative_balance,
                min_positive_balance=min_positive_balance,
            )

        if code_filter:
            normalized_code, filtered = self.filter_service.filter_by_code(
                rows,
                code_filter=code_filter,
            )
            return self.report_builder.build_code_report(
                code_filter=normalized_code,
                rows=filtered,
                near_zero_threshold=self.filter_service.near_zero_threshold,
            )

        grouped = self.filter_service.group_nonzero_by_client(rows)
        return self.report_builder.build_full_report(grouped)

    async def send_scheduled_negative_balances(
        self,
        bot: Bot,
        *,
        currencies: Iterable[str] = ("RUB", "USDT"),
    ) -> None:
        _ = currencies
        scheduled_sections = [
            {
                "code": "RUB",
                "sign": "-",
                "min_negative_balance": SCHEDULED_RUB_DEBT_THRESHOLD,
                "min_positive_balance": None,
            },
            {
                "code": "USDT",
                "sign": "-",
                "min_negative_balance": SCHEDULED_USDT_DEBT_THRESHOLD,
                "min_positive_balance": None,
            },
        ]

        for chat_id in self.admin_chat_ids:
            await bot.send_message(
                chat_id=chat_id,
                text="📊 <b>Ежедневный отчёт по балансам</b>",
                parse_mode="HTML",
            )

            has_any = False
            for section in scheduled_sections:
                chunks = await self.build_report(
                    code_filter=section["code"],
                    sign_filter=section["sign"],
                    min_negative_balance=section["min_negative_balance"],
                    min_positive_balance=section["min_positive_balance"],
                    excluded_client_group=EXCLUDED_SCHEDULED_GROUP,
                )
                if not chunks:
                    continue
                if len(chunks) == 1 and chunks[0].strip().lower().startswith("нет клиентов"):
                    continue

                has_any = True
                for chunk in chunks:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=chunk,
                        parse_mode="HTML",
                    )

            if not has_any:
                await bot.send_message(
                    chat_id=chat_id,
                    text="Подходящих балансов по выбранным условиям нет.",
                    parse_mode="HTML",
                )
