from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def cancel_keyboard(req_id: int | str, table_req_id: int | str | None = None) -> InlineKeyboardMarkup:
    callback_data = f"req_cancel:{req_id}"
    if table_req_id is not None:
        callback_data = f"{callback_data}:{table_req_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отменить заявку", callback_data=callback_data)]
        ]
    )


def request_chat_keyboard(
    *,
    req_id: int | str,
    table_req_id: int | str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Занести в таблицу", callback_data=f"req:table_done:{table_req_id}"),
                InlineKeyboardButton(text="Отменить заявку", callback_data=f"req_cancel:{req_id}:{table_req_id}"),
            ]
        ]
    )
