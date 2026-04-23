from __future__ import annotations


class RequestStatusSessionStore:
    def __init__(self) -> None:
        self._pending_confirm: set[tuple[int, int]] = set()
        self._marked_done: set[tuple[int, int]] = set()

    @staticmethod
    def key(chat_id: int, message_id: int) -> tuple[int, int]:
        return int(chat_id), int(message_id)

    def is_pending_confirm(self, key: tuple[int, int]) -> bool:
        return key in self._pending_confirm

    def add_pending_confirm(self, key: tuple[int, int]) -> None:
        self._pending_confirm.add(key)

    def discard_pending_confirm(self, key: tuple[int, int]) -> None:
        self._pending_confirm.discard(key)

    def is_marked_done(self, key: tuple[int, int]) -> bool:
        return key in self._marked_done

    def mark_done(self, key: tuple[int, int]) -> None:
        self._marked_done.add(key)
