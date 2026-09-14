from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from services.message_archive.models import ArchiveMessage, SaveMessageResult


class ClientRepositoryPort(Protocol):
    async def ensure_client(self, chat_id: int, name: str, client_group: str | None = None) -> int: ...

    async def remove_client(self, chat_id: int) -> bool: ...

    async def list_clients(self) -> list[dict]: ...

    async def list_clients_by_group(self, client_group: str) -> list[dict]: ...

    async def set_client_group_by_chat_id(self, chat_id: int, client_group: str) -> dict | None: ...

    async def update_client_chat_id(self, *, client_id: int, new_chat_id: int) -> None: ...

    async def find_client_by_name_exact(self, name: str) -> dict[str, Any] | None: ...


class WalletRepositoryPort(Protocol):
    async def add_currency(self, client_id: int, currency_code: str, precision: int) -> int: ...

    async def remove_currency(self, client_id: int, currency_code: str) -> bool: ...

    async def snapshot_wallet(self, client_id: int) -> list[dict[str, Any]]: ...

    async def balances_by_client(self) -> list[dict[str, Any]]: ...


class TransactionRepositoryPort(Protocol):
    async def deposit(self, **kwargs) -> int: ...

    async def withdraw(self, **kwargs) -> int: ...

    async def history(
            self,
            account_id: int,
            *,
            limit: int = 50,
            since: str | datetime | None = None,
            until: str | datetime | None = None,
            cursor_txn_at: str | None = None,
            cursor_id: int | None = None,
    ) -> list[dict[str, Any]]: ...

    async def export_transactions(
            self,
            *,
            client_id: int | None = None,
            since: datetime | date | str | None = None,
            until: datetime | date | str | None = None,
    ) -> list[dict[str, Any]]: ...


class RequestScheduleRepositoryPort(Protocol):
    async def next_request_id(self) -> int: ...

    async def upsert_request_schedule_entry(
            self,
            *,
            req_id: str,
            city: str,
            hhmm: str | None,
            request_kind: str,
            line_text: str,
            client_name: str,
            request_chat_id: int,
            request_message_id: int,
    ) -> None: ...

    async def list_request_schedule_entries(self, *, city: str) -> list[dict[str, Any]]: ...

    async def deactivate_request_schedule_entry(self, req_id: str) -> bool: ...

    async def get_request_schedule_board(self, *, city: str) -> dict[str, Any] | None: ...

    async def upsert_request_schedule_board(
            self,
            *,
            city: str,
            board_chat_id: int,
            board_message_id: int,
    ) -> None: ...

    async def deactivate_request_schedule_entry_by_message(
            self,
            *,
            request_chat_id: int,
            request_message_id: int,
    ) -> bool: ...

    async def get_request_schedule_entry_by_message(
            self,
            *,
            request_chat_id: int,
            request_message_id: int,
    ) -> dict[str, Any] | None: ...

    async def get_request_schedule_entry_by_req_id(self, *, req_id: str) -> dict[str, Any] | None: ...


class ExchangeRequestRepositoryPort(Protocol):
    async def upsert_exchange_request_link(
            self,
            *,
            client_req_id: str,
            table_req_id: str,
            client_chat_id: int | None = None,
            client_message_id: int | None = None,
            request_chat_id: int | None = None,
            request_message_id: int | None = None,
            request_text: str | None = None,
            table_in_cur: str | None = None,
            table_out_cur: str | None = None,
            table_in_amount: Any | None = None,
            table_out_amount: Any | None = None,
            table_rate: Any | None = None,
            is_table_done: bool | None = None,
            status: str | None = None,
    ) -> None: ...

    async def get_exchange_request_link(self, *, client_req_id: str) -> dict[str, Any] | None: ...

    async def get_exchange_request_link_by_table_req_id(self, *, table_req_id: str) -> dict[str, Any] | None: ...

    async def mark_exchange_request_table_done(self, *, table_req_id: str, is_table_done: bool = True) -> bool: ...

    async def set_exchange_request_status(self, *, client_req_id: str, status: str) -> bool: ...


class RateOrderRepositoryPort(Protocol):
    async def create_rate_order(
            self,
            *,
            client_chat_id: int,
            client_name: str,
            requested_rate: Decimal,
            created_by_user_id: int | None,
            order_chat_id: int,
            order_message_id: int,
    ) -> int: ...

    async def set_rate_order_message_binding(
            self,
            *,
            order_id: int,
            order_chat_id: int,
            order_message_id: int,
    ) -> None: ...

    async def get_rate_order_by_message(
            self,
            *,
            order_chat_id: int,
            order_message_id: int,
    ) -> dict[str, Any] | None: ...

    async def get_rate_order_by_id(self, order_id: int) -> dict[str, Any] | None: ...

    async def activate_rate_order(
            self,
            *,
            order_id: int,
            commission: Decimal,
            target_ask: Decimal,
            activated_by_user_id: int | None,
    ) -> None: ...

    async def list_active_rate_orders(self) -> list[dict[str, Any]]: ...

    async def mark_rate_order_triggered(self, *, order_id: int) -> bool: ...


class SettingsRepositoryPort(Protocol):
    async def get_setting(self, key: str) -> str | None: ...

    async def set_setting(self, key: str, value: str) -> None: ...


class LiveMessageRepositoryPort(Protocol):
    async def upsert_live_message(self, *, chat_id: int, message_key: str, message_id: int) -> None: ...

    async def get_live_message(self, *, chat_id: int, message_key: str) -> dict[str, Any] | None: ...

    async def delete_live_message(self, *, chat_id: int, message_key: str) -> bool: ...

    async def list_live_messages(self, *, message_key: str | None = None) -> list[dict[str, Any]]: ...


class ActCounterRepositoryPort(Protocol):
    async def link_act_request_transaction(
        self,
        *,
        req_id: str,
        request_chat_id: int,
        request_message_id: int,
        transaction_id: int,
        direction: str,
        table_req_id: str | None = None,
        status: str = "ACTIVE",
    ) -> int: ...

    async def cancel_act_request_transactions(self, *, req_id: str) -> int: ...

    async def get_act_request_transaction(self, *, req_id: str) -> list[dict[str, Any]]: ...


class ManagerRepositoryPort(Protocol):
    async def list_managers(self) -> list[dict]: ...

    async def add_manager(self, user_id: int, display_name: str = "") -> bool: ...

    async def remove_manager(self, user_id: int) -> bool: ...

    async def is_manager(self, user_id: int) -> bool: ...


class MessageArchiveRepositoryPort(Protocol):
    async def save_archive_message(self, message: ArchiveMessage) -> SaveMessageResult: ...

    async def mark_archive_attachment_ready(
        self, *, attachment_id: int, storage_key: str, sha256: str, file_size: int
    ) -> None: ...

    async def mark_archive_attachment_failed(
        self, *, attachment_id: int, error: str, unavailable: bool = False
    ) -> None: ...

    async def list_pending_archive_attachments(self, *, limit: int = 500) -> list[dict[str, Any]]: ...

    async def search_archive_chats(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]: ...

    async def get_archive_chat(self, chat_id: int) -> dict[str, Any] | None: ...

    async def list_archive_messages_page(
        self,
        *,
        chat_id: int,
        cursor_sent_at: datetime | None = None,
        cursor_message_id: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    async def list_archive_attachments_for_messages(
        self, message_ids: list[int]
    ) -> list[dict[str, Any]]: ...

    async def get_archive_statistics(self) -> dict[str, int]: ...

    async def create_archive_import(
        self,
        *,
        source_path: str,
        source_checksum: str,
        dry_run: bool,
        summary: dict[str, Any] | None = None,
    ) -> int: ...

    async def finish_archive_import(
        self,
        *,
        import_id: int,
        status: str,
        chats_found: int = 0,
        messages_created: int = 0,
        messages_updated: int = 0,
        messages_skipped: int = 0,
        files_missing: int = 0,
        summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None: ...

    async def create_archive_export(
        self, *, chat_id: int, requested_by_user_id: int, requested_in_chat_id: int
    ) -> int: ...

    async def finish_archive_export(
        self,
        *,
        export_id: int,
        status: str,
        message_count: int = 0,
        part_count: int = 0,
        total_size: int = 0,
        telegram_message_ids: list[int] | None = None,
        error: str | None = None,
    ) -> None: ...


class PaymentWatchRepositoryPort(Protocol):
    async def create_payment_watch(
        self,
        *,
        chat_id: int,
        chat_name: str | None,
        reply_message_id: int,
        address: str,
        our_address: str,
        created_by_user_id: int | None,
        mode: str,
        phase: str,
        status: str,
        timeout_at: datetime,
    ) -> int: ...

    async def get_payment_watch(self, *, watch_id: int) -> dict | None: ...

    async def get_active_payment_watch_by_reply(
        self,
        *,
        chat_id: int,
        reply_message_id: int,
    ) -> dict | None: ...

    async def list_watching_payment_watches(self, *, limit: int = 100) -> list[dict]: ...

    async def touch_payment_watch_checked_at(self, *, watch_id: int, checked_at: datetime) -> None: ...

    async def add_payment_watch_event(
        self,
        *,
        watch_id: int,
        tx_hash: str,
        event_type: str,
        direction: str,
        amount: Decimal,
        token_symbol: str,
        confirmations: int,
        block_ts: datetime,
    ) -> int: ...

    async def list_payment_watch_events(self, *, watch_id: int) -> list[dict]: ...

    async def get_payment_watch_event_hashes(self, *, watch_id: int) -> set[str]: ...

    async def set_payment_watch_phase(self, *, watch_id: int, phase: str) -> bool: ...

    async def mark_payment_watch_timed_out(self, *, watch_id: int) -> bool: ...

    async def continue_payment_watch(self, *, watch_id: int, timeout_at: datetime) -> bool: ...

    async def stop_payment_watch(self, *, watch_id: int) -> bool: ...

    async def complete_payment_watch(self, *, watch_id: int) -> bool: ...

    async def set_payment_watch_notice_message_id(
        self,
        *,
        watch_id: int,
        notice_message_id: int,
    ) -> bool: ...


class ClientWalletRepositoryPort(ClientRepositoryPort, WalletRepositoryPort, Protocol):
    pass


class WalletTransactionRepositoryPort(WalletRepositoryPort, TransactionRepositoryPort, Protocol):
    pass


class ClientWalletTransactionRepositoryPort(
    ClientRepositoryPort,
    WalletRepositoryPort,
    TransactionRepositoryPort,
    Protocol,
):
    pass


class ClientWalletScheduleRepositoryPort(
    ClientRepositoryPort,
    WalletRepositoryPort,
    RequestScheduleRepositoryPort,
    Protocol,
):
    pass


class ClientTransactionRepositoryPort(
    ClientRepositoryPort,
    TransactionRepositoryPort,
    Protocol,
):
    pass


class ClientRequestScheduleRepositoryPort(
    ClientRepositoryPort,
    RequestScheduleRepositoryPort,
    Protocol,
):
    pass


class ExchangeWorkflowRepositoryPort(
    ClientRepositoryPort,
    WalletRepositoryPort,
    TransactionRepositoryPort,
    ExchangeRequestRepositoryPort,
    RequestScheduleRepositoryPort,
    Protocol,
):
    pass


class ManagedClientWalletTransactionRepositoryPort(
    ManagerRepositoryPort,
    ClientWalletTransactionRepositoryPort,
    Protocol,
):
    pass


class ManagedClientWalletScheduleRepositoryPort(
    ManagerRepositoryPort,
    ClientWalletScheduleRepositoryPort,
    Protocol,
):
    pass


class ClientTransferRepositoryPort(
    ClientWalletTransactionRepositoryPort,
    Protocol,
):
    pass


class ActCounterLedgerRepositoryPort(
    ActCounterRepositoryPort,
    ClientWalletTransactionRepositoryPort,
    Protocol,
):
    pass


class PaymentWatchAppRepositoryPort(
    PaymentWatchRepositoryPort,
    SettingsRepositoryPort,
    Protocol,
):
    pass
