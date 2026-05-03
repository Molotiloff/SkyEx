from __future__ import annotations

from decimal import Decimal

from db_asyncpg.ports import ActCounterRepositoryPort
from services.act_counter.models import (
    ActCounterReport,
    ActMovementLine,
    AppliedExchangeMovement,
)


class ActCounterService:
    USDT_CODE = "USDT"

    def __init__(self, repo: ActCounterRepositoryPort) -> None:
        self.repo = repo

    async def set_checkpoint(
        self,
        *,
        chat_id: int,
        baseline_amount: Decimal,
        set_by_user_id: int | None = None,
        comment: str | None = None,
    ) -> int:
        return await self.repo.create_act_checkpoint(
            chat_id=chat_id,
            baseline_amount=baseline_amount,
            set_by_user_id=set_by_user_id,
            comment=comment,
        )

    async def register_exchange_movements(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        movements: list[AppliedExchangeMovement],
        table_req_id: str | None = None,
    ) -> None:
        for movement in movements:
            if movement.currency_code.upper() != self.USDT_CODE:
                continue
            await self.repo.link_act_request_transaction(
                req_id=req_id,
                table_req_id=table_req_id,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
                transaction_id=movement.transaction_id,
                direction=movement.direction,
                status="ACTIVE",
            )

    async def cancel_request(self, *, req_id: str) -> int:
        return await self.repo.cancel_act_request_transactions(req_id=req_id)

    async def build_report(self, *, request_chat_id: int) -> ActCounterReport:
        return await self._build_report(request_chat_id=request_chat_id, all_time=False)

    async def build_all_time_report(self, *, request_chat_id: int) -> ActCounterReport:
        return await self._build_report(request_chat_id=request_chat_id, all_time=True)

    async def _build_report(self, *, request_chat_id: int, all_time: bool) -> ActCounterReport:
        checkpoint = await self.repo.get_latest_act_checkpoint(chat_id=request_chat_id)
        baseline_amount = Decimal(str(checkpoint["baseline_amount"])) if checkpoint else Decimal("0")
        baseline_at = checkpoint["created_at"] if checkpoint else None
        since = None if all_time else (baseline_at if baseline_at is not None else None)

        summary = await self.repo.get_act_request_transactions_summary(
            request_chat_id=request_chat_id,
            since=since,
        )
        rows = await self.repo.list_active_act_request_transactions(
            request_chat_id=request_chat_id,
            since=since,
        )

        total_in = Decimal(str(summary.get("total_in") or 0))
        total_out = Decimal(str(summary.get("total_out") or 0))
        expected_amount = baseline_amount + total_in - total_out

        movements = [
            ActMovementLine(
                req_id=str(row["req_id"]),
                table_req_id=(str(row["table_req_id"]) if row.get("table_req_id") is not None else None),
                transaction_id=int(row["transaction_id"]),
                direction=str(row["direction"]),
                amount=abs(Decimal(str(row["amount"]))),
                txn_at=row["txn_at"],
            )
            for row in rows
        ]

        return ActCounterReport(
            baseline_amount=baseline_amount,
            baseline_at=baseline_at,
            total_in=total_in,
            total_out=total_out,
            expected_amount=expected_amount,
            movement_count=int(summary.get("movement_count") or 0),
            movements=movements,
        )
