from __future__ import annotations

import random

from aiogram.types import Message

from db_asyncpg.ports import ClientWalletScheduleRepositoryPort
from services.cash_requests.models import RequestContext, ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from utils.info import get_chat_name


class CashRequestUseCaseBase:
    def __init__(
        self,
        *,
        repo: ClientWalletScheduleRepositoryPort,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service

    @staticmethod
    def _split_contacts(kind: str, contact1: str, contact2: str) -> tuple[str, str]:
        if kind in ("dep", "fx"):
            tg_to = contact1
            tg_from = contact2
        else:
            tg_from = contact1
            tg_to = contact2
        return (tg_from or "").strip(), (tg_to or "").strip()

    @staticmethod
    def _gen_req_id() -> str:
        return f"Б-{random.randint(0, 999999):06d}"

    @staticmethod
    def _gen_pin() -> str:
        return f"{random.randint(100, 999)}-{random.randint(100, 999)}"

    @staticmethod
    def _build_schedule_line(
        *,
        kind: str,
        client_name: str,
        pretty_amount: str | None = None,
        code: str | None = None,
        pretty_in: str | None = None,
        in_code: str | None = None,
        pretty_out: str | None = None,
        out_code: str | None = None,
    ) -> str | None:
        client = (client_name or "—").strip() or "—"

        if kind == "dep" and pretty_amount and code:
            return f"+{pretty_amount} {code.upper()} — {client}"

        if kind == "wd" and pretty_amount and code:
            return f"-{pretty_amount} {code.upper()} — {client}"

        if kind == "fx" and pretty_in and in_code and pretty_out and out_code:
            return f"{pretty_in} {in_code.upper()} → {pretty_out} {out_code.upper()} — {client}"

        return None

    async def _build_request_context(self, message: Message, city: str) -> RequestContext:
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id=message.chat.id, name=chat_name)
        return RequestContext(
            city=(city or self.router_service.default_city).strip().lower(),
            request_chat_id=self.router_service.pick_request_chat_for_city(city),
            chat_name=chat_name,
            client_id=client_id,
        )

    async def _sync_schedule_without_time(
        self,
        *,
        req_id: str,
        city: str,
        line_text: str,
        request_kind: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
        bot,
    ) -> None:
        if not line_text:
            return

        await self.schedule_service.upsert_entry(
            ScheduleEntry(
                req_id=req_id,
                city=city,
                hhmm=None,
                request_kind=request_kind,
                line_text=line_text,
                client_name=client_name,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
            )
        )

        if self.router_service.pick_schedule_chat_for_city(city):
            try:
                await self.schedule_service.sync_board(
                    bot=bot,
                    city=city,
                )
            except Exception:
                pass

    async def _sync_schedule_keep_existing_time(
        self,
        *,
        req_id: str,
        city: str,
        hhmm: str | None,
        line_text: str,
        request_kind: str,
        client_name: str,
        request_chat_id: int,
        request_message_id: int,
        bot,
    ) -> None:
        if not line_text:
            return

        await self.schedule_service.upsert_entry(
            ScheduleEntry(
                req_id=req_id,
                city=city,
                hhmm=hhmm,
                request_kind=request_kind,
                line_text=line_text,
                client_name=client_name,
                request_chat_id=request_chat_id,
                request_message_id=request_message_id,
            )
        )

        if self.router_service.pick_schedule_chat_for_city(city):
            try:
                await self.schedule_service.sync_board(
                    bot=bot,
                    city=city,
                )
            except Exception:
                pass

    async def _edit_request_chat_message(
        self,
        *,
        bot,
        req_id: str,
        text_city: str,
        city_markup,
    ) -> tuple[int, int] | None:
        old_entry = await self.repo.get_request_schedule_entry_by_req_id(req_id=req_id)
        if not old_entry:
            return None

        old_chat_id = old_entry.get("request_chat_id")
        old_message_id = old_entry.get("request_message_id")
        if not old_chat_id or not old_message_id:
            return None

        try:
            await bot.edit_message_text(
                chat_id=int(old_chat_id),
                message_id=int(old_message_id),
                text=text_city,
                parse_mode="HTML",
                reply_markup=city_markup,
            )
            return int(old_chat_id), int(old_message_id)
        except Exception:
            return None
