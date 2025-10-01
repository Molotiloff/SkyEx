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

    # --- показать адрес ---
    async def _cmd_show(self, message: Message) -> None:
        # просмотр доступен всем
        addr = await self.repo.get_setting(SETTING_KEY)
        if not addr:
            await message.answer("USDT-кошелёк пока не задан.")
            return

        await message.answer(
            "USDT TRC-20 кошелёк (нажмите, чтобы скопировать):\n"
            f"<code>{html.escape(addr)}</code>",
            parse_mode="HTML",
        )

    # --- задать адрес ---
    async def _cmd_set(self, message: Message) -> None:
        # менять можно только из админского чата
        if self.admin_chat_ids and message.chat.id not in self.admin_chat_ids:
            await message.answer("Эту команду можно использовать только в админском чате.")
            return

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("Использование: /setwallet <адрес USDT> (или /setкош <адрес>)")
            return

        addr = parts[1].strip()
        # Лёгкая валидация: не пусто и разумная длина
        if len(addr) < 26 or len(addr) > 128:
            await message.answer("Похоже, адрес некорректный. Проверьте и попробуйте снова.")
            return

        await self.repo.set_setting(SETTING_KEY, addr)
        await message.answer("✅ USDT-кошелёк обновлён.")

    def _register(self) -> None:
        # показать: /кош и /usdt
        self.router.message.register(self._cmd_show, Command("кош"))
        # задать: /setкош и /setwallet
        self.router.message.register(self._cmd_set, Command("setwallet"))