from __future__ import annotations

import unittest
from decimal import Decimal

from services.act_counter.models import AppliedExchangeMovement
from services.act_counter.service import ActCounterService
from services.exchange.use_case_base import _ExchangeUseCaseBase
from tests.test_act_counter import FakeActRepo


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class ActNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_notification_contains_usdt_and_participating_currency_only(self) -> None:
        repo = FakeActRepo()
        service = ActCounterService(repo)
        await service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="USDT",
            amount=Decimal("500"),
        )
        await service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="USD",
            amount=Decimal("200"),
        )
        await service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="EUR",
            amount=Decimal("300"),
        )
        await service.set_current_amount(
            request_chat_id=-100,
            chat_name="Заявки",
            currency_code="USDW",
            amount=Decimal("400"),
        )
        use_case = _ExchangeUseCaseBase(
            repo=repo,
            request_chat_id=-100,
            balance_service=object(),
            calculator=object(),
            text_builder=object(),
            act_counter_service=service,
        )
        bot = FakeBot()

        await use_case._notify_act_current_amount(
            bot=bot,
            request_chat_id=-100,
            movements=[
                AppliedExchangeMovement(1, "USD", "IN", Decimal("10")),
                AppliedExchangeMovement(2, "USDW", "OUT", Decimal("20")),
                AppliedExchangeMovement(3, "RUB", "OUT", Decimal("800")),
            ],
        )

        self.assertEqual(len(bot.messages), 1)
        self.assertEqual(
            bot.messages[0]["text"],
            "USDT ACT: <code>500.00</code>\n"
            "USD ACT: <code>200.00</code>\n"
            "USDW ACT: <code>400.00</code>",
        )
        self.assertNotIn("EUR ACT", bot.messages[0]["text"])


if __name__ == "__main__":
    unittest.main()
