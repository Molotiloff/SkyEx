# utils/statements.py
from __future__ import annotations

import calendar
import io
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from aiogram.types import CallbackQuery, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

from db_asyncpg.repo import Repo
from utils.info import get_chat_name


def statements_kb() -> InlineKeyboardMarkup:
    """
    Клавиатура для запроса выписок:
      - Выписка за месяц (текущий календарный месяц, UTC)
      - Выписка за всё время
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📄 Выписка за месяц", callback_data="stmt:month")],
            [InlineKeyboardButton(text="📄 Выписка за всё время", callback_data="stmt:all")],
        ]
    )


def _month_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """Начало и конец месяца в UTC [start, end_exclusive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    start = dt.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    days = calendar.monthrange(start.year, start.month)[1]
    end = start + timedelta(days=days)  # начало следующего месяца
    return start, end


def _build_statement_xlsx(rows: list[dict], chat_name: str, period_label: str) -> bytes:
    """
    rows — получены из Repo.export_transactions(...).
    Ожидаемые поля: txn_at (datetime), currency_code, amount, balance_after, group_name, actor_name, comment, source.
    Возвращает байты XLSX.
    """
    import xlsxwriter  # должен быть в requirements

    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {'in_memory': True})
    ws = wb.add_worksheet("Выписка")

    fmt_bold = wb.add_format({'bold': True})
    fmt_header = wb.add_format({'bold': True, 'bg_color': '#EEEEEE', 'border': 1})
    fmt_money = wb.add_format({'num_format': '#,##0.00;[Red]-#,##0.00'})
    fmt_dt = wb.add_format({'num_format': 'yyyy-mm-dd hh:mm'})

    # Заголовок
    ws.write(0, 0, f"Выписка по кошельку: {chat_name}", fmt_bold)
    ws.write(1, 0, f"Период: {period_label}")

    # Шапка
    headers = ["Дата/время (UTC)", "Валюта", "Сумма", "Баланс после", "Группа", "Оператор", "Источник", "Комментарий"]
    for col, h in enumerate(headers):
        ws.write(3, col, h, fmt_header)

    # Данные
    r = 4
    for tx in rows:
        ts = tx.get("txn_at")
        code = (tx.get("currency_code") or "").upper()
        amt = Decimal(str(tx.get("amount"))) if tx.get("amount") is not None else Decimal("0")
        bal_after = Decimal(str(tx.get("balance_after"))) if tx.get("balance_after") is not None else Decimal("0")
        group_name = tx.get("group_name") or ""
        actor_name = tx.get("actor_name") or ""
        source = tx.get("source") or ""
        comment = tx.get("comment") or ""

        # Excel datetime без tz; конвертируем в UTC naive
        if isinstance(ts, datetime) and ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)

        if isinstance(ts, datetime):
            ws.write_datetime(r, 0, ts, fmt_dt)
        else:
            ws.write(r, 0, "")

        ws.write(r, 1, code)
        ws.write_number(r, 2, float(amt), fmt_money)
        ws.write_number(r, 3, float(bal_after), fmt_money)
        ws.write(r, 4, group_name)
        ws.write(r, 5, actor_name)
        ws.write(r, 6, source)
        ws.write(r, 7, comment)
        r += 1

    # Колонки
    ws.set_column(0, 0, 20)
    ws.set_column(1, 1, 9)
    ws.set_column(2, 3, 16)
    ws.set_column(4, 6, 18)
    ws.set_column(7, 7, 40)

    wb.close()
    output.seek(0)
    return output.read()


async def handle_stmt_callback(cq: CallbackQuery, repo: Repo) -> None:
    """
    Универсальный обработчик callback'ов выписок:
      - data = "stmt:month" → текущий месяц
      - data = "stmt:all"   → всё время
    Отправляет XLSX-файл в тот же чат.
    """
    msg = cq.message
    if not msg:
        await cq.answer()
        return

    data = (cq.data or "")
    try:
        kind = data.split(":", 1)[1]
    except Exception:
        await cq.answer("Некорректные данные", show_alert=True)
        return

    try:
        now = datetime.now(timezone.utc)
        if kind == "month":
            dt_from, dt_to = _month_bounds(now)
            period_label = f"{dt_from:%Y-%m-01} — {(dt_to - timedelta(seconds=1)):%Y-%m-%d %H:%M} UTC"
            suffix = f"{dt_from:%Y%m}"
            since_arg, until_arg = dt_from, dt_to
        elif kind == "all":
            period_label = "всё время"
            suffix = "all"
            since_arg, until_arg = None, None
        else:
            await cq.answer("Неизвестный период", show_alert=True)
            return

        chat_id = msg.chat.id
        chat_name = get_chat_name(msg)
        client_id = await repo.ensure_client(chat_id=chat_id, name=chat_name)

        tx_rows = await repo.export_transactions(
            client_id=client_id,
            since=since_arg,
            until=until_arg,
        )

        blob = _build_statement_xlsx(tx_rows or [], chat_name=chat_name, period_label=period_label)
        filename = f"statement_{chat_id}_{suffix}.xlsx"
        file = BufferedInputFile(blob, filename=filename)

        await msg.answer_document(file, caption=f"Выписка: {period_label}")
        await cq.answer("Готово")
    except Exception as e:
        await msg.answer(f"Не удалось сформировать выписку: {e}")
        await cq.answer("Ошибка", show_alert=True)