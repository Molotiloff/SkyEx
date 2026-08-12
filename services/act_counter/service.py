from __future__ import annotations

from decimal import Decimal

from db_asyncpg.ports import ActCounterLedgerRepositoryPort
from services.act_counter.currencies import (
    ACT_CURRENCY_ALIASES,
    ACT_CURRENCY_PRECISIONS,
    DEFAULT_ACT_CURRENCY,
    normalize_act_currency_code,
)
from services.act_counter.models import AppliedExchangeMovement


class ActCounterService:
    DEFAULT_CURRENCY_CODE = DEFAULT_ACT_CURRENCY
    CURRENCY_PRECISIONS = ACT_CURRENCY_PRECISIONS
    CURRENCY_ALIASES = ACT_CURRENCY_ALIASES

    def __init__(self, repo: ActCounterLedgerRepositoryPort) -> None:
        self.repo = repo

    @classmethod
    def normalize_currency_code(cls, raw_code: str | None) -> str | None:
        return normalize_act_currency_code(raw_code)

    @classmethod
    def is_tracked_currency(cls, currency_code: str) -> bool:
        return str(currency_code).strip().upper() in cls.CURRENCY_PRECISIONS

    async def ensure_request_chat_accounts(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None = None,
    ) -> int:
        return await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )

    async def get_current_amount(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None = None,
        currency_code: str = DEFAULT_CURRENCY_CODE,
    ) -> Decimal:
        code = self._require_currency_code(currency_code)
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        rows = await self.repo.snapshot_wallet(client_id)
        for row in rows:
            if str(row["currency_code"]).upper() == code:
                return Decimal(str(row["balance"]))
        return Decimal("0")

    async def get_current_amounts(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None = None,
        currency_codes: set[str] | None = None,
    ) -> dict[str, Decimal]:
        requested_codes = currency_codes or set(self.CURRENCY_PRECISIONS)
        normalized_codes = {self._require_currency_code(code) for code in requested_codes}
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        rows = await self.repo.snapshot_wallet(client_id)
        balances = {
            str(row["currency_code"]).upper(): Decimal(str(row["balance"]))
            for row in rows
            if str(row["currency_code"]).upper() in normalized_codes
        }
        return {code: balances.get(code, Decimal("0")) for code in normalized_codes}

    async def set_current_amount(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None,
        amount: Decimal,
        currency_code: str = DEFAULT_CURRENCY_CODE,
        comment: str | None = None,
        idempotency_key: str | None = None,
    ) -> Decimal:
        code = self._require_currency_code(currency_code)
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        current_amount = await self.get_current_amount(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
            currency_code=code,
        )
        delta = amount - current_amount
        if delta == 0:
            return current_amount

        if delta > 0:
            await self.repo.deposit(
                client_id=client_id,
                currency_code=code,
                amount=delta,
                comment=comment or "act",
                source="act_set",
                idempotency_key=idempotency_key,
            )
        else:
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=code,
                amount=abs(delta),
                comment=comment or "act",
                source="act_set",
                idempotency_key=idempotency_key,
            )
        return amount

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
            if not self.is_tracked_currency(movement.currency_code):
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

    async def apply_request_wallet_movements(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        movements: list[AppliedExchangeMovement],
        table_req_id: str | None = None,
        chat_name: str | None = None,
    ) -> None:
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        for movement in movements:
            code = str(movement.currency_code).upper()
            if not self.is_tracked_currency(code):
                continue
            idem = f"actwallet:{request_chat_id}:{req_id}:{movement.transaction_id}"
            comment = f"ACT req {table_req_id or req_id} {movement.direction}"
            if movement.direction == "IN":
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=movement.amount,
                    comment=comment,
                    source="act_exchange",
                    idempotency_key=idem,
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=code,
                    amount=movement.amount,
                    comment=comment,
                    source="act_exchange",
                    idempotency_key=idem,
                )

    async def revert_request_wallet_movements(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        chat_name: str | None = None,
    ) -> None:
        rows = await self.repo.get_act_request_transaction(req_id=req_id)
        if not rows:
            return
        client_id = await self._ensure_request_chat_client(
            request_chat_id=request_chat_id,
            chat_name=chat_name,
        )
        for row in rows:
            if str(row.get("status") or "").upper() == "CANCELED":
                continue
            code = str(row.get("currency_code") or "").upper()
            if not self.is_tracked_currency(code):
                continue
            amount = abs(Decimal(str(row["amount"])))
            direction = str(row["direction"]).upper()
            tx_id = int(row["transaction_id"])
            idem = f"actwallet:cancel:{request_chat_id}:{req_id}:{tx_id}"
            comment = f"ACT cancel req {row.get('table_req_id') or req_id} {direction}"
            if direction == "IN":
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment=comment,
                    source="act_exchange_cancel",
                    idempotency_key=idem,
                )
            else:
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment=comment,
                    source="act_exchange_cancel",
                    idempotency_key=idem,
                )

    async def cancel_request(self, *, req_id: str) -> int:
        return await self.repo.cancel_act_request_transactions(req_id=req_id)

    async def get_request_currency_codes(self, *, req_id: str) -> set[str]:
        rows = await self.repo.get_act_request_transaction(req_id=req_id)
        return {
            code
            for row in rows
            if (code := str(row.get("currency_code") or "").upper())
            and self.is_tracked_currency(code)
        }

    async def _ensure_request_chat_client(
        self,
        *,
        request_chat_id: int,
        chat_name: str | None = None,
    ) -> int:
        client_id = await self.repo.ensure_client(
            chat_id=int(request_chat_id),
            name=(chat_name or f"ACT {request_chat_id}"),
        )
        rows = await self.repo.snapshot_wallet(client_id)
        existing_codes = {str(row["currency_code"]).upper() for row in rows}
        for code, precision in self.CURRENCY_PRECISIONS.items():
            if code not in existing_codes:
                await self.repo.add_currency(client_id, code, precision)
        return client_id

    @classmethod
    def _require_currency_code(cls, currency_code: str) -> str:
        code = cls.normalize_currency_code(currency_code)
        if code is None:
            raise ValueError(f"Валюта {currency_code!r} не поддерживается ACT")
        return code
