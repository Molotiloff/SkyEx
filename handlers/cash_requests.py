# handlers/cash_requests.py
from __future__ import annotations

import random
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional, Mapping

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest

from db_asyncpg.repo import Repo
from keyboards.request import CB_ISSUE_DONE
from utils.auth import require_manager_or_admin_message
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.request_audit import make_audit_for_new, make_audit_for_edit, audit_lines_for_request_chat

from utils.request_parsing import parse_dep_wd, parse_fx, ParsedRequest
from utils.request_cards import (
    CardDataDepWd,
    CardDataFx,
    build_client_card_dep_wd,
    build_city_card_dep_wd,
    build_client_card_fx,
    build_city_card_fx,
)

# dep/wd команды → (тип, валюта)
CMD_MAP = {
    "депр": ("dep", "RUB"), "депт": ("dep", "USDT"), "депд": ("dep", "USD"),
    "депе": ("dep", "EUR"), "депб": ("dep", "USDW"),
    "выдр": ("wd", "RUB"), "выдт": ("wd", "USDT"), "выдд": ("wd", "USD"),
    "выде": ("wd", "EUR"), "выдб": ("wd", "USDW"),
}

# fx команды → ("fx", in_code, out_code)
FX_CMD_MAP = {
    "првд": ("fx", "RUB", "USD"),
    "пдвр": ("fx", "USD", "RUB"),
    "прве": ("fx", "RUB", "EUR"),
    "певр": ("fx", "EUR", "RUB"),
    "пдве": ("fx", "USD", "EUR"),
    "пбвр": ("fx", "USDW", "RUB"),
}

# --- парсинг старых/новых карточек при редактировании/кнопке ---

# req_id: поддержим и старый числовой, и новый "Б-123456"
_RE_REQ_ID_ANY = re.compile(
    r"^\s*Заявка(?:\s+на\s+(?:внесение|выдачу|обмен))?\s*:\s*(?:<code>)?([A-Za-zА-Яа-я0-9\-]+)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)

_RE_LINE_PIN = re.compile(
    r"^\s*Код:\s*(?:<tg-spoiler>)?(\d{3}-\d{3})(?:</tg-spoiler>)?\s*$",
    re.IGNORECASE | re.M,
)

# dep/wd
_RE_LINE_AMOUNT = re.compile(
    r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M
)

# fx
_RE_LINE_IN = re.compile(
    r"^\s*Принимаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M
)
_RE_LINE_OUT = re.compile(
    r"^\s*Отдаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M
)

# определение типа заявки по карточке
_RE_TITLE_DEP = re.compile(r"^\s*Заявка\s+на\s+внесение\s*:", re.IGNORECASE | re.M)
_RE_TITLE_WD = re.compile(r"^\s*Заявка\s+на\s+выдачу\s*:", re.IGNORECASE | re.M)
_RE_TITLE_FX = re.compile(r"^\s*Заявка\s+на\s+обмен\s*:", re.IGNORECASE | re.M)
_RE_KIND_DEP_LEGACY = re.compile(r"Код\s+получения", re.IGNORECASE)
_RE_KIND_WD_LEGACY = re.compile(r"Код\s+выдачи", re.IGNORECASE)

_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _gen_req_id() -> str:
    # всегда "Б-XXXXXX"
    return f"Б-{random.randint(0, 999999):06d}"


def _gen_pin() -> str:
    return f"{random.randint(100, 999)}-{random.randint(100, 999)}"


def _detect_kind_from_card(text: str) -> Optional[str]:
    if _RE_TITLE_DEP.search(text) or _RE_KIND_DEP_LEGACY.search(text):
        return "dep"
    if _RE_TITLE_WD.search(text) or _RE_KIND_WD_LEGACY.search(text):
        return "wd"
    if _RE_TITLE_FX.search(text):
        return "fx"
    return None


def _parse_amount_code_line(blob: str) -> Optional[tuple[Decimal, str]]:
    """
    '150 000 rub' -> (Decimal('150000'), 'RUB')
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
    Orchestrator:
      - parsing (utils/request_parsing.py)
      - rendering cards (utils/request_cards.py)
      - sending/editing + audit
      - "Выдано" callback only for dep
    """

    def __init__(
        self,
        repo: Repo,
        *,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        request_chat_id: int | None = None,                 # fallback общий чат (если нет city map)
        city_cash_chats: Mapping[str, int] | None = None,   # {"екб": -100..., "члб": -100...}
        default_city: str = "екб",
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.request_chat_id = request_chat_id

        self.city_cash_chats: dict[str, int] = {
            str(k).strip().lower(): int(v) for k, v in (city_cash_chats or {}).items()
        }
        self.default_city = (default_city or "екб").strip().lower()

        self.router = Router()
        self._register()

    def _register(self) -> None:
        cmds = tuple(set(CMD_MAP.keys()) | set(FX_CMD_MAP.keys()))
        self.router.message.register(self._cmd_cash_req, Command(*cmds))
        self.router.callback_query.register(self._cb_issue_done, F.data.startswith(CB_ISSUE_DONE))

    def _pick_request_chat_for_city(self, city: str) -> int | None:
        c = (city or "").strip().lower()
        if c and c in self.city_cash_chats:
            return self.city_cash_chats[c]
        if self.default_city in self.city_cash_chats:
            return self.city_cash_chats[self.default_city]
        return self.request_chat_id

    @staticmethod
    def _split_contacts(kind: str, contact1: str, contact2: str) -> tuple[str, str]:
        """
        Возвращает (tg_from, tg_to)
          tg_from = "Выдает"
          tg_to   = "Принимает"

        dep/fx: contact1=Принимает, contact2=Выдает
        wd    : contact1=Выдает,    contact2=Принимает
        """
        if kind in ("dep", "fx"):
            tg_to = contact1
            tg_from = contact2
        else:
            tg_from = contact1
            tg_to = contact2
        return (tg_from or "").strip(), (tg_to or "").strip()

    def _help_text(self) -> str:
        cities = ", ".join(sorted(self.city_cash_chats.keys())) if self.city_cash_chats else "—"
        return (
            "Форматы:\n"
            "• /депр [город] <сумма/expr> <Принимает> [Выдает] [! комментарий]\n"
            "• /выдр [город] <сумма/expr> <Выдает> [Принимает] [! комментарий]\n"
            "• /првд [город] <сумма_in> <сумма_out> <Принимает(наш)> [Выдает] [! комментарий]\n"
            "• /пдвр [город] <сумма_in> <сумма_out> <Принимает(наш)> [Выдает] [! комментарий]\n"
            "• /прве [город] <сумма_in> <сумма_out> <Принимает(наш)> [Выдает] [! комментарий]\n\n"
            f"Города: {cities}\n"
            f"Если город не указан — по умолчанию: {self.default_city}\n\n"
            "Важно для /прв*: суммы должны быть 2 отдельными токенами.\n"
            "Напр.: /првд члб (700+300) 1000 @petya ! коммент\n\n"
            "Редактирование:\n"
            "• ответьте командой на карточку БОТА — суммы/код/req_id сохранятся;\n"
            "• менять можно город/контакты/комментарий; тип и валюты менять нельзя."
        )

    async def _cmd_cash_req(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        city_keys = set(self.city_cash_chats.keys())

        parsed: Optional[ParsedRequest] = parse_fx(
            message.text or "",
            fx_cmd_map=FX_CMD_MAP,
            city_keys=city_keys,
            default_city=self.default_city,
        )
        if not parsed:
            parsed = parse_dep_wd(
                message.text or "",
                cmd_map=CMD_MAP,
                city_keys=city_keys,
                default_city=self.default_city,
            )
        if not parsed:
            await message.answer(self._help_text())
            return

        reply_msg = getattr(message, "reply_to_message", None)
        is_reply_to_bot = bool(
            reply_msg and reply_msg.from_user and reply_msg.from_user.id == message.bot.id and (reply_msg.text or "")
        )

        if is_reply_to_bot:
            await self._edit_existing_request(
                message=message,
                parsed=parsed,
                old_text=reply_msg.text or "",
                reply_msg_id=reply_msg.message_id,
            )
            return

        await self._create_new_request(message=message, parsed=parsed)

    async def _create_new_request(self, *, message: Message, parsed: ParsedRequest) -> None:
        # validate expressions
        if parsed.kind in ("dep", "wd"):
            try:
                amount_raw = evaluate(parsed.amount_expr)
                if amount_raw <= 0:
                    await message.answer("Сумма должна быть > 0")
                    return
            except (CalcError, InvalidOperation) as e:
                await message.answer(f"Ошибка в выражении суммы: {e}")
                return
        else:  # fx
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

        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=message.chat.id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)

        req_id = _gen_req_id()
        pin_code = _gen_pin()
        tg_from, tg_to = self._split_contacts(parsed.kind, parsed.contact1, parsed.contact2)

        if parsed.kind in ("dep", "wd"):
            acc = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.code), None)
            if not acc:
                await message.answer(f"Счёт {parsed.code} не найден. Добавьте валюту: /добавь {parsed.code} [точность]")
                return

            prec = int(acc.get("precision") or 2)
            q = Decimal(10) ** -prec
            amount = evaluate(parsed.amount_expr).quantize(q).quantize(Decimal("1"))
            pretty_amount = format_amount_core(amount, prec)

            data = CardDataDepWd(
                kind=parsed.kind,
                req_id=req_id,
                city=parsed.city,
                code=parsed.code,
                pretty_amount=pretty_amount,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=parsed.comment,
            )

            text_client, markup = build_client_card_dep_wd(data)
            text_city = build_city_card_dep_wd(
                data,
                chat_name=chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=False,
            )

            await message.answer(text_client, parse_mode="HTML", reply_markup=markup)

        else:
            acc_in = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.in_code), None)
            acc_out = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.out_code), None)
            if not acc_in:
                await message.answer(f"Счёт {parsed.in_code} не найден. Добавьте: /добавь {parsed.in_code} [точность]")
                return
            if not acc_out:
                await message.answer(f"Счёт {parsed.out_code} не найден. Добавьте: /добавь {parsed.out_code} [точность]")
                return

            prec_in = int(acc_in.get("precision") or 2)
            prec_out = int(acc_out.get("precision") or 2)
            q_in = Decimal(10) ** -prec_in
            q_out = Decimal(10) ** -prec_out

            ain = evaluate(parsed.amt_in_expr).quantize(q_in).quantize(Decimal("1"))
            aout = evaluate(parsed.amt_out_expr).quantize(q_out).quantize(Decimal("1"))

            data_fx = CardDataFx(
                req_id=req_id,
                city=parsed.city,
                in_code=parsed.in_code,
                out_code=parsed.out_code,
                pretty_in=format_amount_core(ain, prec_in),
                pretty_out=format_amount_core(aout, prec_out),
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=parsed.comment,
            )

            text_client, markup = build_client_card_fx(data_fx)
            text_city = build_city_card_fx(
                data_fx,
                chat_name=chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=False,
            )

            await message.answer(text_client, parse_mode="HTML", reply_markup=markup)

        req_chat_id = self._pick_request_chat_for_city(parsed.city)
        if req_chat_id:
            try:
                await message.bot.send_message(chat_id=req_chat_id, text=text_city, parse_mode="HTML")
            except Exception:
                pass

    async def _edit_existing_request(
        self,
        *,
        message: Message,
        parsed: ParsedRequest,
        old_text: str,
        reply_msg_id: int,
    ) -> None:
        m_req = _RE_REQ_ID_ANY.search(old_text)
        m_pin = _RE_LINE_PIN.search(old_text)
        if not (m_req and m_pin):
            await message.answer(
                "Не похоже на карточку заявки (не нашёл строки Заявка/Код).\n"
                "Ответьте именно на сообщение БОТА с заявкой."
            )
            return

        old_kind = _detect_kind_from_card(old_text)
        if old_kind and old_kind != parsed.kind:
            await message.answer("Нельзя менять тип заявки при редактировании (деп/выд/обмен).")
            return

        audit = make_audit_for_edit(message, old_text=old_text)

        req_id = m_req.group(1)
        pin_code = m_pin.group(1)

        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=message.chat.id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)

        tg_from, tg_to = self._split_contacts(parsed.kind, parsed.contact1, parsed.contact2)

        if parsed.kind in ("dep", "wd"):
            m_amt = _RE_LINE_AMOUNT.search(old_text)
            if not m_amt:
                await message.answer("Не нашёл строку Сумма в исходной заявке.")
                return

            parsed_old = _parse_amount_code_line(m_amt.group(1))
            if not parsed_old:
                await message.answer("Не удалось распарсить сумму/валюту в исходной заявке.")
                return

            amount_raw_old, code_old = parsed_old
            code_old = code_old.upper()

            if code_old != parsed.code:
                await message.answer(
                    f"Нельзя менять валюту при редактировании.\n"
                    f"В исходной заявке: {code_old}, в команде: {parsed.code}."
                )
                return

            acc = next((r for r in accounts if str(r["currency_code"]).upper() == code_old), None)
            if not acc:
                await message.answer(f"Счёт {code_old} не найден. Добавьте валюту: /добавь {code_old} [точность]")
                return

            prec = int(acc.get("precision") or 2)
            q = Decimal(10) ** -prec
            amount_old = amount_raw_old.quantize(q).quantize(Decimal("1"))
            pretty_amount = format_amount_core(amount_old, prec)

            data = CardDataDepWd(
                kind=parsed.kind,
                req_id=req_id,
                city=parsed.city,
                code=code_old,
                pretty_amount=pretty_amount,
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=parsed.comment,
            )

            text_client, markup = build_client_card_dep_wd(data)
            text_city = build_city_card_dep_wd(
                data,
                chat_name=chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=True,
            )

        else:
            m_in = _RE_LINE_IN.search(old_text)
            m_out = _RE_LINE_OUT.search(old_text)
            if not (m_in and m_out):
                await message.answer("Не нашёл строки Принимаем/Отдаем в исходной FX-заявке.")
                return

            parsed_in = _parse_amount_code_line(m_in.group(1))
            parsed_out = _parse_amount_code_line(m_out.group(1))
            if not parsed_in or not parsed_out:
                await message.answer("Не удалось распарсить суммы/валюты в исходной FX-заявке.")
                return

            amt_in_old, in_code_old = parsed_in
            amt_out_old, out_code_old = parsed_out
            in_code_old = in_code_old.upper()
            out_code_old = out_code_old.upper()

            if in_code_old != parsed.in_code or out_code_old != parsed.out_code:
                await message.answer(
                    "Нельзя менять валюты при редактировании FX-заявки.\n"
                    f"В исходной: {in_code_old}->{out_code_old}, в команде: {parsed.in_code}->{parsed.out_code}."
                )
                return

            acc_in = next((r for r in accounts if str(r["currency_code"]).upper() == in_code_old), None)
            acc_out = next((r for r in accounts if str(r["currency_code"]).upper() == out_code_old), None)
            if not acc_in or not acc_out:
                await message.answer("Не найдены счета для валют FX в кошельке. Добавьте валюты через /добавь ...")
                return

            prec_in = int(acc_in.get("precision") or 2)
            prec_out = int(acc_out.get("precision") or 2)
            q_in = Decimal(10) ** -prec_in
            q_out = Decimal(10) ** -prec_out

            amt_in_old = amt_in_old.quantize(q_in).quantize(Decimal("1"))
            amt_out_old = amt_out_old.quantize(q_out).quantize(Decimal("1"))

            data_fx = CardDataFx(
                req_id=req_id,
                city=parsed.city,
                in_code=in_code_old,
                out_code=out_code_old,
                pretty_in=format_amount_core(amt_in_old, prec_in),
                pretty_out=format_amount_core(amt_out_old, prec_out),
                tg_from=tg_from,
                tg_to=tg_to,
                pin_code=pin_code,
                comment=parsed.comment,
            )

            text_client, markup = build_client_card_fx(data_fx)
            text_city = build_city_card_fx(
                data_fx,
                chat_name=chat_name,
                audit_lines=audit_lines_for_request_chat(audit),
                changed_notice=True,
            )

        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=reply_msg_id,
                text=text_client,
                parse_mode="HTML",
                reply_markup=markup,
            )
        except TelegramBadRequest as e:
            if "message is not modified" not in str(e).lower():
                await message.answer(f"Не удалось отредактировать заявку: {e}")
                return
        except Exception as e:
            await message.answer(f"Не удалось отредактировать заявку: {e}")
            return

        req_chat_id = self._pick_request_chat_for_city(parsed.city)
        if req_chat_id:
            try:
                await message.bot.send_message(chat_id=req_chat_id, text=text_city, parse_mode="HTML")
            except Exception:
                pass

        await message.answer("✅ Заявка обновлена.")

    async def _cb_issue_done(self, cq: CallbackQuery) -> None:
        """
        Кнопка "Выдано" — только для dep.
        """
        msg = cq.message
        if not msg:
            await cq.answer()
            return

        if not await require_manager_or_admin_message(
            self.repo,
            msg,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            await cq.answer("Недостаточно прав.", show_alert=True)
            return

        text = msg.text or ""

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        op_kind: Optional[str] = None
        try:
            parts = (cq.data or "").split(":")
            if len(parts) >= 4 and ":".join(parts[:2]) == CB_ISSUE_DONE:
                op_kind = parts[2].lower()
        except Exception:
            op_kind = None

        if not op_kind:
            op_kind = _detect_kind_from_card(text)

        if op_kind != "dep":
            await cq.answer("Кнопка доступна только для заявок на внесение.", show_alert=True)
            return

        m_amt = _RE_LINE_AMOUNT.search(text)
        if not m_amt:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        parsed_amt = _parse_amount_code_line(m_amt.group(1))
        if not parsed_amt:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        amount_raw, code = parsed_amt
        code = code.upper()

        chat_id = msg.chat.id
        chat_name = get_chat_name(msg)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            await cq.message.answer(f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]")
            await cq.answer()
            return

        prec = int(acc.get("precision") or 2)
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q).quantize(Decimal("1"))

        idem = f"cash:{chat_id}:{msg.message_id}"
        try:
            await self.repo.deposit(
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

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
        prec2 = int(acc2.get("precision") or prec) if acc2 else prec
        pretty_bal = format_amount_core(cur_bal, prec2)

        await cq.message.answer(
            f"Запомнил.\nБаланс: <code>{pretty_bal} {code.lower()}</code>",
            parse_mode="HTML",
        )
        await cq.answer("Отмечено как выдано")