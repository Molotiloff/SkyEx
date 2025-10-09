# handlers/accept_short.py
from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional, Tuple

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from db_asyncpg.repo import Repo
from utils.auth import (
    require_manager_or_admin_message,
    require_manager_or_admin_callback,
)
from utils.calc import evaluate, CalcError
from utils.exchange_base import AbstractExchangeHandler
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.requests import post_request_message


def _fmt_rate(d: Decimal) -> str:
    s = f"{d.normalize():f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


# Ищем номер заявки в тексте сообщения бота
_RE_REQ_ID = re.compile(r"Заявка:\s*(?:<code>)?(\d{6,})(?:</code>)?", re.IGNORECASE)

# Для парсинга сумм из текста заявки (нужно для отмены)
_RE_GET = re.compile(r"^\s*Получаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.I | re.M)
_RE_GIVE = re.compile(r"^\s*Отдаём:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.I | re.M)
_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _parse_amt_code(blob: str) -> Optional[Tuple[Decimal, str]]:
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


def _cancel_kb(req_id: int | str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить заявку", callback_data=f"req_cancel:{req_id}")]
        ]
    )


class AcceptShortHandler(AbstractExchangeHandler):
    """
    /пд|/пе|/пт|/пр|/пб <recv_amount_expr> <од|ое|от|ор|об> <pay_amount_expr> [комментарий]

    Принимаем слева — списываем у клиента; отдаём справа — зачисляем клиенту.
    Если команда отправлена в ответ на сообщение с заявкой (бота ИЛИ исходную команду) —
    редактируем существующую заявку (только менеджеры/админы).
    """
    RECV_MAP = {"пд": "USD", "пе": "EUR", "пт": "USDT", "пр": "RUB", "пб": "USDW"}
    PAY_MAP  = {"од": "USD", "ое": "EUR", "от": "USDT", "ор": "RUB", "об": "USDW"}

    def __init__(
        self,
        repo: Repo,
        admin_chat_ids: set[int] | None = None,
        admin_user_ids: set[int] | None = None,
        request_chat_id: int | None = None,
        *,
        ignore_chat_ids: set[int] | None = None,  # NEW: чаты, где игнорируем короткие команды
    ) -> None:
        super().__init__(repo, request_chat_id=request_chat_id)
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.ignore_chat_ids = set(ignore_chat_ids or set())
        self.router = Router()
        self._register()

    async def _cmd_accept_short(self, message: Message) -> None:
        # Игнорируем команды в «шумных» чатах (заявки/выдачи и т.п.)
        if self.ignore_chat_ids and message.chat and message.chat.id in self.ignore_chat_ids:
            return

        # доступ: админ-чат / админ-пользователь / менеджер
        if not await require_manager_or_admin_message(
            self.repo, message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        raw = (message.text or "")
        m = re.match(
            r"^/(пд|пе|пт|пр|пб)(?:@\w+)?\s+(.+?)\s+(од|ое|от|ор|об)\s+(\S+)(?:\s+(.+))?$",
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
            await message.answer("Не распознал валюты. Используйте: /пд /пе /пт /пр /пб и од/ое/от/ор/об.")
            return

        # Валидируем выражения (без комментария)
        try:
            recv_raw = evaluate(recv_amount_expr)
            pay_raw = evaluate(pay_amount_expr)
            if recv_raw <= 0 or pay_raw <= 0:
                await message.answer("Суммы должны быть > 0")
                return
            _ = recv_raw / pay_raw  # проверка деления
        except (CalcError, InvalidOperation, ZeroDivisionError) as e:
            await message.answer(f"Ошибка в выражениях: {e}")
            return

        # Точности счётов (для форматирования), без изменения балансов
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

        # Квантуем и считаем курс «как людям удобно»
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_amount = recv_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amount = pay_raw.quantize(q_pay,  rounding=ROUND_HALF_UP)
        if recv_amount == 0 or pay_amount == 0:
            await message.answer("Сумма слишком мала для точности выбранных валют.")
            return

        try:
            if recv_code == "RUB" or pay_code == "RUB":
                rub_raw = recv_raw if recv_code == "RUB" else pay_raw
                other_raw = pay_raw if recv_code == "RUB" else recv_raw
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

        # === ПОПЫТКА РЕДАКТИРОВАНИЯ (вынесено в exchange_base) ===
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
            recv_is_deposit=False,   # принимаем — списываем у клиента
            pay_is_withdraw=False,   # отдаём — зачисляем клиенту
        )
        if handled:
            return

        # === СОЗДАНИЕ НОВОЙ ЗАЯВКИ ===
        await self.process(
            message,
            recv_code=recv_code,
            recv_amount_expr=recv_amount_expr,
            pay_code=pay_code,
            pay_amount_expr=pay_amount_expr,
            recv_is_deposit=False,   # принимаем — списываем у клиента
            pay_is_withdraw=False,   # отдаём — зачисляем клиенту
            note=user_note or None,
        )

    # ====== КОЛЛБЭК ОТМЕНЫ ЗАЯВКИ ======
    async def _cb_cancel(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
                self.repo, cq,
                admin_chat_ids=self.admin_chat_ids,
                admin_user_ids=self.admin_user_ids,
        ):
            return

        msg = cq.message
        if not msg or not msg.text:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        # извлечём req_id из callback_data (формат: req_cancel:<id>)
        try:
            _, req_id_s = (cq.data or "").split(":", 1)
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        # парсим суммы/коды из текста
        m_get = _RE_GET.search(msg.text)
        m_give = _RE_GIVE.search(msg.text)
        if not (m_get and m_give):
            await cq.answer("Не удалось распознать заявку", show_alert=True)
            return

        p_get = _parse_amt_code(m_get.group(1))
        p_give = _parse_amt_code(m_give.group(1))
        if not (p_get and p_give):
            await cq.answer("Не удалось распознать суммы", show_alert=True)
            return

        recv_amt_raw, recv_code = p_get  # «Получаем» (слева)
        pay_amt_raw, pay_code = p_give  # «Отдаём»   (справа)

        chat_id = msg.chat.id
        chat_name = get_chat_name(msg)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)

        def _find_acc(code: str):
            return next((r for r in accounts if str(r["currency_code"]).upper() == code.upper()), None)

        acc_recv = _find_acc(recv_code)
        acc_pay = _find_acc(pay_code)
        if not acc_recv or not acc_pay:
            await cq.answer("Счёта клиента изменились. Проверьте /кошелек", show_alert=True)
            return

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])
        q_recv = Decimal(10) ** -recv_prec
        q_pay = Decimal(10) ** -pay_prec
        recv_amt = recv_amt_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
        pay_amt = pay_amt_raw.quantize(q_pay, rounding=ROUND_HALF_UP)

        # Идемпотентные ключи отмены по id сообщения с заявкой
        idem_left = f"cancel:{chat_id}:{msg.message_id}:recv"
        idem_right = f"cancel:{chat_id}:{msg.message_id}:pay"

        try:
            # В этой команде изначально было: LEFT = withdraw, RIGHT = deposit.
            # Откат: LEFT → deposit (вернуть), RIGHT → withdraw (забрать).
            await self.repo.deposit(
                client_id=client_id,
                currency_code=recv_code,
                amount=recv_amt,
                comment=f"cancel req {req_id_s}",
                source="exchange_cancel",
                idempotency_key=idem_left,
            )
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=pay_code,
                amount=pay_amt,
                comment=f"cancel req {req_id_s}",
                source="exchange_cancel",
                idempotency_key=idem_right,
            )
        except Exception as e:
            await cq.answer(f"Не удалось отменить: {e}", show_alert=True)
            return

        # правим сообщение — убираем клавиатуру и помечаем отмену
        try:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_text = f"{msg.text}\n----\nОтмена: <code>{ts}</code>"
            await msg.edit_text(new_text, parse_mode="HTML", reply_markup=None)
        except Exception:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        # уведомление в заявочный чат (кратко)
        if self.request_chat_id:
            try:
                await post_request_message(
                    bot=cq.bot,
                    request_chat_id=self.request_chat_id,
                    text=f"⛔️ Заявка <code>{html.escape(req_id_s)}</code> отменена.",
                    reply_markup=None,
                )
            except Exception:
                pass

        # Итоговое сообщение клиенту: операции по обеим валютам + их балансы
        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc_recv2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == recv_code.upper()), None)
        acc_pay2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == pay_code.upper()), None)

        # суммы операции в «читаемом» виде
        pretty_recv_op = format_amount_core(recv_amt, recv_prec)  # будет со знаками без префикса; префикс добавим сами
        pretty_pay_op = format_amount_core(pay_amt, pay_prec)

        # текущие балансы
        if acc_recv2:
            recv_bal = Decimal(str(acc_recv2["balance"]))
            recv_prec2 = int(acc_recv2["precision"])
            pretty_recv_bal = format_amount_core(recv_bal, recv_prec2)
        else:
            pretty_recv_bal = "—"

        if acc_pay2:
            pay_bal = Decimal(str(acc_pay2["balance"]))
            pay_prec2 = int(acc_pay2["precision"])
            pretty_pay_bal = format_amount_core(pay_bal, pay_prec2)
        else:
            pretty_pay_bal = "—"

        text_client = (
            f"⛔️ Заявка <code>{html.escape(req_id_s)}</code> отменена.\n\n"
            f"Операция по {recv_code.lower()}: <code>+{pretty_recv_op} {recv_code.lower()}</code>\n"
            f"Баланс: <code>{pretty_recv_bal} {recv_code.lower()}</code>\n\n"
            f"Операция по {pay_code.lower()}: <code>-{pretty_pay_op} {pay_code.lower()}</code>\n"
            f"Баланс: <code>{pretty_pay_bal} {pay_code.lower()}</code>"
        )
        await cq.message.answer(text_client, parse_mode="HTML")

        await cq.answer("Заявка отменена")

    def _register(self) -> None:
        self.router.message.register(self._cmd_accept_short, Command("пд"))
        self.router.message.register(self._cmd_accept_short, Command("пе"))
        self.router.message.register(self._cmd_accept_short, Command("пт"))
        self.router.message.register(self._cmd_accept_short, Command("пр"))
        self.router.message.register(self._cmd_accept_short, Command("пб"))
        self.router.message.register(
            self._cmd_accept_short,
            F.text.regexp(r"(?iu)^/(пд|пе|пт|пр|пб)(?:@\w+)?\b"),
        )
        # обработчик коллбэка отмены — ВЕРНУЛИ
        self.router.callback_query.register(self._cb_cancel, F.data.startswith("req_cancel:"))