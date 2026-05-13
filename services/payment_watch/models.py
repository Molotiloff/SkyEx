from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TronTransfer:
    tx_hash: str
    from_address: str
    to_address: str
    amount: Decimal
    token_symbol: str
    block_number: int | None
    block_ts: datetime
    confirmations: int
    confirmed: bool

    @property
    def direction(self) -> str:
        return "IN"


@dataclass(frozen=True, slots=True)
class PaymentWatchNotification:
    chat_id: int
    reply_message_id: int | None
    text: str
    watch_id: int | None = None
    with_timeout_actions: bool = False
    delete_message_id: int | None = None
