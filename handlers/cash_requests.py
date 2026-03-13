from __future__ import annotations

from typing import Iterable, Mapping

from aiogram import F, Router
from aiogram.filters import Command

from db_asyncpg.repo import Repo
from keyboards.request import CB_DEAL_DONE, CB_ISSUE_DONE
from services.cash_requests.constants import CMD_MAP, FX_CMD_MAP
from services.cash_requests.request_deal_done_service import RequestDealDoneService
from services.cash_requests.request_issue_service import RequestIssueService
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from services.cash_requests.request_service import CashRequestService
from services.cash_requests.request_time_service import RequestTimeService


class CashRequestsHandler:
    def __init__(
        self,
        repo: Repo,
        *,
        admin_chat_ids: Iterable[int] | None = None,
        admin_user_ids: Iterable[int] | None = None,
        request_chat_id: int | None = None,
        city_cash_chats: Mapping[str, int] | None = None,
        city_schedule_chats: Mapping[str, int] | None = None,
        default_city: str = "екб",
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])

        self.router = Router()

        self.router_service = RequestRouterService(
            request_chat_id=request_chat_id,
            city_cash_chats=city_cash_chats,
            city_schedule_chats=city_schedule_chats,
            default_city=default_city,
        )

        self.schedule_service = RequestScheduleService(
            repo=repo,
            router_service=self.router_service,
        )

        self.request_service = CashRequestService(
            repo=repo,
            router_service=self.router_service,
            cmd_map=CMD_MAP,
            fx_cmd_map=FX_CMD_MAP,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

        self.request_time_service = RequestTimeService(
            repo=repo,
            router_service=self.router_service,
            schedule_service=self.schedule_service,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

        self.request_issue_service = RequestIssueService(
            repo=repo,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

        self.request_deal_done_service = RequestDealDoneService(
            repo=repo,
            router_service=self.router_service,
            schedule_service=self.schedule_service,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        )

        self._register()

    def _register(self) -> None:
        self.router.message.register(
            self.request_service.handle,
            Command(*self.request_service.supported_commands),
        )
        self.router.message.register(
            self.request_time_service.handle,
            Command("время"),
        )
        self.router.callback_query.register(
            self.request_issue_service.handle,
            F.data.startswith(CB_ISSUE_DONE),
        )
        self.router.callback_query.register(
            self.request_deal_done_service.handle,
            F.data.startswith(CB_DEAL_DONE),
        )