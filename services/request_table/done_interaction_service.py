from __future__ import annotations

import logging
from typing import Iterable

from aiogram.types import CallbackQuery

from db_asyncpg.repo import Repo
from gutils.requests_sheet import SheetsWriteError
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.sheets_trade_gateway import SheetsTradeGateway
from services.request_table.table_done_service import RequestTableDoneService
from utils.req_index import req_index


class RequestTableDoneInteractionService:
    _SCOPE = "table_done"

    def __init__(
        self,
        *,
        repo: Repo,
        request_chat_ids: Iterable[int],
        done_service: RequestTableDoneService,
        session_store: RequestTableSessionStore,
        message_builder: RequestTableMessageBuilder,
        sheets_gateway: SheetsTradeGateway,
    ) -> None:
        self.repo = repo
        self.allowed = {int(x) for x in request_chat_ids}
        self.done_service = done_service
        self.session_store = session_store
        self.message_builder = message_builder
        self.sheets_gateway = sheets_gateway

    async def handle(self, cq: CallbackQuery) -> None:
        msg = cq.message
        if not msg or msg.chat.id not in self.allowed:
            await cq.answer("Недоступно здесь.", show_alert=True)
            return

        key = (msg.chat.id, msg.message_id)
        if self.session_store.is_pending(self._SCOPE, key):
            await cq.answer("⏳ Уже обрабатывается…")
            return
        if self.message_builder.STATUS_LINE_DONE in (msg.text or "") or self.session_store.is_marked(self._SCOPE, key):
            await cq.answer("Статус уже проставлен.")
            return

        parsed = await self._payload_from_callback_or_db(cq.data or "")
        if not parsed:
            logging.error("Invalid table_done callback payload: %r", cq.data)
            await cq.answer("Не удалось найти параметры заявки в БД. В таблицу не записано.", show_alert=True)
            return

        try:
            await msg.edit_reply_markup(reply_markup=self.message_builder.processing_kb())
        except Exception:
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        self.session_store.add_pending(self._SCOPE, key)
        try:
            result = self.done_service.write_by_payload(
                payload=parsed,
                message_dt=getattr(msg, "date", None),
            )
            if parsed.req_id is not None:
                req_index.mark_table_done(str(parsed.req_id))
                await self.repo.mark_exchange_request_table_done(
                    table_req_id=str(parsed.req_id),
                    is_table_done=True,
                )
        except SheetsWriteError as e:
            logging.exception("Sheets write failed: %s", e)
            error_text = str(e).lower()
            if "permission" in error_text or "forbidden" in error_text:
                sa_email = self.sheets_gateway.get_service_account_email() or "service-account@<project>.iam.gserviceaccount.com"
                await cq.answer(
                    self.message_builder.short(
                        f"Нет доступа к таблице.\nВыдайте право «Редактор» для:\n{sa_email}"
                    ),
                    show_alert=True,
                )
            else:
                await cq.answer(self.message_builder.short(str(e)), show_alert=True)
            return
        except Exception as e:
            logging.exception("Unexpected error while writing to Sheets: %s", e)
            await cq.answer(self.message_builder.short("Не удалось записать в таблицу."), show_alert=True)
            return
        finally:
            self.session_store.discard_pending(self._SCOPE, key)

        new_text = self.message_builder.append_status_once(msg.text or "", self.message_builder.STATUS_LINE_DONE)
        try:
            await msg.edit_text(new_text, parse_mode="HTML")
            self.session_store.mark(self._SCOPE, key)
        except Exception:
            pass
        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer(self.message_builder.done_summary(result=result))

    async def _payload_from_callback_or_db(self, data: str):
        parsed = self.done_service.parse_callback_payload(data)
        if parsed:
            return parsed

        table_req_id = self.done_service.parse_table_req_id(data)
        if not table_req_id:
            return None

        row = await self.repo.get_exchange_request_link_by_table_req_id(table_req_id=table_req_id)
        if not row:
            return None
        return self.done_service.payload_from_db_row(row)
