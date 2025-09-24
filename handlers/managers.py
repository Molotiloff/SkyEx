# handlers/managers.py
import html
import re
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from db_asyncpg.repo import Repo

class ManagersHandler:
    """
    /mgr — управление менеджерами.
    Доступ: только в одном админском чате (admin_chat_id). Любой участник этого чата.
    """
    def __init__(self, repo: Repo, admin_chat_id: int) -> None:
        self.repo = repo
        self.admin_chat_id = int(admin_chat_id)
        self.router = Router()
        self._register()

    def _allowed(self, m: Message) -> bool:
        return bool(m.chat and m.chat.id == self.admin_chat_id)

    async def _cmd_mgr(self, message: Message) -> None:
        if not self._allowed(message):
            await message.answer("⛔ Команда доступна только в админском чате.")
            return

        text = (message.text or "").strip()

        # /mgr + <user_id> [display_name...]
        m_add = re.match(r"^/mgr(?:@\w+)?\s*\+\s+(\d+)(?:\s+(.+))?$", text, flags=re.I | re.U)
        if m_add:
            user_id = int(m_add.group(1))
            display_name = (m_add.group(2) or "").strip()
            ok = await self.repo.add_manager(user_id=user_id, display_name=display_name)
            disp = f" — {html.escape(display_name)}" if display_name else ""
            await message.answer(
                ("✅ Добавлен менеджер: <code>{}</code>{}".format(user_id, disp))
                if ok else "❌ Не удалось добавить менеджера.",
                parse_mode="HTML",
            )
            return

        # /mgr - <user_id>
        m_del = re.match(r"^/mgr(?:@\w+)?\s*-\s+(\d+)\s*$", text, flags=re.I | re.U)
        if m_del:
            user_id = int(m_del.group(1))
            ok = await self.repo.remove_manager(user_id=user_id)
            await message.answer(
                ("✅ Удалён менеджер: <code>{}</code>".format(user_id))
                if ok else "❌ Менеджер не найден.",
                parse_mode="HTML",
            )
            return

        # без аргументов — список
        managers = await self.repo.list_managers()
        if not managers:
            await message.answer("Список менеджеров пуст.")
            return

        lines = ["Менеджеры:"]
        for m in managers:
            uid = m["user_id"]
            name = (m.get("display_name") or "").strip()
            lines.append(f"• <code>{uid}</code>{(' — ' + html.escape(name)) if name else ''}")

        await message.answer("\n".join(lines), parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_mgr, Command("mgr"))
