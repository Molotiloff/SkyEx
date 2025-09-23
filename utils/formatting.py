# utils/formatting.py (замените format_amount_core на версию с минусами)
from decimal import Decimal

THIN_APOSTROPHE = u"’"


def _group_int(int_part, sep=THIN_APOSTROPHE):
    rev = int_part[::-1]
    chunks = [rev[i:i + 3] for i in range(0, len(rev), 3)]
    return sep.join(ch[::-1] for ch in chunks[::-1])


def format_amount_core(amount: Decimal, precision: int, sep: str = THIN_APOSTROPHE) -> str:
    """Поддержка отрицательных значений: -1000000.5 -> '-1’000’000.50'."""
    q = Decimal(10) ** -precision
    a = amount.quantize(q)
    neg = a < 0
    a_abs = -a if neg else a

    s = f"{a_abs:f}"  # без экспоненты
    if "." in s:
        int_part, frac_part = s.split(".", 1)
        res = f"{_group_int(int_part, sep)}.{frac_part.ljust(precision, '0')[:precision]}"
    else:
        res = _group_int(s, sep) + ("." + "0" * precision if precision > 0 else "")
    return "-" + res if neg else res


def format_amount_with_sign(amount: Decimal, precision: int, sign: str = "") -> str:
    core = format_amount_core(amount, precision)
    return (sign + core) if sign else core
