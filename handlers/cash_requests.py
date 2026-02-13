# handlers/cash_requests.py
from __future__ import annotations

import html
import random
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Tuple, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from db_asyncpg.repo import Repo
from keyboards.request import CB_ISSUE_DONE
from utils.auth import require_manager_or_admin_message
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core
from utils.info import get_chat_name
from utils.request_audit import (
    make_audit_for_new,
    make_audit_for_edit,
    audit_lines_for_request_chat,
    audit_lines_for_client_card,
)

# команды → (тип, валюта)
CMD_MAP = {
    "депр": ("dep", "RUB"), "депт": ("dep", "USDT"), "депд": ("dep", "USD"),
    "депе": ("dep", "EUR"), "депб": ("dep", "USDW"),
    "выдр": ("wd", "RUB"), "выдт": ("wd", "USDT"), "выдд": ("wd", "USD"),
    "выде": ("wd", "EUR"), "выдб": ("wd", "USDW"),
}

# Участник: @telegram или +телефон (6–15 цифр)
PART = r"(?:@[A-Za-z0-9_]{2,}|\+\d{6,15})"

# Формат:
# /депр|... <amount_expr> <contact1> [contact2] [! comment]
RE_CMD = re.compile(
    rf"""^/(депр|депт|депд|депе|депб|выдр|выдт|выдд|выде|выдб)(?:@\w+)?   # команда
         \s+(.+?)                                                         # сумма/expr
         \s+({PART})                                                      # contact1
         (?:\s+({PART}))?                                                 # [contact2]
         (?:\s*!\s*(.+))?                                                 # [! комментарий]
         \s*$""",
    flags=re.IGNORECASE | re.UNICODE | re.VERBOSE,
)

# "Сумма: <code>150 000 rub</code>"
_RE_LINE_AMOUNT = re.compile(r"^\s*Сумма:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.IGNORECASE | re.M)

# Номер заявки (поддерживаем и старый "Заявка:" и новый "Заявка на внесение/выдачу:")
_RE_REQ_ID = re.compile(
    r"^\s*Заявка(?:\s+на\s+(?:внесение|выдачу))?\s*:\s*(?:<code>)?(\d+)(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)

# Код: может быть со spoiler или без
_RE_LINE_PIN = re.compile(
    r"^\s*Код:\s*(?:<tg-spoiler>)?(\d{3}-\d{3})(?:</tg-spoiler>)?\s*$",
    re.IGNORECASE | re.M,
)

# legacy-маркеры
_RE_KIND_DEP_LEGACY = re.compile(r"Код\s+получения", re.IGNORECASE)
_RE_KIND_WD_LEGACY = re.compile(r"Код\s+выдачи", re.IGNORECASE)

_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _parse_amount_code(blob: str) -> Optional[Tuple[Decimal, str]]:
    """'150 000 rub' → (Decimal('150000'), 'RUB')"""
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


def _issue_keyboard_with_kind(kind: str, req_id: int) -> InlineKeyboardMarkup:
    cb = f"{CB_ISSUE_DONE}:{kind}:{req_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Выдано", callback_data=cb)]]
    )


def _req_title(kind: str) -> str:
    # kind: "dep" | "wd"
    return "Заявка на внесение" if kind == "dep" else "Заявка на выдачу"


class CashRequestsHandler:
    """
    Универсальные заявки наличных:
      /депр|депт|депд|депе|депб <сумма/expr> <контакт1> [контакт2] [! комментарий]
      /выдр|выдт|выдд|выде|выдб <сумма/expr> <контакт1> [контакт2] [! комментарий]

    Семантика контактов:
      • dep*: contact1 = Принимает, contact2 = Выдает (опционально)
      • wd*:  contact1 = Выдает,    contact2 = Принимает (опционально)
    """

    def __init__(
        self,
        repo: Repo,
        *,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        request_chat_id: int | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.request_chat_id = request_chat_id
        self.router = Router()
        self._register()

    def _register(self) -> None:
        cmds = tuple(CMD_MAP.keys())
        self.router.message.register(self._cmd_cash_req, Command(*cmds))
        self.router.message.register(self._cmd_cash_req, F.text.regexp(RE_CMD.pattern))
        self.router.callback_query.register(self._cb_issue_done, F.data.startswith(CB_ISSUE_DONE))

    @staticmethod
    def _split_contacts(kind: str, contact1: str, contact2: str) -> tuple[str, str]:
        """
        Возвращает (tg_from, tg_to)
          tg_from = "Выдает"
          tg_to   = "Принимает"
        """
        if kind == "dep":
            tg_to = contact1
            tg_from = contact2
        else:  # kind == "wd"
            tg_from = contact1
            tg_to = contact2
        return tg_from.strip(), tg_to.strip()

    async def _cmd_cash_req(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        raw = (message.text or "")
        m = RE_CMD.match(raw)
        if not m:
            await message.answer(
                "Форматы:\n"
                "• /депр|депт|депд|депе|депб <сумма/expr> <Принимает> [Выдает] [! комментарий]\n"
                "• /выдр|выдт|выдд|выде|выдб <сумма/expr> <Выдает> [Принимает] [! комментарий]\n"
                "Напр.: /депр 150000 @petya_cashier @vasya_courier ! курс по договору\n"
                "       /депр 150000 @petya_cashier\n"
                "       /выдр (700+300) +79995556677 ! выдать у офиса\n\n"
                "Редактирование контактов:\n"
                "• ответьте этой же командой на карточку БОТА с заявкой — код/сумма не изменятся."
            )
            return

        cmd = m.group(1).lower()
        amount_expr = m.group(2).strip()
        contact1 = (m.group(3) or "").strip()
        contact2 = (m.group(4) or "").strip()
        comment = (m.group(5) or "").strip()

        kind, code = CMD_MAP.get(cmd, (None, None))
        if not kind or not code:
            await message.answer("Не распознал команду/валюту.")
            return

        title = _req_title(kind)
        tg_from, tg_to = self._split_contacts(kind, contact1, contact2)

        # === РЕДАКТИРОВАНИЕ (ответом на карточку БОТА) ===
        reply_msg = getattr(message, "reply_to_message", None)
        is_reply_to_bot = bool(
            reply_msg
            and reply_msg.from_user
            and reply_msg.from_user.id == message.bot.id
            and (reply_msg.text or "")
        )
        if is_reply_to_bot:
            old_text = reply_msg.text or ""

            m_req = _RE_REQ_ID.search(old_text)
            m_pin = _RE_LINE_PIN.search(old_text)
            m_amt = _RE_LINE_AMOUNT.search(old_text)
            if not (m_req and m_pin and m_amt):
                await message.answer(
                    "Не похоже на карточку заявки (не нашёл строки Заявка/Сумма/Код).\n"
                    "Ответьте именно на сообщение БОТА с заявкой."
                )
                return

            audit = make_audit_for_edit(message, old_text=old_text)

            req_id = int(m_req.group(1))
            pin_code = m_pin.group(1)

            parsed_old = _parse_amount_code(m_amt.group(1))
            if not parsed_old:
                await message.answer("Не удалось распарсить сумму/валюту в исходной заявке.")
                return
            amount_raw_old, code_old = parsed_old
            code_old = code_old.upper()

            if code_old != code:
                await message.answer(
                    f"Нельзя менять валюту при редактировании.\n"
                    f"В исходной заявке: {code_old}, в команде: {code}."
                )
                return

            chat_id = message.chat.id
            chat_name = get_chat_name(message)
            client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
            accounts = await self.repo.snapshot_wallet(client_id)
            acc = next((r for r in accounts if str(r["currency_code"]).upper() == code_old), None)
            if not acc:
                await message.answer(f"Счёт {code_old} не найден. Добавьте валюту: /добавь {code_old} [точность]")
                return

            prec = int(acc["precision"]) if acc.get("precision") is not None else 2
            q = Decimal(10) ** -prec
            amount_old = amount_raw_old.quantize(q).quantize(Decimal("1"))
            pretty_amount = format_amount_core(amount_old, prec)

            lines_client = [
                f"<b>{title}</b>: <code>{req_id}</code>",
                "-----",
                f"<b>Сумма</b>: <code>{pretty_amount} {code_old.lower()}</code>",
            ]
            if tg_to:
                lines_client.append(f"<b>Принимает</b>: {tg_to}")
            if tg_from:
                lines_client.append(f"<b>Выдает</b>: {tg_from}")
            lines_client.append(f"<b>Код</b>: <tg-spoiler>{pin_code}</tg-spoiler>")
            if comment:
                lines_client += ["----", f"<b>Комментарий</b>: <code>{html.escape(comment)}</code>❗️"]
            lines_client += audit_lines_for_client_card(audit)
            text_client = "\n".join(lines_client)

            lines_req = [
                "⚠️ <b>Внимание: заявка изменена.</b>",
                "",
                f"<b>{title}</b>: <code>{req_id}</code>",
                f"<b>Клиент</b>: <b>{html.escape(chat_name)}</b>",
                "-----",
                f"<b>Сумма</b>: <code>{pretty_amount} {code_old.lower()}</code>",
            ]
            if tg_to:
                lines_req.append(f"<b>Принимает</b>: {tg_to}")
            if tg_from:
                lines_req.append(f"<b>Выдает</b>: {tg_from}")
            lines_req.append(f"<b>Код</b>: {pin_code}")
            if comment:
                lines_req += ["----", f"<b>Комментарий</b>: <code>{html.escape(comment)}</code>❗️"]
            kind_ru = "Деп" if kind == "dep" else "Выд"
            lines_req += ["----", "✏️ <b>Изменение контактов</b>", f"<b>Тип</b>: <b>{kind_ru}</b>"]
            lines_req += audit_lines_for_request_chat(audit)
            text_req = "\n".join(lines_req)

            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=reply_msg.message_id,
                    text=text_client,
                    parse_mode="HTML",
                    reply_markup=_issue_keyboard_with_kind(kind=kind, req_id=req_id),
                )
            except TelegramBadRequest as e:
                if "message is not modified" not in str(e).lower():
                    await message.answer(f"Не удалось отредактировать заявку: {e}")
                    return
            except Exception as e:
                await message.answer(f"Не удалось отредактировать заявку: {e}")
                return

            if self.request_chat_id:
                try:
                    await message.bot.send_message(
                        chat_id=self.request_chat_id,
                        text=text_req,
                        parse_mode="HTML",
                    )
                except Exception:
                    pass

            await message.answer("✅ Контакты обновлены.")
            return

        # === СОЗДАНИЕ НОВОЙ ЗАЯВКИ ===
        try:
            amount_raw = evaluate(amount_expr)
            if amount_raw <= 0:
                await message.answer("Сумма должна быть > 0")
                return
        except (CalcError, InvalidOperation) as e:
            await message.answer(f"Ошибка в выражении суммы: {e}")
            return

        audit = make_audit_for_new(message)

        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            await message.answer(f"Счёт {code} не найден. Добавьте валюту: /добавь {code} [точность]")
            return

        prec = int(acc["precision"]) if acc.get("precision") is not None else 2
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q).quantize(Decimal("1"))
        pretty_amount = format_amount_core(amount, prec)

        req_id = random.randint(10_000_000, 99_999_999)
        pin_code = f"{random.randint(100, 999)}-{random.randint(100, 999)}"

        lines_client = [
            f"<b>{title}</b>: <code>{req_id}</code>",
            "-----",
            f"<b>Сумма</b>: <code>{pretty_amount} {code.lower()}</code>",
        ]
        if tg_to:
            lines_client.append(f"<b>Принимает</b>: {tg_to}")
        if tg_from:
            lines_client.append(f"<b>Выдает</b>: {tg_from}")
        lines_client.append(f"<b>Код</b>: <tg-spoiler>{pin_code}</tg-spoiler>")
        if comment:
            lines_client += ["----", f"<b>Комментарий</b>: <code>{html.escape(comment)}</code>❗️"]
        lines_client += audit_lines_for_client_card(audit)
        text_client = "\n".join(lines_client)

        lines_req = [
            f"<b>{title}</b>: <code>{req_id}</code>",
            f"<b>Клиент</b>: <code>{html.escape(chat_name)}</code>",
            "-----",
            f"<b>Сумма</b>: <code>{pretty_amount} {code.lower()}</code>",
        ]
        if tg_to:
            lines_req.append(f"<b>Принимает</b>: {tg_to}")
        if tg_from:
            lines_req.append(f"<b>Выдает</b>: {tg_from}")
        lines_req.append(f"<b>Код</b>: {pin_code}")
        if comment:
            lines_req += ["----", f"<b>Комментарий</b>: <code>{html.escape(comment)}</code>❗️"]
        kind_ru = "Деп" if kind == "dep" else "Выд"
        lines_req.append(f"<b>Тип</b>: <b>{kind_ru}</b>")
        lines_req += audit_lines_for_request_chat(audit)
        text_req = "\n".join(lines_req)

        await message.answer(
            text_client,
            parse_mode="HTML",
            reply_markup=_issue_keyboard_with_kind(kind=kind, req_id=req_id),
        )

        if self.request_chat_id:
            try:
                await message.bot.send_message(
                    chat_id=self.request_chat_id,
                    text=text_req,
                    parse_mode="HTML",
                )
            except Exception:
                pass

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
            if len(parts) >= 4 and ":".join(parts[:2]) == CB_ISSUE_DONE:
                maybe_kind = parts[2].lower()
                if maybe_kind in ("dep", "wd"):
                    op_kind = maybe_kind
        except Exception:
            op_kind = None

        if not op_kind:
            if _RE_KIND_DEP_LEGACY.search(text):
                op_kind = "dep"
            elif _RE_KIND_WD_LEGACY.search(text):
                op_kind = "wd"

        if not op_kind:
            await cq.answer("Не удалось распознать тип заявки.", show_alert=True)
            return

        m_amt = _RE_LINE_AMOUNT.search(text)
        if not m_amt:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        parsed = _parse_amount_code(m_amt.group(1))
        if not parsed:
            await cq.answer("Не удалось распознать сумму/валюту.", show_alert=True)
            return

        amount_raw, code = parsed
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

        prec = int(acc["precision"]) if acc.get("precision") is not None else 2
        q = Decimal(10) ** -prec
        amount = amount_raw.quantize(q).quantize(Decimal("1"))

        idem = f"cash:{chat_id}:{msg.message_id}"
        try:
            if op_kind == "dep":
                await self.repo.deposit(
                    client_id=client_id,
                    currency_code=code,
                    amount=amount,
                    comment="cash issue",
                    source="cash_request",
                    idempotency_key=idem,
                )
            else:
                await self.repo.withdraw(
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
        prec2 = int(acc2["precision"]) if acc2 and acc2.get("precision") is not None else prec
        pretty_bal = format_amount_core(cur_bal, prec2)

        await cq.message.answer(
            f"Запомнил.\nБаланс: <code>{pretty_bal} {code.lower()}</code>",
            parse_mode="HTML",
        )
        await cq.answer("Отмечено как выдано")