from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from aiogram.types import CallbackQuery

from keyboards import delete_from_table_keyboard
from services.exchange.card_parser import CANCEL_REQUEST_PREFIX, parse_get_give
from services.exchange.use_case_base import _ExchangeUseCaseBase
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.req_index import req_index
from services.cash_requests import post_request_message

log = logging.getLogger(__name__)


class CancelExchangeRequest(_ExchangeUseCaseBase):
    async def execute(
        self,
        cq: CallbackQuery,
        *,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
    ) -> None:
        msg = cq.message
        if not msg or not msg.text:
            await cq.answer("Нет сообщения", show_alert=True)
            return

        try:
            parts = (cq.data or "").split(":")
            if len(parts) < 2:
                raise ValueError
            req_id_s = parts[1].strip()
            table_req_id_from_cb = parts[2].strip() if len(parts) >= 3 else None
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        parsed_amounts = parse_get_give(msg.text)
        if not parsed_amounts:
            await cq.answer("Не удалось распознать заявку", show_alert=True)
            return

        (recv_amt_raw, recv_code), (pay_amt_raw, pay_code) = parsed_amounts

        chat_id = msg.chat.id
        chat_name = get_chat_name(msg)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)

        def find_account(code: str):
            return next((row for row in accounts if str(row["currency_code"]).upper() == code.upper()), None)

        acc_recv = find_account(recv_code)
        acc_pay = find_account(pay_code)
        if not acc_recv or not acc_pay:
            await cq.answer("Счёта клиента изменились. Проверьте /кошелек", show_alert=True)
            return

        recv_prec = int(acc_recv["precision"])
        pay_prec = int(acc_pay["precision"])
        recv_amt = recv_amt_raw.quantize(Decimal(10) ** -recv_prec, rounding=ROUND_HALF_UP)
        pay_amt = pay_amt_raw.quantize(Decimal(10) ** -pay_prec, rounding=ROUND_HALF_UP)

        try:
            recv_op_sign, pay_op_sign = await self.balance_service.apply_cancel(
                client_id=client_id,
                chat_id=chat_id,
                message_id=msg.message_id,
                req_id=req_id_s,
                recv_code=recv_code,
                recv_amount=recv_amt,
                pay_code=pay_code,
                pay_amount=pay_amt,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
            )
        except Exception as e:
            await cq.answer(f"Не удалось отменить: {e}", show_alert=True)
            return

        try:
            cancelled_at = datetime.now().strftime("%Y-%m-%d %H:%M")
            await msg.edit_text(f"{msg.text}\n----\nОтмена: <code>{cancelled_at}</code>", parse_mode="HTML",
                                reply_markup=None)
        except Exception:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        meta = await self._get_exchange_request_meta(req_id_s)
        table_req_id = (
            table_req_id_from_cb
            or req_index.get_table_req_id(req_id_s)
            or (str(meta["table_req_id"]) if meta and meta.get("table_req_id") else None)
        )
        request_copy = req_index.get_request_chat_copy(req_id_s)
        if request_copy is None and meta and meta.get("request_chat_id") and meta.get("request_message_id"):
            request_copy = (int(meta["request_chat_id"]), int(meta["request_message_id"]))
        request_text = req_index.get_request_chat_text(req_id_s)
        if request_text is None and meta and meta.get("request_text"):
            request_text = str(meta["request_text"])

        if request_copy is not None and request_text:
            try:
                cancelled_text = request_text
                if not cancelled_text.startswith(CANCEL_REQUEST_PREFIX):
                    cancelled_text = f"{CANCEL_REQUEST_PREFIX}\n{cancelled_text}"
                await cq.bot.edit_message_text(
                    chat_id=request_copy[0],
                    message_id=request_copy[1],
                    text=cancelled_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
                req_index.remember_request_chat_copy(
                    req_id=req_id_s,
                    request_chat_id=request_copy[0],
                    request_message_id=request_copy[1],
                    text=cancelled_text,
                )
                await self.repo.upsert_exchange_request_link(
                    client_req_id=str(req_id_s),
                    table_req_id=str(table_req_id or req_id_s),
                    request_chat_id=request_copy[0],
                    request_message_id=request_copy[1],
                    request_text=cancelled_text,
                    status="cancelled",
                )
            except Exception:
                log.exception("Failed to mark request chat copy cancelled for exchange request %s", req_id_s)

        if self.act_counter_service:
            try:
                act_chat_id = None
                if request_copy is not None:
                    act_chat_id = int(request_copy[0])
                elif meta and meta.get("request_chat_id"):
                    act_chat_id = int(meta["request_chat_id"])
                if act_chat_id is not None:
                    if int(chat_id) != int(act_chat_id):
                        await self.act_counter_service.revert_request_wallet_movements(
                            req_id=str(req_id_s),
                            request_chat_id=act_chat_id,
                        )
                await self.act_counter_service.cancel_request(req_id=str(req_id_s))
                if act_chat_id is not None:
                    await self._notify_act_current_amount(
                        bot=cq.bot,
                        request_chat_id=act_chat_id,
                    )
            except Exception:
                log.exception("Failed to cancel ACT movements for exchange request %s", req_id_s)

        table_done = req_index.is_table_done(table_req_id) if table_req_id else False
        if not table_done and meta and table_req_id and str(meta.get("table_req_id") or "") == str(table_req_id):
            table_done = bool(meta.get("is_table_done"))

        if self.request_chat_id and table_req_id and table_done:
            try:
                await post_request_message(
                    bot=cq.bot,
                    request_chat_id=self.request_chat_id,
                    text=(
                        f"⛔️ Заявка <code>{html.escape(req_id_s)}</code> отменена.\n\n"
                        f"Удалить строки в Google Sheets (Покупка/Продажа) "
                        f"с номером <b>{html.escape(table_req_id)}</b>?"
                    ),
                    reply_markup=delete_from_table_keyboard(req_id=table_req_id),
                )
            except Exception:
                log.exception("Failed to post table delete prompt for exchange request %s", req_id_s)

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc_recv2 = next((row for row in accounts2 if str(row["currency_code"]).upper() == recv_code.upper()), None)
        acc_pay2 = next((row for row in accounts2 if str(row["currency_code"]).upper() == pay_code.upper()), None)

        pretty_recv_op = format_amount_core(recv_amt, recv_prec)
        pretty_pay_op = format_amount_core(pay_amt, pay_prec)

        if acc_recv2:
            recv_bal = Decimal(str(acc_recv2["balance"]))
            recv_bal_text = format_amount_core(recv_bal, int(acc_recv2["precision"]))
        else:
            recv_bal_text = "—"

        if acc_pay2:
            pay_bal = Decimal(str(acc_pay2["balance"]))
            pay_bal_text = format_amount_core(pay_bal, int(acc_pay2["precision"]))
        else:
            pay_bal_text = "—"

        await cq.message.answer(
            (
                f"⛔️ Заявка <code>{html.escape(req_id_s)}</code> отменена.\n\n"
                f"Операция по {recv_code.lower()}: <code>{recv_op_sign}{pretty_recv_op} {recv_code.lower()}</code>\n"
                f"Баланс: <code>{recv_bal_text} {recv_code.lower()}</code>\n\n"
                f"Операция по {pay_code.lower()}: <code>{pay_op_sign}{pretty_pay_op} {pay_code.lower()}</code>\n"
                f"Баланс: <code>{pay_bal_text} {pay_code.lower()}</code>"
            ),
            parse_mode="HTML",
        )
        await cq.answer("Заявка отменена")
