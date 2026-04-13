import asyncio
import logging

from aiogram import Bot, Dispatcher

from app.setup_handlers import AppServices, setup_handlers
from config import Config
from db_asyncpg.pool import close_pool, create_pool
from db_asyncpg.repo import Repo
from middlewares.dedup import DedupMiddleware


class BotApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bot = Bot(token=config.bot_token)
        self.repo = Repo()

        request_chat_id = self.config.request_chat_id
        city_cash_chats = self.config.cash_chat_map

        ignore_chat_ids = set()
        if request_chat_id:
            ignore_chat_ids.add(int(request_chat_id))
        ignore_chat_ids.update(int(x) for x in city_cash_chats.values())
        self.ignore_chat_ids = ignore_chat_ids

        self.dp = Dispatcher()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

        self.dp.message.middleware(DedupMiddleware())
        self.dp.callback_query.middleware(DedupMiddleware())

        self.services: AppServices = setup_handlers(
            dp=self.dp,
            bot=self.bot,
            repo=self.repo,
            config=self.config,
            ignore_chat_ids=self.ignore_chat_ids,
        )

    async def _on_startup(self) -> None:
        scheduler = self.services.daily_balances_scheduler
        if scheduler and not scheduler.running:
            scheduler.start()
            logging.info("Daily balances scheduler started")

        if self.services.aml_queue_service:
            await self.services.aml_queue_service.start()
            logging.info("AML queue service started")

        if self.services.grinex_orderbook_service:
            await self.services.grinex_orderbook_service.restore_live_message(
                admin_chat_id=self.config.admin_chat_id,
            )
            logging.info("Grinex live orderbook message restored")

        if self.services.grinex_ws_service:
            await self.services.grinex_ws_service.start()
            logging.info("Grinex websocket service started")

    async def _on_shutdown(self) -> None:
        scheduler = self.services.daily_balances_scheduler
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
            logging.info("Daily balances scheduler stopped")

        if self.services.aml_queue_service:
            await self.services.aml_queue_service.stop()
            logging.info("AML queue service stopped")

        if self.services.grinex_ws_service:
            await self.services.grinex_ws_service.stop()
            logging.info("Grinex websocket service stopped")

    async def run(self) -> None:
        logging.info("Connecting to Postgres…")
        await create_pool(self.config.database_url)
        logging.info(
            "Bot is starting… (request_chat_id=%s, city_cash_chats=%s, ignore_chat_ids=%s, "
            "city_cash_chat_ids=%s, rate_orders_chat_id=%s, aml_enabled=%s)",
            self.config.request_chat_id,
            self.config.cash_chat_map,
            self.ignore_chat_ids,
            self.config.city_cash_chat_ids,
            self.config.rate_orders_chat_id,
            bool(self.config.getblock),
        )
        try:
            await self.dp.start_polling(self.bot)
        finally:
            await close_pool()


def run_app() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    config = Config.from_env()
    app = BotApp(config)
    asyncio.run(app.run())