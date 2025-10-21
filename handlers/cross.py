from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from decimal import Decimal

from utils.xe_rate import fetch_xe_rate, XERateError


class CrossRateHandler:
    def __init__(self) -> None:
        self.router = Router()
        self.router.message.register(self._cmd_cross, Command("крос"))

    async def _cmd_cross(self, message: Message) -> None:
        """
        /кросс                → EUR → USD
        /кросс EUR USD        → явные коды
        /кросс usd rub        → тоже ок
        """
        try:
            parts = (message.text or "").split()
            if len(parts) >= 3:
                base = parts[1].strip().upper()
                quote = parts[2].strip().upper()
            else:
                base, quote = "USD", "EUR"

            rate: Decimal = await fetch_xe_rate(base, quote)
            # компактный вывод
            await message.answer(f"Кросс по XE: 1 {quote} = <b>{rate}</b> {base}", parse_mode="HTML")
        except XERateError as e:
            await message.answer(f"Не удалось получить курс XE: {e}")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")