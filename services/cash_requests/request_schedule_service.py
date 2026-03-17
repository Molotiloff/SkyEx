from __future__ import annotations

from aiogram import Bot

from db_asyncpg.repo import Repo
from services.cash_requests.models import ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService


class RequestScheduleService:
    def __init__(
        self,
        *,
        repo: Repo,
        router_service: RequestRouterService,
    ) -> None:
        self.repo = repo
        self.router_service = router_service

    @staticmethod
    def _norm_city(city: str) -> str:
        return (city or "").strip().lower()

    @staticmethod
    def _decorate_line(line_text: str) -> str:
        text = (line_text or "").strip()
        if text.startswith("-"):
            return f"🟥 {text}"
        if text.startswith("+"):
            return f"🟩 {text}"
        return text

    async def upsert_entry(self, entry: ScheduleEntry) -> None:
        await self.repo.upsert_request_schedule_entry(
            req_id=entry.req_id,
            city=self._norm_city(entry.city),
            hhmm=entry.hhmm,
            request_kind=entry.request_kind,
            line_text=entry.line_text,
            client_name=entry.client_name,
            request_chat_id=entry.request_chat_id,
            request_message_id=entry.request_message_id,
        )

    async def remove_entry(self, *, req_id: str) -> bool:
        return await self.repo.deactivate_request_schedule_entry(req_id=req_id)

    async def render_board(self, city: str) -> str:
        city_norm = self._norm_city(city)
        rows = await self.repo.list_request_schedule_entries(city=city_norm)

        lines = ["📋 <b>Ближайшие клиенты</b>", ""]
        if not rows:
            lines.append("Пока пусто")
            return "\n".join(lines)

        for item in rows:
            decorated = self._decorate_line(item["line_text"])
            lines.append(f"<code>{item['hhmm']}</code> — {decorated}")

        return "\n".join(lines)

    async def sync_board(self, bot: Bot, *, city: str) -> None:
        city_norm = self._norm_city(city)
        schedule_chat_id = self.router_service.pick_schedule_chat_for_city(city_norm)
        if not schedule_chat_id:
            return

        text = await self.render_board(city_norm)
        board = await self.repo.get_request_schedule_board(city=city_norm)

        if board:
            try:
                await bot.edit_message_text(
                    chat_id=int(board["board_chat_id"]),
                    message_id=int(board["board_message_id"]),
                    text=text,
                    parse_mode="HTML",
                )
                return
            except Exception:
                pass

        msg = await bot.send_message(
            chat_id=int(schedule_chat_id),
            text=text,
            parse_mode="HTML",
        )

        await self.repo.upsert_request_schedule_board(
            city=city_norm,
            board_chat_id=int(msg.chat.id),
            board_message_id=int(msg.message_id),
        )