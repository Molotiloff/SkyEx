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
    caption: str
    # основной способ (без аплоада)
    photo_file_id: str | None = None
    # fallback (на время миграции / если file_id ещё не задан)
    image_path: Path | None = None


class OfficeCardsHandler:
    """
    Генератор команд вида /екб, /члб, ...
    Каждая команда отправляет фото + текст (caption).

    Приоритет:
      1) photo_file_id
      2) image_path (fallback, если file_id не задан)
    """

    def __init__(self, cards: Mapping[str, OfficeCard]) -> None:
        self.router = Router()
        self.cards: dict[str, OfficeCard] = {k.strip().lower(): v for k, v in cards.items()}
        self._register()

    def _register(self) -> None:
        # Регистрируем каждую команду явно (ignore_mention=True ловит /cmd@BotName)
        for cmd in self.cards.keys():
            self.router.message.register(self._send_card, Command(cmd, ignore_mention=True))

    async def _send_card(self, message: Message) -> None:
        text = (message.text or "").strip()
        if not text.startswith("/"):
            return

        # "/екб@bot ..." -> "екб"
        cmd_token = text[1:].split(None, 1)[0]
        cmd = cmd_token.split("@", 1)[0].strip().lower()

        card = self.cards.get(cmd)
        if not card:
            return

        # 1) отправка по file_id (без аплоада) — предпочтительно
        if card.photo_file_id:
            await message.answer_photo(
                photo=card.photo_file_id,
                caption=card.caption,
                parse_mode="HTML",
            )
            return

        # 2) fallback на локальный файл (если file_id ещё не задан)
        if not card.image_path:
            await message.answer("Для этой карточки не задан ни photo_file_id, ни image_path.")
            return

        if not card.image_path.exists():
            await message.answer(f"Файл не найден: {card.image_path.as_posix()}")
            return

        sent = await message.answer_photo(
            photo=FSInputFile(card.image_path),
            caption=card.caption,
            parse_mode="HTML",
        )

        # печатаем file_id в лог, чтобы один раз скопировать и дальше аплоада не было
        try:
            if sent.photo:
                fid = sent.photo[-1].file_id
                print(f"[office_cards] command=/{cmd} file_id={fid}")
        except Exception:
            pass