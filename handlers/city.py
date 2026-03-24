import html
import re
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo


class CityAssignHandler:
    """
    /группа <chat_id> <группа>. Доступ: только из admin_chat_ids.
    """

    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_group(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        text = (message.text or "").strip()
        m = re.match(r"^/группа(?:@\w+)?\s+(-?\d+)\s+(.+)$", text, flags=re.IGNORECASE | re.UNICODE)
        if not m:
            await message.answer(
                "Использование: /группа <chat_id> <группа>\n"
                "Пример: /группа 123456789 VIP"
            )
            return

        try:
            target_chat_id = int(m.group(1))
        except ValueError:
            await message.answer("Некорректный chat_id.")
            return

        client_group = m.group(2).strip()
        if not client_group:
            await message.answer("Укажите группу после chat_id.")
            return

        rec = await self.repo.set_client_group_by_chat_id(target_chat_id, client_group)
        if not rec:
            await message.answer(f"Клиент с chat_id={target_chat_id} не найден.")
            return

        safe_name = html.escape(rec.get("name") or "")
        safe_group = html.escape(rec.get("client_group") or "")
        await message.answer(
            f"✅ Группа для «{safe_name}» (chat_id={target_chat_id}) установлена: {safe_group}"
        )

    def _register(self) -> None:
        self.router.message.register(self._cmd_group, Command("группа"))