from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from db_asyncpg.repo import Repo
from keyboards import rmcur_confirm_kb
from services.wallets.models import WalletCommandResult
from utils.calc import CalcError, evaluate
from utils.city_cash_transfer import city_cash_transfer_to_client
from utils.format_wallet_compact import format_wallet_compact
from utils.formatting import format_amount_core, format_amount_with_sign
from utils.info import get_chat_name
from utils.undos import undo_registry

log = logging.getLogger("wallets")


@dataclass(slots=True, frozen=True)
class ParsedCurrencyChange:
    code: str
    expr: str
    amount: Decimal
    tail: str
    is_city_cash: bool
    client_name_for_transfer: str
    extra_comment: str


class WalletService:
    _CURRENCY_ALIASES = {
        "usd": "USD", "дол": "USD", "долл": "USD", "доллар": "USD", "доллары": "USD",
        "usdt": "USDT", "юсдт": "USDT",
        "eur": "EUR", "евро": "EUR",
        "rub": "RUB", "руб": "RUB", "рубль": "RUB", "рубли": "RUB", "рублей": "RUB", "руб.": "RUB", "рубль.": "RUB",
        "usdw": "USDW", "долб": "USDW", "доллбел": "USDW", "долбел": "USDW",
        "eur500": "EUR500", "евро500": "EUR500",
    }

    def __init__(
        self,
        *,
        repo: Repo,
        city_cash_chat_ids: Iterable[int] | None = None,
    ) -> None:
        self.repo = repo
        self.city_cash_chat_ids = set(city_cash_chat_ids or [])

    @staticmethod
    def undo_kb(code: str, sign: str, amount_str: str) -> InlineKeyboardMarkup:
        data = f"undo:{code.upper()}:{sign}:{amount_str}"
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Откатить изменение", callback_data=data)]
            ]
        )

    @classmethod
    def normalize_code_alias(cls, raw_code: str) -> str:
        key = (raw_code or "").strip().lower()
        alias = cls._CURRENCY_ALIASES.get(key)
        if alias:
            return alias
        if key in ("руб", "рубль", "рубли", "рублей", "руб."):
            return "RUB"
        return (raw_code or "").strip().upper()

    @staticmethod
    def extract_expr_prefix(s: str) -> str:
        if not s:
            return ""
        first = s.strip().split(maxsplit=1)[0]
        return first.replace(",", ".")

    @staticmethod
    def split_city_transfer_tail(tail: str) -> tuple[str, str]:
        s = (tail or "").strip()
        if not s:
            return "", ""
        left, sep, right = s.partition("!")
        client_name = left.strip()
        comment = right.strip() if sep else ""
        return client_name, comment

    async def build_wallet_text(self, *, chat_id: int, chat_name: str) -> str:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        rows = await self.repo.snapshot_wallet(client_id)

        safe_title = html.escape(f"Средств у {chat_name}:")
        safe_rows = html.escape(format_wallet_compact(rows, only_nonzero=False))
        return f"<code>{safe_title}\n\n{safe_rows}</code>"

    async def build_remove_currency_confirmation(
        self,
        *,
        chat_id: int,
        chat_name: str,
        raw_code: str,
    ) -> WalletCommandResult:
        client_id = await self.repo.ensure_client(chat_id, chat_name)
        code = self.normalize_code_alias(raw_code)

        accounts = await self.repo.snapshot_wallet(client_id)
        acc = next((r for r in accounts if str(r["currency_code"]).upper() == code), None)
        if not acc:
            return WalletCommandResult(ok=False, message_text=f"Счёт {code} не найден.")

        bal = Decimal(str(acc["balance"]))
        prec = int(acc["precision"])
        pretty_bal = format_amount_core(bal, prec)

        warn = ""
        if bal != 0:
            warn = (
                f"\n⚠️ Внимание: баланс по {code} не нулевой ({pretty_bal} {code.lower()}). "
                f"Удаление допустимо — остаток будет потерян."
            )

        return WalletCommandResult(
            ok=True,
            message_text=f"Вы уверены, что хотите удалить валюту {code}?{warn}",
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
        code = self.normalize_code_alias(raw_code)

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

    async def parse_currency_change(self, message: Message) -> ParsedCurrencyChange | None:
        text = (message.text or message.caption or "").strip()
        if not text.startswith("/"):
            return None

        parts = text[1:].split(None, 1)
        if len(parts) < 2:
            return None

        raw_code = parts[0]
        code = self.normalize_code_alias(raw_code)

        expr_full = parts[1].strip()
        expr = self.extract_expr_prefix(expr_full)
        if not expr:
            raise ValueError("Сумма не указана. Пример: /USD 250")

        first_token = expr_full.strip().split(maxsplit=1)[0]
        tail = expr_full[len(first_token):].strip()

        try:
            amount = evaluate(expr)
        except CalcError as e:
            raise ValueError(f"Ошибка в выражении суммы: {e}") from e

        if amount == 0:
            raise ValueError("Сумма должна быть ненулевой")

        chat_id = message.chat.id
        is_city_cash = chat_id in self.city_cash_chat_ids

        if is_city_cash:
            client_name_for_transfer, extra_comment = self.split_city_transfer_tail(tail)
        else:
            client_name_for_transfer, extra_comment = "", tail

        return ParsedCurrencyChange(
            code=code,
            expr=expr,
            amount=amount,
            tail=tail,
            is_city_cash=is_city_cash,
            client_name_for_transfer=client_name_for_transfer,
            extra_comment=extra_comment,
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
            pretty_amt = format_amount_with_sign(delta_quant, precision, sign="+")
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
            pretty_amt = format_amount_with_sign(delta_quant, precision, sign="-")

        accounts2 = await self.repo.snapshot_wallet(client_id)
        acc2 = next((r for r in accounts2 if str(r["currency_code"]).upper() == parsed.code), None)
        cur_bal = Decimal(str(acc2["balance"])) if acc2 else Decimal("0")
        pretty_bal = format_amount_core(cur_bal, precision)

        amt_str = f"{delta_quant}"

        text = f"Запомнил. {pretty_amt}\nБаланс: {pretty_bal} {parsed.code.lower()}"
        reply_markup = self.undo_kb(parsed.code, sign_flag, amt_str)

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
        code = self.normalize_code_alias(code_raw)

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
        code = self.normalize_code_alias(code_raw)
        key = (chat_id, message_id)

        if await undo_registry.is_done(key):
            client_id = await self.repo.ensure_client(chat_id, chat_name)
            rows = await self.repo.snapshot_wallet(client_id)
            acc = next((r for r in rows if str(r["currency_code"]).upper() == code), None)
            if acc:
                precision = int(acc["precision"])
                cur_bal = Decimal(str(acc["balance"]))
                pretty_bal = format_amount_core(cur_bal, precision)
                return WalletCommandResult(
                    ok=False,
                    message_text=f"Операция уже отменена\nБаланс: {pretty_bal} {code.lower()}",
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
            pretty_bal = format_amount_core(cur_bal, precision)
            pretty_delta = format_amount_with_sign(amount, precision, sign=applied_sign)
            return WalletCommandResult(
                ok=True,
                message_text=f"Запомнил. {pretty_delta}\nБаланс: {pretty_bal} {code.lower()}",
            )

        return WalletCommandResult(ok=True, message_text=f"Счёт {code} не найден.")
