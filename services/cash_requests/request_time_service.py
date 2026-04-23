from __future__ import annotations

import re

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from db_asyncpg.repo import Repo
from services.cash_requests.models import ScheduleEntry
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from utils.auth import manager_or_admin_message_required
from utils.request_text_parser import (
    build_schedule_line_from_plain,
    detect_kind_from_card,
    extract_client_name,
    extract_edit_source,
    starts_with_request,
    upsert_time_line,
)

_RE_TIME_CMD = re.compile(
    r"^/время(?:@\w+)?\s+((?:[01]?\d|2[0-3]):[0-5]\d)\s*$",
    re.IGNORECASE,
)


class RequestTimeService:
    def __init__(
        self,
        *,
        repo: Repo,
        router_service: RequestRouterService,
        schedule_service: RequestScheduleService,
        admin_chat_ids: set[int],
        admin_user_ids: set[int],
    ) -> None:
        self.repo = repo
        self.router_service = router_service
        self.schedule_service = schedule_service
        self.admin_chat_ids = set(admin_chat_ids)
        self.admin_user_ids = set(admin_user_ids)

    @staticmethod
    def _reply_html(reply: Message) -> tuple[str, bool]:
        if reply.caption is not None and not reply.text:
            return (reply.html_caption or reply.caption or ""), True
        return (reply.html_text or reply.text or ""), False

    @staticmethod
    def _reply_plain(reply: Message) -> str:
        if reply.caption is not None and not reply.text:
            return reply.caption or ""
        return reply.text or ""

    @manager_or_admin_message_required
    async def handle(self, message: Message) -> None:
        if not message.chat or not self.router_service.is_request_chat(message.chat.id):
            return

        raw = (message.text or "").strip()
        m = _RE_TIME_CMD.match(raw)
        if not m:
            await message.answer("Формат: /время 10:00")
            return

        hhmm_raw = m.group(1)
        hh, mm = hhmm_raw.split(":")
        hhmm = f"{int(hh):02d}:{mm}"

        reply = getattr(message, "reply_to_message", None)
        if not reply:
            await message.answer("Нужно ответить командой /время на сообщение с заявкой.")
            return

        target_html, is_caption = self._reply_html(reply)
        if not target_html.strip():
            await message.answer("Нужно ответить на сообщение с текстом.")
            return

        if not starts_with_request(target_html):
            await message.answer("Это не похоже на заявку.")
            return

        updated = upsert_time_line(target_html, hhmm)

        try:
            if is_caption:
                await message.bot.edit_message_caption(
                    chat_id=reply.chat.id,
                    message_id=reply.message_id,
                    caption=updated,
                    parse_mode="HTML",
                    reply_markup=reply.reply_markup,
                )
            else:
                await message.bot.edit_message_text(
                    chat_id=reply.chat.id,
                    message_id=reply.message_id,
                    text=updated,
                    parse_mode="HTML",
                    reply_markup=reply.reply_markup,
                )
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                await message.answer("Время уже установлено.")
                return
            await message.answer(f"Не удалось обновить заявку: {e}")
            return
        except Exception as e:
            await message.answer(f"Не удалось обновить заявку: {e}")
            return

        city = self.router_service.city_by_request_chat(message.chat.id)
        if not city:
            await message.answer(
                "Не удалось определить город для этого чата заявок. "
                "Проверь конфигурацию CITY_CASH_CHAT_IDS."
            )
            return

        plain_reply = self._reply_plain(reply)

        line_text = build_schedule_line_from_plain(plain_reply, fallback_client="—")
        request_kind = detect_kind_from_card(plain_reply) or "unknown"
        client_name = extract_client_name(plain_reply, fallback="—")
        src = extract_edit_source(plain_reply)

        if not src:
            await message.answer("Не удалось определить номер заявки.")
            return

        if line_text:
            await self.schedule_service.upsert_entry(
                ScheduleEntry(
                    req_id=src.req_id,
                    city=city,
                    hhmm=hhmm,
                    request_kind=request_kind,
                    line_text=line_text,
                    client_name=client_name,
                    request_chat_id=reply.chat.id,
                    request_message_id=reply.message_id,
                )
            )

            if self.router_service.pick_schedule_chat_for_city(city):
                try:
                    await self.schedule_service.sync_board(
                        bot=message.bot,
                        city=city,
                    )
                except Exception:
                    pass

        await message.answer(f"✅ Время добавлено: {hhmm}")
