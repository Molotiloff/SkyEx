from __future__ import annotations

import re

from aiogram.types import Message

from utils.aml_wallets import is_probable_tron_wallet, normalize_wallet

_TRON_ADDRESS_RE = re.compile(r"T[1-9A-HJ-NP-Za-km-z]{33}")


def extract_tron_address_from_message(message: Message | None) -> str | None:
    if message is None:
        return None

    candidates = [
        message.text or "",
        message.caption or "",
    ]
    for raw in candidates:
        match = _TRON_ADDRESS_RE.search(raw)
        if not match:
            continue
        wallet = normalize_wallet(match.group(0))
        if is_probable_tron_wallet(wallet):
            return wallet
    return None
