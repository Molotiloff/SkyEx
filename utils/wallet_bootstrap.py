# utils/wallet_bootstrap.py
from db_asyncpg.ports import WalletRepositoryPort

DEFAULT_CURRENCIES: list[tuple[str, int]] = [
    ("USD", 2),
    ("USDW", 2),
    ("USDT", 2),
    ("RUB", 2),
    ("EUR", 2),
    ("EUR500", 2),
    ("РУБПЕР", 2),
]


async def ensure_default_accounts(repo: WalletRepositoryPort, client_id: int) -> None:
    rows = await repo.snapshot_wallet(client_id)
    if rows:  # уже есть валюты — ничего не делаем
        return
    # add_currency делает UPSERT, так что вызов идемпотентен
    for code, precision in DEFAULT_CURRENCIES:
        await repo.add_currency(client_id, code, precision)
