from __future__ import annotations

from db_asyncpg.repositories import (
    ClientsRepo,
    LiveMessagesRepo,
    ManagersRepo,
    RateOrdersRepo,
    RequestScheduleRepo,
    SettingsRepo,
    TransactionsRepo,
)


class Repo(
    ClientsRepo,
    TransactionsRepo,
    ManagersRepo,
    SettingsRepo,
    RequestScheduleRepo,
    RateOrdersRepo,
    LiveMessagesRepo,
):
    """
    Фасад над специализированными репозиториями.

    Внешний API оставлен совместимым: остальной код всё ещё может работать
    через `db_asyncpg.repo.Repo`, но реализация теперь разложена по файлам
    по зонам ответственности.
    """

    pass
