from __future__ import annotations

import random
from decimal import Decimal, InvalidOperation
from typing import Mapping

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from db_asyncpg.repo import Repo
from services.cash_requests.models import RequestContext, ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from utils.calc import CalcError, evaluate
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.request_audit import (
    audit_lines_for_request_chat,
    make_audit_for_edit,
    make_audit_for_new,
)
from utils.request_cards import (
    CardDataDepWd,
    CardDataFx,
    build_city_card_dep_wd,
    build_city_card_fx,
    build_client_card_dep_wd,
    build_client_card_fx,
)
from utils.request_parsing import ParsedRequest, parse_dep_wd, parse_fx
from utils.request_text_parser import (
    extract_edit_source,
    extract_time_from_card,
    parse_dep_wd_snapshot,
    parse_fx_snapshot,
)


class CashRequestService:
    def __init__(
        self,
        *,
        repo: Repo,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        cmd_map: Mapping[str, tuple[str, str]],
        fx_cmd_map: Mapping[str, tuple[str, str, str]],
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.cmd_map = dict(cmd_map)
        self.fx_cmd_map = dict(fx_cmd_map)
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)

    @property
    def supported_commands(self) -> tuple[str, ...]:
        return tuple(set(self.cmd_map.keys()) | set(self.fx_cmd_map.keys()))

    @staticmethod
    def _split_contacts(kind: str, contact1: str, contact2: str) -> tuple[str, str]:
        if kind in ("dep", "fx"):
            tg_to = contact1
            tg_from = contact2
        else:
            tg_from = contact1
            tg_to = contact2
        return (tg_from or "").strip(), (tg_to or "").strip()

    @staticmethod
    def _reply_plain(reply: Message) -> str:
        if reply.caption is not None and not reply.text:
            return reply.caption or ""
        return reply.text or ""

    @staticmethod
    def _gen_req_id() -> str:
        return f"Б-{random.randint(0, 999999):06d}"

    @staticmethod
    def _gen_pin() -> str:
        return f"{random.randint(100, 999)}-{random.randint(100, 999)}"

    @staticmethod
    def _build_schedule_line(
        *,
        kind: str,
        client_name: str,
        pretty_amount: str | None = None,
        code: str | None = None,
        pretty_in: str | None = None,
        in_code: str | None = None,
        pretty_out: str | None = None,
        out_code: str | None = None,
    ) -> str | None:
        client = (client_name or "—").strip() or "—"

        if kind == "dep" and pretty_amount and code:
            return f"+{pretty_amount} {code.upper()} — {client}"

        if kind == "wd" and pretty_amount and code:
            return f"-{pretty_amount} {code.upper()} — {client}"

        if kind == "fx" and pretty_in and in_code and pretty_out and out_code:
            return f"{pretty_in} {in_code.upper()} → {pretty_out} {out_code.upper()} — {client}"

        return None

    async def _build_request_context(self, message: Message, city: str) -> RequestContext:
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=message.chat.id, name=chat_name)
        return RequestContext(
            city=(city or self.router_service.default_city).strip().lower(),
            request_chat_id=self.router_service.pick_request_chat_for_city(city),
            chat_name=chat_name,
            client_id=client_id,
        )

    async def _sync_schedule_without_time(
        self,
        *,
        req_id: str,
        city: str,
        line_text: str,
        request_kind: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
        bot,
    ) -> None:
        if not line_text:
            return

        await self.schedule_service.upsert_entry(
            ScheduleEntry(
                req_id=req_id,
                city=city,
                hhmm=None,
                request_kind=request_kind,
                line_text=line_text,
                client_name=client_name,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
            )
        )

        if self.router_service.pick_schedule_chat_for_city(city):
            try:
                await self.schedule_service.sync_board(
                    bot=bot,
                    city=city,
                )
            except Exception:
                pass

    async def _sync_schedule_keep_existing_time(
        self,
        *,
        req_id: str,
        city: str,
        hhmm: str | None,
        line_text: str,
        request_kind: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
        bot,
    ) -> None:
        if not line_text:
            return

        await self.schedule_service.upsert_entry(
            ScheduleEntry(
                req_id=req_id,
                city=city,
                hhmm=hhmm,
                request_kind=request_kind,
                line_text=line_text,
                client_name=client_name,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
            )
        )

        if self.router_service.pick_schedule_chat_for_city(city):
            try:
                await self.schedule_service.sync_board(
                    bot=bot,
                    city=city,
                )
            except Exception:
                pass

    def help_text(self) -> str:
        cities = ", ".join(sorted(self.router_service.city_keys)) if self.router_service.city_keys else "—"
        return (
            "Форматы:\n"
            "• /депр [город] <сумма/expr> [Принимает] [Выдает] [! комментарий]\n"
            "• /выдр [город] <сумма/expr> [Выдает] [Принимает] [! комментарий]\n"
            "• /првд [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /пдвр [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /прве [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n\n"
            f"Города: {cities}\n"
            f"Если город не указан — по умолчанию: {self.router_service.default_city}\n\n"
            "Редактирование:\n"
            "• ответьте командой на карточку БОТА — можно менять сумму, город, контакты, комментарий;\n"
            "• тип и валюты менять нельзя."
        )

    async def handle(self, message: Message) -> None:
        from utils.auth import require_manager_or_admin_message

        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        parsed: ParsedRequest | None = parse_fx(
            message.text or "",
            fx_cmd_map=self.fx_cmd_map,
            city_keys=self.router_service.city_keys,
            default_city=self.router_service.default_city,
        )
        if not parsed:
            parsed = parse_dep_wd(
                message.text or "",
                cmd_map=self.cmd_map,
                city_keys=self.router_service.city_keys,
                default_city=self.router_service.default_city,
            )
        if not parsed:
            await message.answer(self.help_text())
            return

        reply_msg = getattr(message, "reply_to_message", None)
        is_reply_to_bot = bool(
            reply_msg
            and reply_msg.from_user
            and reply_msg.from_user.id == message.bot.id
            and (reply_msg.text or reply_msg.caption)
        )

        if is_reply_to_bot:
            await self._edit_existing_request(
                message=message,
                parsed=parsed,
                old_text=self._reply_plain(reply_msg),
                reply_msg_id=reply_msg.message_id,
            )
            return

        await self._create_new_request(message=message, parsed=parsed)

    async def _create_new_request(self, *, message: Message, parsed: ParsedRequest) -> None:
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

    async def _edit_existing_request(
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
                await self._sync_schedule_keep_existing_time(
                    req_id=src.req_id,
                    city=ctx.city,
                    hhmm=extract_time_from_card(old_text),
                    line_text=schedule_line,
                    request_kind=parsed.kind,
                    client_name=ctx.chat_name,
                    request_chat_id=sent_city.chat.id,
                    request_message_id=sent_city.message_id,
                    bot=message.bot,
                )

        await message.answer("✅ Заявка обновлена.")