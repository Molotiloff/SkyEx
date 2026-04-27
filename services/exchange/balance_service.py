from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from db_asyncpg.ports import TransactionRepositoryPort
from services.exchange.card_parser import parse_get_give


@dataclass(slots=True, frozen=True)
class CancelExchangeResult:
    recv_amount: Decimal
    recv_code: str
    pay_amount: Decimal
    pay_code: str
    recv_op_sign: str
    pay_op_sign: str
    recv_precision: int
    pay_precision: int
    client_id: int


class ExchangeBalanceService:
    def __init__(self, repo: TransactionRepositoryPort) -> None:
        self.repo = repo

    async def apply_create(
        self,
        *,
        client_id: int,
        recv_code: str,
        recv_amount: Decimal,
        recv_comment: str,
        pay_code: str,
        pay_amount: Decimal,
        pay_comment: str,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
        idem_recv: str,
        idem_pay: str,
    ) -> None:
        try:
            if recv_is_deposit:
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=recv_code,
                    amount=recv_amount,
                    comment=recv_comment,
                    source="exchange",
                    idempotency_key=idem_recv,
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=recv_code,
                    amount=recv_amount,
                    comment=recv_comment,
                    source="exchange",
                    idempotency_key=idem_recv,
                )

            if pay_is_withdraw:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=pay_code,
                    amount=pay_amount,
                    comment=pay_comment,
                    source="exchange",
                    idempotency_key=idem_pay,
                )
            else:
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=pay_code,
                    amount=pay_amount,
                    comment=pay_comment,
                    source="exchange",
                    idempotency_key=idem_pay,
                )
        except Exception:
            if recv_is_deposit:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=recv_code,
                    amount=recv_amount,
                    comment="compensate",
                    source="exchange_compensate",
                    idempotency_key=f"{idem_recv}:undo",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=recv_code,
                    amount=recv_amount,
                    comment="compensate",
                    source="exchange_compensate",
                    idempotency_key=f"{idem_recv}:undo",
                )
            raise

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
        parsed = parse_get_give(old_request_text)
        if not parsed:
            return False

        (old_recv_amt_raw, old_recv_code), (old_pay_amt_raw, old_pay_code) = parsed
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        old_recv_amt = old_recv_amt_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        old_pay_amt = old_pay_amt_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
        idem_prefix = f"edit:{chat_id}:{target_bot_msg_id}:{cmd_msg_id}"

        async def apply_recv(amount: Decimal, suffix: str) -> None:
            if recv_is_deposit:
                await self.repo.deposit(
                    client_id=client_id, currency_code=recv_code_new, amount=amount,
                    comment=f"edit recv {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:recv",
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=recv_code_new, amount=amount,
                    comment=f"edit recv {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:recv",
                )

        async def apply_pay(amount: Decimal, suffix: str) -> None:
            if pay_is_withdraw:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=pay_code_new, amount=amount,
                    comment=f"edit pay {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:pay",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id, currency_code=pay_code_new, amount=amount,
                    comment=f"edit pay {suffix}", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:{suffix}:pay",
                )

        if (old_recv_code != recv_code_new) or (old_pay_code != pay_code_new):
            if recv_is_deposit:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=old_recv_code, amount=old_recv_amt,
                    comment="edit revert old recv", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:recv",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id, currency_code=old_recv_code, amount=old_recv_amt,
                    comment="edit revert old recv", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:recv",
                )
            if pay_is_withdraw:
                await self.repo.deposit(
                    client_id=client_id, currency_code=old_pay_code, amount=old_pay_amt,
                    comment="edit revert old pay", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:pay",
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=old_pay_code, amount=old_pay_amt,
                    comment="edit revert old pay", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:revert:pay",
                )
            await apply_recv(recv_amount_new, "apply")
            await apply_pay(pay_amount_new, "apply")
            return True

        d_recv = recv_amount_new - old_recv_amt
        d_pay = pay_amount_new - old_pay_amt

        if d_recv > 0:
            await apply_recv(d_recv, "delta+")
        elif d_recv < 0:
            if recv_is_deposit:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=recv_code_new, amount=(-d_recv),
                    comment="edit recv delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:recv",
                )
            else:
                await self.repo.deposit(
                    client_id=client_id, currency_code=recv_code_new, amount=(-d_recv),
                    comment="edit recv delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:recv",
                )

        if d_pay > 0:
            await apply_pay(d_pay, "delta+")
        elif d_pay < 0:
            if pay_is_withdraw:
                await self.repo.deposit(
                    client_id=client_id, currency_code=pay_code_new, amount=(-d_pay),
                    comment="edit pay delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:pay",
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id, currency_code=pay_code_new, amount=(-d_pay),
                    comment="edit pay delta-", source="exchange_edit",
                    idempotency_key=f"{idem_prefix}:delta-:pay",
                )

        return True

    async def apply_cancel(
        self,
        *,
        client_id: int,
        chat_id: int,
        message_id: int,
        req_id: str,
        recv_code: str,
        recv_amount: Decimal,
        pay_code: str,
        pay_amount: Decimal,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
    ) -> tuple[str, str]:
        idem_left = f"cancel:{chat_id}:{message_id}:recv"
        idem_right = f"cancel:{chat_id}:{message_id}:pay"

        if recv_is_deposit:
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=recv_code,
                amount=recv_amount,
                comment=f"cancel req {req_id}",
                source="exchange_cancel",
                idempotency_key=idem_left,
            )
            recv_op_sign = "-"
        else:
            await self.repo.deposit(
                client_id=client_id,
                currency_code=recv_code,
                amount=recv_amount,
                comment=f"cancel req {req_id}",
                source="exchange_cancel",
                idempotency_key=idem_left,
            )
            recv_op_sign = "+"

        if pay_is_withdraw:
            await self.repo.deposit(
                client_id=client_id,
                currency_code=pay_code,
                amount=pay_amount,
                comment=f"cancel req {req_id}",
                source="exchange_cancel",
                idempotency_key=idem_right,
            )
            pay_op_sign = "+"
        else:
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=pay_code,
                amount=pay_amount,
                comment=f"cancel req {req_id}",
                source="exchange_cancel",
                idempotency_key=idem_right,
            )
            pay_op_sign = "-"

        return recv_op_sign, pay_op_sign
