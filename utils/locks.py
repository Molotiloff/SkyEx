# utils/locks.py
import asyncio
from collections import defaultdict


class ChatLocks:
    def __init__(self) -> None:
        self._locks = defaultdict(asyncio.Lock)

    def for_chat(self, chat_id: int) -> asyncio.Lock:
        return self._locks[chat_id]


chat_locks = ChatLocks()
