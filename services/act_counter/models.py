from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class AppliedExchangeMovement:
    transaction_id: int
    currency_code: str
    direction: str  # IN / OUT relative to request chat stock


@dataclass(frozen=True, slots=True)
class ActMovementLine:
    req_id: str
    table_req_id: str | None
    transaction_id: int
    direction: str
    amount: Decimal
    txn_at: datetime


@dataclass(frozen=True, slots=True)
class ActCounterReport:
    baseline_amount: Decimal
    baseline_at: datetime | None
    total_in: Decimal
    total_out: Decimal
    expected_amount: Decimal
    movement_count: int
    movements: list[ActMovementLine]
