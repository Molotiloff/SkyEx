from __future__ import annotations

from decimal import Decimal, InvalidOperation

from db_asyncpg.repo import Repo
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import WalletCommandResult
from services.wallets.text_builder import WalletTextBuilder
from utils.undos import undo_registry


class WalletUndoService:
    def __init__(
        self,
        *,
        repo: Repo,
        parser: WalletCommandParser | None = None,
        text_builder: WalletTextBuilder | None = None,
    ) -> None:
        self.repo = repo
        self.parser = parser or WalletCommandParser()
        self.text_builder = text_builder or WalletTextBuilder()

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
        code = self.parser.normalize_code_alias(code_raw)
        key = (chat_id, message_id)

        if await undo_registry.is_done(key):
            client_id = await self.repo.ensure_client(chat_id, chat_name)
            rows = await self.repo.snapshot_wallet(client_id)
            acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
            if acc:
                precision = int(acc["precision"])
                cur_bal = Decimal(str(acc["balance"]))
                return WalletCommandResult(
                    ok=False,
                    message_text=self.text_builder.undo_already_done_with_balance(
                        code=code,
                        balance=cur_bal,
                        precision=precision,
                    ),
                )
            return WalletCommandResult(ok=False, message_text=f"Операция уже отменена\nСчёт {code} не найден.")

        try:
            amount = Decimal(amt_str)
        except InvalidOperation:
            return WalletCommandResult(ok=False, message_text="Ошибка суммы")

        client_id = await self.repo.ensure_client(chat_id, chat_name)

        if sign == "+":
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=code,
                amount=amount,
                comment="undo",
                source="undo",
                idempotency_key=f"undo:{chat_id}:{message_id}",
            )
            applied_sign = "-"
        elif sign == "-":
            await self.repo.deposit(
                client_id=client_id,
                currency_code=code,
                amount=amount,
                comment="undo",
                source="undo",
                idempotency_key=f"undo:{chat_id}:{message_id}",
            )
            applied_sign = "+"
        else:
            return WalletCommandResult(ok=False, message_text="Некорректный знак")

        await undo_registry.mark_done(key)

        rows = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
        if acc:
            precision = int(acc["precision"])
            cur_bal = Decimal(str(acc["balance"]))
            return WalletCommandResult(
                ok=True,
                message_text=self.text_builder.undo_success(
                    code=code,
                    amount=amount,
                    precision=precision,
                    applied_sign=applied_sign,
                    balance=cur_bal,
                ),
            )

        return WalletCommandResult(ok=True, message_text=f"Счёт {code} не найден.")
