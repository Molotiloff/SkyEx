from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot, Dispatcher

from config import Config
from db_asyncpg.repo import Repo
from handlers import (
    AcceptShortHandler,
    AdminRequestHandler,
    AMLHandler,
    CashRequestsHandler,
    CityAssignHandler,
    ClientsBalancesHandler,
    ClientsHandler,
    GrinexBookHandler,
    ManagersHandler,
    NonZeroHandler,
    OfficeCardsHandler,
    RateOrderHandler,
    StartHandler,
    UsdtWalletHandler,
    WalletsHandler,
    debug_router,
    get_table_delete_router,
    get_table_done_router,
    CalcHandler,
    BroadcastAllHandler,
)
from services.aml import AMLQueueService, AMLService
from services.daily_balances_scheduler import setup_daily_balances_scheduler
from services.rate_order import (
    OrderbookService,
    RapiraWsService,
    RateOrderService,
)
from utils.offices import OFFICE_CARDS
from utils.requests import get_issue_router


@dataclass(slots=True)
class AppServices:
    daily_balances_scheduler: object | None = None
    market_ws_service: RapiraWsService | None = None
    orderbook_service: OrderbookService | None = None
    rate_order_service: RateOrderService | None = None
    aml_service: AMLService | None = None
    aml_queue_service: AMLQueueService | None = None


def setup_handlers(
    *,
    dp: Dispatcher,
    bot: Bot,
    repo: Repo,
    config: Config,
    ignore_chat_ids: set[int] | None = None,
) -> AppServices:
    admin_chat_list = [config.admin_chat_id] if config.admin_chat_id else None
    admin_user_list = config.admin_ids if config.admin_ids else None

    request_chat_id = config.request_chat_id
    city_cash_chats = config.cash_chat_map
    default_city = config.default_city
    city_cash_chat_ids = config.city_cash_chat_ids
    city_schedule_chats = config.city_schedule_chats
    ignore_chat_ids = set(ignore_chat_ids or [])

    services = AppServices()

    managers_handler = ManagersHandler(
        repo,
        config.admin_chat_id,
    )
    dp.include_router(managers_handler.router)

    usdt_wallet_handler = UsdtWalletHandler(
        repo,
        admin_chat_ids={config.admin_chat_id} if getattr(config, "admin_chat_id", None) else set(),
    )
    dp.include_router(usdt_wallet_handler.router)

    start_handler = StartHandler(repo)
    calc_handler = CalcHandler()

    office_handler = OfficeCardsHandler(OFFICE_CARDS)
    dp.include_router(office_handler.router)
    dp.include_router(debug_router)

    nonzero_handler = NonZeroHandler(repo)

    services.market_ws_service = RapiraWsService()

    services.orderbook_service = OrderbookService(
        ws_service=services.market_ws_service,
        repo=repo,
        exchange_name="Rapira",
        symbol_label="USDT/RUB",
    )

    services.market_ws_service.on_orderbook_update = (
        lambda: services.orderbook_service.refresh_live_message(bot=bot)
    )

    grinex_book_handler = GrinexBookHandler(
        repo,
        orderbook_service=services.orderbook_service,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
    )
    dp.include_router(grinex_book_handler.router)

    if config.rate_orders_chat_id:
        services.rate_order_service = RateOrderService(
            repo=repo,
            orders_chat_id=config.rate_orders_chat_id,
            get_current_best_ask=lambda: (
                services.market_ws_service.best_ask
                if services.market_ws_service else None
            ),
        )

        rate_order_handler = RateOrderHandler(
            repo,
            rate_order_service=services.rate_order_service,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
            orders_chat_id=config.rate_orders_chat_id,
        )
        dp.include_router(rate_order_handler.router)

        services.market_ws_service.on_best_ask = (
            lambda ask: services.rate_order_service.process_best_ask(
                bot=bot,
                best_ask=ask,
            )
        )

    wallets_handler = WalletsHandler(
        repo,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
        ignore_chat_ids=None,
        city_cash_chat_ids=city_cash_chat_ids,
    )

    accept_short_handler = AcceptShortHandler(
        repo,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
        request_chat_id=request_chat_id,
        ignore_chat_ids=None,
    )
    dp.include_router(accept_short_handler.router)

    cash_requests_handler = CashRequestsHandler(
        repo,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
        city_cash_chats=city_cash_chats,
        city_schedule_chats=city_schedule_chats,
        default_city=default_city,
        request_chat_id=request_chat_id,
    )
    dp.include_router(cash_requests_handler.router)

    admin_request_handler = AdminRequestHandler(
        repo,
        admin_chat_id=config.admin_chat_id,
        request_chat_id=request_chat_id,
        admin_user_ids=config.admin_ids,
    )
    dp.include_router(admin_request_handler.router)

    clients_balances_handler = ClientsBalancesHandler(
        repo,
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(clients_balances_handler.router)

    services.daily_balances_scheduler = setup_daily_balances_scheduler(
        repo=repo,
        bot=bot,
        schedule_chat_ids=config.schedule_chat_ids,
        timezone="Asia/Yekaterinburg",
    )

    clients_handler = ClientsHandler(
        repo,
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(clients_handler.router)

    broadcast_all_handler = BroadcastAllHandler(
        repo,
        admin_chat_ids=set(admin_chat_list or []),
        admin_user_ids=set(admin_user_list or []),
    )
    dp.include_router(broadcast_all_handler.router)

    city_handler = CityAssignHandler(
        repo,
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(city_handler.router)

    if config.getblock:
        services.aml_service = AMLService(settings=config.getblock)
        services.aml_queue_service = AMLQueueService(
            aml_service=services.aml_service,
        )
        aml_handler = AMLHandler(
            repo,
            aml_queue_service=services.aml_queue_service,
            admin_chat_ids=admin_chat_list,
            admin_user_ids=admin_user_list,
        )
        dp.include_router(aml_handler.router)

    dp.include_router(start_handler.router)
    dp.include_router(calc_handler.router)
    dp.include_router(nonzero_handler.router)
    dp.include_router(wallets_handler.router)

    if request_chat_id:
        dp.include_router(get_table_done_router(request_chat_ids=[request_chat_id]))
        dp.include_router(get_table_delete_router(request_chat_ids=[request_chat_id]))

    dp.include_router(
        get_issue_router(
            repo=repo,
            admin_chat_ids=[config.admin_chat_id] if config.admin_chat_id else [],
            admin_user_ids=config.admin_ids,
        )
    )

    return services
