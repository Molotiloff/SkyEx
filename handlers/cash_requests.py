# handlers/cash_requests.py
from __future__ import annotations
import html
import random
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Tuple, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from db_asyncpg.repo import Repo
from keyboards.request import issue_keyboard, request_keyboard, CB_ISSUE_DONE
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.auth import require_manager_or_admin_message
from utils.format_wallet_compact import format_wallet_compact

# команды → (тип, валюта)
CMD_MAP = {
    "депр": ("dep", "RUB"),  "депт": ("dep", "USDT"), "депд": ("dep", "USD"),
    "депе": ("dep", "EUR"),  "депб": ("dep", "USDW"),
    "выдр": ("wd",  "RUB"),  "выдт": ("wd",  "USDT"), "выдд": ("wd",  "USD"),
    "выде": ("wd",  "EUR"),  "выдб": ("wd",  "USDW"),
}

# Участник: @telegram или +телефон (6–15 цифр)
PART = r"(?:@[A-Za-z0-9_]{5,}|\+\d{6,15})"

# Формат:
# /депр|... <amount_expr> <who_from(@|+)> [who_to(@|+)] [| comment]
RE_CMD = re.compile(
    rf"""^/(депр|депт|депд|депе|депб|выдр|выдт|выдд|выде|выдб)(?:@\w+)?   # команда
         \s+(.+?)                                                         # сумма/expr (лениво)
         \s+({PART})                                                      # кто приносит
         (?:\s+({PART}))?                                                 # [кто примет]
         (?:\s*\|\s*(.+))?                                                # [| комментарий]
         \s*$""",
    flags=re.IGNORECASE | re.UNICODE | re.VERBOSE,
)

# Разбор строки вида:
# "Депозит: <code>150 000 rub</code>" или "Выдача: <code>700 usdt</code>"
_RE_LINE_DEP = re.compile(r"^\s*Депозит:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M)
_RE_LINE_WD  = re.compile(r"^\s*Выдача:\s*(?:<code>)?(.+?)(?:</code>)?\s*$",  re.IGNORECASE | re.M)
_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}

def _parse_amount_code(blob: str) -> Optional[Tuple[Decimal, str]]:
    """
    blob: "150 000 rub" → (Decimal('150000'), 'RUB')
    """
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


class CashRequestsHandler:
    """
    Универсальные заявки наличных:
      /депр|депт|депд|депе|депб <сумма/expr> <@или+кто_принесёт> [@или+кто_примет] [| комментарий]
      /выдр|выдт|выдд|выде|выдб <сумма/expr> <@или+кто_принесёт> [@или+кто_примет] [| комментарий]

    В чат клиента: заявка + кнопка «Выдано» (жмут менеджеры).
    В заявочный чат: заявка отправляется ТОЛЬКО после нажатия «Выдано».
    После нажатия «Выдано» — проводим операцию по кошельку и показываем актуальные балансы клиента.
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
        # Коллбэк «Выдано»
        self.router.callback_query.register(
            self._cb_issue_done,
            F.data.startswith(CB_ISSUE_DONE),
        )

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
                "• /депр|депт|депд|депе|депб <сумма/expr> <@или+кто_принесёт> [@или+кто_примет] [| комментарий]\n"
                "• /выдр|выдт|выдд|выде|выдб <сумма/expr> <@или+кто_принесёт> [@или+кто_примет] [| комментарий]\n"
                "Напр.: /депр 150000 @vasya_courier @petya_cashier | курс по договору\n"
                "       /выдр (700+300) +79995556677 | выдать у офиса"
            )
            return

        cmd = m.group(1).lower()
        amount_expr = m.group(2).strip()
        tg_from = m.group(3).strip()
        tg_to = (m.group(4) or "").strip()  # может быть пустым
        comment = (m.group(5) or "").strip()

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
                f"Сумма: <code>{pretty_amt} {code.lower()}</code>",
                f"Выдает: <code>{html.escape(tg_from)}</code>",
            ]
            if tg_to:
                lines.append(f"Кто примет: <code>{html.escape(tg_to)}</code>")
            lines.append(f"Код получения: <tg-spoiler>{pin_code}</tg-spoiler>")
        else:
            lines = [
                f"Заявка: <code>{req_id}</code>",
                "-----",
                f"Сумма: <code>{pretty_amt} {code.lower()}</code>",
                f"Выдает: <code>{html.escape(tg_from)}</code>",
            ]
            if tg_to:
                lines.append(f"Принимает: <code>{html.escape(tg_to)}</code>")
            lines.append(f"Код выдачи: <tg-spoiler>{pin_code}</tg-spoiler>")

        if comment:
            lines += ["----", f"Комментарий: <code>{html.escape(comment)}</code>"]

        text = "\n".join(lines)

        # Отправляем только в чат клиента — с кнопкой «Выдано» (+ req_id в callback_data)
        await message.answer(
            text,
            parse_mode="HTML",
            reply_markup=issue_keyboard(req_id=req_id),
        )

    async def _cb_issue_done(self, cq: CallbackQuery) -> None:
        """
        Обработка нажатия «Выдано»:
          - парсим сумму/валюту и тип (Депозит/Выдача) из текста заявки
          - проводим операцию по кошельку (идемпотентно)
          - убираем клавиатуру
          - отправляем заявку в заявочный чат (если настроен)
          - показываем актуальные балансы клиента
        Доступ — те же менеджеры/админы.
        """
        msg = cq.message
        if not msg:
            await cq.answer()
            return

        # проверка доступа по сообщению (тот же чат)
        if not await require_manager_or_admin_message(
            self.repo, msg,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            await cq.answer("Недостаточно прав.", show_alert=True)
            return

        text = msg.text or ""
        # 0) убираем клавиатуру сразу (чтобы не жали повторно)
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        # 1) парсим тип и сумму/валюту
        m_dep = _RE_LINE_DEP.search(text)
        m_wd  = _RE_LINE_WD.search(text)
        op_kind: Optional[str] = None  # "dep" | "wd"
        amt_code_raw: Optional[str] = None

        if m_dep:
            op_kind = "dep"
            amt_code_raw = m_dep.group(1)
        elif m_wd:
            op_kind = "wd"
            amt_code_raw = m_wd.group(1)

        if not op_kind or not amt_code_raw:
            await cq.answer("Не удалось распознать заявку.", show_alert=True)
            return

        parsed = _parse_amount_code(amt_code_raw)
        if not parsed:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        amount_raw, code = parsed  # Decimal, UPPER
        code = code.upper()  # rub→RUB, usdt→USDT и т.д.

        # 2) проводим операцию по счёту клиента (идемпотентность по сообщению заявки)
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
        amount = amount_raw.quantize(q)

        idem = f"cash:{chat_id}:{msg.message_id}"  # на одно сообщение — одна проводка

        try:
            if op_kind == "dep":
                # это «внесли наличные» → зачисляем клиенту
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
            else:
                # это «выдали наличные» → списываем
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

        # 3) отправляем в заявочный чат (если есть)
        if self.request_chat_id:
            try:
                await cq.bot.send_message(
                    chat_id=self.request_chat_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=request_keyboard(),
                )
            except Exception:
                pass

        # 4) показать актуальные балансы клиента
        rows = await self.repo.snapshot_wallet(client_id)
        compact = format_wallet_compact(rows, only_nonzero=True)

        if compact == "Пусто":
            await cq.message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
        else:
            safe_title = html.escape(f"Средств у {chat_name}:")
            safe_rows = html.escape(compact)
            await cq.message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

        await cq.answer("Отмечено как выдано")
