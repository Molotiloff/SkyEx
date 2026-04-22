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
        self._request_chat_links: "OrderedDict[str, tuple[int, int]]" = OrderedDict()
        self._table_done_flags: "OrderedDict[str, bool]" = OrderedDict()

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

    def remember_request_chat_copy(self, req_id: str, request_chat_id: int, request_message_id: int) -> None:
        key = str(req_id)
        self._request_chat_links.pop(key, None)
        self._request_chat_links[key] = (int(request_chat_id), int(request_message_id))
        if len(self._request_chat_links) > self._max:
            self._request_chat_links.popitem(last=False)

    def get_request_chat_copy(self, req_id: str) -> tuple[int, int] | None:
        key = str(req_id)
        value = self._request_chat_links.get(key)
        if value is not None:
            self._request_chat_links.move_to_end(key)
        return value

    def mark_table_done(self, req_id: str) -> None:
        key = str(req_id)
        self._table_done_flags.pop(key, None)
        self._table_done_flags[key] = True
        if len(self._table_done_flags) > self._max:
            self._table_done_flags.popitem(last=False)

    def is_table_done(self, req_id: str) -> bool:
        key = str(req_id)
        value = bool(self._table_done_flags.get(key))
        if value:
            self._table_done_flags.move_to_end(key)
        return value


req_index = ReqIndex()
