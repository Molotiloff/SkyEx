# utils/info.py
from decimal import Decimal

from aiogram.types import Message


def get_chat_name(message: Message) -> str:
    """
    Возвращает удобочитаемое имя чата или пользователя:
    - для групп и каналов: chat.title
    - для личных чатов: first_name + last_name
    - иначе username (с @)
    - если ничего нет, возвращает 'неизвестного'
    """
    chat = message.chat

    if chat.title:  # группы/каналы
        return chat.title

    parts = []
    if chat.first_name:
        parts.append(chat.first_name)
    if chat.last_name:
        parts.append(chat.last_name)
    if parts:
        return " ".join(parts)

    if chat.username:
        return f"@{chat.username}"

    return "незнакомца"


def _fmt_rate(d: Decimal) -> str:
    s = f"{d.normalize():f}"
    return s.rstrip("0").rstrip(".") if "." in s else s
