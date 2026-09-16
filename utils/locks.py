# utils/locks.py
import asyncio
from weakref import WeakValueDictionary


class ChatLocks:
    def __init__(self) -> None:
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    def for_chat(self, chat_id: int) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock


chat_locks = ChatLocks()
