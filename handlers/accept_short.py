from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from db_asyncpg.repo import Repo
from utils.auth import (
    require_manager_or_admin_message,
    require_manager_or_admin_callback,
)
from utils.calc import evaluate, CalcError
from utils.exchange_base import AbstractExchangeHandler


def _fmt_rate(d: Decimal) -> str:
    s = f"{d.normalize():f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


class AcceptShortHandler(AbstractExchangeHandler):
    """
    /пд|/пе|/пт|/пр|/пб <recv_amount_expr> <од|ое|от|ор|об> <pay_amount_expr> [комментарий]

    Принимаем слева — СПИСЫВАЕМ у клиента; отдаём справа — ЗАЧИСЛЯЕМ клиенту.
    Если команда отправлена ответом на карточку бота — редактируем заявку.
    """
    RECV_MAP = {"пд": "USD", "пе": "EUR", "пт": "USDT", "пр": "RUB", "пб": "USDW", "прмск": "РУБМСК", "прспб": "РУБСПБ",
                "прпер": "РУБПЕР", "пп": "EUR500"}
    PAY_MAP = {"од": "USD", "ое": "EUR", "от": "USDT", "ор": "RUB", "об": "USDW", "ормск": "РУБМСК", "орспб": "РУБСПБ",
               "орпер": "РУБПЕР", "оп": "EUR500"}

    def __init__(
            self,
            repo: Repo,
            admin_chat_ids: set[int] | None = None,
            admin_user_ids: set[int] | None = None,
            request_chat_id: int | None = None,
            *,
            ignore_chat_ids: set[int] | None = None,
    ) -> None:
        super().__init__(repo, request_chat_id=request_chat_id)
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or set())
        self.router = Router()
        self._register()

    async def _cmd_accept_short(self, message: Message) -> None:
        # Игнорируем в "шумных" чатах
        if self.ignore_chat_ids and message.chat and message.chat.id in self.ignore_chat_ids:
            return

        # доступ
        if not await require_manager_or_admin_message(
                self.repo, message,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return
        RUB_CODES = {"RUB", "РУБМСК", "РУБСПБ", "РУБПЕР"}

        raw = (message.text or "")
        m = re.match(
            r"^/(пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\s+(.+?)\s+(од|ое|от|ор|об|ормск|орспб|орпер|оп)\s+(\S+)(?:\s+(.+))?$",
            raw, flags=re.IGNORECASE | re.UNICODE
        )
        if not m:
            await message.answer(
                "Формат:\n"
                "  /пд|/пе|/пт|/пр|/пб <сумма/expr> <од|ое|от|ор|об> <сумма/expr> [комментарий]\n\n"
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
        user_note = (m.group(5) or "").strip()

        recv_code = self.RECV_MAP.get(recv_key)
        pay_code = self.PAY_MAP.get(pay_key)
        if not recv_code or not pay_code:
            await message.answer("Не распознал валюты. Используйте: /пд /пе /пт /пр /пб /прмск /прспб и "
                                 "од/ое/от/ор/об/ормск/орспб.")
            return

        # Валидируем выражения
        try:
            recv_raw = evaluate(recv_amount_expr)
            pay_raw = evaluate(pay_amount_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                await message.answer("Суммы должны быть > 0")
                return
            _ = recv_raw / pay_raw  # sanity check деления
        except (CalcError, InvalidOperation, ZeroDivisionError) as e:
            await message.answer(f"Ошибка в выражениях: {e}")
            return

        # Точности (для форматирования)
        chat_id = message.chat.id
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=(message.chat.full_name or ""))
        accounts = await self.repo.snapshot_wallet(client_id)

        def _find_acc(code: str):
            return next((r for r in accounts if str(r["currency_code"]).upper() == code), None)

        acc_recv = _find_acc(recv_code)
        acc_pay = _find_acc(pay_code)
        if not acc_recv or not acc_pay:
            missing = recv_code if not acc_recv else pay_code
            await message.answer(f"Счёт {missing} не найден. Добавьте валюту: /добавь {missing} [точность]")
            return

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])

        # Квантуем и считаем курс
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_amount = recv_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amount = pay_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
        if recv_amount == 0 or pay_amount == 0:
            await message.answer("Сумма слишком мала для точности выбранных валют.")
            return

        try:
            if recv_code in RUB_CODES or pay_code in RUB_CODES:
                if recv_code in RUB_CODES:
                    rub_raw = recv_raw
                    other_raw = pay_raw
                else:
                    rub_raw = pay_raw
                    other_raw = recv_raw
                rate = rub_raw / other_raw
            else:
                rate = pay_raw / recv_raw
            if not rate.is_finite() or rate <= 0:
                await message.answer("Курс невалидный.")
                return
            rate_str = _fmt_rate(rate.quantize(Decimal("1e-8")))
        except (InvalidOperation, ZeroDivisionError):
            await message.answer("Ошибка расчёта курса.")
            return

        # Попытка редактирования (строго ответом на карточку бота)
        handled = await self.try_edit_request(
            message=message,
            recv_code=recv_code,
            pay_code=pay_code,
            recv_amount=recv_amount,
            pay_amount=pay_amount,
            recv_prec=recv_prec,
            pay_prec=pay_prec,
            rate_str=rate_str,
            user_note=(user_note or None),
            recv_is_deposit=False,  # принимаем — списываем
            pay_is_withdraw=False,  # отдаём — зачисляем
        )
        if handled:
            return

        # Создание новой заявки
        await self.process(
            message,
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,
            recv_is_deposit=False,  # принимаем — списываем
            pay_is_withdraw=False,  # отдаём — зачисляем
            note=user_note or None,
        )

    # ====== КОЛЛБЭК ОТМЕНЫ ЗАЯВКИ (делегируем в базовый класс) ======
    async def _cb_cancel(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
                self.repo, cq,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return

        # В этой команде изначально было: LEFT = withdraw, RIGHT = deposit
        await self.handle_cancel_callback(
            cq,
            recv_is_deposit=False,
            pay_is_withdraw=False,
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_accept_short, Command("пд"))
        self.router.message.register(self._cmd_accept_short, Command("пе"))
        self.router.message.register(self._cmd_accept_short, Command("пт"))
        self.router.message.register(self._cmd_accept_short, Command("пр"))
        self.router.message.register(self._cmd_accept_short, Command("пб"))
        self.router.message.register(self._cmd_accept_short, Command("пп"))
        self.router.message.register(self._cmd_accept_short, Command("прмск"))
        self.router.message.register(self._cmd_accept_short, Command("прспб"))
        self.router.message.register(self._cmd_accept_short, Command("прпер"))
        self.router.message.register(
            self._cmd_accept_short,
            F.text.regexp(r"(?iu)^/(пд|пе|пт|пр|пб|прмск|прспб|прпер|пп)(?:@\w+)?\b"),
        )
        self.router.callback_query.register(self._cb_cancel, F.data.startswith("req_cancel:"))
