from __future__ import annotations

from collections import defaultdict

from aiogram.types import Message


class CityCashMediaStore:
    def __init__(self) -> None:
        self._groups: dict[tuple[int, str], list[Message]] = defaultdict(list)

    def add_message(self, *, chat_id: int, media_group_id: str, message: Message) -> None:
        key = (int(chat_id), str(media_group_id))
        self._groups[key].append(message)

    def group_size(self, *, chat_id: int, media_group_id: str) -> int:
        key = (int(chat_id), str(media_group_id))
        return len(self._groups.get(key, []))

    def pop_group(self, *, chat_id: int, media_group_id: str) -> list[Message]:
        key = (int(chat_id), str(media_group_id))
        messages = self._groups.pop(key, [])
        messages.sort(key=lambda msg: int(msg.message_id))
        return messages
