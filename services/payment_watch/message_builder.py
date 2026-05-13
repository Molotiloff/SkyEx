from __future__ import annotations

import html
from decimal import Decimal


def _fmt(amount: Decimal) -> str:
    q = amount.quantize(Decimal("0.001"))
    return f"{q:,.3f}".replace(",", "’")


class PaymentWatchMessageBuilder:
    def build_started(self, *, address: str, test_mode: bool) -> str:
        safe = html.escape(address)
        if test_mode:
            return (
                "⏳ Начал отслеживание оплаты.\n"
                f"Адрес: <code>{safe}</code>\n"
                "Сценарий: тестовая 1 USDT, затем основная.\n"
                "Ищу переводы только между кошельками.\n"
                "Подтверждение: 3 confirmations."
            )
        return (
            "⏳ Начал отслеживание оплаты.\n"
            f"Адрес: <code>{safe}</code>\n"
            "Жду перевод USDT TRC-20  между кошельками.\n"
            "Подтверждение: 3 confirmations."
        )

    def build_test_success(
        self,
        *,
        amount: Decimal,
        tx_hash: str,
        from_address: str,
        to_address: str,
        block_number: int | None,
    ) -> str:
        block_line = (
            f"Номер блока: <code>{block_number}</code>\n"
            if block_number is not None
            else ""
        )
        return (
            "✅ Тестовая транзакция прошла успешно.\n"
            f"Кошелёк отправителя: <code>{html.escape(from_address)}</code>\n"
            f"Кошелёк получателя: <code>{html.escape(to_address)}</code>\n"
            f"Сумма: <code>{_fmt(amount)} USDT</code>\n"
            f"{block_line}"
            f"Хэш транзакции: <code>{html.escape(tx_hash)}</code>\n"
            "Ожидаю основную транзакцию."
        )

    def build_main_success(
        self,
        *,
        amount: Decimal,
        tx_hash: str,
        from_address: str,
        to_address: str,
        block_number: int | None,
    ) -> str:
        block_line = (
            f"Номер блока: <code>{block_number}</code>\n"
            if block_number is not None
            else ""
        )
        return (
            "✅ Сделка проведена успешно.\n"
            f"Кошелёк отправителя: <code>{html.escape(from_address)}</code>\n"
            f"Кошелёк получателя: <code>{html.escape(to_address)}</code>\n"
            f"Сумма: <code>{_fmt(amount)} USDT</code>\n"
            f"{block_line}"
            f"Хэш транзакции: <code>{html.escape(tx_hash)}</code>"
        )

    def build_timeout(self) -> str:
        return "⌛ Оплата не произведена за 3 часа. Продолжить ожидание?"

    def build_continued(self) -> str:
        return "⏳ Ожидание оплаты продлено ещё на 3 часа."

    def build_stopped(self) -> str:
        return "⛔ Ожидание оплаты остановлено."
