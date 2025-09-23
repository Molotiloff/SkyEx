# handlers/city.py
import html
import re
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo


class CityAssignHandler:
    """
    /город <chat_id> <город>. Доступ: только из admin_chat_ids.
    """
    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_city(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        text = (message.text or "").strip()
        # поддержим формат: /город <chat_id> <город ...>
        m = re.match(r"^/город(?:@\w+)?\s+(-?\d+)\s+(.+)$", text, flags=re.IGNORECASE | re.UNICODE)
        if not m:
            await message.answer("Использование: /город <chat_id> <город>\nПример: /город 123456789 Екатеринбург")
            return

        try:
            target_chat_id = int(m.group(1))
        except ValueError:
            await message.answer("Некорректный chat_id.")
            return
        city = m.group(2).strip()
        if not city:
            await message.answer("Укажите город после chat_id.")
            return

        rec = await self.repo.set_client_city_by_chat_id(target_chat_id, city)
        if not rec:
            await message.answer(f"Клиент с chat_id={target_chat_id} не найден.")
            return

        safe_name = html.escape(rec.get("name") or "")
        safe_city = html.escape(rec.get("city") or "")
        await message.answer(f"✅ Город для «{safe_name}» (chat_id={target_chat_id}) установлен: {safe_city}")

    def _register(self) -> None:
        self.router.message.register(self._cmd_city, Command("город"))
