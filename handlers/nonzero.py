# handlers/nonzero.py
import html
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.format_wallet_compact import format_wallet_compact
from utils.info import get_chat_name


class NonZeroHandler:
    """
    /дай — показать все счета с ненулевым балансом (включая отрицательные),
    в «компактном» формате. Команда публичная.
    """
    def __init__(self, repo: Repo, admin_chat_ids=None, admin_user_ids=None) -> None:
        self.repo = repo
        # параметры оставлены для совместимости, но не используются
        self.router = Router()
        self._register()

    async def _cmd_give(self, message: Message) -> None:
        chat_id = message.chat.id
        chat_name = get_chat_name(message)
        client_id = await self.repo.ensure_client(chat_id, chat_name)

        rows = await self.repo.snapshot_wallet(client_id)
        # фильтруем нули и печатаем в компактном формате (правый край, без дублирования кода валюты)
        compact = format_wallet_compact(rows, only_nonzero=True)
        if compact == "Пусто":
            await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
            return

        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(compact)
        await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

    def _register(self) -> None:
        self.router.message.register(self._cmd_give, Command("дай"))
