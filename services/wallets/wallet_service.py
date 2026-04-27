from __future__ import annotations

from typing import Iterable
from typing import cast

from aiogram.types import InlineKeyboardMarkup, Message

from db_asyncpg.ports import (
    ClientTransferRepositoryPort,
    ClientWalletRepositoryPort,
    ClientWalletTransactionRepositoryPort,
)
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import ParsedCurrencyChange, WalletCommandResult
from services.wallets.mutation_service import CurrencyMutationService
from services.wallets.query_service import WalletQueryService
from services.wallets.text_builder import WalletTextBuilder
from services.wallets.undo_service import WalletUndoService


class WalletService:
    def __init__(
        self,
        *,
        repo: ClientTransferRepositoryPort,
        city_cash_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.text_builder = WalletTextBuilder()
        self.parser = WalletCommandParser(city_cash_chat_ids=city_cash_chat_ids)
        wallet_query_repo = cast(ClientWalletRepositoryPort, repo)
        wallet_undo_repo = cast(ClientWalletTransactionRepositoryPort, repo)
        self.query_service = WalletQueryService(repo=wallet_query_repo, text_builder=self.text_builder)
        self.mutation_service = CurrencyMutationService(
            repo=repo,
            parser=self.parser,
            text_builder=self.text_builder,
        )
        self.undo_service = WalletUndoService(
            repo=wallet_undo_repo,
            parser=self.parser,
            text_builder=self.text_builder,
        )

    @staticmethod
    def undo_kb(code: str, sign: str, amount_str: str) -> InlineKeyboardMarkup:
        return WalletTextBuilder.undo_kb(code, sign, amount_str)

    @classmethod
    def normalize_code_alias(cls, raw_code: str) -> str:
        return WalletCommandParser.normalize_code_alias(raw_code)

    @staticmethod
    def extract_expr_prefix(s: str) -> str:
        return WalletCommandParser.extract_expr_prefix(s)

    @staticmethod
    def split_city_transfer_tail(tail: str) -> tuple[str, str]:
        return WalletCommandParser.split_city_transfer_tail(tail)

    async def build_wallet_text(self, *, chat_id: int, chat_name: str) -> str:
        return await self.query_service.build_wallet_text(chat_id=chat_id, chat_name=chat_name)

    async def build_remove_currency_confirmation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
    ) -> WalletCommandResult:
        return await self.mutation_service.build_remove_currency_confirmation(
            chat_id=chat_id,
            chat_name=chat_name,
            raw_code=raw_code,
        )

    async def add_currency(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
        precision: int,
    ) -> WalletCommandResult:
        return await self.mutation_service.add_currency(
            chat_id=chat_id,
            chat_name=chat_name,
            raw_code=raw_code,
            precision=precision,
        )

    async def parse_currency_change(self, message: Message) -> ParsedCurrencyChange | None:
        return await self.parser.parse_currency_change(message)

    async def apply_currency_change(self, *, message: Message, parsed: ParsedCurrencyChange) -> WalletCommandResult:
        return await self.mutation_service.apply_currency_change(message=message, parsed=parsed)

    async def remove_currency_confirmed(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code_raw: str,
    ) -> WalletCommandResult:
        return await self.mutation_service.remove_currency_confirmed(
            chat_id=chat_id,
            chat_name=chat_name,
            code_raw=code_raw,
        )

    async def undo_operation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        message_id: int,
        code_raw: str,
        sign: str,
        amt_str: str,
    ) -> WalletCommandResult:
        return await self.undo_service.undo_operation(
            chat_id=chat_id,
            chat_name=chat_name,
            message_id=message_id,
            code_raw=code_raw,
            sign=sign,
            amt_str=amt_str,
        )
