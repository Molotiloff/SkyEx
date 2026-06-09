from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from aiogram.types import Message

from db_asyncpg.ports import ClientTransferRepositoryPort
from keyboards import rmcur_confirm_kb
from services.wallets.city_cash_media_store import CityCashMediaStore
from services.wallets.command_parser import WalletCommandParser
from services.wallets.models import ParsedCurrencyChange, WalletCommandResult
from services.wallets.text_builder import WalletTextBuilder
from utils.city_cash_transfer import city_cash_transfer_to_client
from utils.formatting import format_amount_core
from utils.info import get_chat_name

log = logging.getLogger("wallets")


class CurrencyMutationService:
    def __init__(
        self,
        *,
        repo: ClientTransferRepositoryPort,
        parser: WalletCommandParser | None = None,
        text_builder: WalletTextBuilder | None = None,
        city_cash_media_store: CityCashMediaStore | None = None,
    ) -> None:
        self.repo = repo
        self.parser = parser or WalletCommandParser()
        self.text_builder = text_builder or WalletTextBuilder()
        self.city_cash_media_store = city_cash_media_store

    async def _apply_wallet_delta(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code: str,
        amount: Decimal,
        expr: str,
        extra_comment: str,
        source: str,
        idempotency_key: str | None,
        with_undo: bool,
    ) -> WalletCommandResult:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            return WalletCommandResult(
                ok=False,
                message_text=(
                    f"Счёт {code} не найден.\n"
                    f"Подсказка: добавьте валюту командой /добавь {code} [точность]"
                ),
            )

        precision = int(acc["precision"]) if acc.get("precision") is not None else 2
        q = Decimal(10) ** -precision
        delta_quant = amount.copy_abs().quantize(q, rounding=ROUND_HALF_UP)

        if delta_quant == 0:
            min_step = format_amount_core(q, precision)
            return WalletCommandResult(
                ok=False,
                message_text=(
                    f"Сумма слишком мала для точности {precision}.\n"
                    f"Минимальный шаг для {code.upper()}: {min_step} {code.lower()}"
                ),
            )

        comment_for_txn = expr if not extra_comment else f"{expr} | {extra_comment}"

        if amount > 0:
            await self.repo.deposit(
                client_id=client_id,
                currency_code=code,
                amount=delta_quant,
                comment=comment_for_txn,
                source=source,
                idempotency_key=idempotency_key,
            )
            sign_flag = "+"
        else:
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=code,
                amount=delta_quant,
                comment=comment_for_txn,
                source=source,
                idempotency_key=idempotency_key,
            )
            sign_flag = "-"

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
        text = self.text_builder.currency_change_success(
            code=code,
            delta=delta_quant,
            precision=precision,
            sign=sign_flag,
            balance=cur_bal,
        )
        reply_markup = None
        if with_undo:
            reply_markup = self.text_builder.undo_kb(code, sign_flag, f"{delta_quant}")

        return WalletCommandResult(
            ok=True,
            message_text=text,
            reply_markup=reply_markup,
        )

    async def build_remove_currency_confirmation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
    ) -> WalletCommandResult:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        code = self.parser.normalize_code_alias(raw_code)

        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            return WalletCommandResult(ok=False, message_text=f"Счёт {code} не найден.")

        bal = Decimal(str(acc["balance"]))
        prec = int(acc["precision"])
        return WalletCommandResult(
            ok=True,
            message_text=self.text_builder.remove_currency_confirmation(
                code=code,
                balance=bal,
                precision=prec,
            ),
            reply_markup=rmcur_confirm_kb(code),
        )

    async def add_currency(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
        precision: int,
    ) -> WalletCommandResult:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        code = self.parser.normalize_code_alias(raw_code)

        if not (0 <= precision <= 8):
            return WalletCommandResult(
                ok=False,
                message_text="Ошибка: точность должна быть в диапазоне 0..8",
            )

        try:
            await self.repo.add_currency(client_id, code, precision=precision)
            return WalletCommandResult(
                ok=True,
                message_text=f"✅ Валюта {code} добавлена (символов после запятой = {precision})",
            )
        except Exception as e:
            log.exception("Failed to add currency %s for client %s", code, client_id)
            return WalletCommandResult(
                ok=False,
                message_text=f"Не удалось добавить валюту: {e}",
            )

    async def apply_currency_change(self, *, message: Message, parsed: ParsedCurrencyChange) -> WalletCommandResult:
        chat_id = message.chat.id
        chat_name = get_chat_name(message)

        log.info(
            "currency_change: chat_id=%s chat_name=%r msg_id=%s code=%s expr=%r amount=%s is_city_cash=%s "
            "client_name=%r extra_comment=%r has_photo=%s has_caption=%s",
            chat_id, chat_name, message.message_id,
            parsed.code, parsed.expr, str(parsed.amount),
            parsed.is_city_cash, parsed.client_name_for_transfer, parsed.extra_comment,
            bool(message.photo), bool(message.caption),
        )

        result = await self._apply_wallet_delta(
            chat_id=chat_id,
            chat_name=chat_name,
            code=parsed.code,
            amount=parsed.amount,
            expr=parsed.expr,
            extra_comment=parsed.extra_comment,
            source="command",
            idempotency_key=f"{chat_id}:{message.message_id}",
            with_undo=True,
        )
        text = result.message_text
        reply_markup = result.reply_markup

        if parsed.is_city_cash and parsed.client_name_for_transfer:
            res = await city_cash_transfer_to_client(
                repo=self.repo,
                bot=message.bot,
                src_message=message,
                media_store=self.city_cash_media_store,
                currency_code=parsed.code,
                amount_signed=parsed.amount,
                amount_expr=parsed.expr,
                client_name_exact=parsed.client_name_for_transfer,
                extra_comment=parsed.extra_comment,
            )
            log.info("city_transfer result: %s", res)

            if not res.ok:
                text += f"\n⚠️ {res.error or 'Не удалось продублировать операцию в чат клиента.'}"
            else:
                text += "\n✅ Транзакция проведена в чате у клиента!"

        return WalletCommandResult(
            ok=result.ok,
            message_text=text,
            reply_markup=reply_markup,
        )

    async def apply_external_currency_change(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code: str,
        amount: Decimal,
        expr: str,
        extra_comment: str = "",
        source: str = "external",
        idempotency_key: str | None = None,
    ) -> WalletCommandResult:
        return await self._apply_wallet_delta(
            chat_id=chat_id,
            chat_name=chat_name,
            code=self.parser.normalize_code_alias(code),
            amount=amount,
            expr=expr,
            extra_comment=extra_comment,
            source=source,
            idempotency_key=idempotency_key,
            with_undo=False,
        )

    async def remove_currency_confirmed(
        self,
        *,
        chat_id: int,
        chat_name: str,
        code_raw: str,
    ) -> WalletCommandResult:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        code = self.parser.normalize_code_alias(code_raw)

        try:
            ok = await self.repo.remove_currency(client_id, code)
            if ok:
                return WalletCommandResult(ok=True, message_text=f"🗑 Валюта {code} удалена из кошелька.")
            return WalletCommandResult(
                ok=False,
                message_text=f"Не удалось удалить {code}: счёт не найден или уже отключён.",
            )
        except Exception as e:
            log.exception("Failed to remove currency %s for client %s", code, client_id)
            return WalletCommandResult(ok=False, message_text=f"Ошибка удаления {code}: {e}")
