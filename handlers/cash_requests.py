# handlers/cash_requests.py
from __future__ import annotations

import html
import random
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Tuple, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from db_asyncpg.repo import Repo
from keyboards.request import CB_ISSUE_DONE
from utils.auth import require_manager_or_admin_message
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core
from utils.info import get_chat_name

# команды → (тип, валюта)
CMD_MAP = {
    "депр": ("dep", "RUB"), "депт": ("dep", "USDT"), "депд": ("dep", "USD"),
    "депе": ("dep", "EUR"), "депб": ("dep", "USDW"),
    "выдр": ("wd", "RUB"), "выдт": ("wd", "USDT"), "выдд": ("wd", "USD"),
    "выде": ("wd", "EUR"), "выдб": ("wd", "USDW"),
}

# Участник: @telegram или +телефон (6–15 цифр)
PART = r"(?:@[A-Za-z0-9_]{5,}|\+\d{6,15})"

# Формат:
# /депр|... <amount_expr> <who_from(@|+)> [who_to(@|+)] [! comment]
RE_CMD = re.compile(
    rf"""^/(депр|депт|депд|депе|депб|выдр|выдт|выдд|выде|выдб)(?:@\w+)?   # команда
         \s+(.+?)                                                         # сумма/expr (лениво)
         \s+({PART})                                                      # кто приносит
         (?:\s+({PART}))?                                                 # [кто примет]
         (?:\s*!\s*(.+))?                                                 # [! комментарий]
         \s*$""",
    flags=re.IGNORECASE | re.UNICODE | re.VERBOSE,
)

# "Сумма: <code>150 000 rub</code>"
_RE_LINE_AMOUNT = re.compile(r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M)

# legacy-маркеры
_RE_KIND_DEP_LEGACY = re.compile(r"Код\s+получения", re.IGNORECASE)
_RE_KIND_WD_LEGACY = re.compile(r"Код\s+выдачи", re.IGNORECASE)

_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _parse_amount_code(blob: str) -> Optional[Tuple[Decimal, str]]:
    """'150 000 rub' → (Decimal('150000'), 'RUB')"""
    try:
        amt_str, code = blob.rsplit(" ", 1)
    except ValueError:
        return None
    for ch in _SEP:
        amt_str = amt_str.replace(ch, "")
    amt_str = amt_str.replace(",", ".").strip()
    try:
        amt = Decimal(amt_str)
    except InvalidOperation:
        return None
    return amt, code.strip().upper()


def _issue_keyboard_with_kind(kind: str, req_id: int) -> InlineKeyboardMarkup:
    cb = f"{CB_ISSUE_DONE}:{kind}:{req_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Выдано", callback_data=cb)]]
    )


class CashRequestsHandler:
    """
    Универсальные заявки наличных:
      /депр|депт|депд|депе|депб <сумма/expr> <@или+кто_принесёт> [@или+кто_примет] [! комментарий]
      /выдр|выдт|выдд|выде|выдб <сумма/expr> <@или+кто_принесёт> [@или+кто_примет] [! комментарий]

    • В чат клиента: полная заявка + спойлер на код + кнопка «Выдано», БЕЗ строки «Клиент».
    • В заявочный чат: полная заявка БЕЗ спойлера, СО строкой «Клиент», без кнопки.
    • На «Выдано» — проводим операцию по кошельку и показываем баланс по валюте.
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
        self.router.callback_query.register(self._cb_issue_done, F.data.startswith(CB_ISSUE_DONE))

    async def _cmd_cash_req(self, message: Message) -> None:
        # доступ: менеджер / админ / админский чат
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        raw = (message.text or "")
        m = RE_CMD.match(raw)
        if not m:
            await message.answer(
                "Форматы:\n"
                "• /депр|депт|депд|депе|депб <сумма/expr> <Выдает> [Принимает] [! комментарий]\n"
                "• /выдр|выдт|выдд|выде|выдб <сумма/expr> <Выдает> [Принимает] [! комментарий]\n"
                "Напр.: /депр 150000 @vasya_courier @petya_cashier ! курс по договору\n"
                "       /выдр (700+300) +79995556677 ! выдать у офиса"
            )
            return

        cmd = m.group(1).lower()
        amount_expr = m.group(2).strip()
        tg_from = m.group(3).strip()
        tg_to = (m.group(4) or "").strip()
        comment = (m.group(5) or "").strip()

        kind, code = CMD_MAP.get(cmd, (None, None))
        if not kind or not code:
            await message.answer("Не распознал команду/валюту.")
            return

        try:
            amount_raw = evaluate(amount_expr)
            if amount_raw <= 0:
                await message.answer("Сумма должна быть > 0")
                return
        except (CalcError, InvalidOperation) as e:
            await message.answer(f"Ошибка в выражении суммы: {e}")
            return

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
        amount = amount_raw.quantize(q).quantize(Decimal("1"))
        pretty_amount = format_amount_core(amount, prec)

        req_id = random.randint(10_000_000, 99_999_999)
        pin_code = f"{random.randint(100, 999)}-{random.randint(100, 999)}"

        # --- Общая часть заявки (без строки "Клиент") ---
        base_lines_common = [
            f"<b>Заявка</b>: <code>{req_id}</code>",
            "-----",
            f"<b>Сумма</b>: <code>{pretty_amount} {code.lower()}</code>",
            f"<b>Выдает</b>: {tg_from}",
        ]
        if tg_to:
            base_lines_common.append(f"<b>Принимает</b>: {tg_to}")

        # код всегда после строки "Принимает" (или после "Выдает", если "Принимает" нет)
        base_lines_common.append(f"<b>Код</b>: <tg-spoiler>{pin_code}</tg-spoiler>")

        if comment:
            base_lines_common += ["----", f"<b>Комментарий</b>: <code>{html.escape(comment)}</code>❗️"]

        # 1) клиенту — БЕЗ "Клиент", со spoiler
        text_client = "\n".join(base_lines_common)

        # 2) заявочный чат — С "Клиент", БЕЗ spoiler, + тип
        base_lines_for_req = [
            f"<b>Заявка</b>: <code>{req_id}</code>",
            f"<b>Клиент</b>: <b>{html.escape(chat_name)}</b>",
            *base_lines_common,
        ]
        base_lines_for_req = [
            line.replace(f"<tg-spoiler>{pin_code}</tg-spoiler>", pin_code)
            for line in base_lines_for_req
        ]
        kind_ru = "Деп" if kind == "dep" else "Выд"
        base_lines_for_req.append(f"<b>Тип</b>: <b>{kind_ru}</b>")
        text_req = "\n".join(base_lines_for_req)

        await message.answer(
            text_client,
            parse_mode="HTML",
            reply_markup=_issue_keyboard_with_kind(kind=kind, req_id=req_id),
        )

        if self.request_chat_id:
            try:
                await message.bot.send_message(
                    chat_id=self.request_chat_id,
                    text=text_req,
                    parse_mode="HTML",
                )
            except Exception:
                pass

    async def _cb_issue_done(self, cq: CallbackQuery) -> None:
        msg = cq.message
        if not msg:
            await cq.answer()
            return

        # доступ по сообщению
        if not await require_manager_or_admin_message(
            self.repo,
            msg,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            await cq.answer("Недостаточно прав.", show_alert=True)
            return

        text = msg.text or ""

        # убираем клавиатуру (анти-даблклик)
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        # тип операции из callback_data (или legacy)
        op_kind: Optional[str] = None
        try:
            parts = (cq.data or "").split(":")
            if len(parts) >= 4 and ":".join(parts[:2]) == CB_ISSUE_DONE:
                maybe_kind = parts[2].lower()
                if maybe_kind in ("dep", "wd"):
                    op_kind = maybe_kind
        except Exception:
            op_kind = None

        if not op_kind:
            if _RE_KIND_DEP_LEGACY.search(text):
                op_kind = "dep"
            elif _RE_KIND_WD_LEGACY.search(text):
                op_kind = "wd"

        if not op_kind:
            await cq.answer("Не удалось распознать тип заявки.", show_alert=True)
            return

        # сумма/валюта
        m_amt = _RE_LINE_AMOUNT.search(text)
        if not m_amt:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        parsed = _parse_amount_code(m_amt.group(1))
        if not parsed:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        amount_raw, code = parsed
        code = code.upper()

        # кошелёк клиента и квантование
        chat_id = msg.chat.id
        chat_name = get_chat_name(msg)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            await cq.message.answer(f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]")
            await cq.answer()
            return

        prec = int(acc["precision"]) if acc["precision"] is not None else 2
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q).quantize(Decimal("1"))

        idem = f"cash:{chat_id}:{msg.message_id}"
        try:
            if op_kind == "dep":
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
            else:
                await self.repo.withdraw(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
        except Exception as e:
            await cq.message.answer(f"Не удалось провести операцию по кошельку: {e}")
            await cq.answer()
            return

        # показать текущий баланс по валюте
        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
        prec2 = int(acc2["precision"]) if acc2 and acc2.get("precision") is not None else prec
        pretty_bal = format_amount_core(cur_bal, prec2)

        await cq.message.answer(
            f"Запомнил.\nБаланс: <code>{pretty_bal} {code.lower()}</code>",
            parse_mode="HTML",
        )
        await cq.answer("Отмечено как выдано")