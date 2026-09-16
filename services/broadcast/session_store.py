from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import Any

from aiogram.types import Message


class BroadcastSessionStore:
    def __init__(self, *, max_pending_items: int = 500) -> None:
        self._pending_prompt_messages: dict[int, set[int]] = defaultdict(set)
        self._pending_prompt_meta: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._media_group_buffer: dict[tuple[int, str], list[Message]] = defaultdict(list)
        self._pending_broadcast_payload: OrderedDict[int, dict[str, Any]] = OrderedDict()
        self._max_pending_items = max(1, max_pending_items)

    def add_prompt(self, *, chat_id: int, prompt_message_id: int, group: str | None) -> None:
        chat_id = int(chat_id)
        prompt_message_id = int(prompt_message_id)
        self._pending_prompt_messages[chat_id].add(prompt_message_id)
        self._pending_prompt_meta[prompt_message_id] = {"group": group, "chat_id": chat_id}
        self._pending_prompt_meta.move_to_end(prompt_message_id)
        self._trim_prompts()

    def is_pending_prompt(self, *, chat_id: int, prompt_message_id: int) -> bool:
        return int(prompt_message_id) in self._pending_prompt_messages.get(int(chat_id), set())

    def prompt_group(self, *, prompt_message_id: int | None) -> str | None:
        if not prompt_message_id:
            return None
        meta = self._pending_prompt_meta.get(int(prompt_message_id), {})
        group = meta.get("group")
        return str(group) if group else None

    def remove_prompt(self, *, chat_id: int, prompt_message_id: int) -> None:
        chat_id = int(chat_id)
        self._pending_prompt_messages[chat_id].discard(int(prompt_message_id))
        if not self._pending_prompt_messages[chat_id]:
            self._pending_prompt_messages.pop(chat_id, None)
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
        control_message_id = int(control_message_id)
        self._pending_broadcast_payload[control_message_id] = payload
        self._pending_broadcast_payload.move_to_end(control_message_id)
        while len(self._pending_broadcast_payload) > self._max_pending_items:
            self._pending_broadcast_payload.popitem(last=False)

    def get_payload(self, *, control_message_id: int) -> dict[str, Any] | None:
        return self._pending_broadcast_payload.get(int(control_message_id))

    def pop_payload(self, *, control_message_id: int) -> dict[str, Any] | None:
        return self._pending_broadcast_payload.pop(int(control_message_id), None)

    def _trim_prompts(self) -> None:
        while len(self._pending_prompt_meta) > self._max_pending_items:
            prompt_message_id, meta = self._pending_prompt_meta.popitem(last=False)
            chat_id = int(meta["chat_id"])
            self._pending_prompt_messages[chat_id].discard(prompt_message_id)
            if not self._pending_prompt_messages[chat_id]:
                self._pending_prompt_messages.pop(chat_id, None)
