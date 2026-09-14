from __future__ import annotations

import unittest
from decimal import Decimal

from services.act_counter.currencies import ACT_CURRENCY_PRECISIONS
from services.act_counter.models import AppliedExchangeMovement
from services.act_counter.service import ActCounterService


class FakeActRepo:
    def __init__(self) -> None:
        self.client_id = 1
        self.accounts: dict[str, dict] = {}
        self.transactions: dict[int, dict] = {}
        self.links: list[dict] = []
        self.idempotency: dict[str, int] = {}
        self.next_transaction_id = 1

    async def ensure_client(self, chat_id: int, name: str, client_group=None) -> int:
        return self.client_id

    async def snapshot_wallet(self, client_id: int) -> list[dict]:
        return [dict(row) for row in self.accounts.values()]

    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int:
        code = currency_code.upper()
        self.accounts.setdefault(
            code,
            {
                "id": len(self.accounts) + 1,
                "currency_code": code,
                "precision": precision,
                "balance": Decimal("0"),
            },
        )
        return int(self.accounts[code]["id"])

    async def deposit(self, **kwargs) -> int:
        return self._apply_transaction(direction="IN", **kwargs)

    async def withdraw(self, **kwargs) -> int:
        return self._apply_transaction(direction="OUT", **kwargs)

    def _apply_transaction(self, *, direction: str, **kwargs) -> int:
        idem = kwargs.get("idempotency_key")
        if idem and idem in self.idempotency:
            return self.idempotency[idem]

        code = str(kwargs["currency_code"]).upper()
        amount = Decimal(str(kwargs["amount"])).copy_abs()
        delta = amount if direction == "IN" else -amount
        self.accounts[code]["balance"] += delta
        transaction_id = self.next_transaction_id
        self.next_transaction_id += 1
        self.transactions[transaction_id] = {
            "transaction_id": transaction_id,
            "currency_code": code,
            "amount": amount,
        }
        if idem:
            self.idempotency[idem] = transaction_id
        return transaction_id

    def add_external_transaction(self, transaction_id: int, code: str, amount: str) -> None:
        self.transactions[transaction_id] = {
            "transaction_id": transaction_id,
            "currency_code": code,
            "amount": Decimal(amount),
        }

    async def link_act_request_transaction(self, **kwargs) -> int:
        transaction = self.transactions[int(kwargs["transaction_id"])]
        self.links.append({**kwargs, **transaction})
        return len(self.links)

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict]:
        return [dict(row) for row in self.links if row["req_id"] == req_id]

    async def cancel_act_request_transactions(self, *, req_id: str) -> int:
        changed = 0
        for row in self.links:
            if row["req_id"] == req_id and row.get("status") != "CANCELED":
                row["status"] = "CANCELED"
                changed += 1
        return changed


class ActCounterServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repo = FakeActRepo()
        self.service = ActCounterService(self.repo)

    async def test_ensure_accounts_and_set_each_currency_independently(self) -> None:
        await self.service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="дол",
            amount=Decimal("1250.50"),
            comment="act",
            idempotency_key="act-usd",
        )

        self.assertEqual(set(self.repo.accounts), set(ACT_CURRENCY_PRECISIONS))
        for code, precision in ACT_CURRENCY_PRECISIONS.items():
            self.assertEqual(self.repo.accounts[code]["precision"], precision)
        self.assertEqual(
            await self.service.get_current_amount(
                request_chat_id=-100,
                currency_code="USD",
            ),
            Decimal("1250.50"),
        )
        self.assertEqual(
            await self.service.get_current_amount(request_chat_id=-100),
            Decimal("0"),
        )

    async def test_apply_and_revert_tracks_only_act_currencies(self) -> None:
        movements = [
            AppliedExchangeMovement(101, "USD", "IN", Decimal("100")),
            AppliedExchangeMovement(102, "EUR", "OUT", Decimal("20")),
            AppliedExchangeMovement(103, "USDT", "IN", Decimal("5")),
            AppliedExchangeMovement(104, "USDW", "OUT", Decimal("30")),
            AppliedExchangeMovement(105, "RUB", "IN", Decimal("9000")),
        ]
        for movement in movements:
            self.repo.add_external_transaction(
                movement.transaction_id,
                movement.currency_code,
                str(movement.amount),
            )

        await self.service.register_exchange_movements(
            req_id="req-1",
            request_chat_id=-100,
            request_message_id=55,
            movements=movements,
        )
        await self.service.apply_request_wallet_movements(
            req_id="req-1",
            request_chat_id=-100,
            request_message_id=55,
            movements=movements,
        )

        self.assertEqual(self.repo.accounts["USD"]["balance"], Decimal("100"))
        self.assertEqual(self.repo.accounts["EUR"]["balance"], Decimal("-20"))
        self.assertEqual(self.repo.accounts["USDT"]["balance"], Decimal("5"))
        self.assertEqual(self.repo.accounts["USDW"]["balance"], Decimal("-30"))
        self.assertNotIn("RUB", self.repo.accounts)
        self.assertEqual(
            await self.service.get_request_currency_codes(req_id="req-1"),
            {"USDT", "USD", "USDW", "EUR"},
        )

        await self.service.revert_request_wallet_movements(
            req_id="req-1",
            request_chat_id=-100,
        )

        self.assertEqual(self.repo.accounts["USD"]["balance"], Decimal("0"))
        self.assertEqual(self.repo.accounts["EUR"]["balance"], Decimal("0"))
        self.assertEqual(self.repo.accounts["USDT"]["balance"], Decimal("0"))
        self.assertEqual(self.repo.accounts["USDW"]["balance"], Decimal("0"))

    async def test_usd_and_usdw_balances_are_independent(self) -> None:
        await self.service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="дол",
            amount=Decimal("100"),
        )
        await self.service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="долб",
            amount=Decimal("250"),
        )

        balances = await self.service.get_current_amounts(request_chat_id=-100)
        self.assertEqual(balances["USD"], Decimal("100"))
        self.assertEqual(balances["USDW"], Decimal("250"))

    async def test_repeated_apply_is_idempotent(self) -> None:
        movement = AppliedExchangeMovement(501, "USD", "IN", Decimal("10"))
        self.repo.add_external_transaction(501, "USD", "10")

        for _ in range(2):
            await self.service.apply_request_wallet_movements(
                req_id="req-idem",
                request_chat_id=-100,
                request_message_id=56,
                movements=[movement],
            )

        self.assertEqual(self.repo.accounts["USD"]["balance"], Decimal("10"))


if __name__ == "__main__":
    unittest.main()
