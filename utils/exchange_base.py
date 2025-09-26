# utils/exchange_base.py
from __future__ import annotations
import random
import html
from abc import ABC
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

from aiogram.types import Message

from db_asyncpg.repo import Repo
from utils.format_wallet_compact import format_wallet_compact
from utils.info import _fmt_rate, get_chat_name
from utils.calc import evaluate, CalcError
from utils.formatting import format_amount_core
from utils.requests import post_request_message


class AbstractExchangeHandler(ABC):
    """
    Базовая реализация обмена (под Postgres Repo).
    """

    def __init__(self, repo: Repo, request_chat_id: int | None = None) -> None:
        self.repo = repo
        self.request_chat_id = request_chat_id

    async def process(
        self,
        message: Message,
        recv_code: str,
        recv_amount_expr: str,
        pay_code: str,
        pay_amount_expr: str,
        *,
        recv_is_deposit: bool = True,
        pay_is_withdraw: bool = True,
        note: str | None = None,
    ) -> None:
        chat_id = message.chat.id
        chat_name = get_chat_name(message)

        # 1) выражения → Decimal
        try:
            recv_amount_raw = evaluate(recv_amount_expr)
            pay_amount_raw = evaluate(pay_amount_expr)
        except CalcError as e:
            await message.answer(f"Ошибка в выражении: {e}")
            return

        if recv_amount_raw <= 0 or pay_amount_raw <= 0:
            await message.answer("Суммы должны быть > 0")
            return

        recv_code = recv_code.strip().upper()
        pay_code = pay_code.strip().upper()

        try:
            # 2) клиент и счета
            client_id = await self.repo.ensure_client(chat_id=chat_id, name=chat_name)
            accounts = await self.repo.snapshot_wallet(client_id)

            def _find_acc(code: str):
                return next((r for r in accounts if str(r["currency_code"]).upper() == code), None)

            acc_recv = _find_acc(recv_code)
            acc_pay = _find_acc(pay_code)
            if not acc_recv or not acc_pay:
                missing = recv_code if not acc_recv else pay_code
                await message.answer(
                    f"Счёт {missing} не найден. Добавьте валюту командой: /addcur {missing} [точность]"
                )
                return

            recv_prec = int(acc_recv["precision"])
            pay_prec = int(acc_pay["precision"])

            # 3) квантование
            q_recv = Decimal(10) ** -recv_prec
            q_pay = Decimal(10) ** -pay_prec
            recv_amount = recv_amount_raw.quantize(q_recv, rounding=ROUND_HALF_UP)
            pay_amount = pay_amount_raw.quantize(q_pay, rounding=ROUND_HALF_UP)
            if recv_amount == 0 or pay_amount == 0:
                await message.answer("Сумма слишком мала для точности выбранных валют.")
                return

            # 4) курс
            try:
                if recv_code == "RUB" or pay_code == "RUB":
                    rub_raw = recv_amount_raw if recv_code == "RUB" else pay_amount_raw
                    other_raw = pay_amount_raw if recv_code == "RUB" else recv_amount_raw
                    if other_raw == 0:
                        await message.answer("Курс не определён (деление на ноль).")
                        return
                    auto_rate = rub_raw / other_raw
                else:
                    if recv_amount_raw == 0:
                        await message.answer("Курс не определён (деление на ноль).")
                        return
                    auto_rate = pay_amount_raw / recv_amount_raw

                if not auto_rate.is_finite() or auto_rate <= 0:
                    await message.answer("Курс невалидный.")
                    return

                q_rate = Decimal("1e-8")
                rate_q = auto_rate.quantize(q_rate, rounding=ROUND_HALF_UP)
                rate_str = _fmt_rate(rate_q)
            except (InvalidOperation, ZeroDivisionError):
                await message.answer("Ошибка расчёта курса.")
                return

            # 5) тексты заявок
            req_id = random.randint(10_000_000, 99_999_999)
            pretty_recv = format_amount_core(recv_amount, recv_prec)
            pretty_pay = format_amount_core(pay_amount, pay_prec)

            base_lines = [
                f"Заявка: <code>{req_id}</code>",
                "-----",
                f"Получаем: <code>{pretty_recv} {recv_code.lower()}</code>",
                f"Курс: <code>{rate_str}</code>",
                f"Отдаём: <code>{pretty_pay} {pay_code.lower()}</code>",
            ]
            if note:
                base_lines += ["----", f"Комментарий: <code>{html.escape(note)}</code>"]

            # Клиенту — без строки «Клиент» и без формулы
            client_text = "\n".join(base_lines)

            # В заявочный чат — добавляем строку «Клиент» сразу после номера заявки + формулу
            request_lines = [
                f"Заявка: <code>{req_id}</code>",
                f"Клиент: <b>{html.escape(chat_name)}</b>",
                "-----",
                f"Получаем: <code>{pretty_recv} {recv_code.lower()}</code>",
                f"Курс: <code>{rate_str}</code>",
                f"Отдаём: <code>{pretty_pay} {pay_code.lower()}</code>",
            ]
            if note:
                request_lines += ["----", f"Комментарий: <code>{html.escape(note)}</code>"]
            request_lines += ["----", f"Формула: <code>{html.escape(pay_amount_expr)}</code>"]

            request_text = "\n".join(request_lines)

            # 6) проведение операций
            idem_recv = f"{chat_id}:{message.message_id}:recv"
            idem_pay = f"{chat_id}:{message.message_id}:pay"

            recv_comment = recv_amount_expr if not note else f"{recv_amount_expr} | {note}"
            pay_comment = pay_amount_expr if not note else f"{pay_amount_expr} | {note}"

            try:
                if recv_is_deposit:
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=recv_code,
                        amount=recv_amount,
                        comment=recv_comment,
                        source="exchange",
                        idempotency_key=idem_recv,
                    )
                else:
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=recv_code,
                        amount=recv_amount,
                        comment=recv_comment,
                        source="exchange",
                        idempotency_key=idem_recv,
                    )

                if pay_is_withdraw:
                    await self.repo.withdraw(
                        client_id=client_id,
                        currency_code=pay_code,
                        amount=pay_amount,
                        comment=pay_comment,
                        source="exchange",
                        idempotency_key=idem_pay,
                    )
                else:
                    await self.repo.deposit(
                        client_id=client_id,
                        currency_code=pay_code,
                        amount=pay_amount,
                        comment=pay_comment,
                        source="exchange",
                        idempotency_key=idem_pay,
                    )

            except Exception as leg_err:
                try:
                    if recv_is_deposit:
                        await self.repo.withdraw(
                            client_id=client_id,
                            currency_code=recv_code,
                            amount=recv_amount,
                            comment="compensate",
                            source="exchange_compensate",
                            idempotency_key=f"{idem_recv}:undo",
                        )
                    else:
                        await self.repo.deposit(
                            client_id=client_id,
                            currency_code=recv_code,
                            amount=recv_amount,
                            comment="compensate",
                            source="exchange_compensate",
                            idempotency_key=f"{idem_recv}:undo",
                        )
                finally:
                    await message.answer(f"Не удалось выполнить обмен: {leg_err}")
                    return

            # 7) клиенту
            await message.answer(client_text, parse_mode="HTML")

            # 7.1) заявочный чат
            if self.request_chat_id:
                try:
                    await post_request_message(
                        bot=message.bot,
                        request_chat_id=self.request_chat_id,
                        text=request_text,
                        reply_markup=None,
                    )
                except Exception:
                    pass

            # 8) счета
            accounts2 = await self.repo.snapshot_wallet(client_id)
            compact = format_wallet_compact(accounts2, only_nonzero=True)

            if compact == "Пусто":
                await message.answer("Все счета нулевые. Посмотреть всё: /кошелек")
            else:
                safe_title = html.escape(f"Средств у {chat_name}:")
                safe_rows = html.escape(compact)
                await message.answer(f"<code>{safe_title}\n\n{safe_rows}</code>", parse_mode="HTML")

        except Exception as e:
            await message.answer(f"Не удалось выполнить операцию: {e}")
