import asyncio
import logging

from aiogram import Bot, Dispatcher

from config import Config
from handlers.admin_request import AdminRequestHandler
from handlers.balances_clients import ClientsBalancesHandler
from handlers.broadcast_all import BroadcastAllHandler
from handlers.cash_requests import CashRequestsHandler
from handlers.city import CityAssignHandler
from handlers.clients import ClientsHandler
from handlers.cross import CrossRateHandler
from handlers.debug import debug_router
from handlers.managers import ManagersHandler
from handlers.request_table_delete import get_table_delete_router
from handlers.usdt_wallet import UsdtWalletHandler
from middlewares.dedup import DedupMiddleware

from handlers.office_cards import OfficeCardsHandler

from db_asyncpg.pool import create_pool, close_pool
from db_asyncpg.repo import Repo

from handlers.start import StartHandler
from handlers.calc import CalcHandler
from handlers.wallets import WalletsHandler
from handlers.nonzero import NonZeroHandler
from handlers.accept_short import AcceptShortHandler
from utils.offices import OFFICE_CARDS
from utils.requests import get_issue_router
from handlers.request_table_done import get_table_done_router
from services.daily_balances_scheduler import setup_daily_balances_scheduler


class BotApp:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bot = Bot(token=config.bot_token)

        admin_chat_list = [self.config.admin_chat_id] if self.config.admin_chat_id else None
        admin_user_list = self.config.admin_ids if self.config.admin_ids else None

        request_chat_id = self.config.request_chat_id

        city_cash_chats = self.config.cash_chat_map
        default_city = self.config.default_city
        city_cash_chat_ids = self.config.city_cash_chat_ids

        ignore_chat_ids = set()
        if request_chat_id:
            ignore_chat_ids.add(int(request_chat_id))
        ignore_chat_ids.update(int(x) for x in city_cash_chats.values())
        city_schedule_chats = config.city_schedule_chats

        self.ignore_chat_ids = ignore_chat_ids

        self.dp = Dispatcher()
        self.dp.startup.register(self._on_startup)
        self.dp.shutdown.register(self._on_shutdown)

        self.dp.message.middleware(DedupMiddleware())
        self.dp.callback_query.middleware(DedupMiddleware())

        self.repo = Repo()
        self.daily_balances_scheduler = None

        self.managers_handler = ManagersHandler(
            self.repo,
            self.config.admin_chat_id,
        )
        self.dp.include_router(self.managers_handler.router)

        self.cross_handler = CrossRateHandler()
        self.dp.include_router(self.cross_handler.router)

        self.usdt_wallet_handler = UsdtWalletHandler(
            self.repo,
            admin_chat_ids={self.config.admin_chat_id} if getattr(self.config, "admin_chat_id", None) else set(),
        )
        self.dp.include_router(self.usdt_wallet_handler.router)

        self.start_handler = StartHandler(self.repo)
        self.calc_handler = CalcHandler()
        office_handler = OfficeCardsHandler(OFFICE_CARDS)
        self.dp.include_router(office_handler.router)
        self.dp.include_router(debug_router)

        self.nonzero_handler = NonZeroHandler(self.repo)

        self.wallets_handler = WalletsHandler(
            self.repo,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
            ignore_chat_ids=ignore_chat_ids,
            city_cash_chat_ids=city_cash_chat_ids,
        )

        self.accept_short = AcceptShortHandler(
            self.repo,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
            request_chat_id=request_chat_id,
            ignore_chat_ids=ignore_chat_ids,
        )

        self.cash_requests = CashRequestsHandler(
            self.repo,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
            city_cash_chats=config.cash_chat_map,
            city_schedule_chats=city_schedule_chats,
            default_city=default_city,
            request_chat_id=request_chat_id,
        )
        self.dp.include_router(self.cash_requests.router)

        self.admin_request_handler = AdminRequestHandler(
            self.repo,
            admin_chat_id=self.config.admin_chat_id,
            request_chat_id=request_chat_id,
            admin_user_ids=self.config.admin_ids,
        )
        self.dp.include_router(self.admin_request_handler.router)

        self.clients_balances = ClientsBalancesHandler(
            self.repo,
            admin_chat_ids=admin_chat_list,
        )
        self.dp.include_router(self.clients_balances.router)

        self.daily_balances_scheduler = setup_daily_balances_scheduler(
            repo=self.repo,
            bot=self.bot,
            schedule_chat_ids=self.config.schedule_chat_ids,
            timezone="Asia/Yekaterinburg",
        )

        self.clients_handler = ClientsHandler(self.repo, admin_chat_ids=admin_chat_list)
        self.dp.include_router(self.clients_handler.router)

        self.city_handler = CityAssignHandler(self.repo, admin_chat_ids=admin_chat_list)
        self.dp.include_router(self.city_handler.router)

        self.dp.include_router(self.start_handler.router)
        self.dp.include_router(self.calc_handler.router)
        self.dp.include_router(self.accept_short.router)
        self.dp.include_router(self.nonzero_handler.router)
        self.dp.include_router(self.wallets_handler.router)

        if request_chat_id:
            self.dp.include_router(get_table_done_router(request_chat_ids=[request_chat_id]))
            self.dp.include_router(get_table_delete_router(request_chat_ids=[request_chat_id]))

        self.dp.include_router(get_issue_router(
            repo=self.repo,
            admin_chat_ids=[self.config.admin_chat_id] if self.config.admin_chat_id else [],
            admin_user_ids=self.config.admin_ids,
        ))

    async def _on_startup(self) -> None:
        if self.daily_balances_scheduler and not self.daily_balances_scheduler.running:
            self.daily_balances_scheduler.start()
            logging.info("Daily balances scheduler started")

    async def _on_shutdown(self) -> None:
        if self.daily_balances_scheduler and self.daily_balances_scheduler.running:
            self.daily_balances_scheduler.shutdown(wait=False)
            logging.info("Daily balances scheduler stopped")

    async def run(self) -> None:
        logging.info("Connecting to Postgres…")
        await create_pool(self.config.database_url)
        logging.info(
            "Bot is starting… (request_chat_id=%s, city_cash_chats=%s, ignore_chat_ids=%s, city_cash_chat_ids=%s)",
            self.config.request_chat_id,
            self.config.cash_chat_map,
            self.ignore_chat_ids,
            self.config.city_cash_chat_ids,
        )
        try:
            await self.dp.start_polling(self.bot)
        finally:
            await close_pool()


def run_app() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    config = Config.from_env()
    app = BotApp(config)
    asyncio.run(app.run())