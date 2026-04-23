from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram.types import Message

from services.cash_requests.request_use_case_base import CashRequestUseCaseBase
from utils.calc import CalcError, evaluate
from utils.formatting import format_amount_core
from utils.request_audit import audit_lines_for_request_chat, make_audit_for_new
from utils.request_cards import (
    CardDataDepWd,
    CardDataFx,
    build_city_card_dep_wd,
    build_city_card_fx,
    build_client_card_dep_wd,
    build_client_card_fx,
)
from utils.request_parsing import ParsedRequest


class CreateCashRequest(CashRequestUseCaseBase):
    async def execute(self, *, message: Message, parsed: ParsedRequest) -> None:
        if parsed.kind in ("dep", "wd"):
            try:
                amount_raw = evaluate(parsed.amount_expr)
                if amount_raw <= 0:
                    await message.answer("Сумма должна быть > 0")
                    return
            except (CalcError, InvalidOperation) as e:
                await message.answer(f"Ошибка в выражении суммы: {e}")
                return
        else:
            try:
                ain = evaluate(parsed.amt_in_expr)
                aout = evaluate(parsed.amt_out_expr)
                if ain <= 0 or aout <= 0:
                    await message.answer("Суммы должны быть > 0")
                    return
            except (CalcError, InvalidOperation) as e:
                await message.answer(f"Ошибка в выражении суммы: {e}")
                return

        audit = make_audit_for_new(message)
        ctx = await self._build_request_context(message, parsed.city)
        accounts = await self.repo.snapshot_wallet(ctx.client_id)

        req_id = self._gen_req_id()
        pin_code = self._gen_pin()
        tg_from, tg_to = self._split_contacts(parsed.kind, parsed.contact1, parsed.contact2)

        if parsed.kind in ("dep", "wd"):
            acc = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.code), None)
            if not acc:
                await message.answer(
                    f"Счёт {parsed.code} не найден. Добавьте валюту: /добавь {parsed.code} [точность]"
                )
                return

            prec = int(acc.get("precision") or 2)
            q = Decimal(10) ** -prec
            amount = evaluate(parsed.amount_expr).quantize(q).quantize(Decimal("1"))
            pretty_amount = format_amount_core(amount, prec)

            data = CardDataDepWd(
                kind=parsed.kind,
                req_id=req_id,
                city=ctx.city,
                code=parsed.code,
                pretty_amount=pretty_amount,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=parsed.comment,
            )
            text_client, _client_markup = build_client_card_dep_wd(data)
            text_city, city_markup = build_city_card_dep_wd(
                data,
                chat_name=ctx.chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=False,
            )
            schedule_line = self._build_schedule_line(
                kind=parsed.kind,
                client_name=ctx.chat_name,
                pretty_amount=pretty_amount,
                code=parsed.code,
            )
        else:
            acc_in = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.in_code), None)
            acc_out = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.out_code), None)
            if not acc_in:
                await message.answer(
                    f"Счёт {parsed.in_code} не найден. Добавьте: /добавь {parsed.in_code} [точность]"
                )
                return
            if not acc_out:
                await message.answer(
                    f"Счёт {parsed.out_code} не найден. Добавьте: /добавь {parsed.out_code} [точность]"
                )
                return

            prec_in = int(acc_in.get("precision") or 2)
            prec_out = int(acc_out.get("precision") or 2)
            q_in = Decimal(10) ** -prec_in
            q_out = Decimal(10) ** -prec_out
            ain = evaluate(parsed.amt_in_expr).quantize(q_in).quantize(Decimal("1"))
            aout = evaluate(parsed.amt_out_expr).quantize(q_out).quantize(Decimal("1"))

            pretty_in = format_amount_core(ain, prec_in)
            pretty_out = format_amount_core(aout, prec_out)

            data_fx = CardDataFx(
                req_id=req_id,
                city=ctx.city,
                in_code=parsed.in_code,
                out_code=parsed.out_code,
                pretty_in=pretty_in,
                pretty_out=pretty_out,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=parsed.comment,
            )
            text_client, _client_markup = build_client_card_fx(data_fx)
            text_city, city_markup = build_city_card_fx(
                data_fx,
                chat_name=ctx.chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=False,
            )
            schedule_line = self._build_schedule_line(
                kind="fx",
                client_name=ctx.chat_name,
                pretty_in=pretty_in,
                in_code=parsed.in_code,
                pretty_out=pretty_out,
                out_code=parsed.out_code,
            )

        await message.answer(text_client, parse_mode="HTML", reply_markup=None)

        if ctx.request_chat_id:
            try:
                sent_city = await message.bot.send_message(
                    chat_id=ctx.request_chat_id,
                    text=text_city,
                    parse_mode="HTML",
                    reply_markup=city_markup,
                )
            except Exception:
                sent_city = None

            if sent_city and schedule_line:
                await self._sync_schedule_without_time(
                    req_id=req_id,
                    city=ctx.city,
                    line_text=schedule_line,
                    request_kind=parsed.kind,
                    client_name=ctx.chat_name,
                    request_chat_id=sent_city.chat.id,
                    request_message_id=sent_city.message_id,
                    bot=message.bot,
                )
