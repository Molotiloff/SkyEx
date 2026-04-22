from __future__ import annotations

import logging
from typing import Iterable, Set, Tuple

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from keyboards.request import CB_TABLE_DONE
from gutils.requests_sheet import (
    SheetsWriteError,
    get_service_account_email,
)
from services.request_table_done_service import RequestTableDoneService
from utils.req_index import req_index

# Константы
STATUS_LINE_DONE = "Статус: Занесена в таблицу ✅"
CB_TABLE_CONFIRM_YES = "req:table_confirm:yes"
CB_TABLE_CONFIRM_NO = "req:table_confirm:no"

# Вспомогательные наборы и шаблоны
_PENDING: Set[Tuple[int, int]] = set()
_MARKED: Set[Tuple[int, int]] = set()
_TABLE_DONE_SERVICE = RequestTableDoneService()


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


def _short(text: str, limit: int = 180) -> str:
    return text if len(text) <= limit else (text[: limit - 1] + "…")


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
            parsed = _TABLE_DONE_SERVICE.parse_callback_payload(cq.data or "")
            if not parsed:
                new_text = _append_status_once(msg.text or "", STATUS_LINE_DONE)
                try:
                    await msg.edit_text(new_text, parse_mode="HTML")
                    _MARKED.add(key)
                except Exception:
                    pass
                await cq.answer("Занесена в таблицу ✅ (нет параметров)")
                return

            result = _TABLE_DONE_SERVICE.write_by_payload(
                payload=parsed,
                message_dt=getattr(msg, "date", None),
            )
            if parsed.req_id is not None:
                req_index.mark_table_done(str(parsed.req_id))

        except SheetsWriteError as e:
            logging.exception("Sheets write failed: %s", e)
            error_text = str(e).lower()
            if "permission" in error_text or "forbidden" in error_text:
                sa_email = get_service_account_email() or "service-account@<project>.iam.gserviceaccount.com"
                await cq.answer(_short(f"Нет доступа к таблице.\nВыдайте право «Редактор» для:\n{sa_email}"), show_alert=True)
            else:
                await cq.answer(_short(str(e)), show_alert=True)
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
                f"Занесена в таблицу ✅ ({result.sheet_type}, {result.in_cur}→{result.out_cur}, "
                f"получено {result.in_amt}, отдано {result.out_amt}, курс {result.rate})"
            )
        )

    return router
