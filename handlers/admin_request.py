# handlers/admin_request.py
from __future__ import annotations

import re
from decimal import InvalidOperation
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.calc import evaluate, CalcError
from utils.exchange_base import AbstractExchangeHandler


class AdminRequestHandler(AbstractExchangeHandler):
    """
    /заявка <от_кого> <пд|пе|пт|пр> <recv_expr> <од|ое|от|ор> <pay_expr> [комментарий]
    Выполняется ТОЛЬКО в админском чате; операции проводятся на балансе админского чата.
    """

    RECV_MAP = {"пд": "USD", "пе": "EUR", "пт": "USDT", "пр": "RUB"}
    PAY_MAP = {"од": "USD", "ое": "EUR", "от": "USDT", "ор": "RUB"}

    # принимаем варианты с/без слеша у коротких токенов
    _RX = re.compile(
        r"""(?ixu)
        ^/заявка(?:@\w+)?\s+
        (?P<who>.+?)\s+                         # от кого (лениво, до следующего токена)
        /?(?P<recv_key>пд|пе|пт|пр)\s+          # принимаем (без/со слеша)
        (?P<recv_expr>.+?)\s+                   # сумма/expr слева
        /?(?P<pay_key>од|ое|от|ор)\s+           # отдаём (без/со слеша)
        (?P<pay_expr>\S+)                       # сумма/expr справа (один токен)
        (?:\s+(?P<comment>.+))?                 # опционально: комментарий
        \s*$
        """
    )

    def __init__(
        self,
        repo: Repo,
        *,
        admin_chat_id: int,
        admin_user_ids: Iterable[int] | None = None,
    ) -> None:
        super().__init__(repo)
        self.admin_chat_id = int(admin_chat_id)
        self.admin_user_ids = set(int(x) for x in (admin_user_ids or []))
        self.router = Router()
        self._register()

    def _register(self) -> None:
        self.router.message.register(self._cmd_admin_request, Command("заявка"))
        self.router.message.register(
            self._cmd_admin_request,
            F.text.regexp(r"(?iu)^/заявка(?:@\w+)?\b"),
        )

    async def _cmd_admin_request(self, message: Message) -> None:
        # Разрешаем ТОЛЬКО в админском чате
        if message.chat.id != self.admin_chat_id:
            await message.answer("Команда доступна только в админском чате.")
            return

        raw = message.text or ""
        m = self._RX.match(raw)
        if not m:
            await message.answer(
                "Формат:\n"
                "  /заявка <от_кого> <пд|пе|пт|пр> <сумма/expr> <од|ое|от|ор> <сумма/expr> [комментарий]\n"
                "Например:\n"
                "  /заявка Клиент_Иванов пр 100000 от 100000/65 Приоритет\n"
                "  /заявка Петров /пт (700+300) /од 1000 срочно"
            )
            return

        who = (m.group("who") or "").strip()
        recv_key = m.group("recv_key").lower()
        recv_amount_expr = (m.group("recv_expr") or "").strip()
        pay_key = m.group("pay_key").lower()
        pay_amount_expr = (m.group("pay_expr") or "").strip()
        user_comment = (m.group("comment") or "").strip()

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            await message.answer("Не распознал валюты. Используйте пд/пе/пт/пр и од/ое/от/ор.")
            return

        # В комментарий добавляем «От: …»
        note = f"От: {who}"
        if user_comment:
            note = f"{note} | {user_comment}"

        # Проверяем выражения заранее, чтобы дать быстрый фидбек
        try:
            _ = evaluate(recv_amount_expr)
            _ = evaluate(pay_amount_expr)
        except (CalcError, InvalidOperation) as e:
            await message.answer(f"Ошибка в выражениях: {e}")
            return

        # Выполняем обмен на балансе АДМИНСКОГО чата
        # Принимаем — списываем; Отдаём — зачисляем
        await self.process(
            message,
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,
            recv_is_deposit=False,
            pay_is_withdraw=False,
            note=note,
        )
