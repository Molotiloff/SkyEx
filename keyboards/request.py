# keyboards/request.py
from __future__ import annotations
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from decimal import Decimal

CB_PARTNER = "req:partner"
CB_TABLE_DONE = "req:table_done"
CB_ISSUE_DONE = "req:issue_done"
CB_DEAL_DONE = "cash:deal_done"

# Удаление строк из таблиц по номеру заявки (подтверждение)
CB_TABLE_DEL = "req:table_del"
CB_TABLE_DEL_YES = "req:table_del:yes"
CB_TABLE_DEL_NO = "req:table_del:no"


def deal_done_kb(req_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сделка завершена",
                    callback_data=f"{CB_DEAL_DONE}:req:{req_id}",
                )
            ]
        ]
    )


def _enc_num(x: Decimal | str) -> str:
    """
    Преобразует число/строку в компактный вид без пробелов
    и с запятой как десятичным разделителем (если была точка).
    """
    s = str(x).strip().replace(" ", "").replace("\u00A0", "")
    if "." in s and "," not in s:
        s = s.replace(".", ",")
    return s


def request_keyboard(
    *,
    in_ccy: str,          # что принимаем
    out_ccy: str,         # что отдаём
    in_amount: Decimal | str,
    out_amount: Decimal | str,
    client_rate: Decimal | str,  # курс из заявки
    req_id: int | str,           # номер заявки
    cb_table_done: str = CB_TABLE_DONE,
) -> InlineKeyboardMarkup:
    """
    Кнопка «Занести в таблицу» с параметрами:
    req:table_done:<REQ_ID>:<IN_CCY>:<OUT_CCY>:<IN_AMT>:<OUT_AMT>:<RATE>
    """
    data = (
        f"{cb_table_done}:"
        f"{req_id}:"
        f"{in_ccy.strip().upper()}:"
        f"{out_ccy.strip().upper()}:"
        f"{_enc_num(in_amount)}:"
        f"{_enc_num(out_amount)}:"
        f"{_enc_num(client_rate)}"
    )
    rows = [[InlineKeyboardButton(text="Занести в таблицу", callback_data=data)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def issue_keyboard(
    req_id: int | str,
    kind: str,
    cb_issue_done: str = CB_ISSUE_DONE,
) -> InlineKeyboardMarkup:
    """
    Кнопка «Выдано» для клиентского чата.
    Формат: req:issue_done:<KIND>:<REQ_ID>
    """
    cb_value = f"{cb_issue_done}:{kind}:{req_id}"
    rows = [[InlineKeyboardButton(text="Выдано", callback_data=cb_value)]]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_from_table_keyboard(
    *,
    req_id: int | str,
) -> InlineKeyboardMarkup:
    """
    Клавиатура подтверждения удаления строк из таблиц «Покупка»/«Продажа» по номеру заявки.
    Формат:
      - да: req:table_del:yes:<REQ_ID>
      - нет: req:table_del:no:<REQ_ID>
    """
    yes = InlineKeyboardButton(text="✅ Удалить из таблиц", callback_data=f"{CB_TABLE_DEL_YES}:{req_id}")
    no  = InlineKeyboardButton(text="✖️ Оставить",          callback_data=f"{CB_TABLE_DEL_NO}:{req_id}")
    return InlineKeyboardMarkup(inline_keyboard=[[yes, no]])