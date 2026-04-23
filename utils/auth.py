# utils/auth.py
from __future__ import annotations
from typing import Iterable
from aiogram.types import Message, CallbackQuery
from db_asyncpg.repo import Repo


def _is_reply_to_public_wallet_message(message: Message) -> bool:
    reply = getattr(message, "reply_to_message", None)
    if not reply or not message.bot or not reply.from_user:
        return False
    if reply.from_user.id != message.bot.id:
        return False
    text = reply.text or reply.caption or ""
    return text.strip().startswith("USDT TRC-20 кошелёк")


async def require_manager_or_admin_message(
        repo: Repo,
        message: Message,
        *,
        admin_chat_ids: Iterable[int],
        admin_user_ids: Iterable[int],
) -> bool:
    """
    Разрешить, если:
      1) команда пришла из админского чата, или
      2) sender.user_id в admin_user_ids, или
      3) sender.user_id в таблице managers.
    Иначе отправляет в чат отказ и возвращает False.
    """
    chat_id = message.chat.id if message.chat else None
    if chat_id in set(admin_chat_ids):
        return True

    if not message.from_user:
        await message.answer("⛔ Не удалось определить пользователя.")
        return False

    uid = message.from_user.id
    if uid in set(admin_user_ids):
        return True

    if await repo.is_manager(uid):
        return True

    if _is_reply_to_public_wallet_message(message):
        return False

    await message.answer("⛔ Доступ только для менеджеров или админов.")
    return False


async def require_manager_or_admin_callback(
        repo: Repo,
        cq: CallbackQuery,
        *,
        admin_chat_ids: Iterable[int],
        admin_user_ids: Iterable[int],
) -> bool:
    """
    То же, что выше, но для CallbackQuery.
    """
    chat_id = cq.message.chat.id if (cq.message and cq.message.chat) else None
    if chat_id in set(admin_chat_ids):
        return True

    if not cq.from_user:
        await cq.answer("⛔ Нет пользователя", show_alert=True)
        return False

    uid = cq.from_user.id
    if uid in set(admin_user_ids):
        return True

    if await repo.is_manager(uid):
        return True

    await cq.answer("⛔ Доступ только для менеджеров или админов.", show_alert=True)
    return False
