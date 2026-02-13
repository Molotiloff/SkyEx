# utils/tg_migrate.py
from __future__ import annotations

from typing import Awaitable, Callable, TypeVar
from aiogram.exceptions import TelegramBadRequest

T = TypeVar("T")


def _extract_migrate_to_chat_id(err: TelegramBadRequest) -> int | None:
    params = getattr(err, "parameters", None)
    mig = getattr(params, "migrate_to_chat_id", None) if params else None
    if mig:
        try:
            return int(mig)
        except Exception:
            return None

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
) -> tuple[T, int | None]:
    """
    send_call(chat_id) -> awaitable (send_message/send_photo/etc)
    Возвращает: (result, new_chat_id_if_migrated)
    """
    try:
        return await send_call(chat_id), None
    except TelegramBadRequest as e:
        new_chat_id = _extract_migrate_to_chat_id(e)
        if not new_chat_id:
            raise

        if repo is not None and client_id is not None:
            try:
                await repo.update_client_chat_id(client_id=client_id, new_chat_id=new_chat_id)
            except Exception:
                pass

        return await send_call(new_chat_id), new_chat_id
