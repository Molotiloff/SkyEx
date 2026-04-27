from __future__ import annotations


class RequestTableSessionStore:
    def __init__(self) -> None:
        self._pending: dict[str, set[tuple[int, int]]] = {}
        self._marked: dict[str, set[tuple[int, int]]] = {}

    def is_pending(self, scope: str, key: tuple[int, int]) -> bool:
        return key in self._pending.get(scope, set())

    def add_pending(self, scope: str, key: tuple[int, int]) -> None:
        self._pending.setdefault(scope, set()).add(key)

    def discard_pending(self, scope: str, key: tuple[int, int]) -> None:
        self._pending.setdefault(scope, set()).discard(key)

    def is_marked(self, scope: str, key: tuple[int, int]) -> bool:
        return key in self._marked.get(scope, set())

    def mark(self, scope: str, key: tuple[int, int]) -> None:
        self._marked.setdefault(scope, set()).add(key)
