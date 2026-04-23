from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from services.cash_requests.request_use_case_base import CashRequestUseCaseBase
from utils.calc import CalcError, evaluate
from utils.formatting import format_amount_core
from utils.request_audit import audit_lines_for_request_chat, make_audit_for_edit
from utils.request_cards import (
    CardDataDepWd,
    CardDataFx,
    build_city_card_dep_wd,
    build_city_card_fx,
    build_client_card_dep_wd,
    build_client_card_fx,
)
from utils.request_parsing import ParsedRequest
from utils.request_text_parser import (
    extract_edit_source,
    extract_time_from_card,
    parse_dep_wd_snapshot,
    parse_fx_snapshot,
)


class EditCashRequest(CashRequestUseCaseBase):
    async def execute(
        self,
        *,
        message: Message,
        parsed: ParsedRequest,
        old_text: str,
        reply_msg_id: int,
    ) -> None:
        src = extract_edit_source(old_text)
        if not src:
            await message.answer("Не похоже на карточку заявки.")
            return

        if src.kind != parsed.kind:
            await message.answer("Нельзя менять тип заявки при редактировании (деп/выд/обмен).")
            return

        audit = make_audit_for_edit(message, old_text=old_text)
        ctx = await self._build_request_context(message, parsed.city)
        accounts = await self.repo.snapshot_wallet(ctx.client_id)
        tg_from, tg_to = self._split_contacts(parsed.kind, parsed.contact1, parsed.contact2)

        if parsed.kind in ("dep", "wd"):
            snap = parse_dep_wd_snapshot(old_text, city=ctx.city)
            if not snap:
                await message.answer("Не удалось распарсить исходную заявку.")
                return

            if snap.code != parsed.code:
                await message.answer(
                    f"Нельзя менять валюту при редактировании.\n"
                    f"В исходной заявке: {snap.code}, в команде: {parsed.code}."
                )
                return

            acc = next((r for r in accounts if str(r["currency_code"]).upper() == snap.code), None)
            if not acc:
                await message.answer(
                    f"Счёт {snap.code} не найден. Добавьте валюту: /добавь {snap.code} [точность]"
                )
                return

            try:
                amount_raw_new = evaluate(parsed.amount_expr)
                if amount_raw_new <= 0:
                    await message.answer("Сумма должна быть > 0")
                    return
            except (CalcError, InvalidOperation) as e:
                await message.answer(f"Ошибка в выражении суммы: {e}")
                return

            prec = int(acc.get("precision") or 2)
            q = Decimal(10) ** -prec
            amount_new = amount_raw_new.quantize(q).quantize(Decimal("1"))
            pretty_amount = format_amount_core(amount_new, prec)

            data = CardDataDepWd(
                kind=parsed.kind,
                req_id=src.req_id,
                city=ctx.city,
                code=snap.code,
                pretty_amount=pretty_amount,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=src.pin_code,
                comment=parsed.comment,
            )
            text_client, _client_markup = build_client_card_dep_wd(data)
            text_city, city_markup = build_city_card_dep_wd(
                data,
                chat_name=ctx.chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=True,
            )
            schedule_line = self._build_schedule_line(
                kind=parsed.kind,
                client_name=ctx.chat_name,
                pretty_amount=pretty_amount,
                code=snap.code,
            )
        else:
            snap = parse_fx_snapshot(old_text, city=ctx.city)
            if not snap:
                await message.answer("Не удалось распарсить исходную FX-заявку.")
                return

            if snap.in_code != parsed.in_code or snap.out_code != parsed.out_code:
                await message.answer(
                    "Нельзя менять валюты при редактировании FX-заявки.\n"
                    f"В исходной: {snap.in_code}->{snap.out_code}, в команде: {parsed.in_code}->{parsed.out_code}."
                )
                return

            acc_in = next((r for r in accounts if str(r["currency_code"]).upper() == snap.in_code), None)
            acc_out = next((r for r in accounts if str(r["currency_code"]).upper() == snap.out_code), None)
            if not acc_in or not acc_out:
                await message.answer("Не найдены счета для валют FX в кошельке. Добавьте валюты через /добавь ...")
                return

            try:
                ain_raw_new = evaluate(parsed.amt_in_expr)
                aout_raw_new = evaluate(parsed.amt_out_expr)
                if ain_raw_new <= 0 or aout_raw_new <= 0:
                    await message.answer("Суммы должны быть > 0")
                    return
            except (CalcError, InvalidOperation) as e:
                await message.answer(f"Ошибка в выражении суммы: {e}")
                return

            prec_in = int(acc_in.get("precision") or 2)
            prec_out = int(acc_out.get("precision") or 2)
            q_in = Decimal(10) ** -prec_in
            q_out = Decimal(10) ** -prec_out
            ain_new = ain_raw_new.quantize(q_in).quantize(Decimal("1"))
            aout_new = aout_raw_new.quantize(q_out).quantize(Decimal("1"))

            pretty_in = format_amount_core(ain_new, prec_in)
            pretty_out = format_amount_core(aout_new, prec_out)

            data_fx = CardDataFx(
                req_id=src.req_id,
                city=ctx.city,
                in_code=snap.in_code,
                out_code=snap.out_code,
                pretty_in=pretty_in,
                pretty_out=pretty_out,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=src.pin_code,
                comment=parsed.comment,
            )
            text_client, _client_markup = build_client_card_fx(data_fx)
            text_city, city_markup = build_city_card_fx(
                data_fx,
                chat_name=ctx.chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=True,
            )
            schedule_line = self._build_schedule_line(
                kind="fx",
                client_name=ctx.chat_name,
                pretty_in=pretty_in,
                in_code=snap.in_code,
                pretty_out=pretty_out,
                out_code=snap.out_code,
            )

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=reply_msg_id,
                text=text_client,
                parse_mode="HTML",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                await message.answer(f"Не удалось отредактировать заявку: {e}")
                return
        except Exception as e:
            await message.answer(f"Не удалось отредактировать заявку: {e}")
            return

        if ctx.request_chat_id:
            edited_msg_ref = await self._edit_request_chat_message(
                bot=message.bot,
                req_id=src.req_id,
                text_city=text_city,
                city_markup=city_markup,
            )

            sent_chat_id: int | None = None
            sent_message_id: int | None = None

            if edited_msg_ref:
                sent_chat_id, sent_message_id = edited_msg_ref
            else:
                try:
                    sent_city = await message.bot.send_message(
                        chat_id=ctx.request_chat_id,
                        text=text_city,
                        parse_mode="HTML",
                        reply_markup=city_markup,
                    )
                    sent_chat_id = int(sent_city.chat.id)
                    sent_message_id = int(sent_city.message_id)
                except Exception:
                    sent_chat_id = None
                    sent_message_id = None

            if sent_chat_id and sent_message_id and schedule_line:
                await self._sync_schedule_keep_existing_time(
                    req_id=src.req_id,
                    city=ctx.city,
                    hhmm=extract_time_from_card(old_text),
                    line_text=schedule_line,
                    request_kind=parsed.kind,
                    client_name=ctx.chat_name,
                    request_chat_id=sent_chat_id,
                    request_message_id=sent_message_id,
                    bot=message.bot,
                )

        await message.answer("✅ Заявка обновлена.")
