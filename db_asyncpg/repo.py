from __future__ import annotations

from db_asyncpg.ports import (
    ActCounterRepositoryPort,
    ClientRepositoryPort,
    ExchangeRequestRepositoryPort,
    LiveMessageRepositoryPort,
    ManagerRepositoryPort,
    MessageArchiveRepositoryPort,
    PaymentWatchRepositoryPort,
    RateOrderRepositoryPort,
    RequestScheduleRepositoryPort,
    SettingsRepositoryPort,
    TransactionRepositoryPort,
    WalletRepositoryPort,
)
from db_asyncpg.repositories import (
    ActCounterRepo,
    ClientsRepo,
    ExchangeRequestsRepo,
    LiveMessagesRepo,
    ManagersRepo,
    MessageArchiveRepo,
    PaymentWatchRepo,
    RateOrdersRepo,
    RequestScheduleRepo,
    SettingsRepo,
    TransactionsRepo,
)


class Repo(
    ActCounterRepo,
    ClientsRepo,
    ExchangeRequestsRepo,
    TransactionsRepo,
    ManagersRepo,
    SettingsRepo,
    RequestScheduleRepo,
    RateOrdersRepo,
    LiveMessagesRepo,
    PaymentWatchRepo,
    MessageArchiveRepo,
    ActCounterRepositoryPort,
    ClientRepositoryPort,
    WalletRepositoryPort,
    TransactionRepositoryPort,
    RequestScheduleRepositoryPort,
    ExchangeRequestRepositoryPort,
    RateOrderRepositoryPort,
    SettingsRepositoryPort,
    LiveMessageRepositoryPort,
    ManagerRepositoryPort,
    PaymentWatchRepositoryPort,
    MessageArchiveRepositoryPort,
):
    """
    Фасад над специализированными репозиториями.

    Внешний API оставлен совместимым: остальной код всё ещё может работать
    через `db_asyncpg.repo.Repo`, но сервисный слой постепенно переводится
    на узкие `Protocol`-порты из `db_asyncpg.ports`.

    То есть `Repo` остаётся composition-root/compatibility facade, а новый код
    должен зависеть не от этого класса целиком, а от конкретных портов:
    client/wallet/transactions/request_schedule/exchange_requests/rate_orders
    и т.д.
    """

    pass
