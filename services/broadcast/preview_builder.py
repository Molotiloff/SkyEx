from __future__ import annotations

from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message


class BroadcastPreviewBuilder:
    BROADCAST_PROMPT_TEXT = (
        "Прикрепите сообщение и картинку для рассылки "
        "в ответ на это сообщение."
    )

    CB_CONFIRM = "broadcast:confirm"
    CB_CANCEL = "broadcast:cancel"

    @classmethod
    def build_confirm_kb(cls) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Отправить рассылку",
                        callback_data=cls.CB_CONFIRM,
                    ),
                    InlineKeyboardButton(
                        text="❌ Отменить рассылку",
                        callback_data=cls.CB_CANCEL,
                    ),
                ]
            ]
        )

    async def preview_single(
        self,
        *,
        source_message: Message,
        target_group: str | None,
    ) -> tuple[int, dict[str, Any]] | None:
        if source_message.photo:
            file_id = source_message.photo[-1].file_id
            preview = await source_message.answer_photo(
                photo=file_id,
                caption=source_message.caption,
                caption_entities=source_message.caption_entities,
                reply_markup=self.build_confirm_kb(),
            )
            return preview.message_id, {
                "type": "photo",
                "file_id": file_id,
                "caption": source_message.caption,
                "caption_entities": source_message.caption_entities,
                "group": target_group,
            }

        if source_message.text:
            preview = await source_message.answer(
                text=source_message.text,
                entities=source_message.entities,
                reply_markup=self.build_confirm_kb(),
            )
            return preview.message_id, {
                "type": "text",
                "text": source_message.text,
                "entities": source_message.entities,
                "group": target_group,
            }

        await source_message.answer("Неподдерживаемый формат сообщения для рассылки.")
        return None

    async def preview_media_group(
        self,
        *,
        messages: list[Message],
        target_group: str | None,
        target_label: str,
    ) -> tuple[int, dict[str, Any]] | None:
        messages = sorted(messages, key=lambda m: m.message_id)

        media: list[InputMediaPhoto] = []
        payload_media: list[dict[str, str]] = []

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
            return None

        await messages[0].bot.send_media_group(
            chat_id=messages[0].chat.id,
            media=media,
        )

        control = await messages[0].answer(
            f"Подтвердите рассылку альбома {target_label}:",
            reply_markup=self.build_confirm_kb(),
        )

        return control.message_id, {
            "type": "media_group",
            "media": payload_media,
            "caption": caption,
            "caption_entities": caption_entities,
            "group": target_group,
        }
