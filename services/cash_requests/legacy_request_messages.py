from __future__ import annotations

from typing import Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message


async def post_request_message(
    bot: Bot,
    request_chat_id: int,
    text: str,
    *,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    disable_notification: bool = False,
) -> Message:
    return await bot.send_message(
        chat_id=request_chat_id,
        text=text,
        parse_mode="HTML",
        reply_markup=reply_markup,
        disable_notification=disable_notification,
    )
