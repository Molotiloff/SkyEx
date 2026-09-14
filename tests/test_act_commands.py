from __future__ import annotations

import unittest
from decimal import Decimal

from handlers.act import parse_act_arguments
from handlers.wallets import is_request_chat_wallet_command_allowed
from services.act_counter.text_builder import ActCounterTextBuilder


class ActCommandTests(unittest.TestCase):
    def test_act_argument_variants(self) -> None:
        cases = {
            "/акт": (None, ""),
            "/акт 1000": ("USDT", "1000"),
            "/акт usd": ("USD", ""),
            "/акт дол 1000": ("USD", "1000"),
            "/акт долл 1200": ("USD", "1200"),
            "/акт usdw": ("USDW", ""),
            "/акт долб 700": ("USDW", "700"),
            "/акт доллбел 800": ("USDW", "800"),
            "/акт долбел 900": ("USDW", "900"),
            "/акт eur": ("EUR", ""),
            "/акт евро 500+20": ("EUR", "500+20"),
            "/акт@SkyEx_bot юсдт 10k": ("USDT", "10k"),
        }
        for raw_text, expected in cases.items():
            with self.subTest(raw_text=raw_text):
                self.assertEqual(parse_act_arguments(raw_text), expected)

    def test_request_chat_wallet_whitelist(self) -> None:
        for command in (
            "/usd 100",
            "/дол 100",
            "/usdw 100",
            "/долб 100",
            "/доллбел 100",
            "/долбел 100",
            "/eur 100",
            "/евро 100",
            "/usdt 1",
            "/юсдт -1",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_request_chat_wallet_command_allowed(command))

        for command in ("/rub 100", "/руб 100", "/eur500 100"):
            with self.subTest(command=command):
                self.assertFalse(is_request_chat_wallet_command_allowed(command))

    def test_multicurrency_text_uses_currency_precision(self) -> None:
        builder = ActCounterTextBuilder()
        text = builder.build_current_amounts_text(
            {
                "EUR": Decimal("2"),
                "USDT": Decimal("1"),
                "USD": Decimal("3"),
                "USDW": Decimal("4"),
            }
        )
        self.assertEqual(
            text,
            "USDT ACT: <code>1.00</code>\n"
            "USD ACT: <code>3.00</code>\n"
            "USDW ACT: <code>4.00</code>\n"
            "EUR ACT: <code>2.00</code>",
        )
        self.assertIn(
            "USD ACT стало: <code>10.00</code>",
            builder.build_reconcile_text(
                previous_amount=Decimal("5"),
                current_amount=Decimal("10"),
                delta=Decimal("5"),
                currency_code="USD",
            ),
        )


if __name__ == "__main__":
    unittest.main()
