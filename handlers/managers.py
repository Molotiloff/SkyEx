# handlers/managers.py
import html
import re
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo


class ManagersHandler:
    """
    /mgr — управление менеджерами. Доступ: только в админском чате (из admin_chat_ids)
    и только пользователям из admin_user_ids (если список не пуст).
    """
    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int], admin_user_ids: list[int]) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()
        self._register()

    def _allowed(self, m: Message) -> bool:
        in_admin_chat = (m.chat and m.chat.id in self.admin_chat_ids)
        if not in_admin_chat:
            return False
        # если список админов-пользователей пуст — разрешим всем в админском чате
        if not self.admin_user_ids:
            return True
        return bool(m.from_user and m.from_user.id in self.admin_user_ids)

    async def _cmd_mgr(self, message: Message) -> None:
        if not self._allowed(message):
            await message.answer("⛔ Команда доступна только админам в админском чате.")
            return

        text = (message.text or "").strip()

        # /mgr + <user_id> [display_name...]
        m_add = re.match(r"^/mgr(?:@\w+)?\s+\+\s+(\d+)(?:\s+(.+))?$", text, flags=re.I | re.U)
        if m_add:
            user_id = int(m_add.group(1))
            display_name = (m_add.group(2) or "").strip()
            ok = await self.repo.add_manager(user_id=user_id, display_name=display_name)
            disp = f" — {html.escape(display_name)}" if display_name else ""
            await message.answer("✅ Добавлен менеджер: <code>{}</code>{}".format(user_id, disp), parse_mode="HTML")
            return

        # /mgr - <user_id>
        m_del = re.match(r"^/mgr(?:@\w+)?\s+\-\s+(\d+)\s*$", text, flags=re.I | re.U)
        if m_del:
            user_id = int(m_del.group(1))
            ok = await self.repo.remove_manager(user_id=user_id)
            await message.answer("✅ Удалён менеджер: <code>{}</code>".format(user_id) if ok else "❌ Не найден.",
                                 parse_mode="HTML")
            return

        # список
        managers = await self.repo.list_managers()
        if not managers:
            await message.answer("Список менеджеров пуст.")
            return

        lines = ["Менеджеры:"]
        for m in managers:
            uid = m["user_id"]
            name = m.get("display_name") or ""
            lines.append(f"• <code>{uid}</code>{' — ' + html.escape(name) if name else ''}")

        await message.answer("\n".join(lines), parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_mgr, Command("mgr"))
