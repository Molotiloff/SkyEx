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
    # RUB - USD
    "првд": ("fx", "RUB", "USD"),
    "пдвр": ("fx", "USD", "RUB"),

    # RUB - EUR
    "прве": ("fx", "RUB", "EUR"),
    "певр": ("fx", "EUR", "RUB"),

    # RUB - USDW
    "првб": ("fx", "RUB", "USDW"),
    "пбвр": ("fx", "USDW", "RUB"),

    # RUB - EUR500
    "првп": ("fx", "RUB", "EUR500"),
    "ппвр": ("fx", "EUR500", "RUB"),

    # USD - EUR
    "пдве": ("fx", "USD", "EUR"),
    "певд": ("fx", "EUR", "USD"),

    # USD - USDW
    "пдвб": ("fx", "USD", "USDW"),
    "пбвд": ("fx", "USDW", "USD"),

    # USD - EUR500
    "пдвп": ("fx", "USD", "EUR500"),
    "ппвд": ("fx", "EUR500", "USD"),

    # EUR - USDW
    "певб": ("fx", "EUR", "USDW"),
    "пбве": ("fx", "USDW", "EUR"),

    # EUR - EUR500
    "певп": ("fx", "EUR", "EUR500"),
    "ппве": ("fx", "EUR500", "EUR"),

    # USDW - EUR500
    "пбвп": ("fx", "USDW", "EUR500"),
    "ппвб": ("fx", "EUR500", "USDW"),
}

# --- парсинг карточек при редактировании/кнопке/времени (PLAIN-текст) ---

_RE_REQ_ID_ANY = re.compile(
    r"^\s*Заявка(?:\s+на\s+(?:внесение|выдачу|обмен))?\s*:\s*(?:<code>)?([A-Za-zА-Яа-я0-9\-]+)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)
_RE_LINE_PIN = re.compile(
    r"^\s*Код:\s*(?:<tg-spoiler>)?(\d{3}-\d{3})(?:</tg-spoiler>)?\s*$",
    re.IGNORECASE | re.M,
)

# dep/wd
_RE_LINE_AMOUNT = re.compile(r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M)

# fx
_RE_LINE_IN = re.compile(r"^\s*Принимаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M)
_RE_LINE_OUT = re.compile(
    r"^\s*(?:Отдаем|Выдаем):\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)

# определение типа заявки по карточке (plain)
_RE_TITLE_DEP = re.compile(r"^\s*Заявка\s+на\s+внесение\s*:", re.IGNORECASE | re.M)
_RE_TITLE_WD = re.compile(r"^\s*Заявка\s+на\s+выдачу\s*:", re.IGNORECASE | re.M)
_RE_TITLE_FX = re.compile(r"^\s*Заявка\s+на\s+обмен\s*:", re.IGNORECASE | re.M)
_RE_KIND_DEP_LEGACY = re.compile(r"Код\s+получения", re.IGNORECASE)
_RE_KIND_WD_LEGACY = re.compile(r"Код\s+выдачи", re.IGNORECASE)

_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}

# /время 10:00
_RE_TIME_CMD = re.compile(r"^/время(?:@\w+)?\s+([0-2]\d:[0-5]\d)\s*$", re.IGNORECASE)
# строка времени (в HTML-карточке)
_RE_LINE_TIME = re.compile(
    r"^\s*Время\s*:\s*(?:<code>)?([0-2]\d:[0-5]\d)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)
# проверяем только "Заявка" в начале (допускаем HTML теги перед словом)
_RE_STARTS_WITH_ZAYAVKA = re.compile(r"^\s*(?:<[^>]+>\s*)*Заявка\b", re.IGNORECASE)


def _is_request_chat(chat_id: int, *, city_cash_chats: dict[str, int], fallback_request_chat_id: int | None) -> bool:
    if chat_id in set(city_cash_chats.values()):
        return True
    return bool(fallback_request_chat_id and chat_id == fallback_request_chat_id)


def _upsert_time_line(card_text: str, hhmm: str) -> str:
    """
    Добавляет/заменяет 'Время: <code>HH:MM</code>' в карточке (HTML-текст).
    Вставка:
      1) если уже есть строка времени — заменяем
      2) иначе вставляем перед '\\n----\\nСоздал:' если есть
      3) иначе — перед последним '\\n----' если он есть
      4) иначе — в конец
    """
    text = card_text or ""
    new_line = f"Время: <code>{hhmm}</code>"

    if _RE_LINE_TIME.search(text):
        return _RE_LINE_TIME.sub(new_line, text)

    mk = "\n----\nСоздал:"
    idx = text.find(mk)
    if idx != -1:
        return text[:idx] + "\n" + new_line + text[idx:]

    last_sep = text.rfind("\n----")
    if last_sep != -1:
        return text[:last_sep] + "\n" + new_line + text[last_sep:]

    if text.endswith("\n"):
        return text + new_line
    return text + "\n" + new_line


def _gen_req_id() -> str:
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
    '10’000.00 rub' -> (Decimal('10000.00'), 'RUB')
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


def _reply_plain(reply: Message) -> str:
    """
    ТОЛЬКО plain текст для парсинга (regex ожидают строки без <b>...</b>).
    """
    if reply.caption is not None and not reply.text:
        return reply.caption or ""
    return reply.text or ""


def _reply_html(reply: Message) -> tuple[str, bool]:
    """
    HTML-текст для редактирования без потери тегов.
    Возвращает (content, is_caption)
    """
    if reply.caption is not None and not reply.text:
        return (reply.html_caption or reply.caption or ""), True
    return (reply.html_text or reply.text or ""), False


class CashRequestsHandler:
    """
    Orchestrator:
      - parsing (utils/request_parsing.py)
      - rendering cards (utils/request_cards.py)
      - sending/editing + audit
      - "Выдано" callback only for dep
      - /время HH:MM в чатах заявок: редактирует карточку без потери HTML-тегов
        (проверяем только, что сообщение начинается со слова "Заявка")
    """

    def __init__(
        self,
        repo: Repo,
        *,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        request_chat_id: int | None = None,
        city_cash_chats: Mapping[str, int] | None = None,
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
        self.router.message.register(self._cmd_set_time, Command("время"))
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

        dep/fx: contact1=Принимает(наш), contact2=Выдает/клиент
        wd    : contact1=Выдает,        contact2=Принимает
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
            "• менять можно город/контакты/комментарий; тип и валюты менять нельзя.\n\n"
            "В чатах заявок:\n"
            "• ответьте на карточку командой /время 10:00 — добавит/заменит строку времени."
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
            reply_msg
            and reply_msg.from_user
            and reply_msg.from_user.id == message.bot.id
            and (reply_msg.text or reply_msg.caption)
        )

        if is_reply_to_bot:
            # ВАЖНО: редактирование заявки парсим только PLAIN,
            # иначе <b>Сумма</b> сломает regex
            old_text = _reply_plain(reply_msg)
            await self._edit_existing_request(
                message=message,
                parsed=parsed,
                old_text=old_text,
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
                await message.answer("Не нашёл строки Принимаем/(Отдаем|Выдаем) в исходной FX-заявке.")
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
            if len(parts) >= 3 and ":".join(parts[:2]) == CB_ISSUE_DONE:
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

    async def _cmd_set_time(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        if not message.chat:
            return
        if not _is_request_chat(
            message.chat.id,
            city_cash_chats=self.city_cash_chats,
            fallback_request_chat_id=self.request_chat_id,
        ):
            return  # молча

        raw = (message.text or "").strip()
        m = _RE_TIME_CMD.match(raw)
        if not m:
            await message.answer("Формат: /время 10:00")
            return
        hhmm = m.group(1)

        reply = getattr(message, "reply_to_message", None)
        if not reply:
            await message.answer("Нужно ответить командой /время на сообщение с заявкой.")
            return

        target_text, is_caption = _reply_html(reply)
        if not target_text.strip():
            await message.answer("Нужно ответить на сообщение с текстом.")
            return

        # теперь проверяем только по первому слову "Заявка"
        if not _RE_STARTS_WITH_ZAYAVKA.search(target_text):
            await message.answer("Это не похоже на заявку (сообщение должно начинаться со слова «Заявка»).")
            return

        updated = _upsert_time_line(target_text, hhmm)

        try:
            if is_caption:
                await message.bot.edit_message_caption(
                    chat_id=reply.chat.id,
                    message_id=reply.message_id,
                    caption=updated,
                    parse_mode="HTML",
                    reply_markup=reply.reply_markup,
                )
            else:
                await message.bot.edit_message_text(
                    chat_id=reply.chat.id,
                    message_id=reply.message_id,
                    text=updated,
                    parse_mode="HTML",
                    reply_markup=reply.reply_markup,
                )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                await message.answer("Время уже установлено.")
                return
            await message.answer(f"Не удалось обновить заявку: {e}")
            return
        except Exception as e:
            await message.answer(f"Не удалось обновить заявку: {e}")
            return

        await message.answer(f"✅ Время добавлено: {hhmm}")