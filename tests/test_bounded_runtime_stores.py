from __future__ import annotations

import gc
import weakref

from services.broadcast.session_store import BroadcastSessionStore
from services.request_table.session_store import RequestTableSessionStore
from utils.locks import ChatLocks


def test_request_table_marked_entries_are_bounded() -> None:
    store = RequestTableSessionStore(max_marked_per_scope=2)
    store.mark("done", (1, 1))
    store.mark("done", (1, 2))
    store.mark("done", (1, 3))

    assert not store.is_marked("done", (1, 1))
    assert store.is_marked("done", (1, 2))
    assert store.is_marked("done", (1, 3))


def test_broadcast_prompts_and_payloads_are_bounded() -> None:
    store = BroadcastSessionStore(max_pending_items=2)
    for message_id in range(1, 4):
        store.add_prompt(chat_id=10, prompt_message_id=message_id, group=None)
        store.add_payload(control_message_id=message_id, payload={"id": message_id})

    assert not store.is_pending_prompt(chat_id=10, prompt_message_id=1)
    assert store.is_pending_prompt(chat_id=10, prompt_message_id=2)
    assert store.get_payload(control_message_id=1) is None
    assert store.get_payload(control_message_id=3) == {"id": 3}


def test_unused_chat_locks_can_be_collected() -> None:
    locks = ChatLocks()
    lock = locks.for_chat(123)
    reference = weakref.ref(lock)

    del lock
    gc.collect()

    assert reference() is None

