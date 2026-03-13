from __future__ import annotations

import os
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Tuple, Set, Optional
from datetime import datetime, timezone, timedelta

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from googleapiclient.discovery import build

from keyboards.request import CB_TABLE_DONE
from gutils.requests_sheet import (
    append_sale_row,
    append_buy_row,
    SheetsWriteError,
    get_service_account_email,
    _get_credentials,
    _extract_id_from_url,
)

# Константы
STATUS_LINE_DONE = "Статус: Занесена в таблицу ✅"
CB_TABLE_CONFIRM_YES = "req:table_confirm:yes"
CB_TABLE_CONFIRM_NO = "req:table_confirm:no"

# Карта курсов и имён валют
_DEFAULT_CELL_MAP = {
    "EUR": "Главная!E2",
    "USDT": "Главная!E8",
    "USD": "Главная!H8",
    "USDW": "Главная!H2",
}
_TABLE_CURRENCY_NAMES = {
    "USD": "USD BL",
    "USDW": "USD WH",
    "EUR": "EUR",
    "USDT": "USDT",
}

# Вспомогательные наборы и шаблоны
_PENDING: Set[Tuple[int, int]] = set()
_MARKED: Set[Tuple[int, int]] = set()
_RE_PAYOUT = re.compile(r"^Отдаём:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.M | re.I)
_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _append_status_once(text: str, status_line: str) -> str:
    """Добавляет строку статуса в конец сообщения, если её там ещё нет."""
    src = text or ""
    if status_line in src:
        return src
    return src.rstrip() + "\n" + status_line


def _processing_kb() -> InlineKeyboardMarkup:
    """Возвращает заглушку-клавиатуру «Обрабатывается…»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⏳ Обрабатывается…", callback_data="noop")]]
    )


def _to_decimal(s: str) -> Decimal:
    s = s.strip().replace(",", ".")
    for ch in _SEP:
        s = s.replace(ch, "")
    return Decimal(s)


def _parse_cb_payload(data: str) -> Optional[tuple[Optional[int], str, str, Decimal, Decimal, Decimal]]:
    """Разбор callback_data в новый и старый формат."""
    parts = (data or "").split(":")
    try:
        if len(parts) >= 8 and parts[0] == "req" and parts[1] == "table_done":
            return (
                int(parts[2]),
                parts[3].strip().upper(),
                parts[4].strip().upper(),
                _to_decimal(parts[5]),
                _to_decimal(parts[6]),
                _to_decimal(parts[7]),
            )
        elif len(parts) >= 7 and parts[0] == "req" and parts[1] == "table_done":
            return (
                None,
                parts[2].strip().upper(),
                parts[3].strip().upper(),
                _to_decimal(parts[4]),
                _to_decimal(parts[5]),
                _to_decimal(parts[6]),
            )
    except (InvalidOperation, ValueError, IndexError):
        return None
    return None


def _short(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else (text[: limit - 1] + "…")


def _read_rate(cell_ref: str) -> Decimal:
    creds = _get_credentials()
    service = build("sheets", "v4", credentials=creds)
    sheet_url = (
        os.getenv("GOOGLE_SHEET_URL")
        or os.getenv("GOOGLE_SHEET_ID")
        or os.getenv("SPREADSHEET_URL")
        or os.getenv("SPREADSHEET_ID")
        or ""
    )
    sid = _extract_id_from_url(sheet_url)
    resp = service.spreadsheets().values().get(spreadsheetId=sid, range=cell_ref).execute()
    vals = resp.get("values", [])
    if not vals or not vals[0]:
        raise SheetsWriteError(f"Не найдено значение в {cell_ref}")
    raw = str(vals[0][0]).replace("\u00A0", "").replace(" ", "").replace(",", ".").strip()
    return Decimal(raw)


def _map_table_currency(cur: str) -> str:
    return _TABLE_CURRENCY_NAMES.get(cur, cur)


# === Основной роутер ===
def get_table_done_router(*, request_chat_ids: Iterable[int]) -> Router:
    allowed = set(int(x) for x in request_chat_ids)
    router = Router()

    @router.callback_query(F.data.startswith(CB_TABLE_DONE))
    async def _cb_table_done(cq: CallbackQuery) -> None:
        msg = cq.message
        if not msg or msg.chat.id not in allowed:
            await cq.answer("Недоступно здесь.", show_alert=True)
            return

        key = (msg.chat.id, msg.message_id)

        # 🔒 Защита от повторов
        if key in _PENDING:
            await cq.answer("⏳ Уже обрабатывается…")
            return
        if STATUS_LINE_DONE in (msg.text or "") or key in _MARKED:
            await cq.answer("Статус уже проставлен.")
            return

        # моментально снимаем клавиатуру
        try:
            await msg.edit_reply_markup(reply_markup=_processing_kb())
        except Exception:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        _PENDING.add(key)
        try:
            parsed = _parse_cb_payload(cq.data or "")
            if not parsed:
                new_text = _append_status_once(msg.text or "", STATUS_LINE_DONE)
                try:
                    await msg.edit_text(new_text, parse_mode="HTML")
                    _MARKED.add(key)
                except Exception:
                    pass
                await cq.answer("Занесена в таблицу ✅ (нет параметров)")
                return

            req_id, in_cur, out_cur, in_amt, out_amt, rate = parsed

            # время UTC+5
            created_at = None
            msg_dt = getattr(msg, "date", None)
            if isinstance(msg_dt, datetime):
                if msg_dt.tzinfo is None:
                    msg_dt = msg_dt.replace(tzinfo=timezone.utc)
                created_at = msg_dt.astimezone(timezone(timedelta(hours=5)))

            # === логика записи ===
            if in_cur == "USDT" and out_cur not in {"EUR", "USD", "USDW"}:
                append_buy_row(currency="USDT", amount=in_amt, rate=rate,
                               created_at=created_at, spreadsheet=None,
                               sheet_name="Покупка", request_id=req_id)
                sheet_type = "Покупка"

            elif out_cur == "USDT" and in_cur not in {"EUR", "USD", "USDW"}:
                append_sale_row(in_currency=in_cur, out_currency="USDT",
                                in_amount=in_amt, out_amount=out_amt, rate=rate,
                                created_at=created_at, spreadsheet=None,
                                sheet_name="Продажа", request_id=req_id)
                sheet_type = "Продажа"

            elif out_cur in {"EUR", "USD", "USDW"} and in_cur == "RUB":
                append_sale_row(in_currency="RUB", out_currency=_map_table_currency(out_cur),
                                in_amount=in_amt, out_amount=out_amt, rate=rate,
                                created_at=created_at, spreadsheet=None,
                                sheet_name="Продажа", request_id=req_id)
                sheet_type = "Продажа"

            elif in_cur in {"EUR", "USD", "USDW"} and out_cur == "RUB":
                append_buy_row(currency=_map_table_currency(in_cur), amount=in_amt, rate=rate,
                               created_at=created_at, spreadsheet=None,
                               sheet_name="Покупка", request_id=req_id)
                sheet_type = "Покупка"

            elif in_cur in {"EUR", "USD", "USDW"} and out_cur == "USDT":
                inner_rate = _read_rate(_DEFAULT_CELL_MAP[in_cur])
                rub_total = in_amt * inner_rate
                append_buy_row(currency=_map_table_currency(in_cur), amount=in_amt, rate=inner_rate,
                               created_at=created_at, spreadsheet=None,
                               sheet_name="Покупка", request_id=req_id)
                final_rate = rub_total / out_amt
                append_sale_row(in_currency=_map_table_currency(in_cur), out_currency="USDT",
                                in_amount=in_amt, out_amount=out_amt, rate=final_rate,
                                created_at=created_at, spreadsheet=None,
                                sheet_name="Продажа", request_id=req_id)
                sheet_type = f"Покупка + Продажа ({_map_table_currency(in_cur)})"

            elif in_cur == "USDT" and out_cur in {"EUR", "USD", "USDW"}:
                inner_rate = _read_rate(_DEFAULT_CELL_MAP[out_cur])
                rub_total = out_amt * inner_rate
                append_sale_row(in_currency="USDT", out_currency=_map_table_currency(out_cur),
                                in_amount=in_amt, out_amount=out_amt, rate=inner_rate,
                                created_at=created_at, spreadsheet=None,
                                sheet_name="Продажа", request_id=req_id)
                final_rate = rub_total / in_amt
                append_buy_row(currency="USDT", amount=in_amt, rate=final_rate,
                               created_at=created_at, spreadsheet=None,
                               sheet_name="Покупка", request_id=req_id)
                sheet_type = f"Продажа + Покупка ({_map_table_currency(out_cur)})"

            elif in_cur in {"EUR", "USD", "USDW"} and out_cur in {"EUR", "USD", "USDW"}:
                in_rate = _read_rate(_DEFAULT_CELL_MAP[in_cur])
                rub_total = in_amt * in_rate
                append_buy_row(currency=_map_table_currency(in_cur), amount=in_amt, rate=in_rate,
                               created_at=created_at, spreadsheet=None,
                               sheet_name="Покупка", request_id=req_id)
                if out_amt <= 0:
                    raise SheetsWriteError("Сумма продажи должна быть > 0.")
                sale_rate = rub_total / out_amt
                pretty_out = _map_table_currency(out_cur)
                custom_cell_map = dict(_DEFAULT_CELL_MAP)
                if pretty_out not in custom_cell_map:
                    custom_cell_map[pretty_out] = _DEFAULT_CELL_MAP[out_cur]
                append_sale_row(in_currency=in_cur, out_currency=pretty_out,
                                in_amount=in_amt, out_amount=out_amt, rate=sale_rate,
                                created_at=created_at, spreadsheet=None,
                                sheet_name="Продажа", cell_map=custom_cell_map,
                                request_id=req_id)
                sheet_type = f"Покупка + Продажа ({_map_table_currency(in_cur)}→{_map_table_currency(out_cur)})"

            else:
                await cq.answer("Неизвестная пара валют. Запись не выполнена.", show_alert=True)
                return

        except SheetsWriteError as e:
            logging.exception("Sheets write failed: %s", e)
            msg = str(e).lower()
            if "permission" in msg or "forbidden" in msg:
                sa_email = get_service_account_email() or "service-account@<project>.iam.gserviceaccount.com"
                await cq.answer(_short(f"Нет доступа к таблице.\nВыдайте право «Редактор» для:\n{sa_email}"), show_alert=True)
            else:
                await cq.answer(_short("Ошибка записи в Google Sheets."), show_alert=True)
            return
        except Exception as e:
            logging.exception("Unexpected error while writing to Sheets: %s", e)
            await cq.answer(_short("Не удалось записать в таблицу."), show_alert=True)
            return
        finally:
            _PENDING.discard(key)

        # ✅ Обновляем сообщение
        new_text = _append_status_once(msg.text or "", STATUS_LINE_DONE)
        try:
            await msg.edit_text(new_text, parse_mode="HTML")
            _MARKED.add(key)
        except Exception:
            pass
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer(
            _short(
                f"Занесена в таблицу ✅ ({sheet_type}, {in_cur}→{out_cur}, "
                f"получено {in_amt}, отдано {out_amt}, курс {rate})"
            )
        )

    return router