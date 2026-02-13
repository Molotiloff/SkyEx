# utils/tg_migrate.py
from __future__ import annotations

from typing import Awaitable, Callable, TypeVar, Any
from aiogram.exceptions import TelegramBadRequest

T = TypeVar("T")


def _extract_migrate_to_chat_id(err: TelegramBadRequest) -> int | None:
    # aiogram часто кладёт ResponseParameters в err.parameters
    params = getattr(err, "parameters", None)
    if params and getattr(params, "migrate_to_chat_id", None):
        try:
            return int(params.migrate_to_chat_id)
        except Exception:
            return None

    # fallback: парсим текст (как у тебя в логе)
    s = str(err)
    marker = "migrated to a supergroup with id "
    if marker in s:
        try:
            tail = s.split(marker, 1)[1]
            num = ""
            for ch in tail:
                if ch in "-0123456789":
                    num += ch
                else:
                    break
            return int(num) if num else None
        except Exception:
            return None
    return None


async def send_with_migrate_retry(
    *,
    repo,
    client_id: int | None,
    chat_id: int,
    send_call: Callable[[int], Awaitable[T]],
) -> tuple[T | None, int | None]:
    """
    send_call(chat_id) -> awaitable (любая отправка: send_message/send_photo/forward/etc)

    Возвращает: (result, new_chat_id_if_migrated)
    """
    try:
        res = await send_call(chat_id)
        return res, None
    except TelegramBadRequest as e:
        new_chat_id = _extract_migrate_to_chat_id(e)
        if not new_chat_id:
            raise

        # обновим БД (если знаем client_id)
        if repo is not None and client_id is not None:
            try:
                await repo.update_client_chat_id(client_id=client_id, new_chat_id=new_chat_id)
            except Exception:
                pass

        # повторяем отправку уже в новый id
        res = await send_call(new_chat_id)
        return res, new_chat_id
