# handlers/office_cards.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile


@dataclass(frozen=True, slots=True)
class OfficeCard:
    command: str
    image_path: Path
    caption: str


class OfficeCardsHandler:
    """
    Генератор команд вида /екб, /члб, ...
    Каждая команда отправляет фото + текст (caption).
    """

    def __init__(self, cards: Mapping[str, OfficeCard]) -> None:
        self.router = Router()
        # ключи нормализуем: "екб" -> OfficeCard(...)
        self.cards: dict[str, OfficeCard] = {k.strip().lower(): v for k, v in cards.items()}
        self._register()

    def _register(self) -> None:
        # регистрируем каждую команду явно
        for cmd in self.cards.keys():
            self.router.message.register(self._send_card, Command(cmd, ignore_mention=True))

        # страховка на случай, если команда пришла как текст:
        # /екб или /екб@BotName
        cmds_re = "|".join(map(repr, self.cards.keys())).replace("'", "")
        self.router.message.register(
            self._send_card,
            lambda m: bool((m.text or "").lower().startswith("/")) and __import__("re").match(
                rf"(?iu)^/({cmds_re})(?:@\w+)?\b", (m.text or "")
            ),
        )

    async def _send_card(self, message: Message) -> None:
        text = (message.text or "").strip()
        if not text.startswith("/"):
            return

        # вытаскиваем команду: "/екб@bot ..." -> "екб"
        cmd_token = text[1:].split(None, 1)[0]
        cmd = cmd_token.split("@", 1)[0].strip().lower()

        card = self.cards.get(cmd)
        if not card:
            return

        if not card.image_path.exists():
            await message.answer(f"Файл не найден: {card.image_path.as_posix()}")
            return

        photo = FSInputFile(card.image_path)
        await message.answer_photo(photo=photo, caption=card.caption)