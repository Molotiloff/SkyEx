# utils/request_audit.py
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from aiogram.types import Message

# «Создал: ...» в тексте карточки
_RE_CREATED_BY = re.compile(r"^\s*Создал:\s*(?:<b>)?(.+?)(?:</b>)?\s*$", re.I | re.M)


@dataclass(frozen=True, slots=True)
class RequestAudit:
    created_by: str
    changed_by: str | None
    changed_ts: str | None


def actor_from_message(message: Message) -> str:
    """
    Читаем, кто сейчас выполняет команду.
    Формат: Full Name или @username или id:<id>
    """
    u = getattr(message, "from_user", None)
    if not u:
        return "unknown"
    if getattr(u, "full_name", None):
        return u.full_name
    if getattr(u, "username", None):
        return f"@{u.username}"
    return f"id:{u.id}"


def created_by_from_old_text(old_text: str) -> Optional[str]:
    """
    Пробуем извлечь 'Создал: ...' из старой карточки (клиентской).
    """
    if not old_text:
        return None
    m = _RE_CREATED_BY.search(old_text)
    if not m:
        return None
    return m.group(1).strip() or None


def make_audit_for_new(message: Message) -> RequestAudit:
    creator = actor_from_message(message)
    return RequestAudit(created_by=creator, changed_by=None, changed_ts=None)


def make_audit_for_edit(message: Message, *, old_text: str) -> RequestAudit:
    creator = created_by_from_old_text(old_text) or actor_from_message(message)
    editor = actor_from_message(message)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    return RequestAudit(created_by=creator, changed_by=editor, changed_ts=ts)


def audit_lines_for_request_chat(a: RequestAudit) -> list[str]:
    """
    Линии для заявочного чата.
    """
    lines: list[str] = ["----", f"<b>Создал</b>: <b>{html.escape(a.created_by)}</b>"]
    if a.changed_by and a.changed_ts:
        lines += [
            f"<b>Изменил</b>: <b>{html.escape(a.changed_by)}</b>",
            f"<b>Изменение</b>: <code>{html.escape(a.changed_ts)}</code>",
        ]
    return lines


def audit_lines_for_client_card(a: RequestAudit) -> list[str]:
    """
    Линии для клиентской карточки (чтобы потом при редактировании можно было
    восстановить создателя).
    """
    lines: list[str] = ["----", f"<b>Создал</b>: <b>{html.escape(a.created_by)}</b>"]
    # В клиентскую карточку обычно НЕ обязательно пихать changed_by/ts,
    # но если нужно — можно добавить аналогично.
    return lines
