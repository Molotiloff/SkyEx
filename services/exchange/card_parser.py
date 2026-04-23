from __future__ import annotations

import re
from decimal import Decimal

_SEP = {" ", "\u00A0", "\u202F", "\u2009", "'", "’", "ʼ", "‛", "`"}
_RE_GET = re.compile(r"^Получаем:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.M | re.I)
_RE_GIVE = re.compile(r"^Отдаём:\s*(?:<code>)?(.+?)(?:</code>)?\s*$", re.M | re.I)
_RE_REQ_ID = re.compile(r"Заявка:\s*(?:<code>)?(\d{6,})(?:</code>)?", re.IGNORECASE)
_RE_CREATED_BY = re.compile(r"^\s*Создал:\s*(?:<b>)?(.+?)(?:</b>)?\s*$", re.I | re.M)

CANCEL_REQUEST_PREFIX = "⛔️ <b>Внимание! Заявка отменена</b>"


def parse_amount_code(payload: str) -> tuple[Decimal, str] | None:
    try:
        amount_raw, code = payload.rsplit(" ", 1)
    except ValueError:
        return None

    for ch in _SEP:
        amount_raw = amount_raw.replace(ch, "")
    amount_raw = amount_raw.replace(",", ".").strip()

    try:
        return Decimal(amount_raw), code.strip().upper()
    except Exception:
        return None


def parse_get_give(text: str) -> tuple[tuple[Decimal, str], tuple[Decimal, str]] | None:
    get_match = _RE_GET.search(text or "")
    give_match = _RE_GIVE.search(text or "")
    if not (get_match and give_match):
        return None

    parsed_get = parse_amount_code(get_match.group(1))
    parsed_give = parse_amount_code(give_match.group(1))
    if not (parsed_get and parsed_give):
        return None

    return parsed_get, parsed_give


def extract_request_id(text: str) -> str | None:
    match = _RE_REQ_ID.search(text or "")
    return match.group(1) if match else None


def extract_created_by(text: str) -> str | None:
    match = _RE_CREATED_BY.search(text or "")
    return match.group(1).strip() if match else None
