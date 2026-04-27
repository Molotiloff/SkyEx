from __future__ import annotations

from typing import Iterable
from typing import cast

from aiogram import F, Router

from db_asyncpg.ports import ExchangeRequestRepositoryPort
from gutils.requests_sheet_gateway import GutilsSheetsTradeGateway
from keyboards.request import CB_TABLE_DONE
from services.request_table.done_interaction_service import RequestTableDoneInteractionService
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.sheets_trade_gateway import SheetsTradeGateway
from services.request_table.table_done_service import RequestTableDoneService


def get_table_done_router(*, repo: ExchangeRequestRepositoryPort, request_chat_ids: Iterable[int]) -> Router:
    router = Router()
    session_store = RequestTableSessionStore()
    message_builder = RequestTableMessageBuilder()
    sheets_gateway = cast(SheetsTradeGateway, GutilsSheetsTradeGateway())
    done_service = RequestTableDoneService(sheets_gateway=sheets_gateway)
    interaction_service = RequestTableDoneInteractionService(
        repo=repo,
        request_chat_ids=request_chat_ids,
        done_service=done_service,
        session_store=session_store,
        message_builder=message_builder,
        sheets_gateway=sheets_gateway,
    )

    router.callback_query.register(
        interaction_service.handle,
        F.data.startswith(CB_TABLE_DONE),
    )
    return router
