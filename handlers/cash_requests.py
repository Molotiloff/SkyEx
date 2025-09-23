# handlers/cash_requests.py
from __future__ import annotations
import html
import random
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from keyboards.request import issue_keyboard
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.auth import require_manager_or_admin_message

# команды → (тип, валюта)
CMD_MAP = {
    "депр": ("dep", "RUB"), "депт": ("dep", "USDT"), "депд": ("dep", "USD"), "депе": ("dep", "EUR"),
    "депб": ("dep", "USDW"),
    "выдр": ("wd", "RUB"), "выдт": ("wd", "USDT"), "выдд": ("wd", "USD"), "выде": ("wd", "EUR"), "выдб": ("wd", "USDW"),
}

# @username: буквы, цифры, подчёркивание, от 5 символов
RE_CMD = re.compile(
    r"^/(депр|депт|депд|депе|депб|выдр|выдт|выдд|выде|выдб)(?:@\w+)?\s+(.+?)\s+(@[A-Za-z0-9_]{5,})\s+(@[A-Za-z0-9_]{"
    r"5,})\s*$",
    flags=re.IGNORECASE | re.UNICODE,
)


class CashRequestsHandler:
    """
    Универсальные заявки наличных:
      /депр|депт|депд|депе <сумма/expr> <@кто_принесёт> <@кто_примет>
      /выдр|выдт|выдд|выде <сумма/expr> <@кто_принесёт> <@кто_примет>

    В чат клиента: заявка + кнопка «Выдано» (жмут менеджеры).
    В заявочный чат: заявка отправляется ТОЛЬКО после нажатия «Выдано»
    (это делает коллбэк в get_issue_router()).
    """

    def __init__(
        self,
        repo: Repo,
        *,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        request_chat_id: int | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.request_chat_id = request_chat_id
        self.router = Router()
        self._register()

    def _register(self) -> None:
        cmds = tuple(CMD_MAP.keys())
        self.router.message.register(self._cmd_cash_req, Command(*cmds))
        self.router.message.register(self._cmd_cash_req, F.text.regexp(RE_CMD.pattern))

    async def _cmd_cash_req(self, message: Message) -> None:
        # доступ: менеджер / админ / админский чат
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        raw = (message.text or "")
        m = RE_CMD.match(raw)
        if not m:
            await message.answer(
                "Форматы:\n"
                "• /депр|депт|депд|депе <сумма/expr> <@кто_принесёт> <@кто_примет>\n"
                "• /выдр|выдт|выдд|выде <сумма/expr> <@кто_принесёт> <@кто_примет>\n"
                "Напр.: /депр 150000 @vasya_courier @petya_cashier\n"
                "       /выдр (700+300) @irina_mgr @nikita_cashier"
            )
            return

        cmd = m.group(1).lower()
        amount_expr = m.group(2).strip()
        tg_from = m.group(3).strip()
        tg_to = m.group(4).strip()

        kind, code = CMD_MAP.get(cmd, (None, None))
        if not kind or not code:
            await message.answer("Не распознал команду/валюту.")
            return

        # считаем выражение
        try:
            amount_raw = evaluate(amount_expr)
            if amount_raw <= 0:
                await message.answer("Сумма должна быть > 0")
                return
        except (CalcError, InvalidOperation) as e:
            await message.answer(f"Ошибка в выражении суммы: {e}")
            return

        # квантование по точности счёта клиента (или требуем добавление)
        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            await message.answer(f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]")
            return

        prec = int(acc["precision"])
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q)

        req_id = random.randint(10_000_000, 99_999_999)
        pin_code = random.randint(100000, 999999)
        pretty_amt = format_amount_core(amount, prec)

        # заголовки отличаются
        if kind == "dep":
            lines = [
                f"Заявка: <code>{req_id}</code>",
                "-----",
                f"Депозит: <code>{pretty_amt} {code.lower()}</code>",
                f"Кто приносит: <code>{html.escape(tg_from)}</code>",
                f"Кто примет: <code>{html.escape(tg_to)}</code>",
                f"Код получения: <tg-spoiler>{pin_code}</tg-spoiler>",
            ]
        else:
            lines = [
                f"Заявка: <code>{req_id}</code>",
                "-----",
                f"Выдача: <code>{pretty_amt} {code.lower()}</code>",
                f"Кто приносит: <code>{html.escape(tg_from)}</code>",
                f"Кто примет: <code>{html.escape(tg_to)}</code>",
                f"Код выдачи: <tg-spoiler>{pin_code}</tg-spoiler>",
            ]

        text = "\n".join(lines)

        # Отправляем только в чат клиента — с кнопкой «Выдано»
        await message.answer(text, parse_mode="HTML", reply_markup=issue_keyboard())
