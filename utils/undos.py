# utils/undos.py
import asyncio
from collections import OrderedDict
from typing import Tuple


class UndoRegistry:
    """
    Храним ключи уже обработанных откатов, чтобы не выполнять их повторно.
    Ключ = (chat_id, message_id)
    """

    def __init__(self, maxsize: int = 20000) -> None:
        self._seen: "OrderedDict[Tuple[int, int], bool]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._max = maxsize

    async def is_done(self, key: Tuple[int, int]) -> bool:
        async with self._lock:
            return key in self._seen

    async def mark_done(self, key: Tuple[int, int]) -> None:
        async with self._lock:
            self._seen[key] = True
            self._seen.move_to_end(key)
            if len(self._seen) > self._max:
                self._seen.popitem(last=False)


undo_registry = UndoRegistry()
