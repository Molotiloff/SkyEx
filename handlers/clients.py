# handlers/clients.py
import html
from typing import Iterable

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo


def _chunk(text: str, limit: int = 3500) -> list[str]:
    out, cur, total = [], [], 0
    for line in text.splitlines(True):
        if total + len(line) > limit and cur:
            out.append("".join(cur))
            cur, total = [], 0
        cur.append(line)
        total += len(line)
    if cur:
        out.append("".join(cur))
    return out


class ClientsHandler:
    """
    /клиенты — список клиентов. Доступ: только из admin_chat_ids.
    """

    def __init__(self, repo: Repo, admin_chat_ids: Iterable[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_clients(self, message: Message) -> None:
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Команда доступна только в админском чате.")
            return

        clients = await self.repo.list_clients()

        lines: list[str] = [f"<b>Клиенты: {len(clients)}</b>"]

        for c in sorted(clients, key=lambda x: (x.get("name") or "").lower()):
            name = html.escape(c.get("name") or "")
            city = html.escape(c.get("city") or "")
            chat_id = c["chat_id"]

            line = f"{name}"
            if city:
                line += f" — {city}"
            line += f"\n    chat_id = <code>{chat_id}</code>"
            lines.append(line)

        text = "\n".join(lines)

        for chunk in _chunk(text):
            await message.answer(chunk, parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_clients, Command("клиенты"))
