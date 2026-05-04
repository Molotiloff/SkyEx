from __future__ import annotations

import html
import logging
import random

from aiogram.types import Message

from keyboards import request_keyboard
from services.exchange.keyboards import cancel_keyboard
from services.exchange.use_case_base import _ExchangeUseCaseBase
from utils.format_wallet_compact import format_wallet_compact
from utils.info import get_chat_name
from utils.req_index import req_index
from services.cash_requests import post_request_message

log = logging.getLogger(__name__)


class CreateExchangeRequest(_ExchangeUseCaseBase):
    async def execute(
        self,
        message: Message,
        recv_code: str,
        recv_amount_expr: str,
        pay_code: str,
        pay_amount_expr: str,
        *,
        recv_is_deposit: bool = True,
        pay_is_withdraw: bool = True,
        note: str | None = None,
    ) -> None:
        chat_id = message.chat.id
        chat_name = get_chat_name(message)

        try:
            client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
            accounts = await self.repo.snapshot_wallet(client_id)
            try:
                calc = self.calculator.calculate(
                    recv_code=recv_code,
                    recv_amount_expr=recv_amount_expr,
                    pay_code=pay_code,
                    pay_amount_expr=pay_amount_expr,
                    accounts=accounts,
                )
            except ValueError as e:
                await message.answer(str(e))
                return

            recv_code = calc.recv_code
            pay_code = calc.pay_code
            recv_amount = calc.recv_amount
            pay_amount = calc.pay_amount
            recv_prec = calc.recv_precision
            pay_prec = calc.pay_precision
            rate = calc.rate
            rate_text = calc.rate_text

            req_id = random.randint(10_000_000, 99_999_999)
            table_req_id = await self.repo.next_request_id()

            creator_name = None
            try:
                user = getattr(message, "from_user", None)
                if user:
                    creator_name = (
                        user.full_name
                        or (f"@{user.username}" if getattr(user, "username", None) else None)
                        or f"id:{user.id}"
                    )
            except Exception:
                pass
            creator_name = creator_name or "unknown"

            texts = self.text_builder.build_new_texts(
                req_id=req_id,
                table_req_id=table_req_id,
                client_name=chat_name,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_prec=recv_prec,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_prec=pay_prec,
                rate=rate_text,
                creator_name=creator_name,
                note=note,
                formula=pay_amount_expr,
            )

            idem_recv = f"{chat_id}:{message.message_id}:recv"
            idem_pay = f"{chat_id}:{message.message_id}:pay"
            recv_comment = recv_amount_expr if not note else f"{recv_amount_expr} | {note}"
            pay_comment = pay_amount_expr if not note else f"{pay_amount_expr} | {note}"

            try:
                create_result = await self.balance_service.apply_create(
                    client_id=client_id,
                    recv_code=recv_code,
                    recv_amount=recv_amount,
                    recv_comment=recv_comment,
                    pay_code=pay_code,
                    pay_amount=pay_amount,
                    pay_comment=pay_comment,
                    recv_is_deposit=recv_is_deposit,
                    pay_is_withdraw=pay_is_withdraw,
                    idem_recv=idem_recv,
                    idem_pay=idem_pay,
                )
            except Exception as leg_err:
                await message.answer(f"Не удалось выполнить обмен: {leg_err}")
                return

            try:
                sent = await message.answer(
                    texts.client_text,
                    parse_mode="HTML",
                    reply_to_message_id=message.message_id,
                    reply_markup=cancel_keyboard(req_id, table_req_id),
                )
            except Exception:
                sent = None

            if sent is not None:
                try:
                    req_index.remember(
                        chat_id=message.chat.id,
                        user_cmd_msg_id=message.message_id,
                        bot_msg_id=sent.message_id,
                        req_id=str(req_id),
                    )
                    await self.repo.upsert_exchange_request_link(
                        client_req_id=str(req_id),
                        table_req_id=str(table_req_id),
                        client_chat_id=sent.chat.id,
                        client_message_id=sent.message_id,
                        table_in_cur=recv_code,
                        table_out_cur=pay_code,
                        table_in_amount=recv_amount,
                        table_out_amount=pay_amount,
                        table_rate=rate,
                        status="active",
                    )
                except Exception:
                    log.exception("Failed to persist exchange request client link %s", req_id)

            if self.request_chat_id:
                try:
                    sent_request = await post_request_message(
                        bot=message.bot,
                        request_chat_id=self.request_chat_id,
                        text=texts.request_text,
                        reply_markup=request_keyboard(
                            in_ccy=recv_code,
                            out_ccy=pay_code,
                            in_amount=recv_amount,
                            out_amount=pay_amount,
                            client_rate=rate_text,
                            req_id=table_req_id,
                        ),
                    )
                    req_index.remember_request_chat_copy(
                        req_id=str(req_id),
                        request_chat_id=sent_request.chat.id,
                        request_message_id=sent_request.message_id,
                        text=texts.request_text,
                    )
                    req_index.remember_table_req_id(
                        req_id=str(req_id),
                        table_req_id=str(table_req_id),
                    )
                    await self.repo.upsert_exchange_request_link(
                        client_req_id=str(req_id),
                        table_req_id=str(table_req_id),
                        request_chat_id=sent_request.chat.id,
                        request_message_id=sent_request.message_id,
                        request_text=texts.request_text,
                        table_in_cur=recv_code,
                        table_out_cur=pay_code,
                        table_in_amount=recv_amount,
                        table_out_amount=pay_amount,
                        table_rate=rate,
                        status="active",
                    )
                    if self.act_counter_service:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=str(req_id),
                            table_req_id=str(table_req_id),
                            request_chat_id=int(sent_request.chat.id),
                            request_message_id=int(sent_request.message_id),
                            movements=create_result.movements,
                        )
                        await self.act_counter_service.apply_request_wallet_movements(
                            req_id=str(req_id),
                            table_req_id=str(table_req_id),
                            request_chat_id=int(sent_request.chat.id),
                            request_message_id=int(sent_request.message_id),
                            movements=create_result.movements,
                            chat_name=getattr(sent_request.chat, "title", None),
                        )
                        await self._notify_act_current_amount(
                            bot=message.bot,
                            request_chat_id=int(sent_request.chat.id),
                        )
                except Exception:
                    log.exception("Failed to post or persist exchange request chat copy %s", req_id)

            accounts2 = await self.repo.snapshot_wallet(client_id)
            compact = format_wallet_compact(accounts2, only_nonzero=True)
            if compact == "Пусто":
                await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
            else:
                safe_title = html.escape(f"Средств у {chat_name}:")
                safe_rows = html.escape(compact)
                await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

        except Exception as e:
            await message.answer(f"Не удалось выполнить операцию: {e}")
