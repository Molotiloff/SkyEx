from __future__ import annotations

from collections import OrderedDict


class RequestTableSessionStore:
    def __init__(self, *, max_marked_per_scope: int = 10_000) -> None:
        self._pending: dict[str, set[tuple[int, int]]] = {}
        self._marked: dict[str, OrderedDict[tuple[int, int], None]] = {}
        self._max_marked_per_scope = max(1, max_marked_per_scope)

    def is_pending(self, scope: str, key: tuple[int, int]) -> bool:
        return key in self._pending.get(scope, set())

    def add_pending(self, scope: str, key: tuple[int, int]) -> None:
        self._pending.setdefault(scope, set()).add(key)

    def discard_pending(self, scope: str, key: tuple[int, int]) -> None:
        pending = self._pending.get(scope)
        if pending is None:
            return
        pending.discard(key)
        if not pending:
            self._pending.pop(scope, None)

    def is_marked(self, scope: str, key: tuple[int, int]) -> bool:
        return key in self._marked.get(scope, set())

    def mark(self, scope: str, key: tuple[int, int]) -> None:
        marked = self._marked.setdefault(scope, OrderedDict())
        marked[key] = None
        marked.move_to_end(key)
        while len(marked) > self._max_marked_per_scope:
            marked.popitem(last=False)
