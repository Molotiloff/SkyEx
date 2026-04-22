# handlers/request_table_delete.py
from __future__ import annotations

import logging
from typing import Iterable, Tuple, Set

from aiogram import Router, F
from aiogram.types import CallbackQuery

from gutils.requests_sheet import delete_rows_by_request_id, SheetsWriteError
from keyboards.request import CB_TABLE_DEL_YES, CB_TABLE_DEL_NO

STATUS_LINE_DELETED = "Статус: Удалено из таблиц 🗑️"

_MARKED: Set[Tuple[int, int]] = set()  # защита от повторной пометки


def _append_status_once(text: str, status_line: str) -> str:
    src = text or ""
    if status_line in src:
        return src
    if not src.endswith("\n"):
        return src + "\n" + status_line
    return src + status_line


def get_table_delete_router(*, request_chat_ids: Iterable[int]) -> Router:
    allowed = set(int(x) for x in request_chat_ids)
    r = Router()

    @r.callback_query(F.data.startswith(CB_TABLE_DEL_NO))
    async def _cb_delete_no(cq: CallbackQuery) -> None:
        if not cq.message or cq.message.chat.id not in allowed:
            await cq.answer("Недоступно здесь.", show_alert=True)
            return
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Оставляем строки в таблицах.")

    @r.callback_query(F.data.startswith(CB_TABLE_DEL_YES))
    async def _cb_delete_yes(cq: CallbackQuery) -> None:
        if not cq.message or cq.message.chat.id not in allowed:
            await cq.answer("Недоступно здесь.", show_alert=True)
            return
        # формат: req:table_del:yes:<REQ_ID>
        try:
            parts = (cq.data or "").split(":")
            req_id = parts[-1].strip()
            if not req_id:
                raise ValueError
        except Exception:
            await cq.answer("Некорректные данные", show_alert=True)
            return

        try:
            res = delete_rows_by_request_id(req_id=req_id, spreadsheet=None, sheets=("Покупка", "Продажа"))
        except SheetsWriteError as e:
            logging.exception("Sheets delete failed: %s", e)
            await cq.answer("Не удалось удалить из Google Sheets.", show_alert=True)
            return
        except Exception as e:
            logging.exception("Unexpected delete error: %s", e)
            await cq.answer("Ошибка при удалении.", show_alert=True)
            return

        # пометка в исходном сообщении (если это было сообщение бота с карточкой/уведомлением)
        new_text = _append_status_once(cq.message.text or "", f"{STATUS_LINE_DELETED} (#{req_id})")
        try:
            await cq.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
            _MARKED.add((cq.message.chat.id, cq.message.message_id))
        except Exception:
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        deleted_p = res.get("Покупка", 0)
        deleted_s = res.get("Продажа", 0)
        await cq.answer(f"Удалено из таблиц: Покупка={deleted_p}, Продажа={deleted_s}")

    return r
