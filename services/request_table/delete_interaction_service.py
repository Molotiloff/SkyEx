from __future__ import annotations

import logging
from typing import Iterable

from aiogram.types import CallbackQuery

from gutils.requests_sheet import SheetsWriteError
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.sheets_trade_gateway import SheetsTradeGateway


class RequestTableDeleteInteractionService:
    _SCOPE = "table_delete"

    def __init__(
        self,
        *,
        request_chat_ids: Iterable[int],
        session_store: RequestTableSessionStore,
        message_builder: RequestTableMessageBuilder,
        sheets_gateway: SheetsTradeGateway,
    ) -> None:
        self.allowed = {int(x) for x in request_chat_ids}
        self.session_store = session_store
        self.message_builder = message_builder
        self.sheets_gateway = sheets_gateway

    async def handle_no(self, cq: CallbackQuery) -> None:
        if not cq.message or cq.message.chat.id not in self.allowed:
            await cq.answer("Недоступно здесь.", show_alert=True)
            return
        try:
            await cq.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        await cq.answer("Оставляем строки в таблицах.")

    async def handle_yes(self, cq: CallbackQuery) -> None:
        if not cq.message or cq.message.chat.id not in self.allowed:
            await cq.answer("Недоступно здесь.", show_alert=True)
            return

        key = (cq.message.chat.id, cq.message.message_id)
        if self.session_store.is_pending(self._SCOPE, key):
            await cq.answer("⏳ Уже обрабатывается…")
            return

        try:
            await cq.message.edit_reply_markup(reply_markup=self.message_builder.processing_kb())
        except Exception:
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        self.session_store.add_pending(self._SCOPE, key)
        try:
            req_id = self._parse_req_id(cq.data or "")
        except ValueError:
            self.session_store.discard_pending(self._SCOPE, key)
            await cq.answer("Некорректные данные", show_alert=True)
            return

        try:
            res = self.sheets_gateway.delete_rows_by_request_id(
                req_id=req_id,
                spreadsheet=None,
                sheets=("Покупка", "Продажа"),
            )
        except SheetsWriteError as e:
            logging.exception("Sheets delete failed: %s", e)
            await cq.answer("Не удалось удалить из Google Sheets.", show_alert=True)
            return
        except Exception as e:
            logging.exception("Unexpected delete error: %s", e)
            await cq.answer("Ошибка при удалении.", show_alert=True)
            return
        finally:
            self.session_store.discard_pending(self._SCOPE, key)

        new_text = self.message_builder.append_status_once(
            cq.message.text or "",
            self.message_builder.deleted_status(req_id),
        )
        try:
            await cq.message.edit_text(new_text, parse_mode="HTML", reply_markup=None)
            self.session_store.mark(self._SCOPE, key)
        except Exception:
            try:
                await cq.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        await cq.answer(
            self.message_builder.deleted_summary(
                deleted_buy=res.get("Покупка", 0),
                deleted_sale=res.get("Продажа", 0),
            )
        )

    @staticmethod
    def _parse_req_id(data: str) -> str:
        parts = (data or "").split(":")
        req_id = parts[-1].strip() if parts else ""
        if not req_id:
            raise ValueError("empty req_id")
        return req_id
