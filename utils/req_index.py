# utils/req_index.py
from __future__ import annotations
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class ReqLink:
    bot_msg_id: int
    req_id: str


class ReqIndex:
    """
    Память O(N) с LRU. Ключ: (chat_id, user_cmd_msg_id) -> ReqLink(bot_msg_id, req_id)
    Нужна, чтобы уметь редактировать заявку, если ответили на исходную команду.
    """

    def __init__(self, maxsize: int = 5000) -> None:
        self._m: "OrderedDict[tuple[int,int], ReqLink]" = OrderedDict()
        self._max = maxsize

    def remember(self, chat_id: int, user_cmd_msg_id: int, bot_msg_id: int, req_id: str) -> None:
        key = (chat_id, user_cmd_msg_id)
        self._m.pop(key, None)
        self._m[key] = ReqLink(bot_msg_id=bot_msg_id, req_id=str(req_id))
        if len(self._m) > self._max:
            self._m.popitem(last=False)

    def lookup(self, chat_id: int, user_cmd_msg_id: int) -> ReqLink | None:
        key = (chat_id, user_cmd_msg_id)
        v = self._m.get(key)
        if v is not None:
            # освежим LRU
            self._m.move_to_end(key)
        return v


req_index = ReqIndex()
