from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from handlers.balances_clients import ClientsBalancesHandler
from db_asyncpg.repo import Repo


def setup_daily_balances_scheduler(
    *,
    repo: Repo,
    bot,
    schedule_chat_ids: set[int],
    timezone: str = "Asia/Yekaterinburg",
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=timezone)

    handler = ClientsBalancesHandler(
        repo=repo,
        admin_chat_ids=schedule_chat_ids,
    )

    scheduler.add_job(
        handler.send_scheduled_negative_balances,
        trigger=CronTrigger(hour=18, minute=0),
        kwargs={
            "bot": bot,
            "currencies": ("RUB", "USDT"),
        },
        id="daily_negative_balances",
        replace_existing=True,
    )

    return scheduler
