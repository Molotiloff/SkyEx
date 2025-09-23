# app/db_asyncpg/utils.py
from decimal import Decimal, ROUND_HALF_UP, getcontext


getcontext().prec = 50 # безопасная общая точность Decimal


def to_upper(code: str) -> str:
    return (code or "").upper()


def quantize_amount(value: Decimal | str | int | float, precision: int) -> Decimal:
    d = Decimal(str(value))
    q = Decimal(10) ** (-precision)
    return d.quantize(q, rounding=ROUND_HALF_UP)