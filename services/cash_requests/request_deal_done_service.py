from __future__ import annotations

from aiogram.types import CallbackQuery

from db_asyncpg.repo import Repo
from services.cash_requests.request_router_service import RequestRouterService
from services.cash_requests.request_schedule_service import RequestScheduleService
from utils.auth import require_manager_or_admin_callback


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
            await cq.answer("Кнопка доступна только в чате заявок.", show_alert=True)
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

        removed = await self.schedule_service.remove_entry(req_id=req_id)
        if not removed:
            await cq.answer("Запись в расписании не найдена.", show_alert=True)
            return

        try:
            await self.schedule_service.sync_board(
                bot=cq.bot,
                city=city,
            )
        except Exception:
            pass

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Сделка завершена")