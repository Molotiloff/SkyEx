from __future__ import annotations

from typing import Iterable, Optional

from aiogram import F, Router
from aiogram.types import CallbackQuery

from gutils.sheets import (
    SheetsNotConfigured,
    SheetsReadError,
    get_firm_balance,
)
from keyboards import confirm_kb as _confirm_kb
from keyboards.request import CB_ISSUE_DONE, CB_PARTNER, CB_TABLE_DONE
from services.cash_requests.legacy_request_messages import post_request_message
from services.cash_requests.legacy_request_parsing import (
    STATUS_LINE_DONE,
    STATUS_LINE_ISSUED,
    append_status_once,
    parse_kind_amount_code,
    parse_payout_amount_code,
)
from services.cash_requests.legacy_request_session import RequestStatusSessionStore
from utils.auth import require_manager_or_admin_callback
from utils.info import get_chat_name

CB_TABLE_CONFIRM_YES = "req:table_confirm:yes"
CB_TABLE_CONFIRM_NO = "req:table_confirm:no"


def get_request_router(*, allowed_chat_ids: Iterable[int] | None = None) -> Router:
    router = Router()
    allowed = set(allowed_chat_ids or [])
    session_store = RequestStatusSessionStore()

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

        key = session_store.key(cq.message.chat.id, cq.message.message_id)

        if STATUS_LINE_DONE in (cq.message.text or "") or session_store.is_marked_done(key):
            await cq.answer("Статус уже установлен.", show_alert=False)
            return

        warn: Optional[str] = None
        parsed = parse_payout_amount_code(cq.message.text or "")
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
                warn = None

        if warn:
            if session_store.is_pending_confirm(key):
                await cq.answer("Подтверждение уже запрошено.", show_alert=False)
                return
            session_store.add_pending_confirm(key)
            try:
                await cq.answer("Требуется подтверждение.", show_alert=False)
            except Exception:
                pass
            try:
                await cq.message.reply(
                    warn,
                    parse_mode="HTML",
                    reply_markup=_confirm_kb(CB_TABLE_CONFIRM_YES, CB_TABLE_CONFIRM_NO),
                )
            except Exception:
                pass
            return

        new_text = append_status_once(cq.message.text or "", STATUS_LINE_DONE)
        try:
            await cq.message.edit_text(new_text, parse_mode="HTML")
            session_store.mark_done(key)
        except Exception:
            pass
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Статус обновлён: занесена в таблицу ✅")

    @router.callback_query(F.data == CB_TABLE_CONFIRM_YES)
    async def _cb_table_confirm_yes(cq: CallbackQuery) -> None:
        if allowed and (not cq.message or cq.message.chat.id not in allowed):
            await cq.answer("Недоступно в этом чате.", show_alert=True)
            return
        if not cq.message or not cq.message.reply_to_message:
            await cq.answer("Не найдено исходное сообщение заявки.", show_alert=True)
            return

        original = cq.message.reply_to_message
        key = session_store.key(original.chat.id, original.message_id)

        if STATUS_LINE_DONE in (original.text or "") or session_store.is_marked_done(key):
            session_store.discard_pending_confirm(key)
            try:
                await cq.message.delete()
            except Exception:
                pass
            await cq.answer("Статус уже проставлен.")
            return

        new_text = append_status_once(original.text or "", STATUS_LINE_DONE)
        try:
            await original.edit_text(new_text, parse_mode="HTML")
            session_store.mark_done(key)
        except Exception:
            pass
        try:
            await original.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        try:
            await cq.message.delete()
        except Exception:
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        session_store.discard_pending_confirm(key)
        await cq.answer("Статус обновлён.")

    @router.callback_query(F.data == CB_TABLE_CONFIRM_NO)
    async def _cb_table_confirm_no(cq: CallbackQuery) -> None:
        if allowed and (not cq.message or cq.message.chat.id not in allowed):
            await cq.answer("Недоступно в этом чате.", show_alert=True)
            return
        if cq.message and cq.message.reply_to_message:
            key = session_store.key(cq.message.reply_to_message.chat.id, cq.message.reply_to_message.message_id)
            session_store.discard_pending_confirm(key)
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
    repo,
    request_chat_id: int | None = None,
    admin_chat_ids: Iterable[int] | None = None,
    admin_user_ids: Iterable[int] | None = None,
) -> Router:
    router = Router()
    admin_chat_ids = set(admin_chat_ids or [])
    admin_user_ids = set(admin_user_ids or [])

    @router.callback_query(F.data == CB_ISSUE_DONE)
    async def _cb_issue_done(cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            repo,
            cq,
            admin_chat_ids=admin_chat_ids,
            admin_user_ids=admin_user_ids,
        ):
            return

        if not cq.message:
            await cq.answer("Нет сообщения.", show_alert=True)
            return

        original_text = cq.message.text or ""
        parsed = parse_kind_amount_code(original_text)
        if not parsed:
            await cq.answer("Не удалось распознать сумму или валюту.", show_alert=True)
            return

        kind_ru, amount, code = parsed
        if amount <= 0:
            await cq.answer("Сумма должна быть > 0.", show_alert=True)
            return

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

        new_text = append_status_once(original_text, STATUS_LINE_ISSUED)
        try:
            await cq.message.edit_text(new_text, parse_mode="HTML")
        except Exception:
            pass
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

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
