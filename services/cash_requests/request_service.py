from __future__ import annotations

from typing import Mapping

from aiogram.types import Message

from db_asyncpg.repo import Repo
from services.cash_requests.create_cash_request import CreateCashRequest
from services.cash_requests.edit_cash_request import EditCashRequest
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from utils.auth import manager_or_admin_message_required
from utils.request_parsing import ParsedRequest, parse_dep_wd, parse_fx


class CashRequestService:
    def __init__(
        self,
        *,
        repo: Repo,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        cmd_map: Mapping[str, tuple[str, str]],
        fx_cmd_map: Mapping[str, tuple[str, str, str]],
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.cmd_map = dict(cmd_map)
        self.fx_cmd_map = dict(fx_cmd_map)
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)
        self.create_cash_request = CreateCashRequest(
            repo=repo,
            router_service=router_service,
            schedule_service=schedule_service,
        )
        self.edit_cash_request = EditCashRequest(
            repo=repo,
            router_service=router_service,
            schedule_service=schedule_service,
        )

    @property
    def supported_commands(self) -> tuple[str, ...]:
        return tuple(set(self.cmd_map.keys()) | set(self.fx_cmd_map.keys()))

    @staticmethod
    def _reply_plain(reply: Message) -> str:
        if reply.caption is not None and not reply.text:
            return reply.caption or ""
        return reply.text or ""

    def help_text(self) -> str:
        cities = ", ".join(sorted(self.router_service.city_keys)) if self.router_service.city_keys else "—"
        return (
            "Форматы:\n"
            "• /депр [город] <сумма/expr> [Принимает] [Выдает] [! комментарий]\n"
            "• /выдр [город] <сумма/expr> [Выдает] [Принимает] [! комментарий]\n"
            "• /првд [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /пдвр [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n"
            "• /прве [город] <сумма_in> <сумма_out> [Кассир] [Клиент] [! комментарий]\n\n"
            f"Города: {cities}\n"
            f"Если город не указан — по умолчанию: {self.router_service.default_city}\n\n"
            "Редактирование:\n"
            "• ответьте командой на карточку БОТА — можно менять сумму, город, контакты, комментарий;\n"
            "• тип и валюты менять нельзя."
        )

    @manager_or_admin_message_required
    async def handle(self, message: Message) -> None:
        parsed: ParsedRequest | None = parse_fx(
            message.text or "",
            fx_cmd_map=self.fx_cmd_map,
            city_keys=self.router_service.city_keys,
            default_city=self.router_service.default_city,
        )
        if not parsed:
            parsed = parse_dep_wd(
                message.text or "",
                cmd_map=self.cmd_map,
                city_keys=self.router_service.city_keys,
                default_city=self.router_service.default_city,
            )
        if not parsed:
            await message.answer(self.help_text())
            return

        reply_msg = getattr(message, "reply_to_message", None)
        is_reply_to_bot = bool(
            reply_msg
            and reply_msg.from_user
            and reply_msg.from_user.id == message.bot.id
            and (reply_msg.text or reply_msg.caption)
        )

        if is_reply_to_bot:
            await self.edit_cash_request.execute(
                message=message,
                parsed=parsed,
                old_text=self._reply_plain(reply_msg),
                reply_msg_id=reply_msg.message_id,
            )
            return

        await self.create_cash_request.execute(message=message, parsed=parsed)
