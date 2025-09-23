from decimal import Decimal

from utils.formatting import format_amount_core


def label_for(code: str) -> str:
    code = code.upper().strip()
    if code == "RUB":
        return "руб"
    if code == "UAH":
        return "грн"
    return code.lower()


def format_wallet_compact(rows: list[dict], *, only_nonzero: bool) -> str:
    """Строка для <code>…</code>:
       «  <amount right-aligned> <label>», без кода валюты слева.
    """
    items: list[tuple[str, str]] = []  # (amount_str, label)
    for r in rows:
        bal = Decimal(str(r["balance"]))
        if only_nonzero and bal == 0:
            continue
        prec = int(r["precision"])
        amount_str = format_amount_core(bal, prec)  # уже с разделителями, 2 знака и т.д.
        label = label_for(str(r["currency_code"]))
        items.append((amount_str, label))

    if not items:
        return "Пусто"

    width = max(len(a) for a, _ in items)
    lines = [f"  {a.rjust(width)} {lbl}" for a, lbl in items]  # 2 пробела слева
    return "\n".join(lines)
