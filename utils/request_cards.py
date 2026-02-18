# utils/request_cards.py
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional, Sequence

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from keyboards.request import CB_ISSUE_DONE


@dataclass(frozen=True, slots=True)
class CardDataDepWd:
    kind: str           # "dep" | "wd"
    req_id: str         # "Б-123456"
    city: str
    code: str           # "RUB"/"USD"/...
    pretty_amount: str  # "150000.00"
    tg_from: str        # "Выдает"
    tg_to: str          # "Принимает"
    pin_code: str       # "123-456"
    comment: str = ""


@dataclass(frozen=True, slots=True)
class CardDataFx:
    req_id: str
    city: str
    in_code: str
    out_code: str
    pretty_in: str
    pretty_out: str
    tg_from: str
    tg_to: str
    pin_code: str
    comment: str = ""


def _req_title(kind: str) -> str:
    if kind == "dep":
        return "Заявка на внесение"
    if kind == "wd":
        return "Заявка на выдачу"
    return "Заявка на обмен"


def _issue_keyboard(kind: str, req_id: str) -> InlineKeyboardMarkup:
    cb = f"{CB_ISSUE_DONE}:{kind}:{req_id}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Выдано", callback_data=cb)]])


def build_client_card_dep_wd(data: CardDataDepWd) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    Карточка клиенту: БЕЗ audit ("Создал..."), код в spoiler.
    Кнопка: только для dep, для wd — None.
    """
    title = _req_title(data.kind)
    lines: list[str] = [
        f"<b>{title}</b>: <code>{html.escape(data.req_id)}</code>",
        f"<b>Город</b>: <code>{html.escape(data.city)}</code>",
        "-----",
        f"<b>Сумма</b>: <code>{html.escape(data.pretty_amount)} {html.escape(data.code.lower())}</code>",
    ]
    if data.tg_to:
        lines.append(f"<b>Принимает</b>: {data.tg_to}")
    if data.tg_from:
        lines.append(f"<b>Выдает</b>: {data.tg_from}")
    lines.append(f"<b>Код</b>: <tg-spoiler>{html.escape(data.pin_code)}</tg-spoiler>")
    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]
    text = "\n".join(lines)

    markup = _issue_keyboard(kind=data.kind, req_id=data.req_id) if data.kind == "dep" else None
    return text, markup


def build_city_card_dep_wd(
    data: CardDataDepWd,
    *,
    chat_name: str,
    audit_lines: Sequence[str],
    changed_notice: bool = False,
) -> str:
    """
    Карточка в чат заявок города: код БЕЗ spoiler, с audit.
    """
    title = _req_title(data.kind)
    lines: list[str] = []
    if changed_notice:
        lines += ["⚠️ <b>Внимание: заявка изменена.</b>", ""]
    lines += [
        f"<b>{title}</b>: <code>{html.escape(data.req_id)}</code>",
        f"<b>Город</b>: <code>{html.escape(data.city)}</code>",
        f"<b>Клиент</b>: <code>{html.escape(chat_name)}</code>",
        "-----",
        f"<b>Сумма</b>: <code>{html.escape(data.pretty_amount)} {html.escape(data.code.lower())}</code>",
    ]
    if data.tg_to:
        lines.append(f"<b>Принимает</b>: {data.tg_to}")
    if data.tg_from:
        lines.append(f"<b>Выдает</b>: {data.tg_from}")
    lines.append(f"<b>Код</b>: {html.escape(data.pin_code)}")
    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]
    if changed_notice:
        lines += ["----", "✏️ <b>Изменение заявки</b>"]
    lines += list(audit_lines)
    return "\n".join(lines)


def build_client_card_fx(data: CardDataFx) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """
    FX клиенту: без audit, без кнопок.
    Формат:
      Заявка на обмен: <Номер>
      -----
      Клиент: +7XXXXXXXXXX / @username(контакт 2)

      Принимаем: 1 000 000 RUB
      Выдаем: 10 000 USD

      Кассир: @good_cashier(контакт 1)
      Код: 688-742
    """
    client_contact = data.tg_from.strip() if (data.tg_from or "").strip() else "—"
    cashier = data.tg_to.strip() if (data.tg_to or "").strip() else "—"

    lines: list[str] = [
        f"<b>Заявка на обмен</b>: <code>{html.escape(data.req_id)}</code>",
        "-----",
        f"<b>Клиент</b>: {html.escape(client_contact)}",
        "",
        f"<b>Принимаем</b>: <code>{html.escape(data.pretty_in)} {html.escape(data.in_code)}</code>",
        f"<b>Выдаем</b>: <code>{html.escape(data.pretty_out)} {html.escape(data.out_code)}</code>",
        "",
        f"<b>Кассир</b>: {html.escape(cashier)}",
        f"<b>Код</b>: <tg-spoiler>{html.escape(data.pin_code)}</tg-spoiler>",
    ]
    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]

    return "\n".join(lines), None


def build_city_card_fx(
    data: CardDataFx,
    *,
    chat_name: str,
    audit_lines: Sequence[str],
    changed_notice: bool = False,
) -> str:
    """
    FX в чат заявок города: с audit, код без spoiler.
    Клиент: <chat_name> / <контакт2>
    Кассир: <контакт1>
    """
    lines: list[str] = []
    if changed_notice:
        lines += ["⚠️ <b>Внимание: заявка изменена.</b>", ""]

    client_line = html.escape(chat_name)
    if (data.tg_from or "").strip():
        client_line = f"{client_line} / {html.escape(data.tg_from.strip())}"

    cashier = data.tg_to.strip() if (data.tg_to or "").strip() else "—"

    lines += [
        f"<b>Заявка на обмен</b>: <code>{html.escape(data.req_id)}</code>",
        "-----",
        f"<b>Клиент</b>: {client_line}",
        "",
        f"<b>Принимаем</b>: <code>{html.escape(data.pretty_in)} {html.escape(data.in_code)}</code>",
        f"<b>Выдаем</b>: <code>{html.escape(data.pretty_out)} {html.escape(data.out_code)}</code>",
        "",
        f"<b>Кассир</b>: {html.escape(cashier)}",
        f"<b>Код</b>: {html.escape(data.pin_code)}",
    ]

    if data.comment:
        lines += ["----", f"<b>Комментарий</b>: <code>{html.escape(data.comment)}</code>❗️"]

    if changed_notice:
        lines += ["----", "✏️ <b>Изменение заявки</b>"]

    lines += list(audit_lines)
    return "\n".join(lines)