from __future__ import annotations

import logging
from decimal import Decimal, ROUND_HALF_UP

from aiogram.types import Message

from db_asyncpg.repo import Repo
from keyboards import rmcur_confirm_kb
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
        repo: Repo,
        parser: WalletCommandParser | None = None,
        text_builder: WalletTextBuilder | None = None,
    ) -> None:
        self.repo = repo
        self.parser = parser or WalletCommandParser()
        self.text_builder = text_builder or WalletTextBuilder()

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

        client_id = await self.repo.ensure_client(chat_id, chat_name)
        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == parsed.code), None)
        if not acc:
            return WalletCommandResult(
                ok=False,
                message_text=(
                    f"Счёт {parsed.code} не найден.\n"
                    f"Подсказка: добавьте валюту командой /добавь {parsed.code} [точность]"
                ),
            )

        precision = int(acc["precision"]) if acc.get("precision") is not None else 2
        q = Decimal(10) ** -precision
        delta_quant = parsed.amount.copy_abs().quantize(q, rounding=ROUND_HALF_UP)

        if delta_quant == 0:
            min_step = format_amount_core(q, precision)
            return WalletCommandResult(
                ok=False,
                message_text=(
                    f"Сумма слишком мала для точности {precision}.\n"
                    f"Минимальный шаг для {parsed.code.upper()}: {min_step} {parsed.code.lower()}"
                ),
            )

        idem = f"{chat_id}:{message.message_id}"
        comment_for_txn = parsed.expr if not parsed.extra_comment else f"{parsed.expr} | {parsed.extra_comment}"

        if parsed.amount > 0:
            await self.repo.deposit(
                client_id=client_id,
                currency_code=parsed.code,
                amount=delta_quant,
                comment=comment_for_txn,
                source="command",
                idempotency_key=idem,
            )
            sign_flag = "+"
        else:
            await self.repo.withdraw(
                client_id=client_id,
                currency_code=parsed.code,
                amount=delta_quant,
                comment=comment_for_txn,
                source="command",
                idempotency_key=idem,
            )
            sign_flag = "-"

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == parsed.code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")

        amt_str = f"{delta_quant}"
        text = self.text_builder.currency_change_success(
            code=parsed.code,
            delta=delta_quant,
            precision=precision,
            sign=sign_flag,
            balance=cur_bal,
        )
        reply_markup = self.text_builder.undo_kb(parsed.code, sign_flag, amt_str)

        if parsed.is_city_cash and parsed.client_name_for_transfer:
            log.info(
                "city_transfer: src_chat_id=%s src_msg_id=%s -> client_name=%r code=%s expr=%r amount=%s",
                chat_id, message.message_id, parsed.client_name_for_transfer,
                parsed.code, parsed.expr, str(parsed.amount),
            )
            res = await city_cash_transfer_to_client(
                repo=self.repo,
                bot=message.bot,
                src_message=message,
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
            ok=True,
            message_text=text,
            reply_markup=reply_markup,
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
            return WalletCommandResult(ok=False, message_text=f"Ошибка удаления {code}: {e}")
