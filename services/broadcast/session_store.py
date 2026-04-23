from __future__ import annotations

from collections import defaultdict
from typing import Any

from aiogram.types import Message


class BroadcastSessionStore:
    def __init__(self) -> None:
        self._pending_prompt_messages: dict[int, set[int]] = defaultdict(set)
        self._pending_prompt_meta: dict[int, dict[str, Any]] = {}
        self._media_group_buffer: dict[tuple[int, str], list[Message]] = defaultdict(list)
        self._pending_broadcast_payload: dict[int, dict[str, Any]] = {}

    def add_prompt(self, *, chat_id: int, prompt_message_id: int, group: str | None) -> None:
        self._pending_prompt_messages[int(chat_id)].add(int(prompt_message_id))
        self._pending_prompt_meta[int(prompt_message_id)] = {"group": group}

    def is_pending_prompt(self, *, chat_id: int, prompt_message_id: int) -> bool:
        return int(prompt_message_id) in self._pending_prompt_messages.get(int(chat_id), set())

    def prompt_group(self, *, prompt_message_id: int | None) -> str | None:
        if not prompt_message_id:
            return None
        meta = self._pending_prompt_meta.get(int(prompt_message_id), {})
        group = meta.get("group")
        return str(group) if group else None

    def remove_prompt(self, *, chat_id: int, prompt_message_id: int) -> None:
        self._pending_prompt_messages[int(chat_id)].discard(int(prompt_message_id))
        self._pending_prompt_meta.pop(int(prompt_message_id), None)

    def add_media_group_message(self, *, chat_id: int, media_group_id: str, message: Message) -> tuple[int, str]:
        key = (int(chat_id), str(media_group_id))
        self._media_group_buffer[key].append(message)
        return key

    def has_media_group(self, key: tuple[int, str]) -> bool:
        return key in self._media_group_buffer

    def pop_media_group(self, key: tuple[int, str]) -> list[Message]:
        return self._media_group_buffer.pop(key, [])

    def add_payload(self, *, control_message_id: int, payload: dict[str, Any]) -> None:
        self._pending_broadcast_payload[int(control_message_id)] = payload

    def get_payload(self, *, control_message_id: int) -> dict[str, Any] | None:
        return self._pending_broadcast_payload.get(int(control_message_id))

    def pop_payload(self, *, control_message_id: int) -> dict[str, Any] | None:
        return self._pending_broadcast_payload.pop(int(control_message_id), None)
