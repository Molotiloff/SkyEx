from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from db_asyncpg.repo import Repo
from utils.auth import require_manager_or_admin_callback, require_manager_or_admin_message

logger = logging.getLogger(__name__)


class BroadcastAllHandler:
    BROADCAST_PROMPT_TEXT = (
        "Прикрепите сообщение и картинку для рассылки "
        "в ответ на это сообщение."
    )

    CB_CONFIRM = "broadcast:confirm"
    CB_CANCEL = "broadcast:cancel"

    def __init__(
        self,
        repo: Repo,
        admin_chat_ids: set[int] | None = None,
        admin_user_ids: set[int] | None = None,
    ) -> None:
        self.repo = repo
        self.admin_chat_ids = set(admin_chat_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])
        self.router = Router()

        # chat_id -> set[prompt_message_id]
        self._pending_prompt_messages: dict[int, set[int]] = defaultdict(set)

        # prompt_message_id -> broadcast target metadata
        self._pending_prompt_meta: dict[int, dict] = {}

        # (chat_id, media_group_id) -> list[Message]
        self._media_group_buffer: dict[tuple[int, str], list[Message]] = defaultdict(list)

        # preview/control message_id -> payload
        self._pending_broadcast_payload: dict[int, dict] = {}

        self._register()

    def _build_confirm_kb(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить рассылку",
                        callback_data=self.CB_CONFIRM,
                    ),
                    InlineKeyboardButton(
                        text="❌ Отменить рассылку",
                        callback_data=self.CB_CANCEL,
                    ),
                ]
            ]
        )

    @staticmethod
    def _extract_group_from_command(text: str) -> str | None:
        parts = (text or "").strip().split(maxsplit=1)
        if len(parts) < 2:
            return None

        group = parts[1].strip()
        return group or None

    async def _get_target_clients(self, *, group: str | None = None) -> list[dict]:
        clients = await self.repo.list_clients()
        if not group:
            return clients

        group_norm = group.strip().casefold()
        return [
            c for c in clients
            if str(c.get("client_group") or "").strip().casefold() == group_norm
        ]

    @staticmethod
    def _target_label(group: str | None) -> str:
        if group:
            return f"для группы «{group}»"
        return "для всех клиентов"

    async def _cmd_all(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        target_group = self._extract_group_from_command(message.text or "")
        clients = await self._get_target_clients(group=target_group)

        if not clients:
            if target_group:
                await message.answer(f"Нет активных клиентов в группе «{target_group}».")
            else:
                await message.answer("Нет активных клиентов для рассылки.")
            return

        prompt = await message.answer(
            f"{self.BROADCAST_PROMPT_TEXT}\n\n"
            f"Целевая аудитория: {self._target_label(target_group)}."
        )
        self._pending_prompt_messages[message.chat.id].add(prompt.message_id)
        self._pending_prompt_meta[prompt.message_id] = {
            "group": target_group,
        }

    async def _handle_broadcast_reply(self, message: Message) -> None:
        if not await require_manager_or_admin_message(
            self.repo,
            message,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        reply_msg = message.reply_to_message
        if not reply_msg:
            return

        pending_ids = self._pending_prompt_messages.get(message.chat.id, set())
        if reply_msg.message_id not in pending_ids:
            return

        if message.media_group_id:
            await self._collect_and_preview_media_group(message)
            return

        await self._preview_and_confirm(message)
        self._pending_prompt_messages[message.chat.id].discard(reply_msg.message_id)
        self._pending_prompt_meta.pop(reply_msg.message_id, None)

    async def _collect_and_preview_media_group(self, message: Message) -> None:
        key = (message.chat.id, message.media_group_id)
        self._media_group_buffer[key].append(message)

        await asyncio.sleep(1.0)

        if key not in self._media_group_buffer:
            return

        messages = self._media_group_buffer.pop(key, [])
        if not messages:
            return

        reply_msg = messages[0].reply_to_message
        if not reply_msg:
            return

        pending_ids = self._pending_prompt_messages.get(messages[0].chat.id, set())
        if reply_msg.message_id not in pending_ids:
            return

        await self._preview_and_confirm_media_group(messages)
        self._pending_prompt_messages[messages[0].chat.id].discard(reply_msg.message_id)
        self._pending_prompt_meta.pop(reply_msg.message_id, None)

    async def _preview_and_confirm(self, source_message: Message) -> None:
        reply_msg = source_message.reply_to_message
        prompt_meta = self._pending_prompt_meta.get(reply_msg.message_id if reply_msg else 0, {})
        target_group = prompt_meta.get("group")

        if source_message.photo:
            file_id = source_message.photo[-1].file_id
            preview = await source_message.answer_photo(
                photo=file_id,
                caption=source_message.caption,
                caption_entities=source_message.caption_entities,
                reply_markup=self._build_confirm_kb(),
            )
            self._pending_broadcast_payload[preview.message_id] = {
                "type": "photo",
                "file_id": file_id,
                "caption": source_message.caption,
                "caption_entities": source_message.caption_entities,
                "group": target_group,
            }
            return

        if source_message.text:
            preview = await source_message.answer(
                text=source_message.text,
                entities=source_message.entities,
                reply_markup=self._build_confirm_kb(),
            )
            self._pending_broadcast_payload[preview.message_id] = {
                "type": "text",
                "text": source_message.text,
                "entities": source_message.entities,
                "group": target_group,
            }
            return

        await source_message.answer("Неподдерживаемый формат сообщения для рассылки.")

    async def _preview_and_confirm_media_group(self, messages: list[Message]) -> None:
        messages = sorted(messages, key=lambda m: m.message_id)

        reply_msg = messages[0].reply_to_message
        prompt_meta = self._pending_prompt_meta.get(reply_msg.message_id if reply_msg else 0, {})
        target_group = prompt_meta.get("group")

        media: list[InputMediaPhoto] = []
        payload_media: list[dict] = []

        caption = None
        caption_entities = None

        for msg in messages:
            if not msg.photo:
                continue

            file_id = msg.photo[-1].file_id

            if caption is None and msg.caption:
                caption = msg.caption
                caption_entities = msg.caption_entities
                media.append(
                    InputMediaPhoto(
                        media=file_id,
                        caption=caption,
                        caption_entities=caption_entities,
                    )
                )
            else:
                media.append(InputMediaPhoto(media=file_id))

            payload_media.append({"file_id": file_id})

        if not media:
            await messages[0].answer("Для альбома не найдено фотографий.")
            return

        await messages[0].bot.send_media_group(
            chat_id=messages[0].chat.id,
            media=media,
        )

        control = await messages[0].answer(
            f"Подтвердите рассылку альбома {self._target_label(target_group)}:",
            reply_markup=self._build_confirm_kb(),
        )

        self._pending_broadcast_payload[control.message_id] = {
            "type": "media_group",
            "media": payload_media,
            "caption": caption,
            "caption_entities": caption_entities,
            "group": target_group,
        }

    async def _handle_broadcast_action(self, cq: CallbackQuery) -> None:
        if not await require_manager_or_admin_callback(
            self.repo,
            cq,
            admin_chat_ids=self.admin_chat_ids,
            admin_user_ids=self.admin_user_ids,
        ):
            return

        msg = cq.message
        if not msg:
            await cq.answer()
            return

        payload = self._pending_broadcast_payload.get(msg.message_id)
        if not payload:
            await cq.answer("Данные рассылки устарели.", show_alert=True)
            return

        if cq.data == self.CB_CANCEL:
            self._pending_broadcast_payload.pop(msg.message_id, None)
            try:
                await msg.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await msg.answer("❌ Рассылка отменена.")
            await cq.answer()
            return

        if cq.data != self.CB_CONFIRM:
            await cq.answer()
            return

        self._pending_broadcast_payload.pop(msg.message_id, None)

        try:
            await msg.edit_reply_markup(reply_markup=None)
        except Exception:
            pass

        await cq.answer("Запускаю рассылку...")

        if payload["type"] in {"text", "photo"}:
            await self._send_broadcast_from_payload(msg, payload)
            return

        if payload["type"] == "media_group":
            await self._send_broadcast_media_group_payload(msg, payload)
            return

        await msg.answer("Неизвестный тип рассылки.")

    async def _send_broadcast_from_payload(self, source_message: Message, payload: dict) -> None:
        target_group = payload.get("group")
        clients = await self._get_target_clients(group=target_group)

        if not clients:
            if target_group:
                await source_message.answer(f"Нет активных клиентов в группе «{target_group}».")
            else:
                await source_message.answer("Нет активных клиентов для рассылки.")
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

    async def _send_broadcast_media_group_payload(self, source_message: Message, payload: dict) -> None:
        target_group = payload.get("group")
        clients = await self._get_target_clients(group=target_group)

        if not clients:
            if target_group:
                await source_message.answer(f"Нет активных клиентов в группе «{target_group}».")
            else:
                await source_message.answer("Нет активных клиентов для рассылки.")
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

    def _register(self) -> None:
        self.router.message.register(self._cmd_all, Command("всем"))
        self.router.message.register(
            self._handle_broadcast_reply,
            F.reply_to_message.as_("reply_to_message"),
        )
        self.router.callback_query.register(
            self._handle_broadcast_action,
            F.data.in_({self.CB_CONFIRM, self.CB_CANCEL}),
        )