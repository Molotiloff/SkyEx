from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

STATUS_LINE_DONE = "Статус: Занесена в таблицу ✅"
STATUS_LINE_ISSUED = "Статус: Выдано ✅"

_RE_LINE = re.compile(
    r"^(Депозит|Выдача):\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.MULTILINE,
)
_RE_PAYOUT = re.compile(
    r"^Отдаём:\s*(?:<code>)?(.+?)(?:</code>)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_SEP_CHARS = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}


def _normalize_amount(raw: str) -> Decimal | None:
    amount_str = raw
    for ch in _SEP_CHARS:
        amount_str = amount_str.replace(ch, "")
    amount_str = amount_str.replace(",", ".").strip()
    try:
        return Decimal(amount_str)
    except (InvalidOperation, ValueError):
        return None


def parse_kind_amount_code(text: str) -> tuple[str, Decimal, str] | None:
    m = _RE_LINE.search(text or "")
    if not m:
        return None
    kind_ru = m.group(1)
    payload = m.group(2).strip()
    try:
        amount_str, code_str = payload.rsplit(" ", 1)
    except ValueError:
        return None

    amount = _normalize_amount(amount_str)
    if amount is None:
        return None
    return kind_ru, amount, code_str.strip().upper()


def parse_payout_amount_code(text: str) -> tuple[Decimal, str] | None:
    m = _RE_PAYOUT.search(text or "")
    if not m:
        return None
    payload = (m.group(1) or "").strip()
    try:
        amount_str, code_str = payload.rsplit(" ", 1)
    except ValueError:
        return None

    amount = _normalize_amount(amount_str)
    if amount is None:
        return None
    return amount, code_str.strip().upper()


def append_status_once(text: str, status_line: str) -> str:
    if status_line in (text or ""):
        return text
    if not (text or "").endswith("\n"):
        return (text or "") + "\n" + status_line
    return (text or "") + status_line
