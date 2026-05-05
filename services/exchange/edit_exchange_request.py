from __future__ import annotations

import html
import logging
from datetime import datetime
from decimal import Decimal

from aiogram.types import Message

from keyboards import request_keyboard
from services.exchange.card_parser import extract_created_by, extract_request_id
from services.exchange.keyboards import cancel_keyboard, request_chat_keyboard
from services.exchange.use_case_base import _ExchangeUseCaseBase
from utils.format_wallet_compact import format_wallet_compact
from utils.info import get_chat_name
from utils.req_index import req_index
from services.cash_requests import post_request_message

log = logging.getLogger(__name__)


class EditExchangeRequest(_ExchangeUseCaseBase):
    async def execute(
        self,
        *,
        message: Message,
        recv_code: str,
        pay_code: str,
        recv_amount: Decimal,
        pay_amount: Decimal,
        recv_prec: int,
        pay_prec: int,
        rate_str: str,
        user_note: str | None,
        recv_is_deposit: bool,
        pay_is_withdraw: bool,
    ) -> bool:
        reply_msg = getattr(message, "reply_to_message", None)
        if not (reply_msg and (reply_msg.text or "")):
            return False

        if reply_msg.from_user and reply_msg.from_user.id == message.bot.id:
            edit_req_id = extract_request_id(reply_msg.text or "")
            if not edit_req_id:
                await message.answer(
                    "Это сообщение бота не похоже на карточку заявки.\n"
                    "Чтобы изменить и пересчитать баланс, ответьте на сообщение БОТА с заявкой."
                )
                return True
            target_bot_msg_id = reply_msg.message_id
        else:
            link = req_index.lookup(message.chat.id, reply_msg.message_id)
            if link is not None:
                await message.answer("Пожалуйста, ответьте на сообщение БОТА с карточкой заявки.")
                return True
            if extract_request_id(reply_msg.text or ""):
                await message.answer(
                    "Похоже, вы ответили на пересланную/чужую карточку.\n"
                    "Ответьте на оригинальное сообщение БОТА с заявкой."
                )
                return True
            await message.answer("Чтобы изменить заявку, ответьте на сообщение БОТА с карточкой заявки.")
            return True

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)

        creator_name: str | None = extract_created_by(reply_msg.text or "")
        if not creator_name:
            user = getattr(message, "from_user", None)
            if user:
                creator_name = (
                    getattr(user, "full_name", None)
                    or (f"@{user.username}" if getattr(user, "username", None) else None)
                    or f"id:{user.id}"
                )
        creator_name = creator_name or "unknown"

        meta = await self._get_exchange_request_meta(str(edit_req_id))
        table_req_id = (
            req_index.get_table_req_id(str(edit_req_id))
            or (str(meta["table_req_id"]) if meta and meta.get("table_req_id") else None)
            or str(edit_req_id)
        )

        changed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        single_request_chat_card = bool(self.request_chat_id and int(chat_id) == int(self.request_chat_id))
        tracked_currency_codes = {"USDT"} if single_request_chat_card else None
        new_client_text = self.text_builder.build_client_text(
            req_id=edit_req_id,
            recv_code=recv_code,
            recv_amount=recv_amount,
            recv_prec=recv_prec,
            pay_code=pay_code,
            pay_amount=pay_amount,
            pay_prec=pay_prec,
            rate=rate_str,
            note=user_note,
            changed_at=changed_at,
        )

        applied_movements = []
        try:
            applied_movements = await self.balance_service.apply_edit_delta(
                client_id=client_id,
                old_request_text=reply_msg.text or "",
                recv_code_new=recv_code,
                pay_code_new=pay_code,
                recv_amount_new=recv_amount,
                pay_amount_new=pay_amount,
                recv_prec=recv_prec,
                pay_prec=pay_prec,
                chat_id=chat_id,
                target_bot_msg_id=target_bot_msg_id,
                cmd_msg_id=message.message_id,
                recv_is_deposit=recv_is_deposit,
                pay_is_withdraw=pay_is_withdraw,
                tracked_currency_codes=tracked_currency_codes,
            )
        except Exception as e:
            await message.answer(f"Не удалось пересчитать балансы: {e}")

        if single_request_chat_card:
            request_text = self.text_builder.build_request_text(
                req_id=edit_req_id,
                table_req_id=table_req_id,
                client_name=chat_name,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_prec=recv_prec,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_prec=pay_prec,
                rate=rate_str,
                creator_name=creator_name,
                note=user_note,
                changed_at=changed_at,
            )
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=target_bot_msg_id,
                    text=request_text,
                    parse_mode="HTML",
                    reply_markup=request_chat_keyboard(req_id=edit_req_id, table_req_id=table_req_id),
                )
            except Exception as e:
                await message.answer(f"Не удалось изменить заявку: {e}")
                return True

            req_index.remember_request_chat_copy(
                req_id=str(edit_req_id),
                request_chat_id=message.chat.id,
                request_message_id=target_bot_msg_id,
                text=request_text,
            )
            await self.repo.upsert_exchange_request_link(
                client_req_id=str(edit_req_id),
                table_req_id=str(table_req_id),
                client_chat_id=message.chat.id,
                client_message_id=target_bot_msg_id,
                request_chat_id=message.chat.id,
                request_message_id=target_bot_msg_id,
                request_text=request_text,
                table_in_cur=recv_code,
                table_out_cur=pay_code,
                table_in_amount=recv_amount,
                table_out_amount=pay_amount,
                table_rate=rate_str.replace(",", "."),
            )
            if self.act_counter_service and applied_movements:
                await self.act_counter_service.register_exchange_movements(
                    req_id=str(edit_req_id),
                    table_req_id=str(table_req_id),
                    request_chat_id=int(message.chat.id),
                    request_message_id=int(target_bot_msg_id),
                    movements=applied_movements,
                )
            if self.act_counter_service:
                await self._notify_act_current_amount(
                    bot=message.bot,
                    request_chat_id=int(message.chat.id),
                )
        else:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=target_bot_msg_id,
                    text=new_client_text,
                    parse_mode="HTML",
                    reply_markup=cancel_keyboard(edit_req_id, table_req_id),
                )
            except Exception as e:
                await message.answer(f"Не удалось изменить заявку: {e}")
                return True

        if single_request_chat_card:
            try:
                await post_request_message(
                    message.bot,
                    message.chat.id,
                    f"✏️ Заявка <code>{html.escape(str(edit_req_id))}</code> была изменена",
                    reply_markup=None,
                )
            except Exception:
                log.exception("Failed to post edit notification for exchange request %s", edit_req_id)

        if self.request_chat_id and not single_request_chat_card:
            request_text = self.text_builder.build_request_text(
                req_id=edit_req_id,
                table_req_id=table_req_id,
                client_name=chat_name,
                recv_code=recv_code,
                recv_amount=recv_amount,
                recv_prec=recv_prec,
                pay_code=pay_code,
                pay_amount=pay_amount,
                pay_prec=pay_prec,
                rate=rate_str,
                creator_name=creator_name,
                note=user_note,
                changed_at=changed_at,
            )
            try:
                request_copy = req_index.get_request_chat_copy(str(edit_req_id))
                if request_copy is None and meta and meta.get("request_chat_id") and meta.get("request_message_id"):
                    request_copy = (int(meta["request_chat_id"]), int(meta["request_message_id"]))
                if request_copy is not None:
                    request_chat_id, request_message_id = request_copy
                    await message.bot.edit_message_text(
                        chat_id=request_chat_id,
                        message_id=request_message_id,
                        text=request_text,
                        parse_mode="HTML",
                        reply_markup=request_keyboard(
                            in_ccy=recv_code,
                            out_ccy=pay_code,
                            in_amount=recv_amount,
                            out_amount=pay_amount,
                            client_rate=rate_str,
                            req_id=table_req_id,
                        ),
                    )
                    req_index.remember_request_chat_copy(
                        req_id=str(edit_req_id),
                        request_chat_id=request_chat_id,
                        request_message_id=request_message_id,
                        text=request_text,
                    )
                    await self.repo.upsert_exchange_request_link(
                        client_req_id=str(edit_req_id),
                        table_req_id=str(table_req_id),
                        request_chat_id=request_chat_id,
                        request_message_id=request_message_id,
                        request_text=request_text,
                        table_in_cur=recv_code,
                        table_out_cur=pay_code,
                        table_in_amount=recv_amount,
                        table_out_amount=pay_amount,
                        table_rate=rate_str.replace(",", "."),
                    )
                    if self.act_counter_service and applied_movements:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=str(edit_req_id),
                            table_req_id=str(table_req_id),
                            request_chat_id=int(request_chat_id),
                            request_message_id=int(request_message_id),
                            movements=applied_movements,
                        )
                        if int(message.chat.id) != int(request_chat_id):
                            await self.act_counter_service.apply_request_wallet_movements(
                                req_id=str(edit_req_id),
                                table_req_id=str(table_req_id),
                                request_chat_id=int(request_chat_id),
                                request_message_id=int(request_message_id),
                                movements=applied_movements,
                            )
                    if self.act_counter_service:
                        await self._notify_act_current_amount(
                            bot=message.bot,
                            request_chat_id=int(request_chat_id),
                        )
                else:
                    sent_request = await post_request_message(
                        message.bot,
                        self.request_chat_id,
                        request_text,
                        reply_markup=request_keyboard(
                            in_ccy=recv_code,
                            out_ccy=pay_code,
                            in_amount=recv_amount,
                            out_amount=pay_amount,
                            client_rate=rate_str,
                            req_id=table_req_id,
                        ),
                    )
                    req_index.remember_request_chat_copy(
                        req_id=str(edit_req_id),
                        request_chat_id=sent_request.chat.id,
                        request_message_id=sent_request.message_id,
                        text=request_text,
                    )
                    req_index.remember_table_req_id(
                        req_id=str(edit_req_id),
                        table_req_id=str(table_req_id),
                    )
                    await self.repo.upsert_exchange_request_link(
                        client_req_id=str(edit_req_id),
                        table_req_id=str(table_req_id),
                        request_chat_id=sent_request.chat.id,
                        request_message_id=sent_request.message_id,
                        request_text=request_text,
                        table_in_cur=recv_code,
                        table_out_cur=pay_code,
                        table_in_amount=recv_amount,
                        table_out_amount=pay_amount,
                        table_rate=rate_str.replace(",", "."),
                    )
                    if self.act_counter_service and applied_movements:
                        await self.act_counter_service.register_exchange_movements(
                            req_id=str(edit_req_id),
                            table_req_id=str(table_req_id),
                            request_chat_id=int(sent_request.chat.id),
                            request_message_id=int(sent_request.message_id),
                            movements=applied_movements,
                        )
                        if int(message.chat.id) != int(sent_request.chat.id):
                            await self.act_counter_service.apply_request_wallet_movements(
                                req_id=str(edit_req_id),
                                table_req_id=str(table_req_id),
                                request_chat_id=int(sent_request.chat.id),
                                request_message_id=int(sent_request.message_id),
                                movements=applied_movements,
                                chat_name=getattr(sent_request.chat, "title", None),
                            )
                    if self.act_counter_service:
                        await self._notify_act_current_amount(
                            bot=message.bot,
                            request_chat_id=int(sent_request.chat.id),
                        )
                await post_request_message(
                    message.bot,
                    self.request_chat_id,
                    f"✏️ Заявка <code>{html.escape(str(edit_req_id))}</code> была изменена",
                    reply_markup=None,
                )
            except Exception:
                log.exception("Failed to update request chat copy for edited exchange request %s", edit_req_id)

        if not single_request_chat_card:
            rows = await self.repo.snapshot_wallet(client_id)
            compact = format_wallet_compact(rows, only_nonzero=True)
            if compact == "Пусто":
                await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
            else:
                safe_title = html.escape(f"Средств у {chat_name}:")
                safe_rows = html.escape(compact)
                await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

        return True
