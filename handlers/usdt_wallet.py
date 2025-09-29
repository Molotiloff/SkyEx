# handlers/usdt_wallet.py
from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from db_asyncpg.repo import Repo

SETTING_KEY = "USDT_WALLET"


class UsdtWalletHandler:
    def __init__(self, repo: Repo, *, admin_chat_ids: set[int] | None = None) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.router = Router()
        self._register()

    async def _cmd_show(self, message: Message) -> None:
        addr = await self.repo.get_setting(SETTING_KEY)
        if not addr:
            await message.answer("USDT-кошелёк пока не задан.")
            return
        # просто текст, без парсинга — чтобы адрес копировался целиком
        await message.answer(f"USDT TRC-20 кошелёк (нажать, для копирования):\n<code>{html.escape(addr)}</code>",
                             parse_mode="HTML")

    async def _cmd_set(self, message: Message) -> None:
        # менять кошелёк можно только из админского чата
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Эту команду можно использовать только в админском чате.")
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("Использование: /setкош <адрес USDT>")
            return

        addr = parts[1].strip()
        # без жёсткой валидации сети — адрес может быть TRC20/ERC20 и т.д.
        await self.repo.set_setting(SETTING_KEY, addr)
        await message.answer("✅ USDT-кошелёк обновлён.")

    def _register(self) -> None:
        self.router.message.register(self._cmd_show, Command("кош"))
        self.router.message.register(self._cmd_set, Command("setwallet"))
