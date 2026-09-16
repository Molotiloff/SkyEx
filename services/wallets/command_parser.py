from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal

from aiogram.types import Message

from services.wallets.models import ParsedCurrencyChange
from utils.calc import CalcError, evaluate


class WalletCommandParser:
    _LEADING_IGNORED_CHARS = " \t\r\n\u200b\u200c\u200d\ufeff\u200e\u200f\u2066\u2067\u2068\u2069"
    _CURRENCY_CHANGE_RE = re.compile(r"(?iu)^/[A-Za-zА-Яа-я0-9_.]+(?:@\w+)?\s+")
    _EXPRESSION_START_CHARS = frozenset("0123456789.,+-(")
    # Условные feature-handlers могут быть отключены конфигурацией. Такие команды
    # не должны проваливаться в универсальный синтаксис `/ВАЛЮТА <выражение>`.
    _RESERVED_NON_CURRENCY_COMMANDS = frozenset({"сообщение"})
    _CURRENCY_ALIASES = {
        "usd": "USD", "дол": "USD", "долл": "USD", "доллар": "USD", "доллары": "USD",
        "usdt": "USDT", "юсдт": "USDT",
        "eur": "EUR", "евро": "EUR",
        "rub": "RUB", "руб": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "руб.": "RUB", "рубль.": "RUB",
        "usdw": "USDW", "долб": "USDW", "доллбел": "USDW", "долбел": "USDW",
        "eur500": "EUR500", "евро500": "EUR500",
    }

    def __init__(self, *, city_cash_chat_ids: Iterable[int] | None = None) -> None:
        self.city_cash_chat_ids = set(city_cash_chat_ids or [])

    @classmethod
    def normalize_code_alias(cls, raw_code: str) -> str:
        key = (raw_code or "").strip().lower()
        alias = cls._CURRENCY_ALIASES.get(key)
        if alias:
            return alias
        if key in ("руб", "рубль", "рубли", "рублей", "руб."):
            return "RUB"
        return (raw_code or "").strip().upper()

    @staticmethod
    def extract_expr_prefix(s: str) -> str:
        if not s:
            return ""
        first = s.strip().split(maxsplit=1)[0]
        return first.replace(",", ".")

    @classmethod
    def normalize_command_text(cls, text: str | None) -> str:
        return (text or "").lstrip(cls._LEADING_IGNORED_CHARS)

    @classmethod
    def looks_like_currency_change(cls, text: str | None) -> bool:
        normalized = cls.normalize_command_text(text)
        match = cls._CURRENCY_CHANGE_RE.match(normalized)
        if not match:
            return False
        command = normalized[1:].split(None, 1)[0].split("@", 1)[0].lower()
        if command in cls._RESERVED_NON_CURRENCY_COMMANDS:
            return False
        expression = normalized[match.end() :].lstrip()
        return bool(expression and expression[0] in cls._EXPRESSION_START_CHARS)

    @staticmethod
    def split_city_transfer_tail(tail: str) -> tuple[str, str]:
        s = (tail or "").strip()
        if not s:
            return "", ""
        left, sep, right = s.partition("!")
        client_name = left.strip()
        comment = right.strip() if sep else ""
        return client_name, comment

    async def parse_currency_change(self, message: Message) -> ParsedCurrencyChange | None:
        text = self.normalize_command_text(message.text or message.caption or "").strip()
        if not text.startswith("/"):
            return None

        parts = text[1:].split(None, 1)
        if len(parts) < 2:
            return None

        raw_code = parts[0].split("@", 1)[0]
        code = self.normalize_code_alias(raw_code)

        expr_full = parts[1].strip()
        expr = self.extract_expr_prefix(expr_full)
        if not expr:
            raise ValueError("Сумма не указана. Пример: /USD 250")

        first_token = expr_full.strip().split(maxsplit=1)[0]
        tail = expr_full[len(first_token):].strip()

        try:
            amount = evaluate(expr)
        except CalcError as e:
            raise ValueError(f"Ошибка в выражении суммы: {e}") from e

        if amount == 0:
            raise ValueError("Сумма должна быть ненулевой")

        chat_id = message.chat.id
        is_city_cash = chat_id in self.city_cash_chat_ids

        if is_city_cash:
            client_name_for_transfer, extra_comment = self.split_city_transfer_tail(tail)
        else:
            client_name_for_transfer, extra_comment = "", tail

        return ParsedCurrencyChange(
            code=code,
            expr=expr,
            amount=Decimal(amount),
            tail=tail,
            is_city_cash=is_city_cash,
            client_name_for_transfer=client_name_for_transfer,
            extra_comment=extra_comment,
        )
