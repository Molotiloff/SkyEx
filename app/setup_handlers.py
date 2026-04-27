from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher

from config import Config
from db_asyncpg.ports import (
    ClientRepositoryPort,
    ClientWalletRepositoryPort,
    ClientWalletScheduleRepositoryPort,
    ClientWalletTransactionRepositoryPort,
    ExchangeRequestRepositoryPort,
    ExchangeWorkflowRepositoryPort,
    LiveMessageRepositoryPort,
    ManagedClientWalletScheduleRepositoryPort,
    ManagedClientWalletTransactionRepositoryPort,
    ManagerRepositoryPort,
    RateOrderRepositoryPort,
    SettingsRepositoryPort,
    WalletRepositoryPort,
)
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
    XEHandler,
    debug_router,
    get_table_delete_router,
    get_table_done_router,
    CalcHandler,
    BroadcastAllHandler,
)
from services.aml import AMLQueueService, AMLService
from services.admin_client import (
    ClientBootstrapService,
    ClientDirectoryService,
    ClientGroupService,
    ManagerAdminService,
    NonZeroWalletQueryService,
    UsdtWalletService,
)
from services.client_balances import (
    ClientBalancesFilterService,
    ClientBalancesQueryService,
    ClientBalancesReportBuilder,
    DailyBalancesReportService,
)
from services.daily_balances_scheduler import setup_daily_balances_scheduler
from services.rate_order import (
    OrderbookService,
    RapiraWsService,
    RateOrderService,
)
from services.xe_api import ConverterAPIService
from utils.offices import OFFICE_CARDS


@dataclass(slots=True)
class AppServices:
    daily_balances_scheduler: AsyncIOScheduler | None = None
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

    manager_repo = cast(ManagerRepositoryPort, repo)
    settings_repo = cast(SettingsRepositoryPort, repo)
    client_repo = cast(ClientRepositoryPort, repo)
    wallet_repo = cast(WalletRepositoryPort, repo)
    client_wallet_repo = cast(ClientWalletRepositoryPort, repo)
    client_wallet_tx_repo = cast(ClientWalletTransactionRepositoryPort, repo)
    managed_client_wallet_tx_repo = cast(ManagedClientWalletTransactionRepositoryPort, repo)
    client_wallet_schedule_repo = cast(ClientWalletScheduleRepositoryPort, repo)
    managed_client_wallet_schedule_repo = cast(ManagedClientWalletScheduleRepositoryPort, repo)
    live_message_repo = cast(LiveMessageRepositoryPort, repo)
    rate_order_repo = cast(RateOrderRepositoryPort, repo)
    exchange_request_repo = cast(ExchangeRequestRepositoryPort, repo)
    exchange_workflow_repo = cast(ExchangeWorkflowRepositoryPort, repo)

    request_chat_id = config.request_chat_id
    city_cash_chats = config.cash_chat_map
    default_city = config.default_city
    city_cash_chat_ids = config.city_cash_chat_ids
    city_schedule_chats = config.city_schedule_chats
    ignore_chat_ids = set(ignore_chat_ids or [])

    services = AppServices()

    managers_handler = ManagersHandler(ManagerAdminService(manager_repo), config.admin_chat_id)
    dp.include_router(managers_handler.router)

    usdt_wallet_handler = UsdtWalletHandler(
        UsdtWalletService(settings_repo),
        admin_chat_ids={config.admin_chat_id} if getattr(config, "admin_chat_id", None) else set(),
    )
    dp.include_router(usdt_wallet_handler.router)

    start_handler = StartHandler(ClientBootstrapService(client_wallet_repo))
    calc_handler = CalcHandler()
    xe_handler = None
    if config.converter_api_base_url and config.converter_api_token:
        xe_handler = XEHandler(
            ConverterAPIService(
                base_url=config.converter_api_base_url,
                api_token=config.converter_api_token,
            )
        )

    office_handler = OfficeCardsHandler(OFFICE_CARDS)
    dp.include_router(office_handler.router)
    dp.include_router(debug_router)

    nonzero_handler = NonZeroHandler(NonZeroWalletQueryService(client_wallet_tx_repo))

    services.market_ws_service = RapiraWsService()

    services.orderbook_service = OrderbookService(
        ws_service=services.market_ws_service,
        repo=live_message_repo,
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
            repo=rate_order_repo,
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
        ignore_chat_ids=ignore_chat_ids,
        city_cash_chat_ids=city_cash_chat_ids,
    )

    accept_short_handler = AcceptShortHandler(
        repo,
        admin_chat_ids=admin_chat_list,
        admin_user_ids=admin_user_list,
        request_chat_id=request_chat_id,
        ignore_chat_ids=ignore_chat_ids,
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

    balances_query_service = ClientBalancesQueryService(wallet_repo)
    balances_filter_service = ClientBalancesFilterService()
    balances_report_builder = ClientBalancesReportBuilder()

    clients_balances_handler = ClientsBalancesHandler(
        report_service=DailyBalancesReportService(
            query_service=balances_query_service,
            filter_service=balances_filter_service,
            report_builder=balances_report_builder,
            admin_chat_ids=admin_chat_list,
        ),
        admin_chat_ids=admin_chat_list,
    )
    dp.include_router(clients_balances_handler.router)

    services.daily_balances_scheduler = setup_daily_balances_scheduler(
        report_service=DailyBalancesReportService(
            query_service=balances_query_service,
            filter_service=balances_filter_service,
            report_builder=balances_report_builder,
            admin_chat_ids=config.schedule_chat_ids,
        ),
        bot=bot,
        timezone="Asia/Yekaterinburg",
    )

    clients_handler = ClientsHandler(
        ClientDirectoryService(client_repo),
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
        ClientGroupService(client_repo),
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
    if xe_handler:
        dp.include_router(xe_handler.router)
    dp.include_router(nonzero_handler.router)
    dp.include_router(wallets_handler.router)

    if request_chat_id:
        dp.include_router(get_table_done_router(repo=exchange_request_repo, request_chat_ids=[request_chat_id]))
        dp.include_router(get_table_delete_router(request_chat_ids=[request_chat_id]))

    return services
