# utils/requests.py
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional, Iterable, Tuple, Set

from aiogram import Bot, Router, F
from aiogram.types import InlineKeyboardMarkup, CallbackQuery

from keyboards.request import request_keyboard as kb_request_keyboard
from keyboards.confirm import confirm_kb as _confirm_kb  # штатная Да/Нет клавиатура
from utils.auth import require_manager_or_admin_callback
from utils.info import get_chat_name

from gutils.sheets import (
    get_firm_balance,
    SheetsNotConfigured,
    SheetsReadError,
)

STATUS_LINE_DONE = "Статус: Занесена в таблицу ✅"
STATUS_LINE_ISSUED = "Статус: Выдано ✅"

CB_PARTNER = "req:partner"
CB_TABLE_DONE = "req:table_done"
CB_ISSUE_DONE = "req:issue_done"

# Подтверждения «Занесена в таблицу»
CB_TABLE_CONFIRM_YES = "req:table_confirm:yes"
CB_TABLE_CONFIRM_NO = "req:table_confirm:no"

# --- Regex ---
_RE_LINE = re.compile(
    r"^(Депозит|Выдача):\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.MULTILINE
)
_RE_PAYOUT = re.compile(
    r"^Отдаём:\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.MULTILINE | re.IGNORECASE
)

# --- Простая защита от дублей подтверждений/статусов для конкретной заявки ---
# ключ = (chat_id, message_id)
_PENDING_CONFIRM: Set[Tuple[int, int]] = set()
_MARKED_DONE: Set[Tuple[int, int]] = set()


def _parse_kind_amount_code(text: str) -> tuple[str, Decimal, str] | None:
    m = _RE_LINE.search(text or "")
    if not m:
        return None
    kind_ru = m.group(1)
    payload = m.group(2).strip()
    try:
        amount_str, code_str = payload.rsplit(" ", 1)
    except ValueError:
        return None
    SEP_CHARS = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}
    for ch in SEP_CHARS:
        amount_str = amount_str.replace(ch, "")
    amount_str = amount_str.replace(",", ".").strip()
    try:
        amount = Decimal(amount_str)
    except (InvalidOperation, ValueError):
        return None
    return kind_ru, amount, code_str.strip().upper()


def _parse_payout_amount_code(text: str) -> tuple[Decimal, str] | None:
    m = _RE_PAYOUT.search(text or "")
    if not m:
        return None
    payload = (m.group(1) or "").strip()
    try:
        amount_str, code_str = payload.rsplit(" ", 1)
    except ValueError:
        return None
    SEP_CHARS = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}
    for ch in SEP_CHARS:
        amount_str = amount_str.replace(ch, "")
    amount_str = amount_str.replace(",", ".").strip()
    try:
        amount = Decimal(amount_str)
    except (InvalidOperation, ValueError):
        return None
    return amount, code_str.strip().upper()


def _append_status_once(text: str, status_line: str) -> str:
    if status_line in (text or ""):
        return text
    if not (text or "").endswith("\n"):
        return (text or "") + "\n" + status_line
    return (text or "") + status_line


async def post_request_message(
        bot: Bot,
        request_chat_id: int,
        text: str,
        *,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        disable_notification: bool = False,
) -> None:
    if reply_markup is None:
        reply_markup = kb_request_keyboard(
            cb_partner=CB_PARTNER,
            cb_table_done=CB_TABLE_DONE,
        )
    await bot.send_message(
        chat_id=request_chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_notification=disable_notification,
    )


def get_request_router(*, allowed_chat_ids: Iterable[int] | None = None) -> Router:
    router = Router()
    allowed = set(allowed_chat_ids or [])

    @router.callback_query(F.data == CB_PARTNER)
    async def _cb_partner(cq: CallbackQuery) -> None:
        if allowed and (not cq.message or cq.message.chat.id not in allowed):
            await cq.answer("Недоступно в этом чате.", show_alert=True)
            return
        await cq.answer("Функция выбора контрагента появится позже.", show_alert=True)

    @router.callback_query(F.data == CB_TABLE_DONE)
    async def _cb_table_done(cq: CallbackQuery) -> None:
        if allowed and (not cq.message or cq.message.chat.id not in allowed):
            await cq.answer("Недоступно в этом чате.", show_alert=True)
            return
        if not cq.message:
            await cq.answer("Нет сообщения.", show_alert=True)
            return

        chat_id = cq.message.chat.id
        msg_id = cq.message.message_id
        key = (chat_id, msg_id)

        # Уже поставлен статус?
        if STATUS_LINE_DONE in (cq.message.text or "") or key in _MARKED_DONE:
            await cq.answer("Статус уже установлен.", show_alert=False)
            return

        # Проверка остатков фирмы (мягкая)
        warn: Optional[str] = None
        parsed = _parse_payout_amount_code(cq.message.text or "")
        if parsed:
            amount, code = parsed
            try:
                firm_bal = get_firm_balance(code)
                if firm_bal < amount:
                    warn = (
                        f"⚠️ Недостаточно {code} на общем счёте:\n"
                        f"нужно <b>{amount}</b>, доступно <b>{firm_bal}</b>.\n\n"
                        f"Продолжить проставление статуса «Занесена в таблицу»?"
                    )
            except (SheetsNotConfigured, SheetsReadError):
                warn = None  # нет проверки — идём как обычно

        if warn:
            # Уже висит запрос подтверждения?
            if key in _PENDING_CONFIRM:
                await cq.answer("Подтверждение уже запрошено.", show_alert=False)
                return
            _PENDING_CONFIRM.add(key)
            try:
                await cq.answer("Требуется подтверждение.", show_alert=False)
            except Exception:
                pass
            # показываем подтверждение ответом
            try:
                await cq.message.reply(
                    warn,
                    parse_mode="HTML",
                    reply_markup=_confirm_kb(CB_TABLE_CONFIRM_YES, CB_TABLE_CONFIRM_NO),
                )
            except Exception:
                pass
            return

        # Без предупреждения — ставим статус сразу (идемпотентно)
        new_text = _append_status_once(cq.message.text or "", STATUS_LINE_DONE)
        try:
            await cq.message.edit_text(new_text, parse_mode="HTML")
            _MARKED_DONE.add(key)
        except Exception:
            pass
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Статус обновлён: занесена в таблицу ✅")

    # Подтверждения
    @router.callback_query(F.data == CB_TABLE_CONFIRM_YES)
    async def _cb_table_confirm_yes(cq: CallbackQuery) -> None:
        if allowed and (not cq.message or cq.message.chat.id not in allowed):
            await cq.answer("Недоступно в этом чате.", show_alert=True)
            return
        if not cq.message or not cq.message.reply_to_message:
            await cq.answer("Не найдено исходное сообщение заявки.", show_alert=True)
            return

        original = cq.message.reply_to_message
        chat_id = original.chat.id
        msg_id = original.message_id
        key = (chat_id, msg_id)

        # Если уже проставили — выходим
        if STATUS_LINE_DONE in (original.text or "") or key in _MARKED_DONE:
            # Снять «ожидание подтверждения» и аккуратно убрать клавиатуру у диалога
            _PENDING_CONFIRM.discard(key)
            try:
                await cq.message.delete()
            except Exception:
                pass
            await cq.answer("Статус уже проставлен.")
            return

        # 1) правим статус у исходной заявки
        new_text = _append_status_once(original.text or "", STATUS_LINE_DONE)
        try:
            await original.edit_text(new_text, parse_mode="HTML")
            _MARKED_DONE.add(key)
        except Exception:
            pass
        try:
            await original.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        # 2) убираем сообщение-подтверждение (НЕ пишем «Принято…»)
        try:
            await cq.message.delete()
        except Exception:
            # fallback — хотя бы убрать кнопки
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        _PENDING_CONFIRM.discard(key)
        await cq.answer("Статус обновлён.")

    @router.callback_query(F.data == CB_TABLE_CONFIRM_NO)
    async def _cb_table_confirm_no(cq: CallbackQuery) -> None:
        if allowed and (not cq.message or cq.message.chat.id not in allowed):
            await cq.answer("Недоступно в этом чате.", show_alert=True)
            return
        if cq.message and cq.message.reply_to_message:
            key = (cq.message.reply_to_message.chat.id, cq.message.reply_to_message.message_id)
            _PENDING_CONFIRM.discard(key)
        # просто удаляем запрос подтверждения
        try:
            await cq.message.delete()
        except Exception:
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
        await cq.answer("Действие отменено")

    return router


def get_issue_router(
        *,
        repo,  # Repo
        request_chat_id: int | None = None,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
) -> Router:
    """
    Клиентские чаты — кнопка 'Выдано' (жать могут только менеджеры/админы).
    После нажатия:
      1) проводим операцию в БД (депозит -> зачислить, выдача -> списать),
      2) помечаем сообщение статусом и убираем кнопку,
      3) дублируем исходный текст (без статуса) в заявочный чат.
    """
    router = Router()
    admin_chat_ids = set(admin_chat_ids or [])
    admin_user_ids = set(admin_user_ids or [])

    @router.callback_query(F.data == CB_ISSUE_DONE)
    async def _cb_issue_done(cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
                repo, cq,
                admin_chat_ids=admin_chat_ids,
                admin_user_ids=admin_user_ids,
        ):
            return

        if not cq.message:
            await cq.answer("Нет сообщения.", show_alert=True)
            return

        original_text = cq.message.text or ""
        parsed = _parse_kind_amount_code(original_text)
        if not parsed:
            await cq.answer("Не удалось распознать сумму или валюту.", show_alert=True)
            return
        kind_ru, amount, code = parsed
        if amount <= 0:
            await cq.answer("Сумма должна быть > 0.", show_alert=True)
            return

        # проводим транзакцию (идемпотентно по message_id)
        try:
            chat_id = cq.message.chat.id
            chat_name = get_chat_name(cq.message)
            client_id = await repo.ensure_client(chat_id=chat_id, name=chat_name)

            accounts = await repo.snapshot_wallet(client_id)
            if not any(str(r["currency_code"]).upper() == code for r in accounts):
                await cq.answer(f"Счёт {code} не найден у клиента.", show_alert=True)
                return

            idem = f"issue:{chat_id}:{cq.message.message_id}"

            if kind_ru == "Депозит":
                await repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash: issued",
                    source="cash_request",
                    idempotency_key=idem,
                )
            elif kind_ru == "Выдача":
                await repo.withdraw(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash: issued",
                    source="cash_request",
                    idempotency_key=idem,
                )
            else:
                await cq.answer("Неизвестный тип заявки.", show_alert=True)
                return

        except Exception as e:
            await cq.answer(f"Операция не выполнена: {e}", show_alert=True)
            return

        # помечаем статус и убираем кнопку
        new_text = _append_status_once(original_text, STATUS_LINE_ISSUED)
        try:
            await cq.message.edit_text(new_text, parse_mode="HTML")
        except Exception:
            pass
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        # дублирование в заявочный чат (без статуса), если задан
        if request_chat_id:
            try:
                await post_request_message(
                    bot=cq.bot,
                    request_chat_id=request_chat_id,
                    text=original_text,
                )
            except Exception:
                pass

        await cq.answer("Отмечено как выдано ✅")

    return router
