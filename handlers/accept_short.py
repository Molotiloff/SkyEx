# handlers/accept_short.py
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.exchange_base import AbstractExchangeHandler
from utils.calc import evaluate, CalcError
from utils.auth import require_manager_or_admin_message


def _fmt_rate(d: Decimal) -> str:
    s = f"{d.normalize():f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class AcceptShortHandler(AbstractExchangeHandler):
    """
    /пд|/пе|/пт|/пр <recv_amount_expr> <од|ое|от|ор> <pay_amount_expr> [комментарий]
    Принимаем слева — списываем у клиента; отдаём справа — зачисляем клиенту.
    """
    RECV_MAP = {"пд": "USD", "пе": "EUR", "пт": "USDT", "пр": "RUB", "пб": "USDW"}
    PAY_MAP = {"од": "USD", "ое": "EUR", "от": "USDT", "ор": "RUB", "об": "USDW"}

    def __init__(
            self,
            repo: Repo,
            admin_chat_ids: Iterable[int] | None = None,
            admin_user_ids: Iterable[int] | None = None,
            request_chat_id: int | None = None,  # ← НОВОЕ
    ) -> None:
        super().__init__(repo, request_chat_id=request_chat_id)  # ← пробрасываем
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_accept_short(self, message: Message) -> None:
        # доступ: админ-чат, админ-пользователь, менеджер
        if not await require_manager_or_admin_message(
                self.repo, message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return

        raw = (message.text or "")
        # recv_amount_expr — лениво с пробелами; pay_amount_expr — один токен; далее — опц. комментарий
        m = re.match(
            r"^/(пд|пе|пт|пр|пб)(?:@\w+)?\s+(.+?)\s+(од|ое|от|ор|об)\s+(\S+)(?:\s+(.+))?$",
            raw, flags=re.IGNORECASE | re.UNICODE
        )
        if not m:
            await message.answer(
                "Формат:\n"
                "  /пд|/пе|/пт|/пр <сумма/expr> <од|ое|от|ор> <сумма/expr> [комментарий]\n\n"
                "Примеры:\n"
                "• /пд 1000 ое 1000/0.92 Клиент Петров\n"
                "• /пе (2500+500) ор 300000 «наличные»\n"
                "• /пт 700 од 700*1.08 срочно\n"
                "• /пр 100000 от 100000/94 договор №42"
            )
            return

        recv_key = m.group(1).lower()
        recv_amount_expr = m.group(2).strip()
        pay_key = m.group(3).lower()
        pay_amount_expr = m.group(4).strip()
        note = (m.group(5) or "").strip()

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            await message.answer("Не распознал валюты. Используйте: /пд /пе /пт /пр и од/ое/от/ор.")
            return

        try:
            # считаем ТОЛЬКО выражения, без комментариев
            recv_raw = evaluate(recv_amount_expr)
            pay_raw = evaluate(pay_amount_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                await message.answer("Суммы должны быть > 0")
                return
            rate_raw = recv_raw / pay_raw
            if not rate_raw.is_finite() or rate_raw <= 0:
                await message.answer("Курс невалидный (деление на ноль или неположительный).")
                return
        except (CalcError, InvalidOperation, ZeroDivisionError) as e:
            await message.answer(f"Ошибка в выражениях: {e}")
            return

        # Передаём note отдельно — AbstractExchangeHandler.process добавит его в комментарии транзакций.
        await self.process(
            message,
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,  # без комментария
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,  # без комментария
            recv_is_deposit=False,  # принимаем — списываем
            pay_is_withdraw=False,  # отдаём — зачисляем
            note=note,  # отдельный комментарий
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_accept_short, Command("пд"))
        self.router.message.register(self._cmd_accept_short, Command("пе"))
        self.router.message.register(self._cmd_accept_short, Command("пт"))
        self.router.message.register(self._cmd_accept_short, Command("пр"))
        self.router.message.register(self._cmd_accept_short, Command("пб"))
        self.router.message.register(
            self._cmd_accept_short,
            F.text.regexp(r"(?iu)^/(пд|пе|пт|пр)(?:@\w+)?\b"),
        )
