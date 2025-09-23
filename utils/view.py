# utils/view.py
from decimal import Decimal
from typing import List, Tuple

from utils.formatting import format_amount_core


def format_wallet_view(snapshot: List[Tuple[str, Decimal]], wallet) -> str:
    """
    Форматирует список счетов с выравниванием сумм по правому краю.

    :param snapshot: список (code, balance)
    :param wallet: объект Wallet, у которого можно вызвать get_currency(code).precision
    :return: текст для вставки внутрь <pre> ... </pre>
    """
    items = []
    max_len = 0

    # вычисляем форматированные строки и максимальную длину суммы
    for code, bal in snapshot:
        prec = wallet.get_currency(code).precision
        pretty = format_amount_core(bal, prec)
        items.append((code, pretty))
        if len(pretty) > max_len:
            max_len = len(pretty)

    # выравниваем
    rows = [f"{pretty.rjust(max_len)} {code.lower()}" for code, pretty in items]
    return "\n".join(rows)
