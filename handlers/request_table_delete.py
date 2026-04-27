from __future__ import annotations

from typing import Iterable
from typing import cast

from aiogram import F, Router

from gutils.requests_sheet_gateway import GutilsSheetsTradeGateway
from keyboards.request import CB_TABLE_DEL_NO, CB_TABLE_DEL_YES
from services.request_table.delete_interaction_service import RequestTableDeleteInteractionService
from services.request_table.message_builder import RequestTableMessageBuilder
from services.request_table.session_store import RequestTableSessionStore
from services.request_table.sheets_trade_gateway import SheetsTradeGateway


def get_table_delete_router(*, request_chat_ids: Iterable[int]) -> Router:
    router = Router()
    sheets_gateway = cast(SheetsTradeGateway, GutilsSheetsTradeGateway())
    interaction_service = RequestTableDeleteInteractionService(
        request_chat_ids=request_chat_ids,
        session_store=RequestTableSessionStore(),
        message_builder=RequestTableMessageBuilder(),
        sheets_gateway=sheets_gateway,
    )

    router.callback_query.register(
        interaction_service.handle_no,
        F.data.startswith(CB_TABLE_DEL_NO),
    )
    router.callback_query.register(
        interaction_service.handle_yes,
        F.data.startswith(CB_TABLE_DEL_YES),
    )
    return router
