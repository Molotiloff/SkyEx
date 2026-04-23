from __future__ import annotations

import logging
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InputMediaPhoto, Message

from db_asyncpg.repo import Repo

logger = logging.getLogger(__name__)


class BroadcastService:
    def __init__(self, *, repo: Repo) -> None:
        self.repo = repo

    @staticmethod
    def extract_group_from_command(text: str) -> str | None:
        parts = (text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            return None

        group = parts[1].strip()
        return group or None

    @staticmethod
    def target_label(group: str | None) -> str:
        if group:
            return f"для группы «{group}»"
        return "для всех клиентов"

    async def get_target_clients(self, *, group: str | None = None) -> list[dict[str, Any]]:
        clients = await self.repo.list_clients()
        if not group:
            return clients

        group_norm = group.strip().casefold()
        return [
            c for c in clients
            if str(c.get("client_group") or "").strip().casefold() == group_norm
        ]

    async def send_from_payload(self, *, source_message: Message, payload: dict[str, Any]) -> None:
        if payload["type"] in {"text", "photo"}:
            await self._send_single_payload(source_message=source_message, payload=payload)
            return

        if payload["type"] == "media_group":
            await self._send_media_group_payload(source_message=source_message, payload=payload)
            return

        await source_message.answer("Неизвестный тип рассылки.")

    async def _send_single_payload(self, *, source_message: Message, payload: dict[str, Any]) -> None:
        target_group = payload.get("group")
        clients = await self.get_target_clients(group=target_group)

        if not clients:
            await source_message.answer(self.empty_clients_text(target_group))
            return

        sent = 0
        blocked = 0
        not_found = 0
        skipped = 0
        other_errors = 0

        for client in clients:
            chat_id = client.get("chat_id")
            if not chat_id:
                skipped += 1
                continue

            try:
                if payload["type"] == "photo":
                    await source_message.bot.send_photo(
                        chat_id=chat_id,
                        photo=payload["file_id"],
                        caption=payload.get("caption"),
                        caption_entities=payload.get("caption_entities"),
                    )
                elif payload["type"] == "text":
                    await source_message.bot.send_message(
                        chat_id=chat_id,
                        text=payload["text"],
                        entities=payload.get("entities"),
                    )
                else:
                    skipped += 1
                    continue

                sent += 1

            except TelegramForbiddenError:
                blocked += 1

            except TelegramBadRequest as e:
                text = str(e).lower()
                if "chat not found" in text or "bot was blocked" in text:
                    not_found += 1
                else:
                    other_errors += 1
                    logger.exception("TelegramBadRequest for chat_id=%s", chat_id)

            except Exception:
                other_errors += 1
                logger.exception("Unexpected error for chat_id=%s", chat_id)

        await source_message.answer(
            "📣 <b>Рассылка завершена</b>\n\n"
            f"✅ Отправлено: <b>{sent}</b>\n",
            parse_mode="HTML",
        )

    async def _send_media_group_payload(self, *, source_message: Message, payload: dict[str, Any]) -> None:
        target_group = payload.get("group")
        clients = await self.get_target_clients(group=target_group)

        if not clients:
            await source_message.answer(self.empty_clients_text(target_group))
            return

        media_payload = payload.get("media") or []
        if not media_payload:
            await source_message.answer("Для альбома не найдено фотографий.")
            return

        sent = 0
        blocked = 0
        not_found = 0
        skipped = 0
        other_errors = 0

        for client in clients:
            chat_id = client.get("chat_id")
            if not chat_id:
                skipped += 1
                continue

            try:
                media: list[InputMediaPhoto] = []
                for idx, item in enumerate(media_payload):
                    file_id = item["file_id"]
                    if idx == 0 and payload.get("caption"):
                        media.append(
                            InputMediaPhoto(
                                media=file_id,
                                caption=payload.get("caption"),
                                caption_entities=payload.get("caption_entities"),
                            )
                        )
                    else:
                        media.append(InputMediaPhoto(media=file_id))

                await source_message.bot.send_media_group(
                    chat_id=chat_id,
                    media=media,
                )
                sent += 1

            except TelegramForbiddenError:
                blocked += 1

            except TelegramBadRequest as e:
                text = str(e).lower()
                if "chat not found" in text or "bot was blocked" in text:
                    not_found += 1
                else:
                    other_errors += 1
                    logger.exception("TelegramBadRequest for chat_id=%s", chat_id)

            except Exception:
                other_errors += 1
                logger.exception("Unexpected error for chat_id=%s", chat_id)

        await source_message.answer(
            "📣 <b>Рассылка завершена</b>\n\n"
            f"✅ Отправлено: <b>{sent}</b>\n",
            parse_mode="HTML",
        )

    @staticmethod
    def empty_clients_text(group: str | None) -> str:
        if group:
            return f"Нет активных клиентов в группе «{group}»."
        return "Нет активных клиентов для рассылки."
