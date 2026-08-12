from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from services.act_counter.currencies import ACT_CURRENCY_PRECISIONS
from utils.formatting import format_amount_core


class ActCounterTextBuilder:
    CURRENCY_PRECISIONS = ACT_CURRENCY_PRECISIONS
    CURRENCY_ORDER = ("USDT", "USD", "EUR")

    def format_amount(self, amount: Decimal, currency_code: str = "USDT") -> str:
        code = currency_code.upper()
        return format_amount_core(amount, self.CURRENCY_PRECISIONS[code])

    def build_report_text(self, amount: Decimal, currency_code: str = "USDT") -> str:
        code = currency_code.upper()
        return f"{code} ACT: <code>{self.format_amount(amount, code)}</code>"

    def build_current_amount_text(self, amount: Decimal, currency_code: str = "USDT") -> str:
        return self.build_report_text(amount, currency_code)

    def build_current_amounts_text(self, amounts: Mapping[str, Decimal]) -> str:
        return "\n".join(
            self.build_current_amount_text(amounts[code], code)
            for code in self.CURRENCY_ORDER
            if code in amounts
        )

    def build_reconcile_text(
        self,
        *,
        previous_amount: Decimal,
        current_amount: Decimal,
        delta: Decimal,
        currency_code: str = "USDT",
    ) -> str:
        code = currency_code.upper()
        lines = [
            f"{code} ACT было: <code>{self.format_amount(previous_amount, code)}</code>",
            f"{code} ACT стало: <code>{self.format_amount(current_amount, code)}</code>",
            f"Изменение: <code>{self.format_amount(delta, code)}</code>",
            "",
            (
                "Новая точка отсчёта сохранена: "
                f"<code>{self.format_amount(current_amount, code)}</code>"
            ),
        ]

        return "\n".join(lines)
