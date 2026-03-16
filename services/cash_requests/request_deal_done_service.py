from __future__ import annotations

import re
from datetime import datetime

from aiogram.types import CallbackQuery

from db_asyncpg.repo import Repo
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from utils.auth import require_manager_or_admin_callback


_RE_DONE_LINE = re.compile(
    r"^\s*Сделка\s+проведена\s*:\s*(?:<code>)?.+?(?:</code>)?\s*$",
    re.IGNORECASE | re.M,
)


class RequestDealDoneService:
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
    def _reply_html_text_and_kind(msg) -> tuple[str, bool]:
        if msg.caption is not None and not msg.text:
            return (msg.html_caption or msg.caption or ""), True
        return (msg.html_text or msg.text or ""), False

    @staticmethod
    def _upsert_done_line(text: str) -> str:
        src = text or ""
        done_line = f"✅ Сделка проведена: <code>{datetime.now().strftime('%Y-%m-%d %H:%M')}</code>"

        if _RE_DONE_LINE.search(src):
            return _RE_DONE_LINE.sub(done_line, src)

        marker = "\n----\nСоздал"
        idx = src.find(marker)
        if idx != -1:
            return src[:idx] + "\n" + done_line + src[idx:]

        if src.endswith("\n"):
            return src + done_line
        return src + "\n" + done_line

    async def handle(self, cq: CallbackQuery) -> None:
        msg = cq.message
        if not msg:
            await cq.answer()
            return

        if not await require_manager_or_admin_callback(
            self.repo,
            cq,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            await cq.answer("Недостаточно прав.", show_alert=True)
            return

        if not self.router_service.is_request_chat(msg.chat.id):
            await cq.answer("Кнопка доступна только в чате сделок.", show_alert=True)
            return

        data = (cq.data or "").strip()
        prefix = "cash:deal_done:req:"
        if not data.startswith(prefix):
            await cq.answer("Некорректные данные.", show_alert=True)
            return

        req_id = data[len(prefix):].strip()
        if not req_id:
            await cq.answer("Не удалось определить заявку.", show_alert=True)
            return

        city = self.router_service.city_by_request_chat(msg.chat.id)
        if not city:
            await cq.answer("Не удалось определить город.", show_alert=True)
            return

        # 1. Удаляем из расписания, если запись там есть.
        removed = await self.schedule_service.remove_entry(req_id=req_id)

        # 2. Обновляем board расписания только если запись реально была.
        if removed:
            try:
                await self.schedule_service.sync_board(
                    bot=cq.bot,
                    city=city,
                )
            except Exception:
                pass

        # 3. Обновляем саму заявку: ставим статус "Сделка проведена"
        old_text, is_caption = self._reply_html_text_and_kind(msg)
        new_text = self._upsert_done_line(old_text)

        try:
            if is_caption:
                await cq.bot.edit_message_caption(
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                    caption=new_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            else:
                await cq.bot.edit_message_text(
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                    text=new_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
        except Exception:
            # если редактирование текста не удалось — хотя бы уберём кнопку
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass

        if removed:
            await cq.answer("Сделка завершена")
        else:
            await cq.answer("Сделка проведена")
